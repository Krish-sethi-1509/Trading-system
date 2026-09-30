# Adaptive and Explainable AI Trading System

Market regime detection with interpretable strategy switching for NSE equities. The application combines an existing transparent rule-based regime detector with a Scikit-learn Random Forest that learns to approximate those rule labels. The classifier is an additional, separately reported model; it does not replace the rule detector.

## Architecture

- `backend/nse_client.py`: NSE quote and symbol-list access; Yahoo Finance historical OHLCV fallback/provider.
- `backend/indicators.py`: reusable causal technical indicators.
- `backend/regime.py`: expanding-volatility rule detector and regime-to-strategy/exposure mapping.
- `backend/ml_regime.py`: per-request chronological Random Forest training, prediction, held-out evaluation and feature importance.
- `backend/backtest.py`: lagged, long-only strategy simulation and common performance metrics.
- `backend/main.py`: FastAPI endpoints and static frontend serving.
- `frontend/`: plain HTML, CSS and JavaScript dashboard; no frontend build step.

## Setup

Use Python 3.11 or 3.12 with the pinned pandas/numpy versions below.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open <http://localhost:8000>. The backend serves the dashboard and API from one process.

## Regime ML method and evaluation

The target is the existing rule-based regime label (Bull Trend, Bear Trend, Range / Sideways, or High Volatility). This makes the classifier a supervised approximation of the interpretable baseline, not a model trained on independently verified market truth. It uses the shared RSI, MACD histogram, rate of change, ADX, historical volatility, volatility percentile, Bollinger width, volume relative to average, and close-to-SMA distances.

Rows remain chronological: the earliest 60% trains the forest, the next 20% selects `max_depth` from 4, 8 and unlimited using macro F1, and the latest 20% is an untouched test segment for accuracy, macro precision/recall/F1, per-class metrics and confusion matrix. The fixed seed is 42. The latest prediction is trained using rows strictly before the latest valid feature row. Indicator warm-up rows and non-finite values are removed. Requests with too little history or fewer than two regimes return HTTP 422 with a useful explanation. No model or prediction is hardcoded or cached across stocks.

Metrics and feature importances are recalculated from each requested stock's current historical data. Results are therefore data- and period-dependent; README does not claim fixed performance numbers. Use a long daily period for the most useful evaluation. Intraday history can be too short for the required chronological split.

## Regime → strategy → exposure → action

| Rule regime | Selected style | Target exposure |
|---|---|---:|
| Bull Trend | Momentum | 100% |
| Bear Trend | Defensive | 0% |
| High Volatility | Mean Reversion (reduced size) | 25% |
| Range / Sideways | Mean Reversion | 50% |

An exposure change is labeled BUY / INCREASE or REDUCE / EXIT; an unchanged target is HOLD. These are educational paper signals, not broker orders. Backtests lag target positions by one bar before applying them to returns, and charge transaction costs on changes in exposure. The dashboard separates the current rule regime, the ML prediction and their agreement, from selected style, target exposure and switch history.

## Backtesting

Adaptive (rule-regime switched), Momentum, Mean Reversion, Buy & Hold, and an independently fetched NIFTY 50 buy-and-hold benchmark are evaluated on daily bars. Strategy returns apply the prior bar's signal, include 0.10% costs per unit of turnover, and use a common indicator warm-up period. Existing Total Return, CAGR, Sharpe, maximum drawdown, win rate and trade count are retained. Added metrics are Sortino, Profit Factor, average winning/losing trade return, maximum consecutive losing trades and average exposure. NIFTY data is optional at runtime; the API reports when it is unavailable.

## API

- `GET /api/symbols?q=reliance` — NSE stock search.
- `GET /api/quote/{symbol}` — quote.
- `GET /api/history/{symbol}?period=1y&interval=1d` — OHLCV.
- `GET /api/regime/{symbol}?period=1y&interval=1d` — rule regime and strategy/exposure/action.
- `GET /api/regime-history/{symbol}?period=1y&interval=1d` — historical regimes and switches.
- `GET /api/ml-regime/{symbol}?period=5y&interval=1d` — ML regime, confidence, four class probabilities, top features and rule agreement.
- `GET /api/ml-evaluation/{symbol}?period=5y&interval=1d` — chronological split, test metrics, per-class results, confusion matrix and feature importance.
- `GET /api/indicators/{symbol}` and `GET /api/decision/{symbol}` — indicator snapshot and explainable BUY/HOLD/REDUCE/EXIT decision.
- `GET /api/risk/{symbol}` — ATR-based position sizing.
- `GET /api/regime-performance/{symbol}?period=5y` — performance by rule regime.
- `GET /api/backtest/{symbol}?period=5y` — strategy curves, benchmark and metrics.

## Tests

Run `cd backend && pytest -q`. Tests cover Random Forest prediction/evaluation, rule detection, strategy switching, position sizing and backtest metrics/benchmark. Tests use deterministic synthetic OHLCV data and do not make network calls.

## Limitations

- Because the ML target is generated by the rule detector, high agreement shows rule imitation rather than predictive alpha or independent regime discovery.
- A single time-based split is not a substitute for broader walk-forward validation across stocks and market cycles. The validation segment currently selects model depth; the test segment is not used for that selection.
- Random Forest probabilities are uncalibrated class-frequency estimates and should not be read as calibrated confidence.
- Yahoo Finance historical data and NSE's public quote endpoints can be delayed, incomplete or rate-limited. NIFTY 50 benchmark availability depends on the same historical provider.
- The app is a research and educational dashboard, not investment advice or order execution. Transaction costs are simplified and omit taxes, liquidity, market impact and broker-specific fees.

