# spec.md — 海龜 × 漁船 × 海溫 空間交叉分析

> 本檔案是三個角色之間的「唯一真相來源」。
> - **構思層(Claude 對話)**：維護本檔案的假說與驗收標準。
> - **執行層(Codex）**：讀本檔案，實作 `src/` 下的模組，讓程式跑得動。
> - **驗證層(Cowork）**：讀本檔案的「驗收標準」，檢查 Codex 產出是否為真，結果寫進 `findings.md`。
>
> 規則：任何 agent 不得修改本檔案的「驗收標準」區塊。要改假說或標準，回到構思層討論後由人更新。

---

## 0. 專案命題（可證偽）

在特定海域與時段，漁船作業強度高的網格，與被追蹤海龜的出現位置，在空間上呈現顯著的迴避（負相關）或重疊（衝突熱點）；且此空間關係隨海表溫度異常而移動。

**三層遞進，每層都是可交付成果：**
1. 描述層：海龜軌跡 + 漁船作業熱區疊在同一張地圖。
2. 關聯層：切網格，逐格算「漁船作業小時」與「海龜出現次數」，檢定相關性。
3. 機制層：引入 SST，檢查海龜分布中心與漁船作業中心是否隨海溫距平同向移動。

**MVP 定義 = 只做第 1 層 + 第 2 層的最小版本。** 第 3 層列為 v2。

---

## 1. 目錄結構（Codex 依此建立）

```
turtle-fishing-sst/
├── spec.md                  # 本檔案（勿由 agent 改動驗收標準）
├── findings.md              # Cowork 寫驗證結果
├── README.md                # Codex 寫：如何安裝與執行
├── .env.example             # 環境變數範本（GFW token 等，勿提交真 token）
├── pyproject.toml           # 依賴管理
├── data/
│   ├── raw/                 # 各來源原始下載（gitignore）
│   └── interim/             # 對齊後的網格資料
├── src/
│   ├── config.py            # 全域參數：bbox、日期範圍、網格解析度
│   ├── fetch_movebank.py    # 模組 A：海龜軌跡
│   ├── fetch_gfw.py         # 模組 B：漁船作業網格
│   ├── fetch_sst.py         # 模組 C：海表溫度（v2 才需要）
│   ├── align.py             # 模組 D：三源對齊到統一網格
│   ├── analyze.py           # 模組 E：疊圖 + 相關檢定
│   └── export_web_data.py   # 模組 F：把對齊結果轉成網站用 JSON
├── web/                     # 模組 F：靜態互動展示站（純 HTML/CSS/JS + Leaflet）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── outputs/
│   ├── figures/             # 疊圖 PNG
│   ├── tables/              # 網格統計 CSV
│   └── web/                 # 模組 F 產出：grid.json、summary.json
└── tests/
    └── test_contracts.py    # 驗收標準的自動化版本
```

---

## 2. 全域參數（src/config.py）

以下為預設值，MVP 先用這組。Codex 不得自行更改，要改回構思層。

```python
# 研究區域：先用一個海龜資料豐富、漁業活躍的海域當範本
# 範例採「北太平洋」——蠵龜公開追蹤資料多、延繩釣漁場活躍
BBOX = {
    "lon_min": 120.0,   # 經度一律用 -180~180 慣例
    "lon_max": 180.0,
    "lat_min": 15.0,
    "lat_max": 45.0,
}
DATE_START = "2018-01-01"   # GFW 作業資料自 2017 起，取 2018 保守
DATE_END   = "2019-12-31"

GRID_DEG = 0.25             # 統一網格解析度，對齊 OISST 原生解析度
TIME_BIN = "W"              # 時間分箱：週。海龜點稀疏，週級較穩
```

---

## 3. 模組契約

### 模組 A — fetch_movebank.py（海龜軌跡）

**來源**：Movebank 公開資料倉儲 https://datarepository.movebank.org/
（該倉儲內資料集皆經審查、綁定論文、對公眾開放，不需向資料擁有者個別索取。）

**存取**：
- 需註冊免費 Movebank 帳號，取得帳密。
- 用 REST：`https://www.movebank.org/movebank/service/direct-read`，帶 `entity_type=event`、`study_id=<ID>`。
- 或用 R 的 `move2` 套件（若走 R）。本專案走 Python，用 `requests` 直接打 REST。

**輸入**：study_id（人工從倉儲挑一筆海龜研究後填入 config）、bbox、日期範圍。

**輸出**：標準化 DataFrame，存 `data/raw/movebank_<study_id>.parquet`，欄位固定為：
```
individual_id : str
timestamp     : datetime64[ns, UTC]
lon           : float   # -180~180
lat           : float
species       : str
```

**⚠️ 已知陷阱（Codex 必讀）**：
- Movebank 各 study 的欄位命名不一致（有的叫 `location-long`，有的叫 `location_long`）。務必寫欄位映射層，不要假設欄名。
- 海龜多為 Argos 定位，有雜訊點與重複時間戳。MVP 階段：先丟棄重複時間戳、丟棄落在 bbox 外的點即可，不做 Douglas filter（列為 v2）。
- 時間戳可能非 UTC，讀入後一律轉 UTC。

---

### 模組 B — fetch_gfw.py（漁船作業網格）

**來源**：Global Fishing Watch API，4Wings（apparent fishing effort）。

**存取**：
- 需在 https://globalfishingwatch.org/our-apis/ 申請**免費 API token**（唯一需要人本人動手的前置作業）。
- 用官方 Python 套件 `gfw-api-python-client`。
- token 放 `.env` 的 `GFW_API_TOKEN`，程式用 `os.environ` 讀，**不得寫死在程式碼**。

**輸入**：bbox、日期範圍、grid_deg。

**輸出**：網格化 DataFrame，存 `data/raw/gfw_effort.parquet`，欄位固定為：
```
lon_bin        : float   # 網格中心經度 -180~180
lat_bin        : float
time_bin       : datetime64   # 週起始日
fishing_hours  : float   # 該格該週的 apparent fishing effort（作業小時）
```

**⚠️ 已知陷阱**：
- GFW 回傳的時間解析度與空間解析度要在請求時就指定，對齊 config 的 GRID_DEG 與 TIME_BIN。
- API 有速率限制，大範圍請求要分頁或分塊，寫重試邏輯。
- 確認回傳經度慣例；若為 0~360 需轉成 -180~180（見模組 D 的統一規則）。

---

### 模組 C — fetch_sst.py（海表溫度，v2 才實作）

**來源**：NOAA OISST v2.1，透過 ERDDAP。免帳號。
- 端點範例：coastwatch/NCEI 的 ERDDAP griddap，資料集 `ncdcOisst21Agg`。
- Python 用 `erddapy` + `xarray`，只抓 bbox 子區域，不要抓全球（全球逐日超過 100GB）。

**輸出**：`data/raw/sst.nc`（netCDF），或網格化後的 parquet，欄位：
```
lon_bin : float   # -180~180
lat_bin : float
time_bin: datetime64
sst     : float   # 攝氏
sst_anom: float   # 相對氣候平均的距平（用內建 ltm 或自算）
```

**⚠️ 已知陷阱**：
- **OISST 原生經度是 0~360**。這是全專案最容易錯的地方，抓下來務必轉 -180~180 再對齊。
- netCDF 時間單位換算要用 xarray 內建解碼，不要手刻。

---

### 模組 D — align.py（三源對齊，本專案技術核心）

把三份不同來源、不同解析度的資料，重新取樣到**同一套 (lon_bin, lat_bin, time_bin) 網格**。

**統一規則（所有模組都須遵守，寫成共用函式）**：
1. **經度**：一律 -180~180。任何來源進來先過 `normalize_lon()`。
2. **網格化**：`lon_bin = floor(lon / GRID_DEG) * GRID_DEG`，緯度同理。
3. **時間分箱**：一律 `pd.Grouper(freq=TIME_BIN)`，週起始日為鍵。
4. **海龜出現次數**：同一格同一週內的定位點數（去重個體後可另算「出現的不同個體數」）。

**輸出**：`data/interim/aligned.parquet`，一列 = 一個 (格, 週)，欄位：
```
lon_bin, lat_bin, time_bin,
turtle_points  : int    # 海龜定位點數
turtle_indivs  : int    # 不同個體數
fishing_hours  : float  # 漁船作業小時（無作業補 0）
sst, sst_anom  : float  # v2 才有，MVP 補 NaN
```

**關鍵設計**：漁船有作業但海龜沒去的格子要保留（fishing_hours>0, turtle_points=0），海龜去了但沒漁船的格子也要保留。這兩類格子正是「迴避/重疊」訊號的所在，不能因為 join 方式錯誤而遺失。用 **outer join** 對齊，缺值補 0（作業）或保留 0（海龜）。

---

### 模組 E — analyze.py（疊圖 + 相關檢定）

1. **疊圖**：一張地圖，底圖為漁船作業熱區（顏色深淺 = fishing_hours），疊上海龜定位點或密度。存 `outputs/figures/overlay.png`。
2. **相關檢定**：對齊表中，逐格的 `fishing_hours` 對 `turtle_points`，算 Spearman 相關（非常態，用 Spearman 不用 Pearson）。輸出相關係數、p 值、樣本格數到 `outputs/tables/correlation.csv`。
3. **敘述**：analyze 結尾印出一句白話結論（正相關/負相關/不顯著），供 Cowork 核對是否與圖一致。

---

### 模組 F — export_web_data.py + web/（互動式靜態展示站）

**定位**：把模組 D/E 的結果包成可在瀏覽器互動瀏覽的靜態網站。**不是即時系統**——
資料是這批固定的歷史快照，要更新資料就重新跑一次 pipeline 再重新產出、重新部署，
網站本身不跑排程、不接後端、不連資料庫。

**前置條件（阻塞規則）**：模組 E 的輸出需先經構思層審查、理想上等 Cowork 依
第 4 節驗收標準 PASS，才可接上真資料並對外部署。前端骨架、版面、互動邏輯可以
先用假資料開發，不受此前置條件限制；但真正把 `outputs/web/*.json` 接上、
以及執行任何「產生公開網址」的部署動作，都要停下來給人確認，不由 Codex 自行上線。

**資料轉出（`src/export_web_data.py`）**：
輸入：`data/interim/aligned.parquet`（模組 D）、`outputs/tables/correlation.csv`（模組 E）。
輸出：
```
outputs/web/grid.json
# 陣列，每筆一個 (格,週)：
# { lon_bin, lat_bin, time_bin, turtle_points, turtle_indivs, fishing_hours }

outputs/web/summary.json
# { correlation, p_value, n_cells, n_individuals, conclusion_text }
```

**前端（`web/`）**：純靜態 HTML/CSS/JS + Leaflet（免 API key）。不需要後端伺服器、
不需要資料庫，任何靜態託管（GitHub Pages / Cloudflare Pages）都能部署。

**功能**：
1. 地圖：底圖漁船作業熱區（`fishing_hours` 深淺）+ 海龜定位點疊加，可切換顯示/隱藏。
2. 時間滑桿：切換不同 `time_bin`（週），觀察熱區隨時間變化。
3. Hover/click 顯示該格數值（`fishing_hours`、`turtle_points`）。
4. 結果面板：Spearman 相關係數、p 值、白話結論。
5. **必要免責文字**：明確寫出「相關不等於因果」，並註明樣本為個位數隻個體的
   追蹤資料，避免視覺效果被誤讀成比實際證據更強的結論（呼應 CONTEXT.md 第 4 節）。

**視覺設計規範（畫面品質要求，不是可省略的細節）**：

原則：**資料編碼維持嚴謹（熱區顏色代表的是真實統計量，不能為了可愛犧牲可讀性），
裝飾/介面風格走可愛路線（吉祥物、圖示、圓潤造型、海洋主題色）**。兩者不衝突，
分開處理。

- **配色（資料層，不可為了可愛改動）**：`fishing_hours` 熱區維持單一色相由淺到深的
  **連續色階**（sequential，藍色，數值愈高愈深）：
  `#cde2fb → #b7d3f6 → #9ec5f4 → #86b6ef → #6da7ec → #5598e7 → #3987e5 → #2a78d6
  → #256abf → #1c5cab → #184f95 → #104281 → #0d366b`（由淺至深，對應數值由低到高）。
  海龜定位點用對比色（紅色 `#e34948`），跟藍色熱區形成清楚區隔，不要用彩虹色階。
  正式定稿前跑 `dataviz` skill 的 `scripts/validate_palette.js` 驗證色階可讀性
  （對比度、色盲安全），不要用肉眼判斷。**這條規則的原因**：熱區顏色深淺是在
  傳達「這格漁船作業了多少小時」這個統計事實，換成可愛的粉彩漸層會讓深淺對應
  的數值大小失真、看不出強弱差異，等於犧牲了網站存在的目的。
- **可愛化的地方（介面/裝飾層，這裡放手做可愛）**：
  1. **海龜定位點**：地圖上的海龜可以用簡化的圓潤海龜圖示（emoji 🐢 或一個
     簡單的扁平插畫 SVG）取代純色圓點，只要維持「點的大小 = 該格定位次數」
     這個資料編碼不變即可。
  2. **漁船意象**：在圖例、頁首、載入畫面等*非資料編碼*的地方，可以放可愛小船
     圖示（例如熱區圖例旁邊配一個小船 icon 提示「這是漁船作業熱區」）。
  3. **海洋主題頁面裝飾**：頁面背景、卡片邊框、分隔線可以用柔和的海洋色系
     （淺藍/薄荷綠/珊瑚橘的淺色調）、波浪形分隔線、圓角卡片、柔和陰影——這些
     都不是資料編碼，可以自由發揮可愛感。
  4. **吉祥物**：建議在頁首/結論面板放一個簡單的海龜吉祥物插畫（例如「這是
     ○○號蠵龜」的擬人化小提示），讓整個網站有溫度，但吉祥物出現的地方要跟
     地圖上代表真實資料的海龜點視覺上區分開（例如吉祥物用插畫風格、地圖上
     的資料點維持簡潔圖示），不要讓使用者搞混「這是裝飾」還是「這是一筆真實定位」。
  5. **字體**：頁面標題、按鈕、裝飾文字可以用圓潤、友善的無襯線字體（例如
     Nunito、Quicksand 這類圓體，需自行選擇可離線嵌入或走 Google Fonts 皆可）；
     但地圖上的數值標籤、圖例刻度、表格數字，維持清晰易讀的系統無襯線字體
     （`system-ui, -apple-system, "Segoe UI", sans-serif`），不要為了風格統一
     犧牲數字的可讀性。
- **互動細節**：hover 熱區/海龜點時要有 tooltip 顯示實際數值；有圖例（legend）
  說明顏色對應的數值區間，不能只有顏色沒有說明；時間滑桿要有清楚的目前週次標示。
  這些功能本身也可以做得可愛（例如 tooltip 用圓角氣泡框、滑桿用海浪造型軌道），
  只要數值本身清楚可讀。
- **不接受**：預設瀏覽器樣式（無 CSS）、把熱區資料色階換成粉彩/彩虹漸層導致
  看不出數值強弱、無圖例的顏色編碼、無 hover 回饋的地圖、可愛裝飾蓋住或混淆
  真實資料點。這些是「畫面粗糙」與「可愛蓋過正確性」的具體定義，Cowork/人審查
  時會對照這份清單。

**部署**：靜態託管（GitHub Pages / Cloudflare Pages 皆可）。**實際對外發布（產生
公開網址）需人確認後才執行**，不由 Codex 自行部署上線。

---

## 4. 驗收標準（⚠️ 勿由 agent 修改；Cowork 依此查核）

每項標註 [自動]（可寫進 tests/）或 [人審]（Cowork 判斷）。

**A. Movebank**
- [自動] 輸出 DataFrame 欄位名與型別完全符合模組 A 契約。
- [自動] 所有 lon 落在 [-180,180]、lat 落在 [-90,90]。
- [自動] 無重複 (individual_id, timestamp)。
- [人審] species 欄的值確實是海龜（Cheloniidae 科），非其他物種。抓錯 study 是常見錯誤。

**B. GFW**
- [自動] fishing_hours 全部 ≥ 0，且至少有一格 > 0（否則等於沒抓到資料）。
- [自動] 所有網格中心落在 config 的 BBOX 內。
- [人審] 抽查 2~3 個高作業格，對照 GFW 官網地圖同區同期，量級是否合理（不能差好幾個數量級）。

**C. 對齊（最容易出隱蔽錯誤，重點查）**
- [自動] aligned.parquet 中，同時存在「有漁船無海龜」與「有海龜無漁船」兩類格子。
- [自動] 經度正規化後，不存在 >180 的 lon_bin。
- [人審] **隨機抽一格，人工回推**：該格該週的原始海龜點數與原始 GFW 作業小時，是否等於對齊表的值。這一步是抓「對齊錯位」的唯一可靠方法。
- [人審] 把對齊後的海龜點反畫回地圖，是否與 Movebank 官網該 study 的軌跡形狀一致（抓經度翻轉錯誤）。

**D. 分析**
- [自動] correlation.csv 有係數、p 值、樣本數三欄，數值非 NaN。
- [人審] 疊圖的視覺印象與 Spearman 結論方向一致（圖上明顯重疊，卻報負相關 = 有 bug）。
- [人審] 結論的因果措辭是否過度。相關不等於因果，敘述須為「空間關聯」而非「漁船導致海龜減少」。

**F. 網站（模組 F，前置條件：模組 E 已 PASS 才可接真資料）**
- [自動] `outputs/web/grid.json`、`outputs/web/summary.json` 存在、為合法 JSON，
  欄位與模組 D/E 輸出一致（`grid.json` 每筆對應 aligned.parquet 一列，
  `summary.json` 的 correlation/p_value/n_cells 與 `correlation.csv` 完全相符）。
- [自動] `grid.json` 的 lon_bin/lat_bin 落在 config BBOX 內，time_bin 落在
  `DATE_START`~`DATE_END`（含週起始日規則的正常誤差）。
- [人審] 本地開啟 `web/index.html`：地圖疊圖、時間滑桿、hover tooltip、圖例、
  結果面板都正常運作，視覺印象與 `correlation.csv` 方向一致。
- [人審] 對照模組 F 的「視覺設計規範」清單逐項核對（連續色階非彩虹色、有圖例、
  有 hover 回饋、非預設瀏覽器樣式）；不符合任一項視為未完成，不是風格建議。
- [人審] 可愛化裝飾（吉祥物、圖示、海洋主題配色）沒有取代或混淆熱區的資料色階，
  也沒有讓人分不清「裝飾插畫」與「地圖上代表真實定位的資料點」。
- [人審] 免責/方法論文字清楚可見，因果措辭不過度（同驗收標準 D 的因果檢查）。
- [人審] 若已部署到公開網址：確認部署動作事先取得人的明確同意，且頁面內容
  與本地版本一致、沒有夾帶 `.env`／任何憑證。

---

## 5. 交接協定

- Codex 完成一個模組 → 更新 README 的執行說明 → 在 commit message 註明對應 spec 章節。
- Cowork 查核 → 把每條驗收標準標為 PASS / FAIL / 存疑，寫進 `findings.md`，FAIL 要附證據（哪個值、期望多少、實際多少）。
- 出現 FAIL 或「資料揭露意外」（例如相關方向與假說相反）→ 回構思層，不由執行/驗證層自行改假說。

---

## 6. 版本邊界

- **MVP**：模組 A、B、D、E（無 SST）。目標產出：一張疊圖 + 一個 Spearman 結論。
- **v2**：加模組 C（SST），做機制層；加 Douglas filter 淨化 Argos 雜訊。
- **v3**：多海域比較、時間動態（海溫距平驅動的分布位移）。
- **模組 F（網站發布）**：獨立於 v2/v3 的資料擴充軸線，不需要等 SST 加入。
  只要模組 E 驗收通過即可著手接真資料；前端骨架與版面可以更早開始（用假資料）。
  真正「對外發布公開網址」永遠需要人明確同意才執行。
