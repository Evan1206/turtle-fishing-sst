"""
align.py — 三源對齊核心（骨架）

這是專案概念核心，也是最容易出隱蔽錯誤的地方。
由構思層先寫好方向正確的骨架，Codex 據此往上長 fetch 模組。

統一規則：
  1. 經度一律 -180~180（進來先 normalize_lon）
  2. 網格化：floor(x / GRID_DEG) * GRID_DEG
  3. 時間分箱：週（週起始日為鍵）
  4. 對齊用 outer join，保留「有漁船無海龜」與「有海龜無漁船」兩類格子

Codex TODO 已用 # TODO(codex) 標出。
"""

from __future__ import annotations
import numpy as np
import pandas as pd

# 這些之後從 src/config.py import，骨架先放預設值
GRID_DEG = 0.25
TIME_BIN = "W"


def normalize_lon(lon: pd.Series | np.ndarray) -> pd.Series:
    """把 0~360 或任意慣例的經度統一成 -180~180。

    OISST 原生是 0~360，這個函式是全專案防呆的第一道關卡。
    """
    lon = pd.Series(lon, dtype="float64")
    # ((lon + 180) mod 360) - 180 → 保證落在 [-180, 180)
    return ((lon + 180.0) % 360.0) - 180.0


def to_grid(coord: pd.Series, grid_deg: float = GRID_DEG) -> pd.Series:
    """把連續座標落到網格中心（用 floor 到網格左下角）。"""
    return np.floor(coord / grid_deg) * grid_deg


def to_time_bin(ts: pd.Series, freq: str = TIME_BIN) -> pd.Series:
    """把時間戳分箱到週起始日。輸入須為 UTC datetime。"""
    ts = pd.to_datetime(ts, utc=True)
    # 用週一為週起點，回傳當週的起始日期
    return ts.dt.to_period(freq).dt.start_time


def grid_turtles(df_turtle: pd.DataFrame) -> pd.DataFrame:
    """海龜軌跡點 → 逐 (格, 週) 的出現次數與個體數。

    輸入契約（見 spec 模組 A）：
        individual_id, timestamp(UTC), lon(-180~180), lat, species
    """
    df = df_turtle.copy()
    df["lon"] = normalize_lon(df["lon"])
    df["lon_bin"] = to_grid(df["lon"])
    df["lat_bin"] = to_grid(df["lat"])
    df["time_bin"] = to_time_bin(df["timestamp"])

    out = (
        df.groupby(["lon_bin", "lat_bin", "time_bin"])
        .agg(
            turtle_points=("individual_id", "size"),
            turtle_indivs=("individual_id", "nunique"),
        )
        .reset_index()
    )
    return out


def grid_fishing(df_gfw: pd.DataFrame) -> pd.DataFrame:
    """GFW 作業資料 → 逐 (格, 週) 的作業小時。

    輸入契約（見 spec 模組 B）：
        lon_bin, lat_bin, time_bin, fishing_hours
    若 GFW 回傳的已是網格資料，這裡主要做經度正規化與時間分箱對齊。
    """
    df = df_gfw.copy()
    df["lon_bin"] = normalize_lon(df["lon_bin"])
    df["lon_bin"] = to_grid(df["lon_bin"])
    df["lat_bin"] = to_grid(df["lat_bin"])
    df["time_bin"] = to_time_bin(df["time_bin"])

    out = (
        df.groupby(["lon_bin", "lat_bin", "time_bin"])
        .agg(fishing_hours=("fishing_hours", "sum"))
        .reset_index()
    )
    return out


def align(
    turtles: pd.DataFrame,
    fishing: pd.DataFrame,
    sst: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """三源對齊。

    關鍵：outer join。有漁船無海龜、有海龜無漁船的格子都要保留，
    這兩類正是「迴避/重疊」訊號所在。
    """
    keys = ["lon_bin", "lat_bin", "time_bin"]

    merged = turtles.merge(fishing, on=keys, how="outer")

    # 海龜側缺值 = 該格該週沒有海龜點 → 補 0
    merged["turtle_points"] = merged["turtle_points"].fillna(0).astype(int)
    merged["turtle_indivs"] = merged["turtle_indivs"].fillna(0).astype(int)
    # 漁船側缺值 = 該格該週沒有作業 → 補 0
    merged["fishing_hours"] = merged["fishing_hours"].fillna(0.0)

    if sst is not None:
        # v2：SST 也做同樣的網格/時間對齊後 left join 上來
        sst = sst.copy()
        sst["lon_bin"] = to_grid(normalize_lon(sst["lon_bin"]))
        sst["lat_bin"] = to_grid(sst["lat_bin"])
        sst["time_bin"] = to_time_bin(sst["time_bin"])
        merged = merged.merge(
            sst[["lon_bin", "lat_bin", "time_bin", "sst", "sst_anom"]],
            on=keys,
            how="left",
        )
    else:
        merged["sst"] = np.nan
        merged["sst_anom"] = np.nan

    return merged.sort_values(keys).reset_index(drop=True)


def _self_check(df: pd.DataFrame) -> None:
    """對照 spec 驗收標準 C 的自動化部分。align 後可呼叫來早期抓錯。"""
    assert (df["lon_bin"] <= 180).all(), "經度正規化失敗：出現 >180 的 lon_bin"
    assert (df["lon_bin"] >= -180).all(), "經度正規化失敗：出現 <-180 的 lon_bin"
    has_fish_no_turtle = ((df["fishing_hours"] > 0) & (df["turtle_points"] == 0)).any()
    has_turtle_no_fish = ((df["turtle_points"] > 0) & (df["fishing_hours"] == 0)).any()
    assert has_fish_no_turtle, "對齊可疑：不存在『有漁船無海龜』的格子"
    assert has_turtle_no_fish, "對齊可疑：不存在『有海龜無漁船』的格子"
    print("[align] self-check passed:", len(df), "個 (格,週) 單元")


if __name__ == "__main__":
    # 煙霧測試：用假資料驗證對齊邏輯本身正確（不需真 API）
    turtles = pd.DataFrame({
        "individual_id": ["t1", "t1", "t2"],
        "timestamp": pd.to_datetime(
            ["2018-03-01", "2018-03-02", "2018-03-08"], utc=True
        ),
        "lon": [125.3, 125.4, 300.0],   # 第三筆用 300 測 0~360 正規化
        "lat": [22.1, 22.2, 30.5],
        "species": ["Chelonia mydas"] * 3,
    })
    fishing = pd.DataFrame({
        "lon_bin": [125.25, 200.0],     # 200 測正規化
        "lat_bin": [22.0, 30.5],
        "time_bin": pd.to_datetime(["2018-03-01", "2018-03-08"], utc=True),
        "fishing_hours": [12.5, 8.0],
    })
    g_t = grid_turtles(turtles)
    g_f = grid_fishing(fishing)
    aligned = align(g_t, g_f)
    _self_check(aligned)
    print(aligned.to_string(index=False))
