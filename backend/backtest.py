"""
Backtest: the actual "trading system" component.

Four long-only, no-leverage, no-shorting strategies are simulated on the same
price series and scored with the same metrics, so the comparison is fair:

    Adaptive (Regime-Switched) -> position set by the detected regime
        Bull Trend 100% | Bear Trend 0% | High Volatility 25% | Range/Sideways 50%
    Momentum                   -> 100% long while SMA20 > SMA50 AND RSI(14) > 50,
                                  otherwise cash
    Mean Reversion             -> buy (100%) when RSI(14) < 30 OR close is below
                                  the lower Bollinger Band; sell when close
                                  recovers to the 20-day SMA (the "mean")
    Buy & Hold                 -> 100% invested throughout

Rules that apply to every strategy (state these in the report):

  * No lookahead: the position held on day T is the one decided from data up
    to day T-1 (shift(1)). Every indicator and the regime label use the day's
    closing price, so trading on the same day's signal would be cheating.
  * Common evaluation window: indicators need a warm-up (SMA50 needs 50 bars).
    All strategies are scored only from the first bar where every indicator
    is valid, so none gets a head start. (Skipped for very short series.)
  * Transaction cost: COST_PCT charged on every change in position size
    (0.10% of the amount traded, a rough all-in figure for NSE delivery
    trades incl. brokerage, STT, slippage). Entering the first position is
    charged too.
  * Sharpe uses a risk-free rate of RISK_FREE_PCT p.a. (~10Y India G-Sec
    yield): mean(daily return - rf/252) / std(daily return) * sqrt(252).
  * CAGR = (final equity / 1) ** (252 / trading days) - 1.
  * "Trade" = one holding leg: a run of consecutive days at the same non-zero
    position size. Win rate = % of legs whose compounded, cost-adjusted return
    is > 0. (A change from 100% to 50% therefore closes one leg and opens
    another.) Buy & Hold is always exactly 1 trade.
"""

import math
import numpy as np
import pandas as pd

from regime import detect_regimes
from indicators import compute_indicators

POSITION_BY_REGIME = {
    "Bull Trend": 1.00,
    "Bear Trend": 0.00,
    "High Volatility": 0.25,
    "Range / Sideways": 0.50,
}
NEUTRAL_POSITION = 0.50  # used when the regime is unknown (warm-up)

COST_PCT = 0.10          # % of traded amount, per unit change in position
RISK_FREE_PCT = 6.5      # annual, for Sharpe
MIN_ROWS_FOR_COMMON_WINDOW = 120
WARMUP_BARS = 50         # longest indicator used by the strategies (SMA50)

STRATEGY_LABELS = {
    "adaptive": "Adaptive (Regime-Switched)",
    "momentum": "Momentum",
    "mean_reversion": "Mean Reversion",
    "buy_hold": "Buy & Hold",
}


def _safe(x):
    """Turn NaN/inf into None so the result is always valid JSON."""
    if x is None:
        return None
    try:
        return None if (isinstance(x, (float, np.floating)) and (math.isnan(x) or math.isinf(x))) else float(x)
    except (TypeError, ValueError):
        return x


def _r(x, nd=2):
    x = _safe(x)
    return None if x is None else round(x, nd)


# ---------------- target positions (decided on day T from data <= T) ----------------

def _adaptive_target(regime: pd.Series) -> pd.Series:
    return regime.map(POSITION_BY_REGIME).fillna(NEUTRAL_POSITION).astype(float)


def _momentum_target(ind: pd.DataFrame) -> pd.Series:
    long = (ind["sma_20"] > ind["sma_50"]) & (ind["rsi_14"] > 50)  # NaN -> False -> cash
    return long.astype(float)


def _mean_reversion_target(ind: pd.DataFrame) -> pd.Series:
    close, rsi = ind["Close"].values, ind["rsi_14"].values
    lower, mean = ind["bb_lower"].values, ind["sma_20"].values
    pos = np.zeros(len(ind))
    holding = False
    for i in range(len(ind)):
        if np.isnan(rsi[i]) or np.isnan(lower[i]) or np.isnan(mean[i]):
            pos[i] = 0.0
            holding = False
            continue
        if holding:
            if close[i] >= mean[i]:
                holding = False
        elif rsi[i] < 30 or close[i] < lower[i]:
            holding = True
        pos[i] = 1.0 if holding else 0.0
    return pd.Series(pos, index=ind.index)


def _buy_hold_target(index) -> pd.Series:
    return pd.Series(1.0, index=index)


# ---------------- metrics ----------------

def _max_drawdown_pct(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((equity - peak) / peak).min() * 100)


def _trade_legs(position: pd.Series, net_returns: pd.Series) -> list:
    """Compounded net return of each run of identical non-zero positions."""
    legs, cur, prev = [], None, 0.0
    for pos, ret in zip(position.values, net_returns.values):
        if pos > 0 and pos == prev and cur is not None:
            cur *= (1 + ret)
        elif pos > 0:
            if cur is not None:
                legs.append(cur - 1)
            cur = 1 + ret
        else:
            if cur is not None:
                legs.append(cur - 1)
            cur = None
        prev = pos
    if cur is not None:
        legs.append(cur - 1)
    return legs


def evaluate(returns: pd.Series, position: pd.Series, cost_pct: float = COST_PCT,
             risk_free_pct: float = RISK_FREE_PCT) -> dict:
    """
    Score any daily position series (already lagged, i.e. the position actually
    held each day) against the asset's daily returns. Reusable for any strategy.
    """
    turnover = position.diff().abs()
    turnover.iloc[0] = abs(position.iloc[0])  # paying to enter the first position
    net = position * returns - turnover * (cost_pct / 100)
    equity = (1 + net).cumprod()

    n = len(net)
    total_return = float(equity.iloc[-1] - 1) * 100
    years = max(n / 252, 1 / 252)
    cagr = (float(equity.iloc[-1]) ** (1 / years) - 1) * 100

    std = float(net.std())
    sharpe = None
    if n > 1 and std > 0:
        sharpe = float((net - risk_free_pct / 100 / 252).mean() / std * math.sqrt(252))

    legs = _trade_legs(position, net)
    win_rate = (sum(1 for x in legs if x > 0) / len(legs) * 100) if legs else None

    stats = {
        "total_return_pct": _r(total_return),
        "cagr_pct": _r(cagr),
        "sharpe": _r(sharpe),
        "max_drawdown_pct": _r(_max_drawdown_pct(equity)),
        "win_rate_pct": _r(win_rate, 1),
        "num_trades": len(legs),
        "avg_exposure_pct": _r(float(position.mean()) * 100, 1),
    }
    return {"equity": equity, "net_returns": net, "stats": stats}


# ---------------- main entry point ----------------

def run_backtest(df: pd.DataFrame) -> dict:
    """
    df: OHLCV DataFrame indexed by date (nse_client.get_history() output).
    Returns a JSON-safe dict with equity curves + metrics for all four strategies.
    """
    if df is None or len(df) < 5:
        raise ValueError("Not enough historical data to run a backtest.")

    ind = compute_indicators(df)
    ind["returns"] = ind["Close"].pct_change()
    ind["regime"] = detect_regimes(df)["regime"]

    targets = {
        "adaptive": _adaptive_target(ind["regime"]),
        "momentum": _momentum_target(ind),
        "mean_reversion": _mean_reversion_target(ind),
        "buy_hold": _buy_hold_target(ind.index),
    }
    # Lag on the FULL frame first, so the first scored day already knows
    # yesterday's decision, then trim to the common window.
    lagged = {k: v.shift(1).fillna(0.0) for k, v in targets.items()}

    start = WARMUP_BARS if len(ind) >= MIN_ROWS_FOR_COMMON_WINDOW else 1
    window = ind.iloc[start:]
    if len(window) < 2:
        raise ValueError("Not enough historical data to run a backtest.")

    strategies = {}
    for key, pos_full in lagged.items():
        pos = pos_full.iloc[start:]
        res = evaluate(window["returns"], pos)
        strategies[key] = {
            "label": STRATEGY_LABELS[key],
            "equity": [_r(v, 4) for v in res["equity"]],
            "position": [_r(p, 2) for p in pos],
            "stats": res["stats"],
        }

    dates = [str(d.date()) if hasattr(d, "date") else str(d) for d in window.index]
    return {
        "dates": dates,
        "regime": [r if isinstance(r, str) else None for r in window["regime"]],
        "strategies": strategies,
        "days_simulated": len(window),
        "assumptions": {
            "cost_pct_per_trade": COST_PCT,
            "risk_free_pct": RISK_FREE_PCT,
            "warmup_bars_skipped": start,
        },
        "position_key": POSITION_BY_REGIME,
    }
