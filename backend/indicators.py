"""
Technical indicator engine.

Computes a standard set of trend / momentum / volatility indicators off the
same OHLCV DataFrame that regime.py and backtest.py already use
(nse_client.get_history() output — indexed by date, must have
Open/High/Low/Close/Volume columns).

This module is deliberately the FIRST piece of the "Phase 1" upgrade: the
AI Decision card, confidence score, and explainability panel all read off
these same numbers, so getting the indicator set right here means those
later features don't need their own separate calculations.

Everything here is a plain, well-known formula (no smoothing library, no
external TA package) so every number can be explained in a viva by pointing
at the formula, which matches the project's "interpretable" claim.
"""

import numpy as np
import pandas as pd

from timeframe import bars_per_year, bars_per_day, label as tf_label, fmt_ts


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI. Uses an exponential (Wilder) moving average of gains
    and losses, which is the standard definition (not a simple rolling mean)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder smoothing == an EMA with alpha = 1/window
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Where avg_loss is 0 (straight up-move streak) RSI is defined as 100.
    rsi = rsi.where(avg_loss != 0, 100)
    return rsi


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range, Wilder-smoothed."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()


def _adx(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average Directional Index — trend STRENGTH (not direction)."""
    high, low = df["High"], df["Low"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr = _atr(df, window)
    plus_di = 100 * plus_dm.ewm(alpha=1 / window, min_periods=window, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / window, min_periods=window, adjust=False).mean() / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    return adx


def _bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0):
    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    # Width as a % of the middle band — comparable across price levels/stocks.
    width_pct = (upper - lower) / sma.replace(0, np.nan) * 100
    return upper, lower, width_pct


def compute_indicators(df: pd.DataFrame, interval="1d") -> pd.DataFrame:
    """
    Returns df with added indicator columns. Every indicator uses only past
    and current-day data (rolling/ewm windows), so nothing here introduces
    lookahead bias.
    """
    bpy = bars_per_year(interval)
    out = df.copy()
    close = out["Close"]

    out["sma_20"] = close.rolling(20).mean()
    out["sma_50"] = close.rolling(50).mean()
    out["sma_200"] = close.rolling(200).mean()
    out["ema_20"] = close.ewm(span=20, adjust=False).mean()
    out["ema_50"] = close.ewm(span=50, adjust=False).mean()

    out["rsi_14"] = _rsi(close, 14)
    macd_line, signal_line, hist = _macd(close)
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = hist
    out["roc_10"] = close.pct_change(10) * 100

    out["atr_14"] = _atr(out, 14)
    out["adx_14"] = _adx(out, 14)
    returns = close.pct_change()
    out["hist_vol_20"] = returns.rolling(20).std() * np.sqrt(bpy) * 100
    # Where this stock's own recent volatility sits vs its own history —
    # a percentile (0-100), not a raw number, so it's comparable across stocks.
    out["vol_percentile"] = out["hist_vol_20"].rolling(252, min_periods=20).rank(pct=True) * 100

    upper, lower, width_pct = _bollinger(close, 20, 2.0)
    out["bb_upper"] = upper
    out["bb_lower"] = lower
    out["bb_width_pct"] = width_pct

    # "52-week" window = one year of THIS timeframe's bars (252 daily, 52 weekly,
    # 12 monthly). For intraday bars a year of history doesn't exist, so it
    # covers everything fetched (the panel labels it "Period" instead).
    win = max(int(round(bpy)), 2)
    mp = min(20, win)
    roll_high_252 = out["High"].rolling(win, min_periods=mp).max()
    roll_low_252 = out["Low"].rolling(win, min_periods=mp).min()
    out["pct_from_52w_high"] = (close - roll_high_252) / roll_high_252 * 100
    out["pct_from_52w_low"] = (close - roll_low_252) / roll_low_252 * 100

    vol_sma_20 = out["Volume"].rolling(20).mean()
    out["volume_vs_avg_pct"] = (out["Volume"] - vol_sma_20) / vol_sma_20.replace(0, np.nan) * 100

    return out


def _safe_round(x, nd=2):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), nd)


def current_indicator_summary(df: pd.DataFrame, interval="1d") -> dict:
    """Latest-row snapshot, grouped the way the UI displays them
    (Trend / Momentum / Volatility / Market structure)."""
    analyzed = compute_indicators(df, interval)
    if analyzed.empty:
        raise ValueError("Not enough historical data to compute indicators.")
    last = analyzed.iloc[-1]

    return {
        "as_of": fmt_ts(last.name, interval),
        "timeframe": tf_label(interval),
        "period_label": "Period" if bars_per_day(interval) > 1 else "52W",
        "trend": {
            "sma_20": _safe_round(last["sma_20"]),
            "sma_50": _safe_round(last["sma_50"]),
            "sma_200": _safe_round(last["sma_200"]),
            "ema_20": _safe_round(last["ema_20"]),
            "ema_50": _safe_round(last["ema_50"]),
            "adx_14": _safe_round(last["adx_14"], 1),
        },
        "momentum": {
            "rsi_14": _safe_round(last["rsi_14"], 1),
            "macd": _safe_round(last["macd"]),
            "macd_signal": _safe_round(last["macd_signal"]),
            "macd_hist": _safe_round(last["macd_hist"]),
            "roc_10_pct": _safe_round(last["roc_10"]),
        },
        "volatility": {
            "atr_14": _safe_round(last["atr_14"]),
            "hist_vol_20_pct": _safe_round(last["hist_vol_20"], 1),
            "vol_percentile": _safe_round(last["vol_percentile"], 0),
            "bb_width_pct": _safe_round(last["bb_width_pct"], 1),
        },
        "market_structure": {
            "pct_from_52w_high": _safe_round(last["pct_from_52w_high"]),
            "pct_from_52w_low": _safe_round(last["pct_from_52w_low"]),
            "volume_vs_avg_pct": _safe_round(last["volume_vs_avg_pct"]),
        },
    }
