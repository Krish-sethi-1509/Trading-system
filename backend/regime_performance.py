"""
Performance by Market Regime — checks whether the regime labels actually
carry information: if days classified "Bull Trend" genuinely outperform
days classified "Bear Trend" on THIS stock's own history, the
classification is doing useful work, not just relabeling noise. This is
the single most direct piece of evidence for whether regime detection is
worth having in the report.

For each regime, this compounds the asset's own daily returns (not any
strategy's — the raw close-to-close move) over every day classified as
that regime, including non-contiguous days. That answers "if I could only
be invested on days labelled X, what would that have earned me" — a fair
comparison across regimes since every one is scored on the same
underlying return series with the same math.
"""

from regime import detect_regimes

REGIME_ORDER = ["Bull Trend", "Bear Trend", "High Volatility", "Range / Sideways"]


def regime_performance(df, interval: str = "1d") -> dict:
    analyzed = detect_regimes(df, interval=interval).copy()
    analyzed["returns"] = analyzed["Close"].pct_change()
    valid = analyzed.dropna(subset=["regime", "returns"])
    if valid.empty:
        raise ValueError("Not enough historical data to break down performance by regime.")

    total_days = len(valid)
    rows = []
    for regime in REGIME_ORDER:
        sub = valid[valid["regime"] == regime]
        days = len(sub)
        if days == 0:
            rows.append({
                "regime": regime,
                "days": 0,
                "pct_of_days": 0.0,
                "total_return_pct": None,
                "avg_daily_return_pct": None,
                "positive_day_pct": None,
            })
            continue
        total_return_pct = (float((1 + sub["returns"]).prod()) - 1) * 100
        avg_daily_return_pct = float(sub["returns"].mean()) * 100
        positive_day_pct = float((sub["returns"] > 0).mean()) * 100
        rows.append({
            "regime": regime,
            "days": days,
            "pct_of_days": round(days / total_days * 100, 1),
            "total_return_pct": round(total_return_pct, 2),
            "avg_daily_return_pct": round(avg_daily_return_pct, 3),
            "positive_day_pct": round(positive_day_pct, 1),
        })

    return {"rows": rows, "total_days": total_days}
