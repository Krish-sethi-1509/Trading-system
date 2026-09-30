from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import pandas as pd
import numpy as np

from nse_client import nse_client
from regime import detect_regimes, current_regime_summary
from backtest import run_backtest
from indicators import current_indicator_summary
from decision import build_decision
from signals import compute_signals
from risk import build_risk_plan
from regime_performance import regime_performance


def _clean_records(df: pd.DataFrame) -> list:
    """Convert a DataFrame to JSON-safe records: NaN/inf (from rolling-window
    warm-up periods) become None instead of crashing json.dumps."""
    return df.replace({np.nan: None, np.inf: None, -np.inf: None}).to_dict(orient="records")

def _analysis_period(period: str, interval: str) -> str:
    """Daily bars need >= 2y so SMA200 / 52-week fields are filled. Other
    timeframes use exactly the period the chart asked for (yfinance limits
    intraday history, so asking for more would just fail)."""
    if interval == "1d" and period not in ("2y", "5y", "10y", "max"):
        return "2y"
    return period


app = FastAPI(title="NSE/BSE Regime Detection Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/indices")
def get_indices():
    """Top ticker strip: NIFTY 50 / SENSEX / BANK NIFTY."""
    return {"indices": nse_client.get_index_quotes()}


@app.get("/api/symbols")
def list_symbols(q: str = Query("", description="Optional search filter")):
    symbols = nse_client.get_all_symbols()
    if q:
        q_lower = q.lower()
        symbols = [
            s for s in symbols
            if q_lower in s["symbol"].lower() or q_lower in s["name"].lower()
        ][:50]
    else:
        symbols = symbols[:50]
    return {"count": len(symbols), "results": symbols}


@app.get("/api/quote/{symbol}")
def get_quote(symbol: str):
    data = nse_client.get_quote(symbol)
    if data.get("error"):
        raise HTTPException(status_code=502, detail=data["error"])
    return data


@app.get("/api/history/{symbol}")
def get_history(symbol: str, period: str = "1y", interval: str = "1d"):
    df = nse_client.get_history(symbol, period=period, interval=interval)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    df = df.reset_index()
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    df[date_col] = df[date_col].astype(str)
    return _clean_records(
        df[[date_col, "Open", "High", "Low", "Close", "Volume"]].rename(columns={date_col: "date"})
    )


@app.get("/api/regime/{symbol}")
def get_regime(symbol: str, period: str = "1y", interval: str = "1d"):
    df = nse_client.get_history(symbol, period=period, interval=interval)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    try:
        summary = current_regime_summary(df, interval)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    summary["symbol"] = symbol.upper()
    return summary


@app.get("/api/regime-history/{symbol}")
def get_regime_history(symbol: str, period: str = "1y", interval: str = "1d"):
    """Full regime timeline, used to shade the chart by regime."""
    df = nse_client.get_history(symbol, period=period, interval=interval)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    analyzed = detect_regimes(df, interval=interval)
    # Confirmed BUY/SELL chart markers — silent in range-bound markets (see signals.py)
    analyzed["signal"] = compute_signals(df, interval)
    analyzed = analyzed.reset_index()
    date_col = "Date" if "Date" in analyzed.columns else analyzed.columns[0]
    analyzed[date_col] = analyzed[date_col].astype(str)
    cols = [date_col, "Open", "High", "Low", "Close", "regime", "strategy", "reason", "slope_pct", "volatility", "signal"]
    return _clean_records(analyzed[cols].rename(columns={date_col: "date"}))


@app.get("/api/indicators/{symbol}")
def get_indicators(symbol: str, period: str = "1y", interval: str = "1d"):
    """
    Latest-row technical indicator snapshot (trend / momentum / volatility /
    market structure), grouped for the Indicators panel. Uses a longer
    lookback than the requested period internally when needed so that
    slow-moving indicators (SMA200, 52-week high/low) aren't all-None just
    because the chart's own period is shorter than a year.
    """
    fetch_period = _analysis_period(period, interval)
    df = nse_client.get_history(symbol, period=fetch_period, interval=interval)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    try:
        summary = current_indicator_summary(df, interval)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    summary["symbol"] = symbol.upper()
    return summary


@app.get("/api/decision/{symbol}")
def get_decision(symbol: str, period: str = "1y", interval: str = "1d"):
    """
    The AI Decision card's data: a BUY/HOLD/REDUCE/EXIT call, a component-based
    confidence score, recommended exposure, risk level, and the plain-language
    reasons behind it. See decision.py for the full methodology.
    """
    fetch_period = _analysis_period(period, interval)
    df = nse_client.get_history(symbol, period=fetch_period, interval=interval)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    try:
        result = build_decision(df, interval)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result["symbol"] = symbol.upper()
    return result


@app.get("/api/risk/{symbol}")
def get_risk(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    portfolio_value: float = 100000.0,
    risk_pct: float = 1.0,
    atr_multiplier: float = 2.0,
    reward_multiple: float = 2.0,
):
    """
    Risk Management card: ATR-based stop-loss/take-profit and a fixed-
    fractional position size for a hypothetical new long entry, capped by
    the AI Decision's regime-based exposure limit. See risk.py.
    """
    fetch_period = _analysis_period(period, interval)
    df = nse_client.get_history(symbol, period=fetch_period, interval=interval)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    try:
        result = build_risk_plan(df, interval, portfolio_value, risk_pct, atr_multiplier, reward_multiple)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result["symbol"] = symbol.upper()
    return result


@app.get("/api/regime-performance/{symbol}")
def get_regime_performance(symbol: str, period: str = "5y"):
    """
    Performance-by-regime breakdown (always daily bars, like the backtest,
    since this needs years of history to say anything meaningful about any
    one regime). See regime_performance.py.
    """
    df = nse_client.get_history(symbol, period=period, interval="1d")
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    try:
        result = regime_performance(df, interval="1d")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result["symbol"] = symbol.upper()
    return result


# Serve the frontend
app.mount("/static", StaticFiles(directory="../frontend"), name="static")


@app.get("/api/backtest/{symbol}")
def get_backtest(symbol: str, period: str = "1y"):
    """
    The actual trading-system component: simulates four long-only strategies
    (Adaptive regime-switched, Momentum, Mean Reversion, Buy & Hold) on the same
    data and returns each equity curve plus Total Return, CAGR, Sharpe, Max
    Drawdown, Win Rate and number of trades. See backtest.py for assumptions.
    """
    df = nse_client.get_history(symbol, period=period, interval="1d")
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data found for symbol")
    try:
        result = run_backtest(df)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result["symbol"] = symbol.upper()
    return result


@app.get("/")
def root():
    return FileResponse("../frontend/index.html")
