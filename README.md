# Turtle Fishing SST

海龜軌跡、漁船作業強度與海表溫度的空間交叉分析專案。

## 安裝

請使用 Python 3.11 以上版本及獨立虛擬環境：

```powershell
python -m pip install -e .
```

將 `.env.example` 複製為 `.env`，並設定 `GFW_API_TOKEN`。不得提交 `.env`。

## 全域參數

`src/config.py` 包含研究區域、日期範圍、網格解析度與時間分箱設定。

確認模組可匯入：

```powershell
python -c "from src import config; print('config import OK')"
```

## GFW apparent fishing effort

`src/fetch_gfw.py` 使用官方 `gfw-api-python-client`，從專案根目錄
`.env` 載入 `GFW_API_TOKEN`。執行完整設定：

```powershell
python -m src.fetch_gfw
```

執行一個月的最小連線測試：

```powershell
python -m src.fetch_gfw --start-date 2018-01-01 --end-date 2018-01-31
```

輸出資料寫入 `data/raw/gfw_effort.parquet`，欄位為
`lon_bin`、`lat_bin`、`time_bin`、`fishing_hours`。
完整日期範圍會依 31 天順序分塊下載，以符合 4Wings 報表限制並降低逾時風險；
所有分塊最後合併成同一份 Parquet。

## Movebank 海龜軌跡

`src/fetch_movebank.py` 從 `src/config.py` 讀取 `STUDY_ID`、BBOX 與日期範圍：

```powershell
python -m src.fetch_movebank
```

目前選定的 CC0 study 可直接從 Movebank Data Repository 公開倉儲下載。
若改用其他 study，請在 `.env` 同時設定 `MOVEBANK_USERNAME` 與
`MOVEBANK_PASSWORD`，模組便會改走 Movebank `direct-read` API。帳密不得提交。

輸出寫入 `data/raw/movebank_<study_id>.parquet`，欄位固定為
`individual_id`、`timestamp`、`lon`、`lat`、`species`。時間戳統一為 UTC；
資料會依 config 日期與 BBOX 過濾，並去除重複 `(individual_id, timestamp)`。

## 對齊與分析

完成 Movebank 與 GFW 下載後執行：

```powershell
python -m src.analyze
```

此指令會呼叫 `src.align` 的既有共用函式，產生：

- `data/interim/aligned.parquet`：outer join 後的 `(lon_bin, lat_bin, time_bin)` 資料。
- `outputs/figures/overlay.png`：累計漁船作業熱區與海龜定位密度疊圖。
- `outputs/tables/correlation.csv`：`fishing_hours` 與 `turtle_points` 的
  Spearman 係數、p 值與格週樣本數。

終端結論只描述正／負或不顯著的「空間關聯」，不推論因果。

## 模組 F 靜態互動網站

先把已審查的分析結果轉成網站 JSON：

```powershell
python -m src.export_web_data
```

這會將 `data/interim/aligned.parquet` 與
`outputs/tables/correlation.csv` 轉為可稽核的完整 `outputs/web/grid.json`、
`outputs/web/summary.json`，並額外產生 `outputs/web/weeks.json` 與
`outputs/web/weeks/<週起始日>.json`。前端只先載入週索引，拖動時間滑桿時才讀取
該週的小檔案，不會一次下載完整兩年份的 33MB 網格資料。

`web/` 是純 HTML/CSS/JavaScript + Leaflet，讀取上述固定歷史快照。請從專案根目錄
啟動本機靜態伺服器：

```powershell
python -m http.server 8000
```

再開啟 `http://localhost:8000/web/`。網站包含逐週滑桿、漁船／海龜圖層切換、
網格與定位點 tooltip、連續藍色色階圖例、Spearman 結果面板及必要免責文字。

依模組 F 阻塞規則，本階段沒有執行任何公開部署。

## 實作假設

- 專案實際資料夾名稱與 spec 第 1 節示意名稱不同；保留現有資料夾名稱，不影響 Python 專案名稱。
- 收到的 token 檔案名稱不是規格要求的 `.env`；在不讀取或改寫內容的前提下，將其重新命名為 `.env`。
- 依「一次一個模組」原則，目前已實作 `src/config.py`、
  `src/fetch_gfw.py`、`src/fetch_movebank.py` 與 `src/analyze.py`；
  SST 模組留待 v2。
- 官方 `gfw-api-python-client` 可用版本要求 Python 3.11 以上，因此專案最低版本設為 Python 3.11。
- 4Wings API 不提供 0.25° 或週級原生解析度；先請求官方 LOW（0.1°）
  逐日資料，再依 `GRID_DEG` 與 `TIME_BIN` 聚合成 0.25°、週級資料。
- Movebank `direct-read` 即使是公開 study 仍要求帳密；目前選定的 CC0 study
  在 Data Repository 有永久公開事件 CSV，因此未設定帳密時使用該公開檔案。
- `summary.json.n_individuals` 是「全期唯一追蹤個體數」，無法由格週彙總後的
  `aligned.parquet.turtle_indivs` 精確反推；exporter 因此只為這個欄位讀取既有
  `data/raw/movebank_<study_id>.parquet` 的 `individual_id` 並計算唯一值。
  其餘網站欄位仍完全取自 spec 指定的 aligned/correlation 輸入。
