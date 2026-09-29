"""
AI Decision engine — the "Adaptive" + "Explainable" part of the project
title made concrete as a single API response.

This module does NOT introduce a new model. It reads the same regime
classification (regime.py) and the same indicator set (indicators.py) that
already exist, and turns them into three things a viewer actually wants to
see at a glance:

  1. A decision label     (BUY / HOLD / REDUCE / EXIT)
  2. A confidence score    (0-100, built from four named, checkable components
                            — never an invented number)
  3. Plain-language reasons (so "confidence: 82%" doesn't sit unexplained)

Confidence methodology (deliberately simple enough to defend in a viva):

  Trend strength        — ADX (0-100), which is exactly what ADX measures.
                           ADX 25 is the conventional "is this actually
                           trending" cutoff, ADX 40+ is a strong trend, so
                           we scale ADX/40 -> 0-100%, capped.
  Volatility clarity     — how far today's volatility sits from the 1.5x
                           median cutoff that regime.py itself uses to call
                           something "High Volatility". Far from the cutoff
                           (either side) = confident; right at the cutoff =
                           a coin flip, which the score reflects.
  Momentum agreement      — does RSI / MACD histogram actually point the
                           direction the regime label claims? For a
                           Range/Sideways regime, "agreement" instead means
                           RSI sitting close to neutral (50), since that's
                           what "no clear direction" should look like. For
                           High Volatility, momentum direction isn't
                           informative, so it scores a neutral 50.
  Regime consistency      — % of the last 10 trading days that were
                           classified the same way. A regime that's held
                           for 10 days straight is a much safer read than
                           one that flipped yesterday.

Overall confidence is the plain average of those four. No component is
weighted higher because it "felt right" — that would defeat the point of
making this explainable.
"""

import numpy as np

from timeframe import label as tf_label, fmt_ts

from regime import detect_regimes
from indicators import current_indicator_summary
from backtest import POSITION_BY_REGIME, NEUTRAL_POSITION

CONSISTENCY_LOOKBACK = 10
ADX_TREND_STRENGTH_CAP = 40  # ADX at/above this scores 100% trend strength
VOL_HIGH_VOL_MULTIPLIER = 1.5  # matches regime.py's own "High Volatility" cutoff


def _clip(x, lo=0.0, hi=100.0):
    return float(np.clip(x, lo, hi))


def _trend_strength_score(adx):
    if adx is None or (isinstance(adx, float) and np.isnan(adx)):
        return 50.0  # unknown -> neutral, not a guess in either direction
    return _clip(adx / ADX_TREND_STRENGTH_CAP * 100)


def _volatility_clarity_score(vol, vol_median, is_high_vol_regime):
    if not vol_median or (isinstance(vol, float) and np.isnan(vol)):
        return 50.0
    ratio = vol / vol_median
    if is_high_vol_regime:
        # Further above the 1.5x cutoff -> more confident it's genuinely high-vol.
        return _clip((ratio - VOL_HIGH_VOL_MULTIPLIER) / 1.0 * 100 + 50)
    # Further below the cutoff -> more confident it's NOT high-vol.
    return _clip((VOL_HIGH_VOL_MULTIPLIER - ratio) / VOL_HIGH_VOL_MULTIPLIER * 100)


def _momentum_agreement_score(regime, rsi, macd_hist):
    rsi = 50.0 if rsi is None or np.isnan(rsi) else rsi
    macd_hist = 0.0 if macd_hist is None or np.isnan(macd_hist) else macd_hist

    if regime == "Bull Trend":
        rsi_score = _clip((rsi - 50) / 20 * 100)
        macd_score = 100.0 if macd_hist > 0 else 0.0
    elif regime == "Bear Trend":
        rsi_score = _clip((50 - rsi) / 20 * 100)
        macd_score = 100.0 if macd_hist < 0 else 0.0
    elif regime == "High Volatility":
        # A volatile market can move hard in either direction, so momentum
        # direction says nothing about whether the label is right. Score it
        # neutral rather than penalizing a legitimate call.
        return 50.0
    else:
        # Range/Sideways: "agreement" means momentum is roughly neutral,
        # i.e. RSI sitting close to 50 rather than pointing hard either way.
        rsi_score = _clip(100 - abs(rsi - 50) / 20 * 100)
        macd_score = rsi_score
    return (rsi_score + macd_score) / 2


def _regime_consistency_score(regime_series, current_regime, lookback=CONSISTENCY_LOOKBACK):
    recent = regime_series.dropna().tail(lookback)
    if recent.empty:
        return 50.0
    return float((recent == current_regime).mean() * 100)


def _decision_label(regime, confidence):
    if regime == "Bull Trend":
        return "BUY" if confidence >= 60 else "HOLD"
    if regime == "Bear Trend":
        return "EXIT" if confidence >= 60 else "REDUCE"
    if regime == "High Volatility":
        return "REDUCE"
    return "HOLD"  # Range / Sideways


def _risk_level(vol_percentile):
    if vol_percentile is None:
        return "Unknown"
    if vol_percentile >= 80:
        return "High"
    if vol_percentile >= 50:
        return "Moderate"
    return "Low"


def build_decision(df, interval="1d") -> dict:
    tf = tf_label(interval)
    analyzed = detect_regimes(df, interval=interval)
    valid = analyzed.dropna(subset=["regime"])
    if valid.empty:
        raise ValueError(
            f"Not enough historical data ({len(df)} rows) to make a decision — "
            "try a longer period."
        )
    last = valid.iloc[-1]
    regime = last["regime"]
    slope = float(last["slope_pct"])
    vol = float(last["volatility"])
    vol_median = float(last["vol_median"])
    reason = last["reason"]
    strategy = last["strategy"]
    as_of = fmt_ts(last.name, interval)

    ind = current_indicator_summary(df, interval)
    adx = ind["trend"]["adx_14"]
    rsi = ind["momentum"]["rsi_14"]
    macd_hist = ind["momentum"]["macd_hist"]
    vol_percentile = ind["volatility"]["vol_percentile"]

    is_high_vol_regime = regime == "High Volatility"
    trend_score = _trend_strength_score(adx)
    vol_clarity_score = _volatility_clarity_score(vol, vol_median, is_high_vol_regime)
    momentum_score = _momentum_agreement_score(regime, rsi, macd_hist)
    consistency_score = _regime_consistency_score(valid["regime"], regime)

    confidence = round((trend_score + vol_clarity_score + momentum_score + consistency_score) / 4)
    decision = _decision_label(regime, confidence)
    exposure_pct = round(POSITION_BY_REGIME.get(regime, NEUTRAL_POSITION) * 100)
    risk = _risk_level(vol_percentile)

    reasons = [
        f"Based on {len(valid)} {tf} bars up to {as_of} — this call is specific to the {tf} chart.",
        reason,
    ]
    if adx is not None:
        reasons.append(
            f"ADX at {adx:.1f} indicates {'strong' if adx >= 25 else 'weak'} directional movement."
        )
    if rsi is not None:
        mood = "bullish" if rsi > 55 else "bearish" if rsi < 45 else "neutral"
        reasons.append(f"RSI at {rsi:.1f} is {mood}.")
    if macd_hist is not None:
        reasons.append(
            f"MACD histogram is {'positive' if macd_hist > 0 else 'negative'} ({macd_hist:+.2f})."
        )
    reasons.append(
        f"This regime has held for {round(consistency_score)}% of the last "
        f"{CONSISTENCY_LOOKBACK} {tf} bars."
    )

    return {
        "as_of": as_of,
        "timeframe": tf,
        "regime": regime,
        "strategy": strategy,
        "decision": decision,
        "confidence": confidence,
        "confidence_breakdown": {
            "trend_strength": round(trend_score),
            "volatility_clarity": round(vol_clarity_score),
            "momentum_agreement": round(momentum_score),
            "regime_consistency": round(consistency_score),
        },
        "recommended_exposure_pct": exposure_pct,
        "risk": risk,
        "reasons": reasons,
        "slope_pct": round(slope, 3),
        "volatility_pct": round(vol, 2),
    }
