"""Align the MVP data, plot spatial overlap, and run Spearman correlation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm
from scipy.stats import spearmanr

from src.align import _self_check, align, grid_fishing, grid_turtles
from src.config import BBOX, DATE_END, DATE_START, STUDY_ID


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TURTLES = PROJECT_ROOT / "data" / "raw" / f"movebank_{STUDY_ID}.parquet"
DEFAULT_FISHING = PROJECT_ROOT / "data" / "raw" / "gfw_effort.parquet"
DEFAULT_ALIGNED = PROJECT_ROOT / "data" / "interim" / "aligned.parquet"
DEFAULT_FIGURE = PROJECT_ROOT / "outputs" / "figures" / "overlay.png"
DEFAULT_CORRELATION = PROJECT_ROOT / "outputs" / "tables" / "correlation.csv"

ALIGNED_COLUMNS = [
    "lon_bin",
    "lat_bin",
    "time_bin",
    "turtle_points",
    "turtle_indivs",
    "fishing_hours",
    "sst",
    "sst_anom",
]


def build_aligned(
    turtles: pd.DataFrame,
    fishing: pd.DataFrame,
) -> pd.DataFrame:
    """Grid both sources through align.py and preserve the outer-join signal."""
    turtle_grid = grid_turtles(turtles)
    fishing_grid = grid_fishing(fishing)
    result = align(turtle_grid, fishing_grid)
    _self_check(result)

    if list(result.columns) != ALIGNED_COLUMNS:
        raise AssertionError(f"Unexpected aligned columns: {list(result.columns)}")
    if result.duplicated(["lon_bin", "lat_bin", "time_bin"]).any():
        raise AssertionError("aligned data contains duplicate grid-week keys.")
    return result


def calculate_correlation(aligned: pd.DataFrame) -> pd.DataFrame:
    """Calculate the specified grid-week Spearman spatial association."""
    sample = aligned[["fishing_hours", "turtle_points"]].dropna()
    if len(sample) < 2:
        raise ValueError("At least two aligned grid-week rows are required.")
    if sample["fishing_hours"].nunique() < 2:
        raise ValueError("fishing_hours has no variation.")
    if sample["turtle_points"].nunique() < 2:
        raise ValueError("turtle_points has no variation.")

    statistic = spearmanr(
        sample["fishing_hours"],
        sample["turtle_points"],
        nan_policy="raise",
    )
    rho = float(statistic.statistic)
    p_value = float(statistic.pvalue)
    if not np.isfinite(rho) or not np.isfinite(p_value):
        raise ValueError("Spearman calculation returned a non-finite result.")

    return pd.DataFrame(
        {
            "spearman_rho": [rho],
            "p_value": [p_value],
            "n_samples": [int(len(sample))],
        }
    )


def _spatial_totals(aligned: pd.DataFrame) -> pd.DataFrame:
    """Collapse grid-week rows into two-year spatial totals for the overlay."""
    return (
        aligned.groupby(["lon_bin", "lat_bin"], as_index=False)
        .agg(
            fishing_hours=("fishing_hours", "sum"),
            turtle_points=("turtle_points", "sum"),
            turtle_indivs=("turtle_indivs", "max"),
        )
        .sort_values(["lat_bin", "lon_bin"])
        .reset_index(drop=True)
    )


def plot_overlay(
    aligned: pd.DataFrame,
    destination: str | Path,
) -> None:
    """Plot cumulative fishing effort with turtle grid density overlaid."""
    spatial = _spatial_totals(aligned)
    positive_fishing = spatial.loc[spatial["fishing_hours"] > 0]
    turtles = spatial.loc[spatial["turtle_points"] > 0]
    if positive_fishing.empty:
        raise ValueError("No positive fishing effort is available to plot.")
    if turtles.empty:
        raise ValueError("No turtle observations are available to plot.")

    fishing_grid = spatial.pivot(
        index="lat_bin",
        columns="lon_bin",
        values="fishing_hours",
    ).sort_index()
    fishing_values = np.ma.masked_less_equal(fishing_grid.to_numpy(), 0)

    figure, axis = plt.subplots(figsize=(12, 6.8), constrained_layout=True)
    heatmap = axis.pcolormesh(
        fishing_grid.columns.to_numpy(),
        fishing_grid.index.to_numpy(),
        fishing_values,
        shading="nearest",
        cmap="YlOrBr",
        norm=LogNorm(
            vmin=float(positive_fishing["fishing_hours"].min()),
            vmax=float(positive_fishing["fishing_hours"].max()),
        ),
        rasterized=True,
    )

    point_sizes = 12 + 18 * np.log1p(turtles["turtle_points"])
    axis.scatter(
        turtles["lon_bin"],
        turtles["lat_bin"],
        s=point_sizes,
        facecolors="#2E86AB",
        edgecolors="white",
        linewidths=0.45,
        alpha=0.78,
        label="Sea turtle grid density (size = fixes)",
        zorder=3,
    )

    axis.set(
        xlim=(BBOX["lon_min"], BBOX["lon_max"]),
        ylim=(BBOX["lat_min"], BBOX["lat_max"]),
        xlabel="Longitude (°E)",
        ylabel="Latitude (°N)",
    )
    axis.set_title(
        (
            "Fishing effort and tracked sea turtle locations\n"
            f"Cumulative apparent fishing hours and turtle fixes, "
            f"{DATE_START} to {DATE_END}; 0.25° grid"
        ),
        loc="left",
        pad=12,
        fontsize=14,
        color="#333333",
    )
    axis.grid(color="#D9D9D9", linewidth=0.5, alpha=0.55)
    axis.legend(loc="upper left", frameon=True, framealpha=0.92)

    colorbar = figure.colorbar(heatmap, ax=axis, pad=0.02)
    colorbar.set_label("Cumulative apparent fishing hours (log scale)")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=180, facecolor="white")
    plt.close(figure)


def describe_correlation(correlation: pd.DataFrame, alpha: float = 0.05) -> str:
    """Return a conservative, non-causal plain-language conclusion."""
    rho = float(correlation.at[0, "spearman_rho"])
    p_value = float(correlation.at[0, "p_value"])
    p_text = "p<1e-300" if p_value == 0 else f"p={p_value:.4g}"
    if p_value >= alpha:
        return (
            "結論：漁船作業小時與海龜定位點之間未呈現統計顯著的空間關聯"
            f"（Spearman ρ={rho:.4f}, {p_text}）；此分析不代表因果關係。"
        )
    direction = "正" if rho > 0 else "負"
    return (
        f"結論：漁船作業小時與海龜定位點呈現統計顯著的{direction}空間關聯"
        f"（Spearman ρ={rho:.4f}, {p_text}）；此分析不代表因果關係。"
    )


def run_analysis(
    *,
    turtle_path: str | Path = DEFAULT_TURTLES,
    fishing_path: str | Path = DEFAULT_FISHING,
    aligned_path: str | Path = DEFAULT_ALIGNED,
    figure_path: str | Path = DEFAULT_FIGURE,
    correlation_path: str | Path = DEFAULT_CORRELATION,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Run the complete MVP alignment and analysis pipeline."""
    turtles = pd.read_parquet(turtle_path)
    fishing = pd.read_parquet(fishing_path)
    aligned = build_aligned(turtles, fishing)

    aligned_path = Path(aligned_path)
    aligned_path.parent.mkdir(parents=True, exist_ok=True)
    aligned.to_parquet(aligned_path, index=False)

    correlation = calculate_correlation(aligned)
    correlation_path = Path(correlation_path)
    correlation_path.parent.mkdir(parents=True, exist_ok=True)
    correlation.to_csv(correlation_path, index=False)

    plot_overlay(aligned, figure_path)
    conclusion = describe_correlation(correlation)
    return aligned, correlation, conclusion


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turtles", type=Path, default=DEFAULT_TURTLES)
    parser.add_argument("--fishing", type=Path, default=DEFAULT_FISHING)
    parser.add_argument("--aligned", type=Path, default=DEFAULT_ALIGNED)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--correlation", type=Path, default=DEFAULT_CORRELATION)
    args = parser.parse_args()

    aligned, correlation, conclusion = run_analysis(
        turtle_path=args.turtles,
        fishing_path=args.fishing,
        aligned_path=args.aligned,
        figure_path=args.figure,
        correlation_path=args.correlation,
    )
    print(f"aligned_rows={len(aligned)}")
    print(correlation.to_string(index=False))
    print(conclusion)


if __name__ == "__main__":
    main()
