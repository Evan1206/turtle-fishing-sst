const FISHING_PALETTE = [
  "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
  "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
  "#184f95", "#104281", "#0d366b",
];

const DATA_URLS = {
  weeks: "../outputs/web/weeks.json",
  summary: "../outputs/web/summary.json",
};

const map = L.map("map", {
  center: [31, 149],
  zoom: 4,
  minZoom: 3,
  maxZoom: 9,
  zoomControl: true,
  preferCanvas: true,
});

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

const fishingLayer = L.layerGroup().addTo(map);
const turtleLayer = L.layerGroup().addTo(map);
const slider = document.querySelector("#week-slider");
const weekLabel = document.querySelector("#week-label");
const fishingToggle = document.querySelector("#toggle-fishing");
const turtleToggle = document.querySelector("#toggle-turtles");
const loadingState = document.querySelector("#loading-state");

let weeks = [];
let weekMetadata = new Map();
const weekCache = new Map();
let gridDeg = 0.25;
let maxFishing = 0;
let renderRequest = 0;

function formatWeek(isoDate) {
  return new Intl.DateTimeFormat("zh-TW", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${isoDate}T00:00:00Z`));
}

function formatPValue(value) {
  if (value === 0) return "< 1e−300";
  return value < 0.001 ? value.toExponential(2) : value.toFixed(3);
}

function fishingColor(value) {
  if (value <= 0 || maxFishing <= 0) return FISHING_PALETTE[0];
  const normalized = Math.log1p(value) / Math.log1p(maxFishing);
  const index = Math.min(
    FISHING_PALETTE.length - 1,
    Math.floor(normalized * FISHING_PALETTE.length),
  );
  return FISHING_PALETTE[index];
}

function tooltipMarkup(row) {
  return [
    `<strong>${formatWeek(row.time_bin)}</strong>`,
    `漁船作業：${row.fishing_hours.toLocaleString("zh-TW", { maximumFractionDigits: 2 })} 小時`,
    `海龜定位：${row.turtle_points.toLocaleString("zh-TW")} 點`,
    `同格個體：${row.turtle_indivs.toLocaleString("zh-TW")} 隻`,
  ].join("<br>");
}

function turtleIcon(pointCount) {
  const size = Math.min(32, 16 + Math.sqrt(pointCount) * 2.4);
  return L.divIcon({
    className: "turtle-data-marker",
    html: '<span aria-hidden="true">🐢</span>',
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    tooltipAnchor: [0, -size / 2],
  });
}

async function renderWeek(index, { initial = false } = {}) {
  const requestId = ++renderRequest;
  const week = weeks[index];
  const metadata = weekMetadata.get(week);
  weekLabel.textContent = `週起始日 ${formatWeek(week)} · 載入中`;

  let rows = weekCache.get(week);
  if (!rows) {
    const response = await fetch(`../outputs/web/${metadata.path}`);
    if (!response.ok) throw new Error(`週資料無法讀取：${week}`);
    rows = await response.json();
    weekCache.set(week, rows);
  }
  if (requestId !== renderRequest) return;

  fishingLayer.clearLayers();
  turtleLayer.clearLayers();
  weekLabel.textContent = `週起始日 ${formatWeek(week)}`;

  rows.forEach((row) => {
    const bounds = [
      [row.lat_bin, row.lon_bin],
      [row.lat_bin + gridDeg, row.lon_bin + gridDeg],
    ];
    const tooltip = tooltipMarkup(row);

    L.rectangle(bounds, {
      color: "#104281",
      weight: 0.45,
      fillColor: fishingColor(row.fishing_hours),
      fillOpacity: row.fishing_hours > 0 ? 0.76 : 0.28,
    })
      .bindTooltip(tooltip, { sticky: true })
      .bindPopup(tooltip)
      .addTo(fishingLayer);

    if (row.turtle_points > 0) {
      L.marker(
        [row.lat_bin + gridDeg / 2, row.lon_bin + gridDeg / 2],
        { icon: turtleIcon(row.turtle_points), riseOnHover: true },
      )
        .bindTooltip(tooltip, { sticky: true })
        .bindPopup(tooltip)
      .addTo(turtleLayer);
    }
  });

  if (initial) {
    loadingState.classList.add("is-hidden");
    requestAnimationFrame(() => {
      requestAnimationFrame(() => map.invalidateSize({ animate: false }));
    });
  }
}

function populateSummary(summary) {
  document.querySelector("#correlation").textContent = summary.correlation.toFixed(3);
  document.querySelector("#p-value").textContent = formatPValue(summary.p_value);
  document.querySelector("#n-cells").textContent = summary.n_cells.toLocaleString("zh-TW");
  document.querySelector("#n-individuals").textContent =
    summary.n_individuals.toLocaleString("zh-TW");
  document.querySelector("#conclusion-text").textContent = summary.conclusion_text;
  const effectNote = document.querySelector("#effect-note");
  effectNote.textContent = Math.abs(summary.correlation) < 0.2
    ? "ρ 的絕對值小於 0.2，代表效應量很小；統計顯著不等於關係很強。"
    : "相關係數描述空間關聯強度，不代表因果機制。";
}

function prepareWeeks(manifest) {
  gridDeg = manifest.grid_deg;
  maxFishing = manifest.max_fishing_hours;
  weeks = manifest.weeks.map((week) => week.time_bin);
  weekMetadata = new Map(
    manifest.weeks.map((week) => [week.time_bin, week]),
  );

  slider.max = String(weeks.length - 1);
  slider.value = "0";
  document.querySelector("#week-start").textContent = formatWeek(weeks[0]);
  document.querySelector("#week-end").textContent = formatWeek(weeks.at(-1));
  document.querySelector("#legend-min").textContent = "0";
  document.querySelector("#legend-max").textContent =
    Math.round(maxFishing).toLocaleString("zh-TW");
}

async function loadData() {
  try {
    const [weeksResponse, summaryResponse] = await Promise.all([
      fetch(DATA_URLS.weeks),
      fetch(DATA_URLS.summary),
    ]);
    if (!weeksResponse.ok || !summaryResponse.ok) {
      throw new Error("資料檔案無法讀取");
    }
    const [manifest, summary] = await Promise.all([
      weeksResponse.json(),
      summaryResponse.json(),
    ]);
    if (!Array.isArray(manifest.weeks) || manifest.weeks.length === 0) {
      throw new Error("weeks.json 沒有可顯示的週資料");
    }

    prepareWeeks(manifest);
    populateSummary(summary);
    await renderWeek(0, { initial: true });
  } catch (error) {
    loadingState.classList.add("has-error");
    loadingState.querySelector("strong").textContent = "歷史資料載入失敗";
    loadingState.querySelector("span").textContent =
      "請確認已先執行 exporter，並透過本機 HTTP 伺服器開啟頁面。";
    console.error(error);
  }
}

slider.addEventListener("input", (event) => {
  renderWeek(Number(event.target.value)).catch((error) => {
    weekLabel.textContent = "週資料載入失敗";
    console.error(error);
  });
});

fishingToggle.addEventListener("change", () => {
  if (fishingToggle.checked) fishingLayer.addTo(map);
  else fishingLayer.remove();
});

turtleToggle.addEventListener("change", () => {
  if (turtleToggle.checked) turtleLayer.addTo(map);
  else turtleLayer.remove();
});

loadData();

const resizeObserver = new ResizeObserver(() => {
  map.invalidateSize({ animate: false });
});
resizeObserver.observe(document.querySelector("#map"));
