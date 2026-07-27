# Findings

驗證結果由 Cowork 依 `spec.md` 第 4 節填寫。

---

## 2026-07-26 — src/fetch_gfw.py 一致性修正(非 Cowork 驗收,例外記錄)

**身分說明**:以下不是 Cowork 的 PASS/FAIL 驗收,是構思層(Claude Code)審查
Codex 產出時發現的架構一致性問題,經人確認後**例外直接修改**(正常應由 Codex
改)。記錄在此供 Codex 後續模組(尤其 `fetch_movebank.py`)對照,避免同樣問題
再發生。

**問題**:`src/fetch_gfw.py` 的 `_standardize()` 內嵌重寫了三段本應呼叫
`src/align.py` 共用函式的邏輯,而不是 import 它們:

| align.py 共用函式 | fetch_gfw.py 原本的內嵌版本(已移除) |
|---|---|
| `normalize_lon()` | `((df["lon"] + 180.0) % 360.0) - 180.0` |
| `to_grid()` | `np.floor(df["lon"] / grid_deg) * grid_deg` |
| `to_time_bin()` | `.dt.tz_localize(None).dt.to_period(time_bin).dt.start_time` |

**依據**:
- `spec.md` 模組 D:「統一規則(**所有模組都須遵守,寫成共用函式**)」——
  明文規定經度正規化、網格化、時間分箱都必須是共用函式,不是各模組各寫一份。
- `CONTEXT.md` 第 3 節陷阱 1:內嵌重寫是「疊圖不報錯但海龜點左右翻轉、跟漁船
  錯位」這類隱蔽錯誤的溫床——一旦 `align.py` 的邏輯之後被修正(例如 antimeridian
  邊界、NaN 處理),內嵌的私有副本不會跟著改,兩份邏輯就會悄悄分岔。
- 現階段數值上內嵌公式與 `align.py` 完全等價,**不是**當下算錯的 bug,是架構
  違規 + 未來 drift 風險。

**修改內容**:
- `src/fetch_gfw.py` 新增 `from src.align import normalize_lon, to_grid, to_time_bin`。
- `_standardize()` 內三處內嵌公式改為呼叫上述三個函式;因此移除了不再需要的
  `import numpy as np`。
- **`src/align.py` 本身未動一行**,符合「align.py 勿改邏輯」原則。

**驗證**:用合成資料跑過 `_standardize()`,確認行為與修改前一致——
lon=300 正規化為 -60、落在 bbox 外被濾除;兩筆 lon=125.3 的資料正確落入同一
(lon_bin, lat_bin, time_bin) 格並加總 fishing_hours。唯一可觀察到的差異是
改用 `to_time_bin()` 後會多印一個 `UserWarning`("Converting to PeriodArray/Index
representation will drop timezone information"),這是無害的、align.py 原本
就有的行為(此警告在 `align.py` 現有邏輯下本來就會出現,先前 fetch_gfw.py是
用 `tz_localize(None)` 繞過去而已),**非新增的錯誤**,不需處理。

**給 Codex 的提醒**:接下來實作 `fetch_movebank.py`(或 v2 的 `fetch_sst.py`)時,
凡是牽涉經度正規化、網格化(lon_bin/lat_bin)、週分箱(time_bin)的地方,一律
`from src.align import normalize_lon, to_grid, to_time_bin` 呼叫,不要重新推導
公式,即使公式再簡單也一樣。
