"""
Chart BUY / SELL signals — deliberately quiet in range-bound markets.

The old marker logic fired on every regime flip, so a choppy sideways stretch
(where the regime label flickers Bull -> Range -> Bear -> Bull ...) produced
a cluster of BUY/SELL arrows. Trend indicators (SMA slope, MACD, ADX) simply
don't give reliable entries there, so this module only signals when a real
trend is confirmed:

  BUY  requires ALL of:
        - regime has been "Bull Trend" for CONFIRM_DAYS days in a row
        - ADX >= ADX_MIN (20: below this the market is range-bound)
        - MACD histogram > 0 and RSI > 50  (momentum agrees)
  SELL requires ALL of:
        - regime has been "Bear Trend" for CONFIRM_DAYS days in a row
        - ADX >= ADX_MIN
  Signals alternate (flat -> BUY -> SELL -> BUY ...): no SELL without an open
  BUY, no second BUY while already long. While the regime is Range/Sideways
  or High Volatility, or ADX is weak, the state simply carries over — nothing
  is drawn.

Every input is from day T or earlier (rolling windows), so no lookahead.
"""

import pandas as pd

from regime import detect_regimes
from indicators import compute_indicators

CONFIRM_DAYS = 3
ADX_MIN = 20


def compute_signals(df: pd.DataFrame, interval="1d") -> pd.Series:
    """Series of 'BUY' / 'SELL' / None, indexed like df."""
    ind = compute_indicators(df, interval)
    reg = detect_regimes(df, interval=interval)["regime"]

    bull_held = (reg == "Bull Trend").rolling(CONFIRM_DAYS).sum() == CONFIRM_DAYS
    bear_held = (reg == "Bear Trend").rolling(CONFIRM_DAYS).sum() == CONFIRM_DAYS
    trending = ind["adx_14"] >= ADX_MIN  # NaN -> False

    buy_ok = bull_held & trending & (ind["macd_hist"] > 0) & (ind["rsi_14"] > 50)
    sell_ok = bear_held & trending

    out, long = [], False
    for b, s in zip(buy_ok.values, sell_ok.values):
        if not long and b:
            out.append("BUY")
            long = True
        elif long and s:
            out.append("SELL")
            long = False
        else:
            out.append(None)
    return pd.Series(out, index=df.index, dtype=object)
