"""Export reviewed analysis outputs as the static website JSON contract."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

from src.analyze import describe_correlation
from src.config import BBOX, DATE_END, DATE_START, GRID_DEG, STUDY_ID


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALIGNED = PROJECT_ROOT / "data" / "interim" / "aligned.parquet"
DEFAULT_CORRELATION = PROJECT_ROOT / "outputs" / "tables" / "correlation.csv"
DEFAULT_TURTLES = PROJECT_ROOT / "data" / "raw" / f"movebank_{STUDY_ID}.parquet"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "web"

GRID_COLUMNS = [
    "lon_bin",
    "lat_bin",
    "time_bin",
    "turtle_points",
    "turtle_indivs",
    "fishing_hours",
]
CORRELATION_COLUMNS = ["spearman_rho", "p_value", "n_samples"]


def _validate_inputs(
    aligned: pd.DataFrame,
    correlation: pd.DataFrame,
    turtles: pd.DataFrame,
) -> None:
    missing_grid = sorted(set(GRID_COLUMNS).difference(aligned.columns))
    if missing_grid:
        raise ValueError(f"aligned data is missing columns: {missing_grid}")
    missing_correlation = sorted(
        set(CORRELATION_COLUMNS).difference(correlation.columns)
    )
    if missing_correlation:
        raise ValueError(
            f"correlation data is missing columns: {missing_correlation}"
        )
    if "individual_id" not in turtles.columns:
        raise ValueError("Movebank data is missing individual_id.")
    if len(correlation) != 1:
        raise ValueError("correlation data must contain exactly one result row.")
    if aligned.empty:
        raise ValueError("aligned data is empty.")
    if aligned.duplicated(["lon_bin", "lat_bin", "time_bin"]).any():
        raise ValueError("aligned data contains duplicate grid-week keys.")

    numeric = aligned[
        ["lon_bin", "lat_bin", "turtle_points", "turtle_indivs", "fishing_hours"]
    ]
    if numeric.isna().any().any():
        raise ValueError("website grid columns must not contain missing values.")
    if (aligned[["turtle_points", "turtle_indivs", "fishing_hours"]] < 0).any().any():
        raise ValueError("website grid counts and effort must be non-negative.")
    if not aligned["lon_bin"].between(BBOX["lon_min"], BBOX["lon_max"]).all():
        raise ValueError("website grid contains longitude outside config BBOX.")
    if not aligned["lat_bin"].between(BBOX["lat_min"], BBOX["lat_max"]).all():
        raise ValueError("website grid contains latitude outside config BBOX.")

    parsed_time = pd.to_datetime(aligned["time_bin"], errors="coerce")
    if parsed_time.isna().any():
        raise ValueError("website grid contains invalid time_bin values.")
    earliest_allowed = pd.Timestamp(DATE_START) - pd.Timedelta(days=7)
    latest_allowed = pd.Timestamp(DATE_END)
    if not parsed_time.between(earliest_allowed, latest_allowed).all():
        raise ValueError("website grid contains time_bin outside the analysis window.")

    result = correlation.iloc[0]
    for column in CORRELATION_COLUMNS:
        if not math.isfinite(float(result[column])):
            raise ValueError(f"correlation column {column} must be finite.")
    if int(result["n_samples"]) != len(aligned):
        raise ValueError("correlation n_samples does not match aligned row count.")
    if turtles["individual_id"].dropna().nunique() < 1:
        raise ValueError("Movebank data contains no identifiable individuals.")


def export_web_data(
    *,
    aligned_path: str | Path = DEFAULT_ALIGNED,
    correlation_path: str | Path = DEFAULT_CORRELATION,
    turtles_path: str | Path = DEFAULT_TURTLES,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, Path, Path]:
    """Write the audit JSON plus an index and per-week browser payloads."""
    aligned = pd.read_parquet(aligned_path)
    correlation = pd.read_csv(correlation_path)
    turtles = pd.read_parquet(turtles_path, columns=["individual_id"])
    _validate_inputs(aligned, correlation, turtles)

    grid = aligned.loc[:, GRID_COLUMNS].copy()
    grid["time_bin"] = pd.to_datetime(grid["time_bin"]).dt.strftime("%Y-%m-%d")
    grid["turtle_points"] = grid["turtle_points"].astype("int64")
    grid["turtle_indivs"] = grid["turtle_indivs"].astype("int64")
    grid = grid.sort_values(
        ["time_bin", "lat_bin", "lon_bin"],
        kind="stable",
    ).reset_index(drop=True)

    result = correlation.iloc[0]
    summary = {
        "correlation": float(result["spearman_rho"]),
        "p_value": float(result["p_value"]),
        "n_cells": int(result["n_samples"]),
        "n_individuals": int(turtles["individual_id"].dropna().nunique()),
        "conclusion_text": describe_correlation(correlation),
    }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    grid_path = output_dir / "grid.json"
    summary_path = output_dir / "summary.json"
    weeks_path = output_dir / "weeks.json"
    week_dir = output_dir / "weeks"
    week_dir.mkdir(parents=True, exist_ok=True)

    grid.to_json(
        grid_path,
        orient="records",
        force_ascii=False,
        double_precision=12,
    )

    week_entries = []
    for time_bin, week in grid.groupby("time_bin", sort=True):
        filename = f"{time_bin}.json"
        destination = week_dir / filename
        week.to_json(
            destination,
            orient="records",
            force_ascii=False,
            double_precision=12,
        )
        week_entries.append(
            {
                "time_bin": time_bin,
                "path": f"weeks/{filename}",
                "row_count": int(len(week)),
                "bytes": destination.stat().st_size,
            }
        )

    weeks_manifest = {
        "grid_deg": GRID_DEG,
        "max_fishing_hours": float(grid["fishing_hours"].max()),
        "n_rows": int(len(grid)),
        "weeks": week_entries,
    }
    weeks_path.write_text(
        json.dumps(weeks_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return grid_path, summary_path, weeks_path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aligned", type=Path, default=DEFAULT_ALIGNED)
    parser.add_argument("--correlation", type=Path, default=DEFAULT_CORRELATION)
    parser.add_argument("--turtles", type=Path, default=DEFAULT_TURTLES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    grid_path, summary_path, weeks_path = export_web_data(
        aligned_path=args.aligned,
        correlation_path=args.correlation,
        turtles_path=args.turtles,
        output_dir=args.output_dir,
    )
    grid_rows = len(pd.read_json(grid_path))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"grid_json={grid_path} rows={grid_rows}")
    print(
        f"summary_json={summary_path} "
        f"rho={summary['correlation']:.6f} "
        f"n_cells={summary['n_cells']} "
        f"n_individuals={summary['n_individuals']}"
    )
    manifest = json.loads(weeks_path.read_text(encoding="utf-8"))
    print(
        f"weeks_json={weeks_path} "
        f"weeks={len(manifest['weeks'])} "
        f"largest_week_bytes={max(week['bytes'] for week in manifest['weeks'])}"
    )


if __name__ == "__main__":
    main()
