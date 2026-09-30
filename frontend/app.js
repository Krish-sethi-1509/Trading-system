const API = "";
const WATCHLIST_KEY = "nse_dashboard_watchlist";
const PINNED_TF_KEY = "nse_dashboard_pinned_tf";

// Each timeframe pairs a bar interval with a period Yahoo will actually
// return data for at that resolution (1m only works ~7 days back, up to
// 30m/90m works ~60 days back, 1h works out to ~2 years, 1d+ is unrestricted).
// There's no "seconds" group here on purpose: no data source this app uses
// (yfinance, or NSE's own site) hands out sub-minute bars for NSE symbols,
// so a seconds option would just be a UI element with nothing behind it.
const TIMEFRAMES = [
  { key: "1m",  label: "1 minute",  short: "1m",  group: "Minutes", interval: "1m",  period: "5d" },
  { key: "2m",  label: "2 minutes", short: "2m",  group: "Minutes", interval: "2m",  period: "60d" },
  { key: "5m",  label: "5 minutes", short: "5m",  group: "Minutes", interval: "5m",  period: "60d" },
  { key: "15m", label: "15 minutes",short: "15m", group: "Minutes", interval: "15m", period: "60d" },
  { key: "30m", label: "30 minutes",short: "30m", group: "Minutes", interval: "30m", period: "60d" },
  { key: "1h",  label: "1 hour",    short: "1H",  group: "Hours",   interval: "1h",  period: "2y" },
  { key: "1d",  label: "1 day",     short: "1D",  group: "Days",    interval: "1d",  period: "5y" },
  { key: "1wk", label: "1 week",    short: "1W",  group: "Days",    interval: "1wk", period: "max" },
  { key: "1mo", label: "1 month",   short: "1M",  group: "Days",    interval: "1mo", period: "max" },
];
const DEFAULT_TF_KEY = "1d";

const searchBox = document.getElementById("search-box");
const searchResults = document.getElementById("search-results");
const dashboard = document.getElementById("dashboard");
const statusMsg = document.getElementById("status-msg");
const periodPills = document.getElementById("period-pills");
const intradayNote = document.getElementById("intraday-note");
const watchlistEl = document.getElementById("watchlist");
const watchlistEmpty = document.getElementById("watchlist-empty");
const watchlistBtn = document.getElementById("watchlist-btn");
const compareToggle = document.getElementById("compare-toggle");
const comparePanel = document.getElementById("compare-panel");
const compareBox = document.getElementById("compare-box");
const compareResults = document.getElementById("compare-results");
const compareActive = document.getElementById("compare-active");
const chartLoading = document.getElementById("chart-loading");
const exportBtn = document.getElementById("export-btn");
const backtestLoading = document.getElementById("backtest-loading");
const indexStrip = document.getElementById("index-strip");
const backtestPanel = document.querySelector(".backtest-panel");

let currentSymbol = null;
let compareSymbol = null;
let currentTf = TIMEFRAMES.find((t) => t.key === DEFAULT_TF_KEY); // default: 1D
let quotePollTimer = null;
let priceChart = null;       // lightweight-charts instance
let candleSeries = null;
let compareSeries = null;
let backtestChart = null;    // Chart.js instance (equity curve stays a line chart)
let lastHistory = null;

const REGIME_COLORS = {
  "Bull Trend": "#1fb45c",
  "Bear Trend": "#e5484d",
  "High Volatility": "#f2b134",
  "Range / Sideways": "#64748b",
};

// ================= WATCHLIST =================

function getWatchlist() {
  try { return JSON.parse(localStorage.getItem(WATCHLIST_KEY) || "[]"); }
  catch (e) { return []; }
}
function saveWatchlist(list) { localStorage.setItem(WATCHLIST_KEY, JSON.stringify(list)); }
function isInWatchlist(symbol) { return getWatchlist().includes(symbol); }

function toggleWatchlist(symbol) {
  let list = getWatchlist();
  list = list.includes(symbol) ? list.filter((s) => s !== symbol) : [...list, symbol];
  saveWatchlist(list);
  renderWatchlist();
  updateWatchlistBtn();
}

function removeFromWatchlist(symbol) {
  saveWatchlist(getWatchlist().filter((s) => s !== symbol));
  renderWatchlist();
  if (symbol === currentSymbol) updateWatchlistBtn();
}

function renderWatchlist() {
  const list = getWatchlist();
  watchlistEl.innerHTML = "";
  watchlistEmpty.classList.toggle("hidden", list.length > 0);

  for (const symbol of list) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span><span class="wl-sym">${symbol}</span></span>
      <span style="display:flex;align-items:center;gap:8px;">
        <span class="wl-price" data-price-for="${symbol}"></span>
        <button class="wl-remove" title="Remove">✕</button>
      </span>`;
    li.addEventListener("click", (e) => {
      if (e.target.classList.contains("wl-remove")) return;
      selectSymbol(symbol);
    });
    li.querySelector(".wl-remove").addEventListener("click", (e) => {
      e.stopPropagation();
      removeFromWatchlist(symbol);
    });
    watchlistEl.appendChild(li);

    fetch(`${API}/api/quote/${symbol}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((q) => {
        if (!q || q.last_price == null) return;
        const el = li.querySelector(`[data-price-for="${symbol}"]`);
        if (el) el.textContent = `₹${q.last_price}`;
      })
      .catch(() => {});
  }
}

function updateWatchlistBtn() {
  if (!currentSymbol) return;
  const active = isInWatchlist(currentSymbol);
  watchlistBtn.textContent = active ? "★" : "☆";
  watchlistBtn.classList.toggle("active", active);
}

watchlistBtn.addEventListener("click", () => { if (currentSymbol) toggleWatchlist(currentSymbol); });

// ================= SEARCH =================

let searchDebounce = null;
searchBox.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  const q = searchBox.value.trim();
  if (q.length < 1) { searchResults.classList.remove("show"); return; }
  searchDebounce = setTimeout(() => runSearch(q, searchResults, selectSymbol), 250);
});

async function runSearch(q, resultsEl, onPick) {
  try {
    const res = await fetch(`${API}/api/symbols?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    renderSearchResults(data.results, resultsEl, onPick);
  } catch (e) {
    setStatus("Search failed — is the backend running?", true);
  }
}

function renderSearchResults(results, resultsEl, onPick) {
  if (!results.length) {
    resultsEl.innerHTML = `<div class="search-result-item">No matches</div>`;
    resultsEl.classList.add("show");
    return;
  }
  resultsEl.innerHTML = results
    .map((r) => `<div class="search-result-item" data-symbol="${r.symbol}">
      <span class="sym">${r.symbol}</span><span class="name">${r.name}</span>
    </div>`)
    .join("");
  resultsEl.classList.add("show");
  resultsEl.querySelectorAll(".search-result-item").forEach((el) => {
    el.addEventListener("click", () => {
      const symbol = el.getAttribute("data-symbol");
      if (symbol) onPick(symbol);
    });
  });
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".global-search")) searchResults.classList.remove("show");
  if (!e.target.closest(".compare-panel")) compareResults.classList.remove("show");
});

// ================= COMPARE MODE =================

compareToggle.addEventListener("click", () => comparePanel.classList.toggle("hidden"));

let compareDebounce = null;
compareBox.addEventListener("input", () => {
  clearTimeout(compareDebounce);
  const q = compareBox.value.trim();
  if (q.length < 1) { compareResults.classList.remove("show"); return; }
  compareDebounce = setTimeout(() => runSearch(q, compareResults, selectCompareSymbol), 250);
});

function selectCompareSymbol(symbol) {
  compareSymbol = symbol;
  compareBox.value = "";
  compareResults.classList.remove("show");
  compareActive.classList.remove("hidden");
  compareActive.innerHTML = `Comparing with <b>${symbol}</b> <button id="clear-compare">✕</button>`;
  document.getElementById("clear-compare").addEventListener("click", clearCompare);
  document.getElementById("compare-legend-label").textContent = `${symbol} (comparison)`;
  document.getElementById("compare-legend").classList.remove("hidden");
  if (currentSymbol) { loadChartAndRegime(currentSymbol); loadAnalysisPanels(currentSymbol); }
}

function clearCompare() {
  compareSymbol = null;
  compareActive.classList.add("hidden");
  document.getElementById("compare-legend").classList.add("hidden");
  document.getElementById("compare-regime-card").classList.add("hidden");
  if (currentSymbol) loadChartAndRegime(currentSymbol);
}

// ================= TIMEFRAME DROPDOWN (grouped, with star-pin) =================

function getPinnedTf() {
  return localStorage.getItem(PINNED_TF_KEY);
}

// Preserve TIMEFRAMES order within each group, but keep groups in a fixed
// display order regardless of where entries fall in the array.
const TF_GROUP_ORDER = ["Minutes", "Hours", "Days"];

function groupedTimeframes() {
  const groups = {};
  TIMEFRAMES.forEach((tf) => {
    (groups[tf.group] = groups[tf.group] || []).push(tf);
  });
  return TF_GROUP_ORDER.filter((g) => groups[g]).map((g) => [g, groups[g]]);
}

function renderPeriodPills() {
  const pinned = getPinnedTf();
  const wasOpen = periodPills.querySelector(".tf-menu.open") != null;

  const menuHtml = groupedTimeframes().map(([groupName, tfs]) => `
    <div class="tf-group">
      <div class="tf-group-label">${groupName}</div>
      ${tfs.map((tf) => {
        const isActive = tf.key === currentTf.key;
        const isPinned = tf.key === pinned;
        return `<div class="tf-option${isActive ? " active" : ""}" data-key="${tf.key}">
          <button class="tf-select" data-key="${tf.key}">${tf.label}</button>
          <button class="tf-pin${isPinned ? " pinned" : ""}" data-key="${tf.key}" title="Set as default">${isPinned ? "★" : "☆"}</button>
        </div>`;
      }).join("")}
    </div>
  `).join("");

  periodPills.innerHTML = `
    <button id="tf-trigger" class="tf-trigger" type="button">
      <span>${currentTf.short}</span><span class="tf-caret">▾</span>
    </button>
    <div class="tf-menu${wasOpen ? " open" : ""}">${menuHtml}</div>
  `;
}

periodPills.addEventListener("click", (e) => {
  const trigger = e.target.closest("#tf-trigger");
  const pinBtn = e.target.closest("button.tf-pin");
  const selectBtn = e.target.closest("button.tf-select");
  const menu = periodPills.querySelector(".tf-menu");

  if (trigger) {
    menu.classList.toggle("open");
    return;
  }

  if (pinBtn) {
    const key = pinBtn.getAttribute("data-key");
    const currentlyPinned = getPinnedTf();
    localStorage.setItem(PINNED_TF_KEY, currentlyPinned === key ? "" : key);
    renderPeriodPills();
    return;
  }

  if (selectBtn) {
    const key = selectBtn.getAttribute("data-key");
    const tf = TIMEFRAMES.find((t) => t.key === key);
    periodPills.querySelector(".tf-menu")?.classList.remove("open");
    if (!tf || tf.key === currentTf.key) return;
    currentTf = tf;
    renderPeriodPills();
    if (currentSymbol) refreshForTimeframe(currentSymbol);
  }
});

// Close the dropdown when clicking anywhere outside it.
document.addEventListener("click", (e) => {
  if (!periodPills.contains(e.target)) {
    periodPills.querySelector(".tf-menu")?.classList.remove("open");
  }
});

// Regime, indicators, AI Decision and BUY/SELL signals are computed on the
// SAME bars as the chart (1-minute bars for the 1m view, hourly for 1H, ...),
// so each timeframe gets its own numbers and explanation. Only the backtest
// stays on daily bars (5y), because it needs a long history.
function tfQuery() {
  return `period=${currentTf.period}&interval=${currentTf.interval}`;
}

async function refreshForTimeframe(symbol) {
  const isDaily = currentTf.interval === "1d";
  intradayNote.classList.toggle("hidden", isDaily);
  intradayNote.textContent =
    `Regime, indicators, AI Decision, Risk Management and BUY/SELL signals below are calculated on ${currentTf.label} bars. ` +
    `The strategy backtest and Performance by Regime always use daily bars.`;

  await Promise.all([
    loadChartAndRegime(symbol),
    loadAnalysisPanels(symbol),
    loadBacktest(symbol),
  ]);
}

async function loadAnalysisPanels(symbol) {
  const qs = tfQuery();
  const requests = [
    fetch(`${API}/api/regime/${symbol}?${qs}`),
    fetch(`${API}/api/indicators/${symbol}?${qs}`),
    fetch(`${API}/api/ml-regime/${symbol}?period=5y&interval=${currentTf.interval}`),
    fetch(`${API}/api/ml-evaluation/${symbol}?period=5y&interval=${currentTf.interval}`),
  ];
  if (compareSymbol) requests.push(fetch(`${API}/api/regime/${compareSymbol}?${qs}`));
  try {
    const responses = await Promise.all(requests);
    if (responses[0].ok) {
      renderRegimeCard(await responses[0].json());
    } else {
      renderRegimeCard({ regime: "—", strategy: "n/a", reason: "Not enough data on this timeframe to classify a regime." });
    }
    renderIndicatorsPanel(responses[1].ok ? await responses[1].json() : null);
    renderMLRegime(responses[2]?.ok ? await responses[2].json() : null,
                   responses[3]?.ok ? await responses[3].json() : null);
    if (compareSymbol && responses[4] && responses[4].ok) {
      renderCompareRegimeCard(await responses[4].json());
    } else {
      document.getElementById("compare-regime-card").classList.add("hidden");
    }
  } catch (e) {
    renderIndicatorsPanel(null);
  }
  loadDecision(symbol, qs);
  loadRiskPanel(symbol, qs);
}

// ================= RISK MANAGEMENT =================

async function loadRiskPanel(symbol, qs) {
  try {
    const res = await fetch(`${API}/api/risk/${symbol}?${qs}`);
    if (!res.ok) throw new Error("risk fetch failed");
    renderRiskPanel(await res.json());
  } catch (e) {
    renderRiskPanel(null);
  }
}

function renderRiskPanel(r) {
  const ids = ["risk-entry", "risk-stop", "risk-target", "risk-rr", "risk-shares",
               "risk-position-value", "risk-position-pct", "risk-amount"];
  const capNote = document.getElementById("risk-cap-note");

  if (!r) {
    ids.forEach((id) => (document.getElementById(id).textContent = "—"));
    capNote.classList.add("hidden");
    return;
  }

  document.getElementById("risk-entry").textContent = `₹${r.entry_price.toFixed(2)}`;
  document.getElementById("risk-stop").textContent = `₹${r.stop_loss.toFixed(2)} (${r.stop_distance_pct}%)`;
  document.getElementById("risk-target").textContent = `₹${r.take_profit.toFixed(2)}`;
  document.getElementById("risk-rr").textContent = r.risk_reward_ratio;
  document.getElementById("risk-shares").textContent = r.shares.toLocaleString("en-IN");
  document.getElementById("risk-position-value").textContent = `₹${r.position_value.toLocaleString("en-IN")}`;
  document.getElementById("risk-position-pct").textContent = `${r.position_pct_of_portfolio}%`;
  document.getElementById("risk-amount").textContent = `₹${r.risk_amount.toLocaleString("en-IN")}`;

  if (r.capped_by_exposure) {
    capNote.textContent = `Position size capped at ${r.exposure_cap_pct}% exposure — the AI Decision's regime call, not the risk formula, is the binding limit here.`;
    capNote.classList.remove("hidden");
  } else {
    capNote.classList.add("hidden");
  }
}

// ================= SELECTING A SYMBOL =================

async function selectSymbol(symbol) {
  currentSymbol = symbol;
  searchBox.value = symbol;
  searchResults.classList.remove("show");
  dashboard.classList.remove("hidden");
  compareToggle.classList.remove("hidden");
  updateWatchlistBtn();
  setStatus("Loading...");

  const pinned = getPinnedTf();
  currentTf = TIMEFRAMES.find((t) => t.key === pinned) || TIMEFRAMES.find((t) => t.key === DEFAULT_TF_KEY);
  renderPeriodPills();

  await Promise.all([loadQuote(symbol), refreshForTimeframe(symbol)]);

  if (quotePollTimer) clearInterval(quotePollTimer);
  quotePollTimer = setInterval(() => loadQuote(symbol), 30000);

  setStatus("");
}

// ================= LIVE QUOTE =================

async function loadQuote(symbol) {
  try {
    const res = await fetch(`${API}/api/quote/${symbol}`);
    if (!res.ok) throw new Error("quote fetch failed");
    renderQuote(await res.json());
  } catch (e) {
    setStatus("Live quote temporarily unavailable — showing last known data.", true);
  }
}

function renderQuote(q) {
  document.getElementById("q-symbol").textContent = q.symbol;
  document.getElementById("q-company").textContent = q.company_name || "";
  document.getElementById("q-price").textContent = q.last_price != null ? `₹${q.last_price}` : "—";

  const changeEl = document.getElementById("q-change");
  if (q.change != null) {
    const up = q.change >= 0;
    changeEl.textContent = `${up ? "+" : ""}${q.change} (${q.pct_change}%)`;
    changeEl.className = `change ${up ? "up" : "down"}`;
  } else {
    changeEl.textContent = "—";
    changeEl.className = "change";
  }

  document.getElementById("q-open").textContent = q.open ?? "—";
  document.getElementById("q-high").textContent = q.day_high ?? "—";
  document.getElementById("q-low").textContent = q.day_low ?? "—";
  document.getElementById("q-prevclose").textContent = q.prev_close ?? "—";
  document.getElementById("q-market-status").textContent = q.market_open ? "Market Open" : "Market Closed";

  let sourceLabel = q.source === "nse" ? "NSE live" : "Yahoo Finance (delayed)";
  if (q.stale) sourceLabel += " · stale, retrying";
  document.getElementById("q-source").textContent = sourceLabel;
}

// ================= CHART + REGIME =================

async function loadChartAndRegime(symbol) {
  chartLoading.classList.remove("hidden");
  const qs = tfQuery();

  try {
    // regime-history also carries the confirmed BUY/SELL signal for every bar,
    // calculated on this timeframe's own bars.
    const requests = [fetch(`${API}/api/regime-history/${symbol}?${qs}`)];
    if (compareSymbol) requests.push(fetch(`${API}/api/regime-history/${compareSymbol}?${qs}`));
    const responses = await Promise.all(requests);

    let history, compareHistory = null;
    if (responses[0].ok) {
      history = await responses[0].json();
    } else {
      // Not enough bars for the regime model (e.g. very short window): show plain candles.
      const plain = await fetch(`${API}/api/history/${symbol}?${qs}`);
      if (!plain.ok) throw new Error("history fetch failed");
      history = await plain.json();
    }
    if (compareSymbol && responses[1] && responses[1].ok) compareHistory = await responses[1].json();

    lastHistory = { symbol, history, compareSymbol, compareHistory };
    renderChart(history, compareHistory);
    renderSwitchHistory(history);
  } catch (e) {
    setStatus("Could not load chart data for this symbol/timeframe.", true);
  } finally {
    chartLoading.classList.add("hidden");
  }
}

function renderSwitchHistory(history) {
  const body = document.getElementById("switch-history-body");
  const changes = history.filter((row, i) => row.regime && (i === 0 || row.regime !== history[i - 1].regime)).slice(-10).reverse();
  body.innerHTML = changes.length ? changes.map((row) => `<tr><td>${row.date}</td><td>${row.regime}</td><td>${row.selected_strategy || "—"}</td><td>${row.exposure_pct == null ? "—" : `${row.exposure_pct}%`}</td><td>${row.trade_action || "HOLD"}</td></tr>`).join("") : `<tr><td colspan="5">No regime transitions in this period.</td></tr>`;
}

function renderRegimeCard(r) {
  const badge = document.getElementById("regime-badge");
  badge.textContent = r.regime;
  badge.style.background = (REGIME_COLORS[r.regime] || "#444") + "26";
  badge.style.color = REGIME_COLORS[r.regime] || "#ccc";
  badge.style.border = `1px solid ${REGIME_COLORS[r.regime] || "#444"}`;
  document.getElementById("regime-strategy").textContent = `Suggested style: ${r.strategy}`;
  document.getElementById("regime-reason").textContent = r.reason;
  document.getElementById("switch-strategy").textContent = r.selected_strategy || r.strategy || "—";
  document.getElementById("switch-exposure").textContent = r.exposure_pct == null ? "—" : `${r.exposure_pct}%`;
  document.getElementById("switch-trade").textContent = r.trade_action || "—";
}

function renderMLRegime(prediction, evaluation) {
  const badge = document.getElementById("ml-regime-badge");
  if (!prediction) {
    badge.textContent = "Unavailable";
    document.getElementById("ml-confidence").textContent = "—";
    document.getElementById("ml-agreement").textContent = "Insufficient history";
    document.getElementById("ml-probabilities").textContent = "—";
    document.getElementById("ml-features").textContent = "—";
    document.getElementById("ml-evaluation").textContent = "ML requires sufficient multi-regime history.";
    return;
  }
  badge.textContent = prediction.predicted_regime;
  const color = REGIME_COLORS[prediction.predicted_regime] || "#808a9c";
  badge.style.background = color + "26";
  badge.style.color = color;
  badge.style.border = `1px solid ${color}`;
  document.getElementById("ml-confidence").textContent = `${(prediction.confidence * 100).toFixed(1)}%`;
  document.getElementById("ml-agreement").textContent = `${prediction.comparison} · rule: ${prediction.rule_based_regime}`;
  document.getElementById("ml-probabilities").textContent = Object.entries(prediction.class_probabilities).map(([k, v]) => `${k}: ${(v * 100).toFixed(1)}%`).join(" · ");
  document.getElementById("ml-features").textContent = prediction.important_features.map((x) => `${x.feature} (${(x.importance * 100).toFixed(1)}%)`).join(" · ");
  const m = evaluation?.metrics;
  document.getElementById("ml-evaluation").textContent = m ? `Held-out test · accuracy ${(m.accuracy * 100).toFixed(1)}% · precision ${(m.precision_macro * 100).toFixed(1)}% · recall ${(m.recall_macro * 100).toFixed(1)}% · F1 ${(m.f1_macro * 100).toFixed(1)}% · ${evaluation.split.test_rows} test rows` : "Held-out evaluation unavailable for this history.";
}

function renderCompareRegimeCard(r) {
  const card = document.getElementById("compare-regime-card");
  card.classList.remove("hidden");
  const badge = document.getElementById("compare-regime-badge");
  badge.textContent = r.regime;
  badge.style.background = (REGIME_COLORS[r.regime] || "#444") + "26";
  badge.style.color = REGIME_COLORS[r.regime] || "#ccc";
  badge.style.border = `1px solid ${REGIME_COLORS[r.regime] || "#444"}`;
  document.getElementById("compare-regime-symbol").textContent = `${r.symbol} — ${r.strategy}`;
  document.getElementById("compare-regime-reason").textContent = r.reason;
}

// ================= AI DECISION CARD =================

const DECISION_COLORS = {
  "BUY": "#26a69a",
  "HOLD": "#8ab4f8",
  "REDUCE": "#f5a623",
  "EXIT": "#ef5350",
};

async function loadDecision(symbol, qs) {
  try {
    const res = await fetch(`${API}/api/decision/${symbol}?${qs}`);
    if (!res.ok) throw new Error("decision fetch failed");
    renderDecisionCard(await res.json());
  } catch (e) {
    renderDecisionCard(null);
  }
}

function renderDecisionCard(d) {
  const badge = document.getElementById("decision-badge");
  const list = document.getElementById("decision-reasons");
  const ids = ["decision-confidence", "decision-exposure", "decision-strategy", "decision-risk",
               "conf-trend", "conf-vol", "conf-momentum", "conf-consistency"];

  if (!d) {
    badge.textContent = "—";
    badge.style.cssText = "";
    ids.forEach((id) => (document.getElementById(id).textContent = "—"));
    document.getElementById("confidence-bar").style.width = "0%";
    document.getElementById("exposure-bar").style.width = "0%";
    list.innerHTML = `<li class="indicators-empty">Not enough data to make a decision for this period.</li>`;
    return;
  }

  const color = DECISION_COLORS[d.decision] || "#888";
  badge.textContent = d.decision;
  badge.style.background = color + "26";
  badge.style.color = color;
  badge.style.border = `1px solid ${color}`;

  document.getElementById("decision-confidence").textContent = `${d.confidence}%`;
  document.getElementById("confidence-bar").style.width = `${d.confidence}%`;
  document.getElementById("decision-exposure").textContent = `${d.recommended_exposure_pct}%`;
  document.getElementById("exposure-bar").style.width = `${d.recommended_exposure_pct}%`;
  document.getElementById("decision-strategy").textContent = d.strategy;
  document.getElementById("decision-risk").textContent = d.risk;

  const b = d.confidence_breakdown;
  document.getElementById("conf-trend").textContent = `${b.trend_strength}%`;
  document.getElementById("conf-vol").textContent = `${b.volatility_clarity}%`;
  document.getElementById("conf-momentum").textContent = `${b.momentum_agreement}%`;
  document.getElementById("conf-consistency").textContent = `${b.regime_consistency}%`;

  list.innerHTML = d.reasons.map((r) => `<li>${r}</li>`).join("");
}

// ================= INDICATORS PANEL =================

const INDICATOR_ROWS = {
  trend: [
    ["sma_20", "SMA 20"], ["sma_50", "SMA 50"], ["sma_200", "SMA 200"],
    ["ema_20", "EMA 20"], ["ema_50", "EMA 50"], ["adx_14", "ADX (14)"],
  ],
  momentum: [
    ["rsi_14", "RSI (14)"], ["macd", "MACD"], ["macd_signal", "MACD Signal"],
    ["macd_hist", "MACD Hist."], ["roc_10_pct", "ROC (10 bars)", "%"],
  ],
  volatility: [
    ["atr_14", "ATR (14)"], ["hist_vol_20_pct", "Hist. Volatility", "%"],
    ["vol_percentile", "Vol. Percentile", "th"], ["bb_width_pct", "BB Width", "%"],
  ],
  market_structure: [
    ["pct_from_52w_high", "vs 52W High", "%"], ["pct_from_52w_low", "vs 52W Low", "%"],
    ["volume_vs_avg_pct", "Volume vs Avg", "%"],
  ],
};
const GROUP_LABELS = { trend: "Trend", momentum: "Momentum", volatility: "Volatility", market_structure: "Market Structure" };

function renderIndicatorsPanel(data) {
  const body = document.getElementById("indicators-body");
  if (!data) {
    body.innerHTML = `<p class="indicators-empty">Not enough history to compute indicators for this period.</p>`;
    return;
  }
  body.innerHTML = Object.entries(INDICATOR_ROWS).map(([groupKey, rows]) => {
    const values = data[groupKey] || {};
    return `
      <div class="indicator-group">
        <div class="indicator-group-label">${GROUP_LABELS[groupKey]}</div>
        <div class="indicator-grid">
        ${rows.map(([key, rawLabel, suffix]) => {
          const label = rawLabel.replace("52W", data.period_label || "52W");
          const v = values[key];
          const shown = v === null || v === undefined ? "—" : `${v}${suffix || ""}`;
          return `<div class="stat-row"><span>${label}</span><b>${shown}</b></div>`;
        }).join("")}
        </div>
      </div>`;
  }).join("");
}

const INTRADAY_INTERVALS = ["1m", "2m", "5m", "15m", "30m", "1h"];

function toChartTime(dateStr) {
  // Daily+ bars: lightweight-charts wants a plain 'yyyy-mm-dd' business-day
  // string. Intraday bars: multiple bars share the same calendar day, so a
  // date-only string would collapse them onto one point — use a Unix
  // timestamp (seconds) instead, which lightweight-charts also accepts.
  if (INTRADAY_INTERVALS.includes(currentTf.interval)) {
    return Math.floor(new Date(dateStr.replace(" ", "T")).getTime() / 1000);
  }
  return dateStr.slice(0, 10);
}

function computeSignalMarkers(history) {
  // Markers come from the backend's confirmed-trend signals (backend/signals.py):
  // a BUY/SELL only fires when the regime has held for several days, ADX says
  // the market is genuinely trending and momentum agrees — so nothing is drawn
  // while the market is range-bound. Intraday/weekly history has no .signal
  // field, so this naturally returns an empty list for those.
  return history
    .filter((h) => h.signal === "BUY" || h.signal === "SELL")
    .map((h) => h.signal === "BUY"
      ? { time: toChartTime(h.date), position: "belowBar", color: "#1fb45c", shape: "arrowUp", text: "BUY" }
      : { time: toChartTime(h.date), position: "aboveBar", color: "#e5484d", shape: "arrowDown", text: "SELL" });
}

// The UI scales fluidly with viewport width (see html font-size in style.css),
// so canvas-drawn text (charts) has to scale with it too.
function uiScale() {
  return parseFloat(getComputedStyle(document.documentElement).fontSize) / 16;
}

let chartResizeObserver = null;

function renderChart(history, compareHistory) {
  const candleData = history
    .map((h) => ({ time: toChartTime(h.date), open: h.Open, high: h.High, low: h.Low, close: h.Close }))
    .filter((d) => d.open != null && d.high != null && d.low != null && d.close != null);

  const container = document.getElementById("price-chart");

  if (priceChart) {
    priceChart.remove();
    priceChart = null;
    candleSeries = null;
    compareSeries = null;
  }

  priceChart = LightweightCharts.createChart(container, {
    layout: { background: { color: "transparent" }, textColor: "#808a9c", fontFamily: "'JetBrains Mono', monospace", fontSize: Math.round(11 * uiScale()) },
    grid: { vertLines: { color: "#1c2230" }, horzLines: { color: "#1c2230" } },
    rightPriceScale: { borderColor: "#232a37" },
    timeScale: { borderColor: "#232a37", timeVisible: INTRADAY_INTERVALS.includes(currentTf.interval), secondsVisible: false, minBarSpacing: 0.05, rightOffset: 4 },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    autoSize: true,
  });

  candleSeries = priceChart.addCandlestickSeries({
    upColor: "#1fb45c",
    downColor: "#e5484d",
    borderVisible: false,
    wickUpColor: "#1fb45c",
    wickDownColor: "#e5484d",
  });
  candleSeries.setData(candleData);
  candleSeries.setMarkers(computeSignalMarkers(history));

  if (compareHistory && compareHistory.length) {
    compareSeries = priceChart.addLineSeries({
      color: "#38bdf8",
      lineWidth: 1,
      priceScaleId: "left",
    });
    const compareData = compareHistory
      .map((h) => ({ time: toChartTime(h.date), value: h.Close }))
      .filter((d) => d.value != null);
    compareSeries.setData(compareData);
    priceChart.priceScale("left").applyOptions({ visible: true, borderColor: "#232a37" });
  } else {
    priceChart.priceScale("left").applyOptions({ visible: false });
  }

  priceChart.timeScale().fitContent();

  // The chart can be created before its container has its final width (the
  // dashboard is un-hidden and laid out around the same moment). Without a
  // refit, the bars stay squeezed at the narrow width's spacing and leave a
  // huge empty gap on the left once the container grows. Refit on every
  // container resize so the data always fills the visible width.
  if (chartResizeObserver) chartResizeObserver.disconnect();
  chartResizeObserver = new ResizeObserver(() => {
    if (priceChart) requestAnimationFrame(() => priceChart && priceChart.timeScale().fitContent());
  });
  chartResizeObserver.observe(container);
}

// ================= BACKTEST (the actual "trading system") =================

async function loadBacktest(symbol) {
  backtestLoading.classList.remove("hidden");
  try {
    const res = await fetch(`${API}/api/backtest/${symbol}?period=5y`);
    if (!res.ok) throw new Error("backtest fetch failed");
    const data = await res.json();
    renderBacktestTable(data);
    renderBacktestChart(data);
  } catch (e) {
    setStatus("Could not run backtest for this symbol/period.", true);
  } finally {
    backtestLoading.classList.add("hidden");
  }
  loadRegimePerformance(symbol);
}

async function loadRegimePerformance(symbol) {
  const body = document.getElementById("regime-perf-body");
  try {
    const res = await fetch(`${API}/api/regime-performance/${symbol}?period=5y`);
    if (!res.ok) throw new Error("regime-performance fetch failed");
    renderRegimePerformanceTable(await res.json());
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6">Not enough history to break this down.</td></tr>`;
  }
}

function renderRegimePerformanceTable(data) {
  const body = document.getElementById("regime-perf-body");
  const fmtPct = (v, sign) => (v == null ? "—" : `${sign && v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
  body.innerHTML = data.rows.map((row) => {
    const color = REGIME_COLORS[row.regime] || "#808a9c";
    const retCls = row.total_return_pct == null ? "" : row.total_return_pct >= 0 ? "up" : "down";
    const avgCls = row.avg_daily_return_pct == null ? "" : row.avg_daily_return_pct >= 0 ? "up" : "down";
    return `<tr>
      <td><span class="swatch" style="background:${color}"></span>${row.regime}</td>
      <td>${row.days}</td>
      <td>${row.pct_of_days}%</td>
      <td class="${retCls}">${fmtPct(row.total_return_pct, true)}</td>
      <td class="${avgCls}">${row.avg_daily_return_pct == null ? "—" : fmtPct(row.avg_daily_return_pct, true)}</td>
      <td>${row.positive_day_pct == null ? "—" : `${row.positive_day_pct.toFixed(1)}%`}</td>
    </tr>`;
  }).join("");
}

const STRATEGY_STYLE = {
  adaptive:       { color: "#38bdf8", width: 2.2, dash: [] },
  momentum:       { color: "#1fb45c", width: 1.5, dash: [] },
  mean_reversion: { color: "#f2b134", width: 1.5, dash: [] },
  buy_hold:       { color: "#808a9c", width: 1.5, dash: [4, 3] },
};

function renderBacktestTable(data) {
  const body = document.getElementById("bt-table-body");
  const entries = Object.entries(data.strategies);
  const fmtPct = (v, sign) => (v == null ? "—" : `${sign && v >= 0 ? "+" : ""}${v.toFixed(2)}%`);
  const cols = [
    { key: "total_return_pct", fmt: (v) => fmtPct(v, true), color: true, best: "max" },
    { key: "cagr_pct", fmt: (v) => fmtPct(v, true), color: true, best: "max" },
    { key: "sharpe", fmt: (v) => (v == null ? "—" : v.toFixed(2)), color: true, best: "max" },
    { key: "sortino", fmt: (v) => (v == null ? "—" : v.toFixed(2)), color: true, best: "max" },
    { key: "max_drawdown_pct", fmt: (v) => fmtPct(v, false), color: false, best: "max" }, // closest to 0 is best
    { key: "win_rate_pct", fmt: (v) => (v == null ? "—" : `${v.toFixed(1)}%`), color: false, best: null },
    { key: "profit_factor", fmt: (v) => (v == null ? "—" : v.toFixed(2)), color: false, best: "max" },
    { key: "average_winning_trade_pct", fmt: (v) => (v == null ? "—" : `${v.toFixed(2)}%`), color: true, best: null },
    { key: "average_losing_trade_pct", fmt: (v) => (v == null ? "—" : `${v.toFixed(2)}%`), color: false, best: null },
    { key: "max_consecutive_losses", fmt: (v) => String(v ?? "—"), color: false, best: null },
    { key: "avg_exposure_pct", fmt: (v) => (v == null ? "—" : `${v.toFixed(1)}%`), color: false, best: null },
    { key: "num_trades", fmt: (v) => String(v), color: false, best: null },
  ];
  // Highlight the best value in each column (ignoring nulls).
  const bestVal = {};
  for (const c of cols) {
    if (!c.best) continue;
    const vals = entries.map(([, s]) => s.stats[c.key]).filter((v) => v != null);
    bestVal[c.key] = vals.length ? Math.max(...vals) : null;
  }
  body.innerHTML = entries.map(([key, s]) => {
    const st = STRATEGY_STYLE[key] || { color: "#808a9c" };
    const cells = cols.map((c) => {
      const v = s.stats[c.key];
      const cls = [];
      if (c.color && v != null) cls.push(v >= 0 ? "up" : "down");
      if (c.best && v != null && v === bestVal[c.key] && entries.length > 1) cls.push("best");
      return `<td class="${cls.join(" ")}">${c.fmt(v)}</td>`;
    }).join("");
    return `<tr class="${key === "adaptive" ? "is-adaptive" : ""}"><td><span class="swatch" style="background:${st.color}"></span>${s.label}</td>${cells}</tr>`;
  }).join("");

  if (data.benchmark) {
    const s = data.benchmark;
    body.innerHTML += `<tr><td><span class="swatch" style="background:#e879f9"></span>${s.label}</td>${cols.map((c) => `<td>${c.fmt(s.stats[c.key])}</td>`).join("")}</tr>`;
  }

  const a = data.assumptions;
  document.getElementById("backtest-note").textContent =
    `Long-only simulation · no lookahead (trades on prior day's signal) · ${a.cost_pct_per_trade}% cost per trade · ` +
    `Sharpe uses ${a.risk_free_pct}% risk-free · ${data.days_simulated} trading days scored · underlined = best in column (win rate not ranked: it depends on trade count)`;
}

function renderBacktestChart(data) {
  const labels = data.dates;
  // Equity curves are cumulative growth of ₹1 invested — shown as % gain/loss
  const ctx = document.getElementById("backtest-chart").getContext("2d");
  if (backtestChart) backtestChart.destroy();

  backtestChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [...Object.entries(data.strategies), ...(data.benchmark ? [["nifty_50", data.benchmark]] : [])].map(([key, strat]) => {
        const st = STRATEGY_STYLE[key] || { color: "#e879f9", width: 1.8, dash: [6, 3] };
        return {
          label: strat.label,
          // Equity is growth of ₹1 — shown as % gain/loss
          data: strat.equity.map((v) => (v == null ? null : (v - 1) * 100)),
          borderColor: st.color,
          borderWidth: st.width,
          borderDash: st.dash,
          pointRadius: 0,
          tension: 0.1,
          fill: false,
        };
      }),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#e7ebf2", font: { family: "Inter, -apple-system, 'Segoe UI', sans-serif", size: Math.round(11 * uiScale()) } } },
        tooltip: {
          titleFont: { family: "'JetBrains Mono', ui-monospace, monospace" },
          bodyFont: { family: "'JetBrains Mono', ui-monospace, monospace" },
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y >= 0 ? "+" : ""}${ctx.parsed.y.toFixed(2)}%`,
          },
        },
      },
      scales: {
        x: { ticks: { color: "#808a9c", maxTicksLimit: 8, font: { family: "'JetBrains Mono', ui-monospace, monospace", size: Math.round(10 * uiScale()) } }, grid: { color: "#1c2230" } },
        y: {
          ticks: { color: "#808a9c", font: { family: "'JetBrains Mono', ui-monospace, monospace", size: Math.round(10 * uiScale()) }, callback: (v) => `${v}%` },
          grid: { color: "#1c2230" },
        },
      },
    },
  });
}

// ================= CSV EXPORT =================

exportBtn.addEventListener("click", () => {
  if (!lastHistory || !lastHistory.history.length) {
    setStatus("Nothing to export yet — select a stock first.", true);
    return;
  }
  const rows = ["date,close,regime,strategy,slope_pct,volatility_pct,reason"];
  for (const h of lastHistory.history) {
    const reason = (h.reason || "").replace(/"/g, '""');
    rows.push([h.date, h.Close, h.regime ?? "", h.strategy ?? "", h.slope_pct ?? "", h.volatility ?? "", `"${reason}"`].join(","));
  }
  const blob = new Blob([rows.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${lastHistory.symbol}_regime_history.csv`;
  a.click();
  URL.revokeObjectURL(url);
});

function setStatus(msg, isError = false) {
  statusMsg.textContent = msg;
  statusMsg.style.color = isError ? "#e5484d" : "#808a9c";
}

// ================= INDEX TICKER STRIP =================

async function loadIndices() {
  try {
    const res = await fetch(`${API}/api/indices`);
    if (!res.ok) return;
    const data = await res.json();
    indexStrip.innerHTML = data.indices
      .map((idx) => {
        if (idx.error) return "";
        const up = idx.change >= 0;
        return `<span class="idx-item">
          <span class="idx-name">${idx.name}</span>
          <span class="idx-price">${idx.last_price.toLocaleString("en-IN")}</span>
          <span class="idx-change ${up ? "up" : "down"}">${up ? "+" : ""}${idx.change} (${idx.pct_change}%)</span>
        </span>`;
      })
      .join("");
  } catch (e) {
    // Silently skip — the ticker strip is a nice-to-have, not core functionality.
  }
}

renderWatchlist();
loadIndices();
setInterval(loadIndices, 30000);

