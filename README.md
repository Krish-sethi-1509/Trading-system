# Adaptive and Explainable AI Trading System
Market Regime Detection with Interpretable Strategy Switching — NSE/BSE

## How the earlier problems are fixed

| Problem you hit | Fix in this build |
|---|---|
| Only got a few companies | `/api/symbols` loads the **full official NSE equity list** (~2000 companies) directly from NSE's archive CSV, searchable by name or symbol. |
| Didn't get live data | Live quotes come from **NSE's own quote API** (`nseindia.com`) via a properly warmed-up session (cookies + browser headers), not Yahoo Finance, which is the more reliable free source for Indian live prices. |
| Random failures | Session auto-refreshes and retries once on failure. If NSE is briefly unreachable, the last good quote is served and clearly labeled **"stale, retrying"** instead of showing an error or nothing. |
| Delayed vs live confusion | Every quote is tagged with its source (`nse` live vs `yfinance_fallback` delayed) and whether the market is currently open. |

## Stack
- **Backend**: Python + FastAPI (`backend/`)
- **Frontend**: Plain HTML/CSS/JS + Chart.js (`frontend/`) — no build step, so there's nothing to configure, just run and open.
- **Data**: NSE official API (live) + yfinance (historical OHLCV for the regime model)

## Setup

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Then open **http://localhost:8000** — the backend serves the frontend directly, so there's only one thing to run.

## What "regime detection" does here

`backend/regime.py` classifies each day into one of four regimes using a
transparent, rule-based method (moving-average slope for trend + rolling
annualized volatility) — deliberately not a black-box model, so every
classification comes with the exact numbers behind it (see the `reason`
field in `/api/regime/{symbol}`). This is the "Explainable AI" and
"Interpretable Strategy Switching" part of your title, and it's fast
enough to compute per-request with no training step or model file needed.

If your course wants a more "ML-flavored" second model to reference in the
report, the natural upgrade is a Gaussian Hidden Markov Model
(`hmmlearn.GaussianHMM`) fit on returns + volatility — worth mentioning as
a discussed-but-not-required extension if you have spare time before Oct 10,
not something to attempt first.

## API endpoints
- `GET /api/symbols?q=reliance` — search the full NSE company list
- `GET /api/quote/{symbol}` — live quote (e.g. `RELIANCE`)
- `GET /api/history/{symbol}?period=1y&interval=1d` — raw OHLCV
- `GET /api/regime/{symbol}?period=1y` — current regime + explanation
- `GET /api/regime-history/{symbol}?period=1y` — full regime timeline (drives the chart shading)
- `GET /api/indicators/{symbol}` — technical indicator snapshot
- `GET /api/decision/{symbol}` — BUY/HOLD/REDUCE/EXIT call, confidence score, reasons
- `GET /api/backtest/{symbol}?period=5y` — Adaptive vs Momentum vs Mean Reversion vs Buy & Hold, with Total Return, CAGR, Sharpe, Max Drawdown, Win Rate, Trades

## Known limitations to mention in your report
- NSE's public API is unofficial and can rate-limit aggressive scraping — fine for a single-user dashboard, not built for high concurrency.
- BSE-only-listed stocks (no NSE listing) fall back to `.BO` for historical data only; live quotes are NSE-only in this version — extending live quotes to BSE would mean adding BSE's separate (and less stable) API.
- Regime detection is intentionally rule-based for reliability/interpretability, not a trained ML model — a deliberate design choice, explained above, not a shortcut you need to apologize for in the report.

## Backtest methodology (see `backend/backtest.py`)
- Four long-only strategies scored on identical data: Adaptive (regime-switched), Momentum (SMA20 > SMA50 and RSI > 50), Mean Reversion (buy RSI < 30 or below lower Bollinger Band, sell at SMA20), Buy & Hold.
- No lookahead: each day trades on the previous day's signal. All strategies are scored from the same start date, after the 50-bar indicator warm-up.
- 0.10% cost on every change in position size; Sharpe uses a 6.5% annual risk-free rate; CAGR is annualized over 252 trading days.
- A "trade" is one holding leg (a run of days at the same non-zero position size); win rate is the share of legs with a positive net return.
