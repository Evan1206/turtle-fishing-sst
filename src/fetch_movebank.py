"""Fetch and standardize sea-turtle tracking events from Movebank."""

from __future__ import annotations

import argparse
import os
from io import BytesIO
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.align import normalize_lon
from src.config import BBOX, DATE_END, DATE_START, STUDY_ID


DIRECT_READ_URL = "https://www.movebank.org/movebank/service/direct-read"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The selected study is CC0 and archived in the Movebank Data Repository.
# This permanent bitstream is the public fallback when API credentials are absent.
PUBLIC_EVENT_FILES = {
    1417866900: (
        "https://datarepository.movebank.org/server/api/core/bitstreams/"
        "7045a29d-6034-43ff-8ec8-7ce46c736691/content"
    ),
}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "individual_id": (
        "individual-local-identifier",
        "individual_local_identifier",
        "individual local identifier",
        "animal-id",
        "animal_id",
        "animal id",
    ),
    "timestamp": (
        "timestamp",
        "event-timestamp",
        "event_timestamp",
        "event timestamp",
    ),
    "lon": (
        "location-long",
        "location_long",
        "location long",
        "longitude",
        "lon",
    ),
    "lat": (
        "location-lat",
        "location_lat",
        "location lat",
        "latitude",
        "lat",
    ),
    "species": (
        "individual-taxon-canonical-name",
        "individual_taxon_canonical_name",
        "individual taxon canonical name",
        "animal-taxon",
        "animal_taxon",
        "animal taxon",
        "species",
    ),
}

OUTPUT_COLUMNS = ["individual_id", "timestamp", "lon", "lat", "species"]


def _http_session() -> requests.Session:
    """Create a session with bounded retries for transient HTTP failures."""
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _movebank_credentials() -> tuple[str, str] | None:
    """Load optional Movebank credentials without logging their values."""
    load_dotenv(PROJECT_ROOT / ".env")
    username = os.environ.get("MOVEBANK_USERNAME", "").strip()
    password = os.environ.get("MOVEBANK_PASSWORD", "").strip()
    if bool(username) != bool(password):
        raise RuntimeError(
            "Set both MOVEBANK_USERNAME and MOVEBANK_PASSWORD, or neither."
        )
    return (username, password) if username else None


def _download_events(study_id: int, timeout: float = 120.0) -> pd.DataFrame:
    """Download event CSV via direct-read or the selected study's CC0 archive."""
    credentials = _movebank_credentials()
    session = _http_session()
    try:
        if credentials is not None:
            response = session.get(
                DIRECT_READ_URL,
                params={"entity_type": "event", "study_id": study_id},
                auth=credentials,
                timeout=timeout,
            )
        else:
            public_url = PUBLIC_EVENT_FILES.get(study_id)
            if public_url is None:
                raise RuntimeError(
                    "This study has no configured public archive. Set "
                    "MOVEBANK_USERNAME and MOVEBANK_PASSWORD for direct-read access."
                )
            response = session.get(public_url, timeout=timeout)

        response.raise_for_status()
        return pd.read_csv(BytesIO(response.content), low_memory=False)
    finally:
        session.close()


def _resolve_columns(
    columns: Sequence[object],
    aliases: Mapping[str, Sequence[str]] = COLUMN_ALIASES,
) -> dict[str, str]:
    """Map source-specific Movebank names onto the module-A contract."""
    normalized: dict[str, str] = {}
    for column in columns:
        source_name = str(column)
        key = source_name.strip().lower()
        if key in normalized:
            raise ValueError(f"Movebank response has duplicate column: {source_name!r}")
        normalized[key] = source_name

    resolved: dict[str, str] = {}
    for destination, candidates in aliases.items():
        match = next(
            (normalized[candidate.lower()] for candidate in candidates if candidate.lower() in normalized),
            None,
        )
        if match is None:
            raise ValueError(
                f"Movebank response has no column for {destination!r}; "
                f"accepted aliases: {list(candidates)}"
            )
        resolved[match] = destination
    return resolved


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "individual_id": pd.Series(dtype="object"),
            "timestamp": pd.Series(dtype="datetime64[ns, UTC]"),
            "lon": pd.Series(dtype="float64"),
            "lat": pd.Series(dtype="float64"),
            "species": pd.Series(dtype="object"),
        }
    )[OUTPUT_COLUMNS]


def standardize_movebank(
    raw: pd.DataFrame,
    *,
    bbox: Mapping[str, float] = BBOX,
    start_date: str = DATE_START,
    end_date: str = DATE_END,
) -> pd.DataFrame:
    """Apply field mapping and MVP cleaning to raw Movebank events."""
    if raw.empty:
        return _empty_result()

    rename_map = _resolve_columns(raw.columns)
    df = raw.rename(columns=rename_map)[OUTPUT_COLUMNS].copy()
    df["individual_id"] = df["individual_id"].astype("string").str.strip()
    df["species"] = df["species"].astype("string").str.strip()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")

    df = df.dropna(subset=OUTPUT_COLUMNS)
    df = df.loc[(df["individual_id"] != "") & (df["species"] != "")]
    df["lon"] = normalize_lon(df["lon"]).to_numpy()

    start = pd.Timestamp(start_date, tz="UTC")
    end_exclusive = pd.Timestamp(end_date, tz="UTC") + pd.Timedelta(days=1)
    in_period = df["timestamp"].ge(start) & df["timestamp"].lt(end_exclusive)
    in_bbox = (
        df["lon"].between(bbox["lon_min"], bbox["lon_max"], inclusive="both")
        & df["lat"].between(bbox["lat_min"], bbox["lat_max"], inclusive="both")
    )
    df = df.loc[in_period & in_bbox]

    df = (
        df.drop_duplicates(subset=["individual_id", "timestamp"], keep="first")
        .sort_values(["individual_id", "timestamp"])
        .reset_index(drop=True)
    )
    df["individual_id"] = df["individual_id"].astype(object)
    df["species"] = df["species"].astype(object)
    df["lon"] = df["lon"].astype("float64")
    df["lat"] = df["lat"].astype("float64")
    return df[OUTPUT_COLUMNS]


def _validate_contract(df: pd.DataFrame) -> None:
    """Check the automated module-A acceptance criteria before writing."""
    if list(df.columns) != OUTPUT_COLUMNS:
        raise AssertionError(f"Unexpected columns: {list(df.columns)}")
    if str(df["timestamp"].dtype) != "datetime64[ns, UTC]":
        raise AssertionError(f"Unexpected timestamp dtype: {df['timestamp'].dtype}")
    if df["lon"].dtype != "float64" or df["lat"].dtype != "float64":
        raise AssertionError("lon and lat must both be float64.")
    if not df["lon"].between(-180, 180, inclusive="both").all():
        raise AssertionError("Longitude outside [-180, 180].")
    if not df["lat"].between(-90, 90, inclusive="both").all():
        raise AssertionError("Latitude outside [-90, 90].")
    if df.duplicated(["individual_id", "timestamp"]).any():
        raise AssertionError("Duplicate (individual_id, timestamp) rows remain.")


def fetch_movebank(
    *,
    study_id: int = STUDY_ID,
    bbox: Mapping[str, float] = BBOX,
    start_date: str = DATE_START,
    end_date: str = DATE_END,
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    """Fetch, clean, validate, and optionally save Movebank tracking events."""
    raw = _download_events(study_id)
    result = standardize_movebank(
        raw,
        bbox=bbox,
        start_date=start_date,
        end_date=end_date,
    )
    _validate_contract(result)

    destination = (
        Path(output_path)
        if output_path is not None
        else PROJECT_ROOT / "data" / "raw" / f"movebank_{study_id}.parquet"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(destination, index=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study-id", type=int, default=STUDY_ID)
    parser.add_argument("--start-date", default=DATE_START)
    parser.add_argument("--end-date", default=DATE_END)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = fetch_movebank(
        study_id=args.study_id,
        start_date=args.start_date,
        end_date=args.end_date,
        output_path=args.output,
    )
    print(f"rows={len(result)}")
    print(f"individuals={result['individual_id'].nunique()}")
    print(f"species={','.join(sorted(result['species'].unique()))}")


if __name__ == "__main__":
    main()
