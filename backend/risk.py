"""
Risk Management — position sizing and a stop-loss/target for a hypothetical
NEW long entry at the last close, sized by a fixed risk-per-trade rule and
capped by the AI Decision's regime-based exposure limit.

This is a planning calculator for the paper-trading side of the project,
not a broker connection: nothing here places an order, and every response
carries the same "not financial advice" framing as the rest of the app.

Stop-loss — ATR-based: entry - ATR_MULTIPLIER * ATR(14). This is the
standard volatility-adjusted stop (wider on a skittish stock, tighter on a
calm one) rather than an arbitrary fixed percentage, and reuses the same
ATR the Indicators panel already shows.

Take-profit — entry + REWARD_MULTIPLE * stop_distance, i.e. a stated
Risk:Reward ratio (default 1:2, the standard "only take trades where the
target is at least twice the stop" screen).

Position sizing — "fixed fractional" risk (the standard retail approach):
    risk_amount    = portfolio_value * risk_pct / 100
    shares         = floor(risk_amount / stop_distance)
    position_value = shares * entry
That raw share count is then capped so the position never exceeds the
regime's recommended exposure from decision.py — a tight ATR stop could
otherwise size a bigger position than the AI Decision's own call supports
(e.g. sizing 100% into a stock the regime model only rates 25% exposure).
"""

import math

from indicators import current_indicator_summary
from decision import build_decision

ATR_MULTIPLIER = 2.0
REWARD_MULTIPLE = 2.0
DEFAULT_PORTFOLIO_VALUE = 100_000.0
DEFAULT_RISK_PCT = 1.0


def build_risk_plan(
    df,
    interval: str = "1d",
    portfolio_value: float = DEFAULT_PORTFOLIO_VALUE,
    risk_pct: float = DEFAULT_RISK_PCT,
    atr_multiplier: float = ATR_MULTIPLIER,
    reward_multiple: float = REWARD_MULTIPLE,
) -> dict:
    if portfolio_value <= 0 or risk_pct <= 0:
        raise ValueError("portfolio_value and risk_pct must both be positive.")

    entry = float(df["Close"].iloc[-1])
    ind = current_indicator_summary(df, interval)
    atr = ind["volatility"]["atr_14"]
    if atr is None or atr <= 0:
        raise ValueError("Not enough history to compute an ATR-based stop-loss.")

    decision = build_decision(df, interval)
    exposure_cap_pct = decision["recommended_exposure_pct"]

    stop_distance = atr_multiplier * atr
    stop_loss = round(entry - stop_distance, 2)
    take_profit = round(entry + reward_multiple * stop_distance, 2)

    risk_amount = portfolio_value * risk_pct / 100
    raw_shares = math.floor(risk_amount / stop_distance) if stop_distance > 0 else 0

    max_position_value = portfolio_value * exposure_cap_pct / 100
    exposure_capped_shares = math.floor(max_position_value / entry) if entry > 0 else 0
    shares = max(0, min(raw_shares, exposure_capped_shares))
    capped_by_exposure = shares < raw_shares

    position_value = round(shares * entry, 2)
    position_pct_of_portfolio = round(position_value / portfolio_value * 100, 2) if portfolio_value else 0.0

    return {
        "entry_price": round(entry, 2),
        "atr_14": round(atr, 2),
        "atr_multiplier": atr_multiplier,
        "stop_loss": stop_loss,
        "stop_distance": round(stop_distance, 2),
        "stop_distance_pct": round(stop_distance / entry * 100, 2) if entry else None,
        "take_profit": take_profit,
        "risk_reward_ratio": f"1 : {reward_multiple:g}",
        "portfolio_value": portfolio_value,
        "risk_pct": risk_pct,
        "risk_amount": round(risk_amount, 2),
        "shares": shares,
        "position_value": position_value,
        "position_pct_of_portfolio": position_pct_of_portfolio,
        "exposure_cap_pct": exposure_cap_pct,
        "capped_by_exposure": capped_by_exposure,
        "decision": decision["decision"],
        "note": (
            f"Sizing assumes a NEW long entry at the last close, for a hypothetical "
            f"\u20b9{portfolio_value:,.0f} paper portfolio risking {risk_pct:g}% per trade. "
            "Educational simulation \u2014 not financial advice."
        ),
    }
