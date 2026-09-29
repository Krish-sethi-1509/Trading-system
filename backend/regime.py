"""
Market regime detection with interpretable strategy switching.

Design choice: this is a RULE-BASED / statistical classifier over
transparent indicators, not a black-box ML model with post-hoc explanation
bolted on. That's a deliberate reading of "explainable AI" for this
project — every regime label comes with the exact numeric reasons behind
it, so there's nothing to "explain away" after the fact. This is also far
faster to build and defend in a viva than training/justifying an HMM or
LSTM in the time you have left.

Regimes:
  - "Bull Trend"      : strong upward trend, normal/low volatility
  - "Bear Trend"       : strong downward trend, normal/low volatility
  - "High Volatility"  : large recent price swings regardless of direction
  - "Range/Sideways"   : weak trend, low volatility

Strategy switching:
  Each regime maps to a suggested strategy style (not real trading advice —
  this is the "interpretable strategy switching" component of the title):
  - Bull Trend      -> Trend-following (momentum)
  - Bear Trend      -> Defensive / reduce exposure
  - High Volatility -> Mean-reversion with wide stops, or stay out
  - Range/Sideways  -> Mean-reversion / range trading

If you want a second, more "ML-flavored" model to point to in your report,
an HMM (hmmlearn.GaussianHMM) fit on returns + volatility is the natural
upgrade — flagged in the README as an optional extension, not required to
ship a working app by the deadline.

Note on the volatility threshold: "typical" volatility is an EXPANDING
median (computed from data up to and including each day), not a median over
the whole series — so a day's label never depends on volatility from days
that hadn't happened yet.
"""

import pandas as pd
import numpy as np

from timeframe import bars_per_year, trend_threshold_pct, label as tf_label, fmt_ts


def _annualized_vol(returns: pd.Series, window: int, bpy: float = 252) -> pd.Series:
    return returns.rolling(window).std() * np.sqrt(bpy)


def detect_regimes(df: pd.DataFrame, trend_window=20, vol_window=20, interval="1d") -> pd.DataFrame:
    """
    df: OHLCV DataFrame indexed by date, must have a 'Close' column
        (this is exactly what nse_client.get_history() returns).
    Returns df with added columns: sma, slope_pct, volatility, regime,
    strategy, reason.

    Windows auto-shrink for short date ranges (e.g. a "1mo" period has too
    few trading days for a 20-day SMA + 20-day slope to ever produce a
    valid reading) so every period the UI offers returns at least some
    classified rows instead of an all-NaN warm-up.
    """
    n = len(df)
    # Need roughly 2x the trend_window of data before the slope is valid
    # (one window to build the SMA, another to measure its % change).
    max_usable_window = max(3, n // 2 - 1)
    trend_window = min(trend_window, max_usable_window)
    vol_window = min(vol_window, max_usable_window)

    bpy = bars_per_year(interval)
    slope_thr = trend_threshold_pct(interval)  # % SMA move that counts as a trend
    tf = tf_label(interval)

    out = df.copy()
    out["returns"] = out["Close"].pct_change()
    out["sma"] = out["Close"].rolling(trend_window).mean()
    # Trend strength: % change of the SMA itself over the window
    out["slope_pct"] = out["sma"].pct_change(trend_window) * 100
    out["volatility"] = _annualized_vol(out["returns"], vol_window, bpy) * 100  # as %

    # "Typical" volatility level to compare each day against, computed as an
    # EXPANDING median (data up to and including that day only) rather than
    # a single median over the whole series. A whole-series median would let
    # an early day's regime label be decided using volatility from days that
    # hadn't happened yet — harmless for today's "current regime" read (which
    # legitimately has the full history available) but a lookahead problem
    # for every earlier day's label, including the ones the backtest and
    # regime-history chart both display.
    vol_median_series = out["volatility"].expanding(min_periods=1).median()

    regimes, strategies, reasons = [], [], []
    for (_, row), vol_median in zip(out.iterrows(), vol_median_series):
        slope = row["slope_pct"]
        vol = row["volatility"]

        if pd.isna(slope) or pd.isna(vol) or pd.isna(vol_median):
            regimes.append(None)
            strategies.append(None)
            reasons.append("Insufficient data (warm-up period)")
            continue

        high_vol = vol > vol_median * 1.5

        if high_vol:
            regime = "High Volatility"
            strategy = "Mean-reversion (wide stops) / reduce position size"
            reason = (
                f"Annualized volatility {vol:.1f}% is well above the "
                f"typical level ({vol_median:.1f}%) on the {tf} chart, regardless of "
                f"trend direction ({slope:+.2f}% SMA slope)."
            )
        elif slope > slope_thr:
            regime = "Bull Trend"
            strategy = "Trend-following (momentum)"
            reason = (
                f"{trend_window}-bar ({tf}) moving average rose {slope:.2f}% "
                f"(trend needs > {slope_thr:.2f}%) with volatility ({vol:.1f}%) near normal."
            )
        elif slope < -slope_thr:
            regime = "Bear Trend"
            strategy = "Defensive — reduce exposure"
            reason = (
                f"{trend_window}-bar ({tf}) moving average fell {slope:.2f}% "
                f"(trend needs < -{slope_thr:.2f}%) with volatility ({vol:.1f}%) near normal."
            )
        else:
            regime = "Range / Sideways"
            strategy = "Mean-reversion / range trading"
            reason = (
                f"Weak trend on the {tf} chart ({slope:+.2f}% SMA slope, needs beyond "
                f"±{slope_thr:.2f}%) and normal volatility ({vol:.1f}%) — no clear directional edge."
            )

        regimes.append(regime)
        strategies.append(strategy)
        reasons.append(reason)

    out["regime"] = regimes
    out["strategy"] = strategies
    out["reason"] = reasons
    out["vol_median"] = vol_median_series
    return out


def current_regime_summary(df: pd.DataFrame, interval="1d") -> dict:
    """Convenience wrapper: run detect_regimes and return just the latest row
    as a plain dict, which is what the API endpoint serves to the frontend."""
    analyzed = detect_regimes(df, interval=interval)
    valid = analyzed.dropna(subset=["regime"])
    if valid.empty:
        raise ValueError(
            f"Not enough historical data ({len(df)} rows) to classify a "
            "regime — try a longer period."
        )
    last = valid.iloc[-1]
    return {
        "regime": last["regime"],
        "strategy": last["strategy"],
        "reason": last["reason"],
        "slope_pct": round(float(last["slope_pct"]), 3),
        "volatility_pct": round(float(last["volatility"]), 2),
        "timeframe": tf_label(interval),
        "as_of": fmt_ts(last.name, interval),
    }
