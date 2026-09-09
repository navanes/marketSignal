const form = document.querySelector("#researchForm");
const queryInput = document.querySelector("#query");
const marketCombo = document.querySelector("#marketCombo");
const marketToggle = document.querySelector("#marketToggle");
const marketOptions = document.querySelector("#marketOptions");
const quickPicks = document.querySelector("#quickPicks");
const report = document.querySelector("#report");
const reportTitle = document.querySelector("#reportTitle");
const action = document.querySelector("#action");
const confidence = document.querySelector("#confidence");
const price = document.querySelector("#price");
const exchange = document.querySelector("#exchange");
const change = document.querySelector("#change");
const range = document.querySelector("#range");
const tone = document.querySelector("#tone");
const toneScore = document.querySelector("#toneScore");
const signals = document.querySelector("#signals");
const headlines = document.querySelector("#headlines");
const sourceStatus = document.querySelector("#sourceStatus");
const copyReport = document.querySelector("#copyReport");
const priceChart = document.querySelector("#priceChart");
const volatility = document.querySelector("#volatility");
const drawdown = document.querySelector("#drawdown");
const projection = document.querySelector("#projection");
const flowchart = document.querySelector("#flowchart");
const rangeFilter = document.querySelector("#rangeFilter");
const overlayFilter = document.querySelector("#overlayFilter");
const panLeft = document.querySelector("#panLeft");
const panRight = document.querySelector("#panRight");
const chartPanSlider = document.querySelector("#chartPanSlider");
const zoomOut = document.querySelector("#zoomOut");
const zoomIn = document.querySelector("#zoomIn");
const zoomReset = document.querySelector("#zoomReset");
const zoomLabel = document.querySelector("#zoomLabel");
const trendState = document.querySelector("#trendState");
const trendDetail = document.querySelector("#trendDetail");
const rsiValue = document.querySelector("#rsiValue");
const rsiState = document.querySelector("#rsiState");
const macdValue = document.querySelector("#macdValue");
const macdState = document.querySelector("#macdState");
const levelsValue = document.querySelector("#levelsValue");
const levelsState = document.querySelector("#levelsState");
const volumeState = document.querySelector("#volumeState");
const volumeValue = document.querySelector("#volumeValue");
const probabilityValue = document.querySelector("#probabilityValue");
const probabilityState = document.querySelector("#probabilityState");
const scenarioReport = document.querySelector("#scenarioReport");
const refreshPredictions = document.querySelector("#refreshPredictions");
const buyPickAsOf = document.querySelector("#buyPickAsOf");
const buyPickSymbol = document.querySelector("#buyPickSymbol");
const buyPickReason = document.querySelector("#buyPickReason");
const buyPickRunner = document.querySelector("#buyPickRunner");
const buyPickRanked = document.querySelector("#buyPickRanked");
const buyPickNote = document.querySelector("#buyPickNote");
const scorecardHeadline = document.querySelector("#scorecardHeadline");
const scorecardGrid = document.querySelector("#scorecardGrid");
const scorecardRegime = document.querySelector("#scorecardRegime");
const scorecardSymbols = document.querySelector("#scorecardSymbols");
const predictionTotal = document.querySelector("#predictionTotal");
const predictionAccuracy = document.querySelector("#predictionAccuracy");
const predictionError = document.querySelector("#predictionError");
const predictionPending = document.querySelector("#predictionPending");
const predictionChart = document.querySelector("#predictionChart");
const symbolAccuracy = document.querySelector("#symbolAccuracy");
const predictionRows = document.querySelector("#predictionRows");
const horizonFilter = document.querySelector("#horizonFilter");
const horizonCustom = document.querySelector("#horizonCustom");
const recentPanel = document.querySelector("#recentPanel");
const recentList = document.querySelector("#recentList");
const clearRecents = document.querySelector("#clearRecents");
const modelForecast = document.querySelector("#modelForecast");
const modelDir = document.querySelector("#modelDir");
const modelMeta = document.querySelector("#modelMeta");
const modelComponents = document.querySelector("#modelComponents");

let lastReport = "";
let lastAnalytics = null;
let lastPredictionData = null;
let currentPeriod = "6mo";
let currentHorizonDays = 10;
let chartZoom = 1;
let chartPanStart = null;
let dragState = null;
const activeOverlays = new Set(["trendline", "levels", "volume"]);

const DEFAULT_MARKETS = [
  { symbol: "AAPL", name: "Apple", group: "Top stocks" },
  { symbol: "MSFT", name: "Microsoft", group: "Top stocks" },
  { symbol: "NVDA", name: "NVIDIA", group: "Top stocks" },
  { symbol: "AMZN", name: "Amazon", group: "Top stocks" },
  { symbol: "GOOGL", name: "Alphabet", group: "Top stocks" },
  { symbol: "META", name: "Meta Platforms", group: "Top stocks" },
  { symbol: "TSLA", name: "Tesla", group: "Top stocks" },
  { symbol: "BRK-B", name: "Berkshire Hathaway", group: "Top stocks" },
  { symbol: "AVGO", name: "Broadcom", group: "Top stocks" },
  { symbol: "LLY", name: "Eli Lilly", group: "Top stocks" },
  { symbol: "JPM", name: "JPMorgan Chase", group: "Top stocks" },
  { symbol: "V", name: "Visa", group: "Top stocks" },
  { symbol: "XOM", name: "Exxon Mobil", group: "Top stocks" },
  { symbol: "UNH", name: "UnitedHealth", group: "Top stocks" },
  { symbol: "MA", name: "Mastercard", group: "Top stocks" },
  { symbol: "COST", name: "Costco", group: "Top stocks" },
  { symbol: "WMT", name: "Walmart", group: "Top stocks" },
  { symbol: "HD", name: "Home Depot", group: "Top stocks" },
  { symbol: "PG", name: "Procter & Gamble", group: "Top stocks" },
  { symbol: "SPY", name: "S&P 500 ETF", group: "Top stocks" },
];

const CRYPTO_MARKETS = [
  { symbol: "BTC-USD", name: "Bitcoin", group: "Top crypto" },
  { symbol: "ETH-USD", name: "Ethereum", group: "Top crypto" },
  { symbol: "USDT-USD", name: "Tether", group: "Top crypto" },
  { symbol: "BNB-USD", name: "BNB", group: "Top crypto" },
  { symbol: "USDC-USD", name: "USDC", group: "Top crypto" },
  { symbol: "XRP-USD", name: "XRP", group: "Top crypto" },
  { symbol: "SOL-USD", name: "Solana", group: "Top crypto" },
  { symbol: "TRX-USD", name: "TRON", group: "Top crypto" },
  { symbol: "DOGE-USD", name: "Dogecoin", group: "Top crypto" },
  { symbol: "ADA-USD", name: "Cardano", group: "Top crypto" },
  { symbol: "HYPE-USD", name: "Hyperliquid", group: "Top crypto" },
  { symbol: "BCH-USD", name: "Bitcoin Cash", group: "Top crypto" },
  { symbol: "LINK-USD", name: "Chainlink", group: "Top crypto" },
  { symbol: "XLM-USD", name: "Stellar", group: "Top crypto" },
  { symbol: "SUI-USD", name: "Sui", group: "Top crypto" },
  { symbol: "LTC-USD", name: "Litecoin", group: "Top crypto" },
  { symbol: "AVAX-USD", name: "Avalanche", group: "Top crypto" },
  { symbol: "TON-USD", name: "Toncoin", group: "Top crypto" },
  { symbol: "SHIB-USD", name: "Shiba Inu", group: "Top crypto" },
  { symbol: "DOT-USD", name: "Polkadot", group: "Top crypto" },
];

const MARKET_STORAGE_KEY = "marketsignal.searchOptions";

function readSavedMarkets() {
  try {
    const saved = JSON.parse(localStorage.getItem(MARKET_STORAGE_KEY) || "[]");
    return Array.isArray(saved) ? saved.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function marketLabel(item) {
  return typeof item === "string" ? item : `${item.symbol} - ${item.name}`;
}

function marketValue(item) {
  return typeof item === "string" ? item : item.symbol;
}

function allMarketOptions() {
  const seen = new Set();
  return [...DEFAULT_MARKETS, ...CRYPTO_MARKETS, ...readSavedMarkets()]
    .filter((item) => {
      const key = marketValue(item).trim().toUpperCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 80);
}

function filteredMarketOptions() {
  const search = queryInput.value.trim().toUpperCase();
  if (!search) return allMarketOptions();
  return allMarketOptions().filter((item) => {
    const label = marketLabel(item).toUpperCase();
    return label.includes(search) || marketValue(item).toUpperCase().includes(search);
  });
}

function closeMarketMenu() {
  marketOptions.classList.remove("open");
  queryInput.setAttribute("aria-expanded", "false");
}

function openMarketMenu() {
  renderMarketOptions();
  marketOptions.classList.add("open");
  queryInput.setAttribute("aria-expanded", "true");
}

function chooseMarket(value) {
  queryInput.value = value;
  closeMarketMenu();
  queryInput.focus();
}

function renderMarketOptions() {
  marketOptions.innerHTML = "";
  const options = filteredMarketOptions();
  const search = queryInput.value.trim();
  const groups = [
    { title: "Top stocks", items: options.filter((item) => item.group === "Top stocks") },
    { title: "Top crypto", items: options.filter((item) => item.group === "Top crypto") },
    { title: "Saved searches", items: options.filter((item) => !item.group) },
  ].filter((group) => group.items.length);

  const buildOption = (item) => {
    const option = document.createElement("button");
    option.type = "button";
    option.className = "market-option";
    option.setAttribute("role", "option");
    option.dataset.value = marketValue(item);
    const symbol = document.createElement("strong");
    symbol.textContent = marketValue(item);
    const label = document.createElement("span");
    label.textContent = marketLabel(item).replace(`${marketValue(item)} - `, "");
    option.append(symbol, label);
    option.addEventListener("mousedown", (event) => event.preventDefault());
    option.addEventListener("click", () => chooseMarket(marketValue(item)));
    return option;
  };

  if (search) {
    const heading = document.createElement("div");
    heading.className = "market-group full";
    heading.textContent = "Matching markets";
    marketOptions.appendChild(heading);
    const resultGrid = document.createElement("div");
    resultGrid.className = "market-results";
    options.forEach((item) => resultGrid.appendChild(buildOption(item)));
    marketOptions.appendChild(resultGrid);
  } else {
    const layout = document.createElement("div");
    layout.className = "market-menu-grid";
    groups.forEach((group) => {
      const section = document.createElement("section");
      section.className = "market-section";
      const heading = document.createElement("div");
      heading.className = "market-group";
      heading.textContent = group.title;
      section.appendChild(heading);
      group.items.forEach((item) => section.appendChild(buildOption(item)));
      layout.appendChild(section);
    });
    marketOptions.appendChild(layout);
  }

  if (!options.length) {
    const empty = document.createElement("div");
    empty.className = "market-empty";
    empty.textContent = "Press Research to add this market";
    marketOptions.appendChild(empty);
  }

  quickPicks.innerHTML = "";
  [...DEFAULT_MARKETS.slice(0, 10), ...CRYPTO_MARKETS.slice(0, 5)].forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pick-chip";
    button.textContent = item.symbol;
    button.title = item.name;
    button.addEventListener("click", () => {
      queryInput.value = item.symbol;
      form.requestSubmit();
    });
    quickPicks.appendChild(button);
  });
}

function addMarketOption(value) {
  const cleaned = value.trim();
  if (!cleaned) return;
  const defaultKeys = new Set([...DEFAULT_MARKETS, ...CRYPTO_MARKETS].map((item) => item.symbol.toUpperCase()));
  const saved = readSavedMarkets();
  const savedKeys = new Set(saved.map((item) => item.toUpperCase()));
  const key = cleaned.toUpperCase();
  if (!defaultKeys.has(key) && !savedKeys.has(key)) {
    localStorage.setItem(MARKET_STORAGE_KEY, JSON.stringify([cleaned, ...saved].slice(0, 500)));
  } else if (savedKeys.has(key)) {
    // bump an existing entry to the top
    const reordered = [cleaned, ...saved.filter((item) => item.toUpperCase() !== key)];
    localStorage.setItem(MARKET_STORAGE_KEY, JSON.stringify(reordered.slice(0, 500)));
  }
  renderMarketOptions();
  renderRecentSearches();
}

function removeSavedMarket(value) {
  const key = String(value).toUpperCase();
  const next = readSavedMarkets().filter((item) => item.toUpperCase() !== key);
  localStorage.setItem(MARKET_STORAGE_KEY, JSON.stringify(next));
  renderMarketOptions();
  renderRecentSearches();
}

function clearSavedMarkets() {
  localStorage.removeItem(MARKET_STORAGE_KEY);
  renderMarketOptions();
  renderRecentSearches();
}

function renderRecentSearches() {
  const saved = readSavedMarkets();
  recentPanel.hidden = saved.length === 0;
  recentList.innerHTML = "";
  saved.forEach((value) => {
    const li = document.createElement("li");
    li.className = "recent-item";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "recent-open";
    open.textContent = value;
    open.addEventListener("click", () => runResearch(value));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "recent-x";
    remove.setAttribute("aria-label", `Remove ${value}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeSavedMarket(value));
    li.append(open, remove);
    recentList.appendChild(li);
  });
}

function formatMoney(value, currency = "USD") {
  if (typeof value !== "number") return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency || "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPct(value) {
  if (typeof value !== "number") return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatNumber(value, digits = 2) {
  if (typeof value !== "number") return "-";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatShortMoney(value) {
  if (typeof value !== "number") return "-";
  if (Math.abs(value) >= 1000000) return `$${(value / 1000000).toFixed(1)}M`;
  if (Math.abs(value) >= 1000) return `$${(value / 1000).toFixed(1)}K`;
  return `$${value.toFixed(2)}`;
}

function monthName(dateText) {
  const date = new Date(`${dateText}T00:00:00`);
  return date.toLocaleDateString("en-US", { month: "short" });
}

function drawEmptyChart(message = "Run research to load price history") {
  const canvas = priceChart;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.max(240, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = "#60707b";
  ctx.font = "700 15px system-ui";
  ctx.textAlign = "center";
  ctx.fillText(message, rect.width / 2, rect.height / 2);
}

function drawEmptyPredictionChart(message = "Prediction history will appear here") {
  const canvas = predictionChart;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.max(220, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = "#60707b";
  ctx.font = "800 14px system-ui";
  ctx.textAlign = "center";
  ctx.fillText(message, rect.width / 2, rect.height / 2);
}

function pathPoints(points, xFor, yFor) {
  return points
    .filter((point) => typeof point.value === "number")
    .map((point) => ({ x: xFor(point.index), y: yFor(point.value) }));
}

function strokeLine(ctx, points, color, dash = []) {
  if (points.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.setLineDash(dash);
  ctx.beginPath();
  points.forEach((point, index) => {
    if (index === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  ctx.stroke();
  ctx.restore();
}

function overlayEnabled(name) {
  return activeOverlays.has(name);
}

function updateZoomLabel() {
  const panLabel = chartPanStart === null ? "latest" : "panned";
  zoomLabel.textContent = chartZoom === 1 ? "Full view" : `${chartZoom.toFixed(1)}x zoom / ${panLabel}`;
}

function clampZoom(value) {
  return Math.min(8, Math.max(1, value));
}

function chartWindow(chartLength) {
  const visibleCount = Math.max(12, Math.ceil(chartLength / chartZoom));
  const maxStart = Math.max(0, chartLength - visibleCount);
  const start = chartPanStart === null ? maxStart : Math.min(maxStart, Math.max(0, Math.round(chartPanStart)));
  chartPanStart = start === maxStart ? null : start;
  return { visibleCount, visibleStart: start, maxStart };
}

function setChartZoom(nextZoom, anchorRatio = 0.5) {
  if (!lastAnalytics?.chart?.length) {
    chartZoom = clampZoom(nextZoom);
    updateZoomLabel();
    return;
  }

  const chartLength = lastAnalytics.chart.length;
  const before = chartWindow(chartLength);
  const anchorIndex = before.visibleStart + before.visibleCount * anchorRatio;
  chartZoom = clampZoom(nextZoom);
  const afterVisibleCount = Math.max(12, Math.ceil(chartLength / chartZoom));
  const afterMaxStart = Math.max(0, chartLength - afterVisibleCount);
  const desiredStart = Math.round(anchorIndex - afterVisibleCount * anchorRatio);
  chartPanStart = Math.min(afterMaxStart, Math.max(0, desiredStart));
  if (chartPanStart === afterMaxStart) chartPanStart = null;
  updateZoomLabel();
}

function panChart(deltaIndex) {
  if (!lastAnalytics?.chart?.length) return;
  const chartLength = lastAnalytics.chart.length;
  const { visibleStart, maxStart } = chartWindow(chartLength);
  chartPanStart = Math.min(maxStart, Math.max(0, Math.round(visibleStart + deltaIndex)));
  if (chartPanStart === maxStart) chartPanStart = null;
  updateZoomLabel();
}

function updateChartControls() {
  if (!lastAnalytics?.chart?.length) {
    panLeft.disabled = true;
    panRight.disabled = true;
    chartPanSlider.disabled = true;
    chartPanSlider.max = "0";
    chartPanSlider.value = "0";
    return;
  }

  const { visibleStart, maxStart } = chartWindow(lastAnalytics.chart.length);
  const canPan = maxStart > 0;
  panLeft.disabled = !canPan || visibleStart <= 0;
  panRight.disabled = !canPan || visibleStart >= maxStart;
  chartPanSlider.disabled = !canPan;
  chartPanSlider.max = String(maxStart);
  chartPanSlider.value = String(visibleStart);
}

function panByPage(direction) {
  if (!lastAnalytics?.chart?.length) return;
  const { visibleCount } = chartWindow(lastAnalytics.chart.length);
  panChart(direction * Math.max(4, Math.round(visibleCount * 0.65)));
  drawPriceChart(lastAnalytics);
}

function drawHorizontalLevel(ctx, yFor, pad, width, level) {
  if (typeof level.value !== "number") return;
  const y = yFor(level.value);
  ctx.save();
  ctx.strokeStyle = level.color;
  ctx.fillStyle = level.color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash(level.dash || [4, 4]);
  ctx.beginPath();
  ctx.moveTo(pad.left, y);
  ctx.lineTo(width - pad.right, y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = "900 11px system-ui";
  ctx.textAlign = "left";
  ctx.fillText(`${level.label} ${formatShortMoney(level.value)}`, pad.left + 8, y - 6);
  ctx.restore();
}

function drawPriceChart(analytics) {
  const chart = analytics?.chart || [];
  const forecast = analytics?.forecast || [];
  const scenarioPaths = analytics?.scenario_paths || {};
  const monthlyPeaks = analytics?.monthly_peaks || [];
  const technical = analytics?.technical || {};
  const chartAnalysis = analytics?.chart_analysis || {};
  if (!chart.length) {
    drawEmptyChart("No price chart available for this search");
    return;
  }

  const canvas = priceChart;
  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.max(240, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

  const width = rect.width;
  const height = rect.height;
  const pad = { top: 26, right: 20, bottom: 48, left: 68 };
  const { visibleCount, visibleStart } = chartWindow(chart.length);
  const visibleEnd = Math.min(chart.length - 1, visibleStart + visibleCount - 1);
  const futureVisible = visibleEnd >= chart.length - 1;
  const visibleChart = chart
    .slice(visibleStart, visibleEnd + 1)
    .map((row, index) => ({ ...row, globalIndex: visibleStart + index }));
  const futureSlots = futureVisible ? Math.max(12, forecast.length * 3, Math.ceil(visibleChart.length * 0.28)) : 0;
  const futureStep = forecast.length ? futureSlots / forecast.length : 1;
  const virtualStart = visibleStart;
  const virtualEnd = visibleEnd + futureSlots;
  const futureIndex = (index) => chart.length - 1 + (index + 1) * futureStep;
  const projectedLineValue = (line) => {
    if (!line) return null;
    const slope = (line.end - line.start) / Math.max(1, line.end_index - line.start_index);
    return line.end + slope * (virtualEnd - line.end_index);
  };
  const projectedChannelValues = (channel) => {
    if (!channel) return [];
    const upperSlope = (channel.upper_end - channel.upper_start) / Math.max(1, channel.end_index - channel.start_index);
    const lowerSlope = (channel.lower_end - channel.lower_start) / Math.max(1, channel.end_index - channel.start_index);
    return [
      channel.upper_end + upperSlope * (virtualEnd - channel.end_index),
      channel.lower_end + lowerSlope * (virtualEnd - channel.end_index),
    ];
  };
  const scenarioValues = Object.values(scenarioPaths)
    .flat()
    .map((row) => row.close);
  const allValues = [
    ...visibleChart.map((row) => row.close),
    ...visibleChart.map((row) => row.sma20).filter((value) => typeof value === "number"),
    ...visibleChart.map((row) => row.sma50).filter((value) => typeof value === "number"),
    ...(futureVisible ? forecast.map((row) => row.close) : []),
    ...(futureVisible ? scenarioValues : []),
    technical.support,
    technical.resistance,
    chartAnalysis?.channel?.upper_start,
    chartAnalysis?.channel?.upper_end,
    chartAnalysis?.channel?.lower_start,
    chartAnalysis?.channel?.lower_end,
    projectedLineValue(chartAnalysis?.trendline),
    ...projectedChannelValues(chartAnalysis?.channel),
    ...(chartAnalysis?.fibonacci || []).map((level) => level.value),
  ].filter((value) => typeof value === "number");
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const span = max - min || 1;
  const xFor = (index) =>
    pad.left + ((index - virtualStart) / Math.max(1, virtualEnd - virtualStart)) * (width - pad.left - pad.right);
  const yFor = (value) => pad.top + ((max - value) / span) * (height - pad.top - pad.bottom);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f7faf9";
  ctx.fillRect(0, 0, width, height);

  if (overlayEnabled("volume")) {
    const volumes = chart.map((row) => row.volume).filter((value) => typeof value === "number");
    const maxVolume = Math.max(...volumes, 1);
    const volumeBase = height - pad.bottom;
    const volumeHeight = Math.min(58, (height - pad.top - pad.bottom) * 0.2);
    ctx.save();
    ctx.fillStyle = "rgba(46, 94, 158, 0.12)";
    visibleChart.forEach((row) => {
      if (typeof row.volume !== "number") return;
      const barHeight = (row.volume / maxVolume) * volumeHeight;
      const x = xFor(row.globalIndex);
      const barWidth = Math.max(1, (width - pad.left - pad.right) / Math.max(80, visibleChart.length + futureSlots));
      ctx.fillRect(x - barWidth / 2, volumeBase - barHeight, barWidth, barHeight);
    });
    ctx.restore();
  }

  ctx.strokeStyle = "#d8e0e4";
  ctx.lineWidth = 1;
  ctx.fillStyle = "#60707b";
  ctx.font = "700 12px system-ui";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i += 1) {
    const value = min + (span * i) / 4;
    const y = yFor(value);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(formatShortMoney(value), pad.left - 8, y + 4);
  }

  const monthStarts = [];
  let previousMonth = "";
  visibleChart.forEach((row) => {
    const month = row.date.slice(0, 7);
    if (month !== previousMonth) {
      monthStarts.push({ index: row.globalIndex, date: row.date });
      previousMonth = month;
    }
  });
  ctx.save();
  ctx.strokeStyle = "rgba(96, 112, 123, 0.22)";
  ctx.fillStyle = "#60707b";
  ctx.font = "800 12px system-ui";
  ctx.textAlign = "center";
  const monthSkip = monthStarts.length > 18 ? Math.ceil(monthStarts.length / 12) : 1;
  monthStarts.forEach((item, labelIndex) => {
    const x = xFor(item.index);
    ctx.beginPath();
    ctx.moveTo(x, pad.top);
    ctx.lineTo(x, height - pad.bottom);
    ctx.stroke();
    if (labelIndex % monthSkip === 0) {
      const label = monthStarts.length > 18 ? item.date.slice(0, 4) : monthName(item.date);
      ctx.fillText(label, x, height - 22);
    }
  });
  ctx.restore();

  ctx.save();
  ctx.strokeStyle = "rgba(46, 94, 158, 0.26)";
  const forecastStartX = xFor(chart.length - 1);
  const plotRight = width - pad.right;
  if (futureVisible) {
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(forecastStartX, pad.top);
    ctx.lineTo(forecastStartX, height - pad.bottom);
    ctx.stroke();
  }
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "rgba(46, 94, 158, 0.06)";
  if (futureVisible && forecastStartX < plotRight) {
    ctx.fillRect(Math.max(pad.left, forecastStartX), pad.top, plotRight - Math.max(pad.left, forecastStartX), height - pad.top - pad.bottom);
    ctx.fillStyle = "#60707b";
    ctx.font = "900 11px system-ui";
    ctx.textAlign = "left";
    ctx.fillText("Future scenarios", Math.max(pad.left + 8, forecastStartX + 8), pad.top + 16);
  }
  ctx.restore();

  const area = pathPoints(visibleChart.map((row) => ({ index: row.globalIndex, value: row.close })), xFor, yFor);
  if (area.length > 1) {
    ctx.beginPath();
    ctx.moveTo(area[0].x, height - pad.bottom);
    area.forEach((point) => ctx.lineTo(point.x, point.y));
    ctx.lineTo(area[area.length - 1].x, height - pad.bottom);
    ctx.closePath();
    const gradient = ctx.createLinearGradient(0, pad.top, 0, height - pad.bottom);
    gradient.addColorStop(0, "rgba(18, 107, 97, 0.18)");
    gradient.addColorStop(1, "rgba(18, 107, 97, 0.02)");
    ctx.fillStyle = gradient;
    ctx.fill();
  }

  strokeLine(ctx, area, "#126b61");
  strokeLine(
    ctx,
    pathPoints(visibleChart.map((row) => ({ index: row.globalIndex, value: row.sma20 })), xFor, yFor),
    "#b86b19"
  );
  const currentPoint = { index: chart.length - 1, value: chart[chart.length - 1].close };
  const drawScenarioPath = (path, color, dash = []) => {
    const points = [currentPoint, ...path.map((row, index) => ({ index: futureIndex(index), value: row.close }))];
    strokeLine(ctx, pathPoints(points, xFor, yFor), color, dash);
  };
  if (futureVisible) {
    drawScenarioPath(scenarioPaths.bullish || [], "rgba(18, 107, 97, 0.75)", [8, 5]);
    drawScenarioPath(scenarioPaths.base || forecast, "#2e5e9e", [6, 5]);
    drawScenarioPath(scenarioPaths.bearish || [], "rgba(178, 59, 59, 0.75)", [8, 5]);
    [
      { path: scenarioPaths.bullish, label: "Bullish", color: "#126b61", offset: -18 },
      { path: scenarioPaths.base || forecast, label: "Base", color: "#2e5e9e", offset: 0 },
      { path: scenarioPaths.bearish, label: "Bearish", color: "#b23b3b", offset: 18 },
    ].forEach((scenario) => {
      if (!scenario.path?.length) return;
      const last = scenario.path[scenario.path.length - 1];
      ctx.save();
      ctx.fillStyle = scenario.color;
      ctx.font = "900 11px system-ui";
      ctx.textAlign = "right";
      ctx.fillText(scenario.label, xFor(futureIndex(scenario.path.length - 1)), yFor(last.close) + scenario.offset);
      ctx.restore();
    });
  }

  if (overlayEnabled("channel") && chartAnalysis.channel) {
    const channel = chartAnalysis.channel;
    const upperSlope = (channel.upper_end - channel.upper_start) / Math.max(1, channel.end_index - channel.start_index);
    const lowerSlope = (channel.lower_end - channel.lower_start) / Math.max(1, channel.end_index - channel.start_index);
    const upperFuture = channel.upper_end + upperSlope * (virtualEnd - channel.end_index);
    const lowerFuture = channel.lower_end + lowerSlope * (virtualEnd - channel.end_index);
    strokeLine(
      ctx,
      [
        { x: xFor(channel.start_index), y: yFor(channel.upper_start) },
        { x: xFor(virtualEnd), y: yFor(upperFuture) },
      ],
      "rgba(184, 107, 25, 0.8)",
      [8, 5]
    );
    strokeLine(
      ctx,
      [
        { x: xFor(channel.start_index), y: yFor(channel.lower_start) },
        { x: xFor(virtualEnd), y: yFor(lowerFuture) },
      ],
      "rgba(184, 107, 25, 0.8)",
      [8, 5]
    );
    if (futureVisible && forecastStartX < plotRight) {
      ctx.save();
      ctx.fillStyle = "rgba(184, 107, 25, 0.1)";
      ctx.beginPath();
      ctx.moveTo(Math.max(pad.left, forecastStartX), yFor(channel.upper_end + upperSlope * (Math.max(virtualStart, chart.length - 1) - channel.end_index)));
      ctx.lineTo(xFor(virtualEnd), yFor(upperFuture));
      ctx.lineTo(xFor(virtualEnd), yFor(lowerFuture));
      ctx.lineTo(Math.max(pad.left, forecastStartX), yFor(channel.lower_end + lowerSlope * (Math.max(virtualStart, chart.length - 1) - channel.end_index)));
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }
  }

  if (overlayEnabled("trendline") && chartAnalysis.trendline) {
    const trendline = chartAnalysis.trendline;
    const slope = (trendline.end - trendline.start) / Math.max(1, trendline.end_index - trendline.start_index);
    const futureEnd = trendline.end + slope * (virtualEnd - trendline.end_index);
    strokeLine(
      ctx,
      [
        { x: xFor(trendline.start_index), y: yFor(trendline.start) },
        { x: xFor(virtualEnd), y: yFor(futureEnd) },
      ],
      "#172026",
      [2, 0]
    );
  }

  if (overlayEnabled("fibonacci") && Array.isArray(chartAnalysis.fibonacci)) {
    chartAnalysis.fibonacci.forEach((level) =>
      drawHorizontalLevel(ctx, yFor, pad, width, {
        value: level.value,
        label: `Fib ${level.label}`,
        color: "#2e5e9e",
        dash: [2, 4],
      })
    );
  }

  if (overlayEnabled("levels")) {
    [
      { value: technical.support, label: "Support", color: "#126b61" },
      { value: technical.resistance, label: "Resistance", color: "#b23b3b" },
    ].forEach((level) => drawHorizontalLevel(ctx, yFor, pad, width, level));
  }

  if (overlayEnabled("patterns") && Array.isArray(chartAnalysis.patterns) && chartAnalysis.patterns.length) {
    ctx.save();
    ctx.fillStyle = "#b86b19";
    ctx.font = "900 11px system-ui";
    ctx.textAlign = "right";
    chartAnalysis.patterns.slice(0, 3).forEach((pattern, index) => {
      ctx.fillText(pattern.name, width - pad.right, pad.top + 18 + index * 16);
    });
    ctx.restore();
  }

  ctx.save();
  ctx.fillStyle = "#172026";
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.font = "800 11px system-ui";
  ctx.textAlign = "center";
  const visiblePeaks = monthlyPeaks.filter((peak) => peak.index >= visibleStart && peak.index <= visibleEnd);
  const peakSkip = visiblePeaks.length > 16 ? Math.ceil(visiblePeaks.length / 10) : 1;
  visiblePeaks.forEach((peak, peakIndex) => {
    const x = xFor(peak.index);
    const y = yFor(peak.close);
    ctx.beginPath();
    ctx.arc(x, y, 4.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    if (peakIndex % peakSkip !== 0) return;
    const labelY = Math.max(pad.top + 10, y - 10);
    ctx.fillStyle = "#34434d";
    ctx.fillText(formatShortMoney(peak.close), x, labelY);
    ctx.fillStyle = "#172026";
  });
  ctx.restore();

  if (visiblePeaks.length) {
    ctx.save();
    ctx.fillStyle = "#60707b";
    ctx.font = "800 12px system-ui";
    ctx.textAlign = "right";
    ctx.fillText("Monthly peaks", width - pad.right, pad.top - 8);
    ctx.restore();
  }

  ctx.fillStyle = "#60707b";
  ctx.textAlign = "left";
  ctx.fillText(visibleChart[0]?.date || chart[0].date, pad.left, height - 8);
  ctx.textAlign = "right";
  ctx.fillText(futureVisible ? chart[chart.length - 1].date : visibleChart[visibleChart.length - 1].date, width - pad.right, height - 8);
  updateChartControls();
}

function renderFlow(flow) {
  flowchart.innerHTML = "";
  if (!flow?.length) {
    flowchart.innerHTML = '<div class="flow-node empty">No decision flow available</div>';
    return;
  }
  flow.forEach((item) => {
    const node = document.createElement("div");
    node.className = `flow-node ${item.state || "neutral"}`;
    const label = document.createElement("strong");
    label.textContent = item.label;
    const value = document.createElement("span");
    value.textContent = item.value;
    node.append(label, value);
    flowchart.appendChild(node);
  });
}

function renderTechnical(technical = {}) {
  const probability = technical.probability || {};
  trendState.textContent = technical.trend || "-";
  trendDetail.textContent = technical.pattern || "Price vs 20D/50D averages and MACD";
  rsiValue.textContent = typeof technical.rsi14 === "number" ? formatNumber(technical.rsi14, 1) : "-";
  rsiState.textContent = technical.rsi_state || "Momentum oscillator";
  macdValue.textContent = formatNumber(technical.macd_histogram, 2);
  macdState.textContent = technical.macd_state || "Momentum crossover";
  levelsValue.textContent =
    typeof technical.support === "number" && typeof technical.resistance === "number"
      ? `${formatShortMoney(technical.support)} / ${formatShortMoney(technical.resistance)}`
      : "-";
  levelsState.textContent =
    technical.support_date && technical.resistance_date
      ? `Support ${technical.support_date} / resistance ${technical.resistance_date}`
      : "Recent range levels";
  volumeState.textContent = technical.volume_state || "-";
  volumeValue.textContent =
    typeof technical.volume_change_pct === "number"
      ? `${formatPct(technical.volume_change_pct)} versus baseline`
      : "Recent activity";
  probabilityValue.textContent =
    typeof probability.up_pct === "number" ? `${formatPct(probability.up_pct)} up` : "-";
  probabilityState.textContent =
    typeof probability.down_pct === "number"
      ? `${formatPct(probability.down_pct)} down / ${probability.sample_size || 0} matches over ${probability.lookahead_sessions || 10} sessions`
      : "Similar historical setups";
}

function renderScenario(analytics = {}) {
  const chartAnalysis = analytics.chart_analysis || {};
  scenarioReport.textContent =
    chartAnalysis.scenario_report || "No chart scenario available for this search yet.";
}

function drawPredictionChart(predictions = []) {
  const evaluated = predictions
    .filter((item) => item.status === "evaluated" && typeof item.target_error_pct === "number")
    .slice()
    .reverse();
  if (!evaluated.length) {
    drawEmptyPredictionChart("No evaluated predictions yet");
    return;
  }

  const canvas = predictionChart;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.max(220, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

  const width = rect.width;
  const height = rect.height;
  const pad = { top: 24, right: 18, bottom: 42, left: 58 };
  const values = evaluated.map((item) => item.target_error_pct);
  const max = Math.max(5, ...values);
  const xFor = (index) => pad.left + (index / Math.max(1, evaluated.length - 1)) * (width - pad.left - pad.right);
  const yFor = (value) => pad.top + ((max - value) / max) * (height - pad.top - pad.bottom);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f7faf9";
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "#d8e0e4";
  ctx.fillStyle = "#60707b";
  ctx.font = "750 11px system-ui";
  ctx.textAlign = "right";
  for (let i = 0; i <= 4; i += 1) {
    const value = (max * i) / 4;
    const y = yFor(value);
    ctx.beginPath();
    ctx.moveTo(pad.left, y);
    ctx.lineTo(width - pad.right, y);
    ctx.stroke();
    ctx.fillText(`${value.toFixed(1)}%`, pad.left - 8, y + 4);
  }

  const points = evaluated.map((item, index) => ({ x: xFor(index), y: yFor(item.target_error_pct), item }));
  strokeLine(ctx, points, "#2e5e9e", [5, 4]);
  points.forEach((point) => {
    ctx.save();
    ctx.fillStyle = point.item.direction_correct ? "#126b61" : "#b23b3b";
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  });

  ctx.fillStyle = "#60707b";
  ctx.textAlign = "left";
  ctx.fillText(evaluated[0].actual_date || evaluated[0].due_date, pad.left, height - 12);
  ctx.textAlign = "right";
  const last = evaluated[evaluated.length - 1];
  ctx.fillText(last.actual_date || last.due_date, width - pad.right, height - 12);
}

function renderPredictionTracker(data = {}) {
  lastPredictionData = data;
  if (!predictionTotal) return; // tracker table isn't on this page (home)
  const summary = data.summary || {};
  const predictions = data.predictions || [];
  predictionTotal.textContent = summary.total ?? "-";
  predictionAccuracy.textContent = formatPct(summary.direction_accuracy_pct);
  predictionError.textContent = formatPct(summary.avg_target_error_pct);
  predictionPending.textContent = summary.pending ?? "-";

  symbolAccuracy.innerHTML = "";
  if (data.by_symbol?.length) {
    data.by_symbol.forEach((item) => {
      const row = document.createElement("div");
      row.className = "accuracy-row";
      row.innerHTML = `<strong>${item.symbol}</strong><span>${formatPct(item.accuracy_pct)} / ${item.total} checks</span>`;
      symbolAccuracy.appendChild(row);
    });
  } else {
    symbolAccuracy.innerHTML = '<p class="empty">No evaluated predictions yet.</p>';
  }

  predictionRows.innerHTML = "";
  if (predictions.length) {
    predictions.slice(0, 60).forEach((item) => {
      const tr = document.createElement("tr");
      const status = item.status === "evaluated" ? (item.direction_correct ? "Correct" : "Missed") : "Pending";
      tr.className = item.status === "evaluated" ? (item.direction_correct ? "good-row" : "bad-row") : "";
      const horizon = item.horizon_days ? `${item.horizon_days}d` : `${item.horizon_sessions || "-"}s`;
      tr.innerHTML = `
        <td>${item.symbol}</td>
        <td>${item.predicted_direction}</td>
        <td>${horizon}</td>
        <td>${formatShortMoney(item.target_price)}</td>
        <td>${item.due_date || "-"}</td>
        <td>${typeof item.actual_price === "number" ? formatShortMoney(item.actual_price) : "-"}</td>
        <td>${status}</td>
      `;
      predictionRows.appendChild(tr);
    });
  } else {
    predictionRows.innerHTML = '<tr><td colspan="7">Predictions will appear after research runs.</td></tr>';
  }
  drawPredictionChart(predictions);
}

async function loadPredictionTracker() {
  try {
    const response = await fetch("/api/predictions");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load prediction history.");
    renderPredictionTracker(data);
  } catch {
    drawEmptyPredictionChart("Could not load prediction history");
  }
  loadBuyRecommendation();
}

function renderBuyRecommendation(data = {}) {
  if (!buyPickSymbol) return; // pick card isn't on this page
  const pick = data.pick;
  buyPickAsOf.textContent = data.as_of ? `as of ${String(data.as_of).replace("T", " ")}` : "";
  if (!pick) {
    buyPickSymbol.textContent = data.note || "Not enough prediction history yet.";
    buyPickReason.textContent = "";
    buyPickRunner.textContent = "";
    buyPickRanked.innerHTML = "";
    buyPickNote.textContent = "";
    return;
  }
  const arrow = pick.direction === "up" ? "▲" : pick.direction === "down" ? "▼" : "▬";
  buyPickSymbol.textContent = `${arrow} ${pick.symbol} — ${pick.name || pick.symbol}`;
  buyPickSymbol.dataset.dir = pick.direction || "";
  buyPickReason.textContent = pick.reason || "";
  buyPickRunner.textContent = data.runner_up
    ? `Runner-up: ${data.runner_up.symbol} — ${data.runner_up.reason}`
    : "";
  const ranked = data.ranked || [];
  buyPickRanked.innerHTML = ranked
    .map((r) => {
      const arrow = r.direction === "up" ? "▲" : r.direction === "down" ? "▼" : "▬";
      const move = typeof r.expected_return_pct === "number"
        ? `${r.expected_return_pct >= 0 ? "+" : ""}${r.expected_return_pct.toFixed(1)}%`
        : r.direction;
      const hz = r.horizon_days ? `/${r.horizon_days}d` : "";
      const acc = typeof r.accuracy_pct === "number" ? ` · ${r.accuracy_pct.toFixed(0)}% hit` : "";
      return `<li><span class="rk-sym">${arrow} ${r.symbol}</span><span class="rk-meta">${move}${hz}${acc}</span></li>`;
    })
    .join("");
  buyPickNote.textContent = data.note || "";
}

async function loadBuyRecommendation() {
  try {
    const response = await fetch("/api/recommendation");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load recommendation.");
    renderBuyRecommendation(data);
  } catch {
    buyPickSymbol.textContent = "Could not load the recommendation.";
    buyPickReason.textContent = "";
    buyPickRunner.textContent = "";
    buyPickNote.textContent = "";
  }
  loadScorecard();
}

function renderScorecard(data = {}) {
  if (!scorecardHeadline) return; // scorecard isn't on this page
  if (!data || !data.sample) {
    scorecardHeadline.textContent = data.headline || "not run yet";
    scorecardGrid.innerHTML = "";
    scorecardRegime.textContent = "";
    scorecardSymbols.textContent = "";
    return;
  }
  const o = data.overall || {};
  scorecardHeadline.textContent = data.headline || "";
  const cells = [
    ["Direction accuracy", formatPct(o.direction_accuracy_pct)],
    ["Up-call precision", formatPct(o.up_call?.precision_pct)],
    ["Down-call precision", formatPct(o.down_call?.precision_pct)],
    ["Called 'sideways'", formatPct(o.sideways_share_pct)],
    ["Brier (0.25 = coin flip)", o.brier ?? "-"],
    ["Avg target error", formatPct(o.avg_target_error_pct)],
  ];
  scorecardGrid.innerHTML = cells
    .map(([k, v]) => `<div class="scorecard-cell"><span>${k}</span><strong>${v}</strong></div>`)
    .join("");
  const learned = data.learned;
  if (learned && learned.walkforward) {
    const w = learned.walkforward;
    const b = learned.incumbent || learned.baseline_blend_v1 || {};
    const bp = typeof b.mean_pnl_pct === "number" ? `${b.mean_pnl_pct >= 0 ? "+" : ""}${b.mean_pnl_pct}%/trade` : "n/a";
    scorecardRegime.textContent =
      `${learned.model || "learned model"} (${learned.activated ? "LIVE" : "not activated"}, walk-forward, out-of-sample): ` +
      `${formatPct(w.hit_rate_pct)} directional hit · ${w.mean_pnl_pct >= 0 ? "+" : ""}${w.mean_pnl_pct}%/trade · ` +
      `Sharpe ${w.sharpe_like} · deployed ${formatPct(w.deployed_pct)}  — prior champion ${bp}`;
  } else {
    const regime = (data.by_regime || [])
      .map((r) => `${r.regime} ${formatPct(r.accuracy_pct)}`)
      .join(" · ");
    scorecardRegime.textContent = regime ? `By regime: ${regime}` : "";
  }
  const best = (data.best_symbols || []).slice(0, 4).map((s) => `${s.symbol} ${formatPct(s.accuracy_pct)}`).join(", ");
  const worst = (data.worst_symbols || []).slice(0, 4).map((s) => `${s.symbol} ${formatPct(s.accuracy_pct)}`).join(", ");
  scorecardSymbols.textContent = best ? `Best: ${best}  —  Worst: ${worst}` : "";
}

async function loadScorecard() {
  try {
    const response = await fetch("/api/scorecard");
    const data = await response.json();
    renderScorecard(data);
  } catch {
    scorecardHeadline.textContent = "could not load scorecard";
  }
}

function setActivePeriod(period) {
  currentPeriod = period;
  rangeFilter.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.period === period);
  });
}

function setLoading(isLoading) {
  const button = form.querySelector("button[type='submit']");
  button.disabled = isLoading;
  queryInput.disabled = isLoading;
  marketToggle.disabled = isLoading;
  button.innerHTML = isLoading
    ? '<span aria-hidden="true">...</span> Researching'
    : '<span aria-hidden="true">Go</span> Research';
  sourceStatus.textContent = isLoading ? "Working" : "Live data";
}

function renderError(message) {
  action.textContent = "Error";
  confidence.textContent = "Try another ticker or market";
  reportTitle.textContent = "Research issue";
  report.textContent = message;
  sourceStatus.textContent = "Check data";
  volatility.textContent = "-";
  drawdown.textContent = "-";
  projection.textContent = "-";
  renderTechnical();
  renderScenario();
  drawEmptyChart("Research failed");
  renderFlow([]);
}

function render(data) {
  const q = data.quote;
  const s = data.signal;
  const sentiment = data.sentiment;
  const analytics = data.analytics || {};
  const stats = analytics.stats || {};
  const currency = q.currency || "USD";

  action.textContent = s.action;
  confidence.textContent = `${s.confidence} confidence`;
  price.textContent = formatMoney(q.price, currency);
  exchange.textContent = [q.symbol, q.exchange].filter(Boolean).join(" / ");
  change.textContent = formatPct(q.change_pct_6m);
  range.textContent = `${formatMoney(q.low_6m, currency)} to ${formatMoney(q.high_6m, currency)}`;
  tone.textContent = sentiment.label;
  tone.className = sentiment.label === "positive" ? "positive" : sentiment.label === "negative" ? "negative" : "neutral";
  toneScore.textContent = `headline score ${sentiment.score}`;
  reportTitle.textContent = `${q.name} (${q.symbol})`;
  report.textContent = data.report;
  lastReport = data.report;
  lastAnalytics = analytics;
  copyReport.disabled = false;
  volatility.textContent = formatPct(stats.volatility_annualized_pct);
  drawdown.textContent = formatPct(stats.max_drawdown_pct);
  projection.textContent = formatPct(stats.projection_change_pct);
  renderTechnical(analytics.technical);
  renderScenario(analytics);
  renderModelForecast(data.forecast_model, data.horizon_days || currentHorizonDays);
  drawPriceChart(analytics);
  renderFlow(analytics.flow);

  signals.innerHTML = "";
  if (s.reasons.length) {
    s.reasons.forEach((reason) => {
      const li = document.createElement("li");
      li.textContent = reason;
      signals.appendChild(li);
    });
  } else {
    signals.innerHTML = "<li>No strong signal detected.</li>";
  }

  headlines.innerHTML = "";
  sentiment.articles.slice(0, 8).forEach((article) => {
    const link = document.createElement("a");
    link.className = "headline";
    link.href = article.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    const title = document.createElement("strong");
    title.textContent = article.title || "Untitled headline";
    const meta = document.createElement("span");
    meta.textContent = [article.source, article.published ? new Date(article.published).toLocaleString() : ""]
      .filter(Boolean)
      .join(" / ");
    link.append(title, meta);
    headlines.appendChild(link);
  });

  if (!sentiment.articles.length) {
    headlines.innerHTML = '<p class="empty">No headlines were returned by the news source.</p>';
  }
}

async function runResearch(query, period = currentPeriod) {
  if (!query) return;

  setLoading(true);
  setActivePeriod(period);
  chartPanStart = null;
  chartZoom = 1;
  updateZoomLabel();
  reportTitle.textContent = `Researching ${query}`;
  report.textContent = "Gathering market data, reading headlines, scoring momentum, and building a report...";
  copyReport.disabled = true;

  try {
    const response = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, period, horizon_days: currentHorizonDays }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Research failed.");
    addMarketOption(query);
    render(data);
    await loadPredictionTracker();
  } catch (error) {
    renderError(error.message);
  } finally {
    setLoading(false);
  }
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  closeMarketMenu();
  await runResearch(queryInput.value.trim());
});

queryInput?.addEventListener("input", openMarketMenu);
queryInput?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeMarketMenu();
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    openMarketMenu();
    marketOptions.querySelector(".market-option")?.focus();
  }
});

marketToggle?.addEventListener("click", () => {
  if (marketOptions.classList.contains("open")) closeMarketMenu();
  else {
    queryInput.focus();
    openMarketMenu();
  }
});

marketOptions?.addEventListener("keydown", (event) => {
  const options = Array.from(marketOptions.querySelectorAll(".market-option"));
  const index = options.indexOf(document.activeElement);
  if (event.key === "Escape") {
    closeMarketMenu();
    queryInput.focus();
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    options[Math.min(options.length - 1, index + 1)]?.focus();
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (index <= 0) queryInput.focus();
    else options[index - 1]?.focus();
  }
});

document.addEventListener("click", (event) => {
  if (!marketCombo.contains(event.target)) closeMarketMenu();
});

rangeFilter?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-period]");
  if (!button) return;
  const query = queryInput.value.trim();
  setActivePeriod(button.dataset.period);
  if (query) await runResearch(query, button.dataset.period);
});

overlayFilter?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-overlay]");
  if (!button) return;
  const overlay = button.dataset.overlay;
  if (activeOverlays.has(overlay)) activeOverlays.delete(overlay);
  else activeOverlays.add(overlay);
  button.classList.toggle("active", activeOverlays.has(overlay));
  if (lastAnalytics) drawPriceChart(lastAnalytics);
});

zoomIn?.addEventListener("click", () => {
  setChartZoom(chartZoom * 1.35);
  if (lastAnalytics) drawPriceChart(lastAnalytics);
});

zoomOut?.addEventListener("click", () => {
  setChartZoom(chartZoom / 1.35);
  if (lastAnalytics) drawPriceChart(lastAnalytics);
});

panLeft?.addEventListener("click", () => {
  panByPage(-1);
});

panRight?.addEventListener("click", () => {
  panByPage(1);
});

chartPanSlider?.addEventListener("input", () => {
  if (!lastAnalytics?.chart?.length) return;
  const maxStart = Number(chartPanSlider.max || 0);
  const nextStart = Number(chartPanSlider.value || 0);
  chartPanStart = nextStart >= maxStart ? null : nextStart;
  updateZoomLabel();
  drawPriceChart(lastAnalytics);
});

zoomReset?.addEventListener("click", () => {
  chartZoom = 1;
  chartPanStart = null;
  updateZoomLabel();
  if (lastAnalytics) drawPriceChart(lastAnalytics);
});

priceChart?.addEventListener(
  "wheel",
  (event) => {
    if (!lastAnalytics) return;
    event.preventDefault();
    const rect = priceChart.getBoundingClientRect();
    if (Math.abs(event.deltaX) > Math.abs(event.deltaY) || event.shiftKey) {
      const plotWidth = Math.max(1, rect.width - 88);
      const { visibleCount } = chartWindow(lastAnalytics.chart.length);
      panChart((event.deltaX / plotWidth) * visibleCount);
    } else {
      const anchorRatio = Math.min(1, Math.max(0, (event.clientX - rect.left - 68) / Math.max(1, rect.width - 88)));
      setChartZoom(event.deltaY < 0 ? chartZoom * 1.15 : chartZoom / 1.15, anchorRatio);
    }
    drawPriceChart(lastAnalytics);
  },
  { passive: false }
);

priceChart?.addEventListener("pointerdown", (event) => {
  if (!lastAnalytics?.chart?.length) return;
  event.preventDefault();
  priceChart.setPointerCapture(event.pointerId);
  const { visibleStart, visibleCount } = chartWindow(lastAnalytics.chart.length);
  dragState = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startPan: visibleStart,
    visibleCount,
    width: Math.max(1, priceChart.getBoundingClientRect().width - 88),
  };
  priceChart.classList.add("dragging");
});

priceChart?.addEventListener("pointermove", (event) => {
  if (!dragState || dragState.pointerId !== event.pointerId || !lastAnalytics?.chart?.length) return;
  event.preventDefault();
  const deltaX = event.clientX - dragState.startX;
  const deltaIndex = (-deltaX / dragState.width) * dragState.visibleCount;
  chartPanStart = dragState.startPan + deltaIndex;
  panChart(0);
  drawPriceChart(lastAnalytics);
});

function endChartDrag(event) {
  if (!dragState || dragState.pointerId !== event.pointerId) return;
  dragState = null;
  priceChart.classList.remove("dragging");
}

priceChart?.addEventListener("pointerup", endChartDrag);
priceChart?.addEventListener("pointercancel", endChartDrag);

refreshPredictions?.addEventListener("click", loadPredictionTracker);

copyReport?.addEventListener("click", async () => {
  if (!lastReport) return;
  await navigator.clipboard.writeText(lastReport);
  copyReport.innerHTML = '<span aria-hidden="true">OK</span> Copied';
  setTimeout(() => {
    copyReport.innerHTML = '<span aria-hidden="true">[]</span> Copy';
  }, 1200);
});

function setActiveHorizon(days) {
  currentHorizonDays = days;
  horizonFilter.querySelectorAll("button[data-horizon]").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.horizon) === days);
  });
}

horizonFilter?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-horizon]");
  if (!button) return;
  horizonCustom.value = "";
  setActiveHorizon(Number(button.dataset.horizon));
});

horizonCustom?.addEventListener("change", () => {
  if (!horizonCustom.value) return;
  const days = Math.round((new Date(horizonCustom.value) - new Date()) / 86400000);
  if (days >= 1 && days <= 365) {
    horizonFilter.querySelectorAll("button[data-horizon]").forEach((button) => button.classList.remove("active"));
    currentHorizonDays = days;
  } else {
    horizonCustom.value = "";
  }
});

clearRecents?.addEventListener("click", () => {
  if (confirm("Clear all recent searches?")) clearSavedMarkets();
});

function renderModelForecast(model, horizonDays) {
  if (!model || !model.direction) {
    modelForecast.hidden = true;
    return;
  }
  modelForecast.hidden = false;
  modelDir.textContent = model.direction;
  modelDir.className = `model-dir dir-${model.direction}`;
  const parts = [`${horizonDays}-day view`, `${model.confidence} confidence`];
  if (typeof model.expected_return_pct === "number") {
    parts.push(`expected ${model.expected_return_pct > 0 ? "+" : ""}${model.expected_return_pct.toFixed(1)}%`);
  }
  if (typeof model.band_pct === "number") parts.push(`flat band ±${model.band_pct.toFixed(1)}%`);
  const ov = model.overlays;
  if (ov && typeof model.model_expected_return_pct === "number" && Math.abs(ov.total_tilt_pct || 0) >= 0.05) {
    const m = model.model_expected_return_pct;
    parts.push(
      `model ${m > 0 ? "+" : ""}${m.toFixed(1)}% ${ov.total_tilt_pct > 0 ? "+" : "−"} ${Math.abs(ov.total_tilt_pct).toFixed(1)}% overlay` +
      (ov.notes && ov.notes.length ? ` (${ov.notes.join("; ")})` : "")
    );
  }
  modelMeta.textContent = parts.join(" · ");
  modelComponents.innerHTML = "";
  (model.components || []).slice(0, 5).forEach((component) => {
    const li = document.createElement("li");
    const sign = component.contribution > 0 ? "+" : component.contribution < 0 ? "−" : "·";
    li.innerHTML = `<span>${component.name.replace(/_/g, " ")}</span><span class="mc-${sign === "+" ? "up" : sign === "−" ? "down" : "flat"}">${sign}${Math.abs(component.contribution).toFixed(2)}</span>`;
    modelComponents.appendChild(li);
  });
}

const PAGE = document.body?.dataset.page || "";

const footerYear = document.querySelector("#footerYear");
if (footerYear) footerYear.textContent = String(new Date().getFullYear());

if (PAGE === "research") {
  renderMarketOptions();
  renderRecentSearches();
  setActivePeriod(currentPeriod);
  setActiveHorizon(currentHorizonDays);
  updateZoomLabel();
  drawEmptyChart();
}

if (document.querySelector("#predictionChart")) {
  drawEmptyPredictionChart();
}

if (PAGE === "home" || PAGE === "model") {
  loadPredictionTracker();
}

window.addEventListener("resize", () => {
  if (typeof lastAnalytics !== "undefined" && lastAnalytics) drawPriceChart(lastAnalytics);
  else if (document.querySelector("#priceChart")) drawEmptyChart();
  if (typeof lastPredictionData !== "undefined" && lastPredictionData) {
    drawPredictionChart(lastPredictionData.predictions || []);
  } else if (document.querySelector("#predictionChart")) {
    drawEmptyPredictionChart();
  }
});

/* Home page: a few headline numbers pulled from the same feeds. */
async function renderHomeFacts() {
  const lead = document.querySelector("#homeStandLead");
  const facts = document.querySelector("#homeFacts");
  if (!lead || !facts) return;
  try {
    const [scRes, recRes] = await Promise.all([fetch("/api/scorecard"), fetch("/api/recommendation")]);
    const sc = await scRes.json();
    const rec = await recRes.json();
    const learned = sc.learned && sc.learned.walkforward ? sc.learned.walkforward : null;
    const rows = [];
    if (learned) {
      lead.textContent = `Live model, walk-forward tested: ${formatPct(learned.hit_rate_pct)} directional hit rate, ${
        learned.mean_pnl_pct >= 0 ? "+" : ""
      }${learned.mean_pnl_pct}% average per call.`;
      rows.push(["Model", sc.learned.model || "learned"]);
      rows.push(["Walk-forward hit rate", formatPct(learned.hit_rate_pct)]);
      rows.push(["Avg P&L / call", `${learned.mean_pnl_pct >= 0 ? "+" : ""}${learned.mean_pnl_pct}%`]);
    } else {
      lead.textContent = "Backtest scorecard is still building.";
    }
    if (sc.sample) rows.push(["Backtest sample", `${Number(sc.sample).toLocaleString()} calls`]);
    if (rec && rec.pick) rows.push(["Today's top pick", rec.pick.symbol]);
    facts.innerHTML = rows.map(([k, v]) => `<li><span>${k}</span><span>${v}</span></li>`).join("");
  } catch {
    lead.textContent = "Couldn't load the current numbers.";
  }
}
if (PAGE === "home") renderHomeFacts();
