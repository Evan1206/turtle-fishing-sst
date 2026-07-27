"""Fetch and standardize apparent fishing effort from GFW 4Wings API v3."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

import pandas as pd
from dotenv import load_dotenv
from gfwapiclient import Client
from gfwapiclient.exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadGatewayError,
    GatewayTimeoutError,
    InternalServerError,
    RateLimitError,
    RequestTimeoutError,
    ServiceUnavailableError,
)

from src.align import normalize_lon, to_grid, to_time_bin
from src.config import BBOX, DATE_END, DATE_START, GRID_DEG, TIME_BIN


BASE_URL = "https://gateway.api.globalfishingwatch.org/v3/"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "raw" / "gfw_effort.parquet"
RETRYABLE_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    BadGatewayError,
    GatewayTimeoutError,
    InternalServerError,
    RateLimitError,
    RequestTimeoutError,
    ServiceUnavailableError,
)
REPORT_CHUNK_DAYS = 31


def _load_token() -> str:
    """Load the GFW token from the project .env without exposing its value."""
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.environ.get("GFW_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            f"GFW_API_TOKEN is missing; set it in {PROJECT_ROOT / '.env'}."
        )
    return token


def _validate_inputs(
    bbox: Mapping[str, float],
    start_date: str,
    end_date: str,
    grid_deg: float,
) -> None:
    required = {"lon_min", "lon_max", "lat_min", "lat_max"}
    missing = required.difference(bbox)
    if missing:
        raise ValueError(f"BBOX is missing keys: {sorted(missing)}")
    if not (-180 <= bbox["lon_min"] < bbox["lon_max"] <= 180):
        raise ValueError("BBOX longitude bounds must satisfy -180 <= min < max <= 180.")
    if not (-90 <= bbox["lat_min"] < bbox["lat_max"] <= 90):
        raise ValueError("BBOX latitude bounds must satisfy -90 <= min < max <= 90.")
    if date.fromisoformat(start_date) > date.fromisoformat(end_date):
        raise ValueError("start_date must not be after end_date.")
    if grid_deg <= 0:
        raise ValueError("grid_deg must be positive.")


def _bbox_geojson(bbox: Mapping[str, float]) -> dict[str, object]:
    """Return a GeoJSON polygon covering the configured bounding box."""
    west, east = bbox["lon_min"], bbox["lon_max"]
    south, north = bbox["lat_min"], bbox["lat_max"]
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


async def _request_report(
    *,
    token: str,
    bbox: Mapping[str, float],
    start_date: str,
    end_date: str,
    max_retries: int,
) -> pd.DataFrame:
    """Request a low-resolution daily 4Wings fishing-effort report."""
    client = Client(
        access_token=token,
        base_url=BASE_URL,
        timeout=120.0,
        connect_timeout=15.0,
    )
    try:
        for attempt in range(max_retries + 1):
            try:
                result = await client.fourwings.create_fishing_effort_report(
                    spatial_resolution="LOW",
                    temporal_resolution="DAILY",
                    start_date=start_date,
                    end_date=end_date,
                    spatial_aggregation=False,
                    geojson=_bbox_geojson(bbox),
                )
                return result.df(include={"lon", "lat", "date", "hours"})
            except AuthenticationError:
                # Authentication failures must be reported without trying another token.
                raise
            except RETRYABLE_ERRORS:
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(2**attempt)
    finally:
        await client._http_client.aclose()

    raise RuntimeError("GFW request ended without a response.")


def _date_chunks(
    start_date: str,
    end_date: str,
    *,
    chunk_days: int = REPORT_CHUNK_DAYS,
) -> list[tuple[str, str]]:
    """Split an inclusive date range into bounded, non-overlapping chunks."""
    if chunk_days <= 0:
        raise ValueError("chunk_days must be positive.")
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date.")

    chunks: list[tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=chunk_days - 1), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


async def _request_reports(
    *,
    token: str,
    bbox: Mapping[str, float],
    start_date: str,
    end_date: str,
    max_retries: int,
) -> pd.DataFrame:
    """Request date chunks sequentially to respect 4Wings report limits."""
    frames: list[pd.DataFrame] = []
    for chunk_start, chunk_end in _date_chunks(start_date, end_date):
        frame = await _request_report(
            token=token,
            bbox=bbox,
            start_date=chunk_start,
            end_date=chunk_end,
            max_retries=max_retries,
        )
        frames.append(frame)
        print(f"[gfw] downloaded {chunk_start}..{chunk_end}: {len(frame)} rows")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _standardize(
    raw: pd.DataFrame,
    *,
    bbox: Mapping[str, float],
    grid_deg: float,
    time_bin: str,
) -> pd.DataFrame:
    """Aggregate GFW's 0.1-degree daily response to the project grid and weeks."""
    columns = ["lon_bin", "lat_bin", "time_bin", "fishing_hours"]
    if raw.empty:
        return pd.DataFrame(
            {
                "lon_bin": pd.Series(dtype="float64"),
                "lat_bin": pd.Series(dtype="float64"),
                "time_bin": pd.Series(dtype="datetime64[ns]"),
                "fishing_hours": pd.Series(dtype="float64"),
            }
        )[columns]

    required = {"lon", "lat", "date", "hours"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"GFW response is missing fields: {sorted(missing)}")

    df = raw.rename(columns={"hours": "fishing_hours"}).copy()
    df["lon"] = pd.to_numeric(df["lon"], errors="raise")
    df["lat"] = pd.to_numeric(df["lat"], errors="raise")
    df["fishing_hours"] = pd.to_numeric(
        df["fishing_hours"], errors="raise"
    ).astype("float64")
    df["lon"] = normalize_lon(df["lon"])
    df["time_bin"] = to_time_bin(df["date"], time_bin)
    df["lon_bin"] = to_grid(df["lon"], grid_deg)
    df["lat_bin"] = to_grid(df["lat"], grid_deg)

    in_bbox = (
        df["lon_bin"].between(bbox["lon_min"], bbox["lon_max"], inclusive="both")
        & df["lat_bin"].between(
            bbox["lat_min"], bbox["lat_max"], inclusive="both"
        )
    )
    df = df.loc[in_bbox]

    if (df["fishing_hours"] < 0).any():
        raise ValueError("GFW returned negative fishing_hours.")

    return (
        df.groupby(["lon_bin", "lat_bin", "time_bin"], as_index=False)
        .agg(fishing_hours=("fishing_hours", "sum"))
        .sort_values(["lon_bin", "lat_bin", "time_bin"])
        .reset_index(drop=True)[columns]
    )


def fetch_gfw(
    *,
    bbox: Mapping[str, float] = BBOX,
    start_date: str = DATE_START,
    end_date: str = DATE_END,
    grid_deg: float = GRID_DEG,
    time_bin: str = TIME_BIN,
    output_path: str | Path | None = DEFAULT_OUTPUT,
    max_retries: int = 3,
) -> pd.DataFrame:
    """Fetch apparent fishing effort and return the module-B contract DataFrame."""
    _validate_inputs(bbox, start_date, end_date, grid_deg)
    if max_retries < 0:
        raise ValueError("max_retries must be non-negative.")

    raw = asyncio.run(
        _request_reports(
            token=_load_token(),
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
            max_retries=max_retries,
        )
    )
    result = _standardize(
        raw,
        bbox=bbox,
        grid_deg=grid_deg,
        time_bin=time_bin,
    )

    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        result.to_parquet(destination, index=False)
    return result


def main() -> None:
    """Run a small command-line fetch and print only non-sensitive summary values."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=DATE_START)
    parser.add_argument("--end-date", default=DATE_END)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        result = fetch_gfw(
            start_date=args.start_date,
            end_date=args.end_date,
            output_path=args.output,
        )
    except APIStatusError as exc:
        print(f"GFW API error ({exc.status_code}): {exc}")
        raise SystemExit(1) from exc

    print(f"grid_rows={len(result)}")
    print(f"fishing_hours_sum={result['fishing_hours'].sum():.6f}")


if __name__ == "__main__":
    main()
