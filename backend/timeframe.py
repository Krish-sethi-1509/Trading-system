"""
Per-timeframe helpers, so regime detection, indicators, the AI decision and
the BUY/SELL signals are computed on the SAME bars the chart is showing
(1-minute bars for the 1m view, hourly bars for the 1H view, and so on)
instead of always falling back to daily data.

Everything that used to assume "252 bars a year" now asks this module.
"""

import math

# NSE regular session is 09:15-15:30 = 375 minutes.
_BARS_PER_DAY = {
    "1m": 375, "2m": 187, "5m": 75, "15m": 25, "30m": 13,
    "60m": 7, "1h": 7,
    "1d": 1, "1wk": 1 / 5, "1mo": 1 / 21,
}
_LABELS = {
    "1m": "1-minute", "2m": "2-minute", "5m": "5-minute", "15m": "15-minute",
    "30m": "30-minute", "60m": "1-hour", "1h": "1-hour",
    "1d": "daily", "1wk": "weekly", "1mo": "monthly",
}
TRADING_DAYS = 252
BASE_TREND_THRESHOLD_PCT = 2.0  # the daily-bar threshold used since day one


def bars_per_day(interval: str) -> float:
    return _BARS_PER_DAY.get(interval, 1)


def bars_per_year(interval: str) -> float:
    return TRADING_DAYS * bars_per_day(interval)


def is_intraday(interval: str) -> bool:
    return bars_per_day(interval) > 1


def label(interval: str) -> str:
    return _LABELS.get(interval, interval)


def trend_threshold_pct(interval: str) -> float:
    """
    % change of the 20-bar SMA that counts as a real trend.

    On daily bars this is the original 2%. The same 20-bar window covers a
    very different amount of time on other timeframes (20 minutes vs 20 days
    vs 20 weeks), and price ranges scale with the square root of time, so the
    threshold scales by 1/sqrt(bars per day). Without this, a 2% move in
    20 one-minute bars would essentially never happen and every intraday
    view would read "Range / Sideways".
    """
    return BASE_TREND_THRESHOLD_PCT / math.sqrt(bars_per_day(interval))


def fmt_ts(ts, interval: str) -> str:
    """Date for daily+ bars, date and time for intraday bars."""
    if is_intraday(interval) and hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M")
    return str(ts.date()) if hasattr(ts, "date") else str(ts)
