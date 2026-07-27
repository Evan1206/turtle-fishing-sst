"""Project-wide configuration values defined by spec.md section 2."""

# 研究區域：先用一個海龜資料豐富、漁業活躍的海域當範本
# 範例採「北太平洋」——蠵龜公開追蹤資料多、延繩釣漁場活躍
BBOX = {
    "lon_min": 120.0,   # 經度一律用 -180~180 慣例
    "lon_max": 180.0,
    "lat_min": 15.0,
    "lat_max": 45.0,
}
# Movebank 公開研究:study_id 1417866900
# 「Post-nesting migrations of loggerhead sea turtles nesting in Japan」
# Caretta caretta、CC0、12 隻個體、17360 個定位點,87% 落在上方 BBOX 內。
# 2026-07-26 由構思層選定(見 CONTEXT.md 第2節「Movebank study 選定」)。
STUDY_ID = 1417866900

# 2026-07-26 調整:原訂 2018-2019 只涵蓋此 study 3 隻個體/2446 個定位點，
# 改成 2017-2018 可涵蓋 8 隻個體/9390 個定位點，仍在 GFW 資料可靠期（2017 年後）內。
DATE_START = "2017-01-01"
DATE_END = "2018-12-31"

GRID_DEG = 0.25             # 統一網格解析度，對齊 OISST 原生解析度
TIME_BIN = "W"              # 時間分箱：週。海龜點稀疏，週級較穩
