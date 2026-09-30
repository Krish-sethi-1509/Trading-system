import numpy as np
import pandas as pd
import pytest

from backtest import evaluate, run_backtest
from ml_regime import evaluate_regime_model, predict_regime
from regime import detect_regimes
from risk import build_risk_plan


@pytest.fixture(scope="module")
def history():
    rng = np.random.default_rng(42)
    n = 1000
    # Alternating drift and volatility blocks create varied, reproducible regimes.
    drifts = np.resize(np.array([0.0008, -0.0007, 0.00005, 0.0001]), n)
    scales = np.resize(np.array([0.008, 0.009, 0.004, 0.025]), n)
    returns = drifts + rng.normal(0, scales)
    close = 100 * np.exp(np.cumsum(returns))
    dates = pd.bdate_range("2019-01-01", periods=n)
    return pd.DataFrame({"Open": close * (1 - returns / 3), "High": close * 1.012,
                         "Low": close * 0.988, "Close": close,
                         "Volume": rng.integers(100_000, 500_000, n)}, index=dates)


def test_ml_prediction_and_chronological_evaluation(history):
    prediction = predict_regime(history)
    evaluation = evaluate_regime_model(history)
    assert prediction["predicted_regime"] in {"Bull Trend", "Bear Trend", "Range / Sideways", "High Volatility"}
    assert 0 <= prediction["confidence"] <= 1
    assert set(prediction["class_probabilities"]) == {"Bull Trend", "Bear Trend", "Range / Sideways", "High Volatility"}
    assert evaluation["split"]["train_end"] < evaluation["split"]["test_start"]
    assert 0 <= evaluation["metrics"]["accuracy"] <= 1
    assert len(evaluation["metrics"]["confusion_matrix"]["matrix"]) >= 2
    assert evaluation["feature_importance"]


def test_rule_detection_and_strategy_switching(history):
    analyzed = detect_regimes(history)
    assert analyzed["regime"].notna().any()
    row = analyzed.dropna(subset=["regime"]).iloc[-1]
    assert row["selected_strategy"]
    assert row["exposure_pct"] in (0, 25, 50, 100)
    assert row["trade_action"] in ("BUY / INCREASE", "REDUCE / EXIT", "HOLD")


def test_risk_position_sizing(history):
    plan = build_risk_plan(history, portfolio_value=100_000, risk_pct=1)
    assert plan["shares"] >= 0
    assert plan["position_value"] <= 100_000
    assert plan["position_pct_of_portfolio"] <= plan["exposure_cap_pct"]


def test_backtest_metrics_and_benchmark(history):
    benchmark = history.copy()
    result = run_backtest(history, benchmark_df=benchmark)
    assert result["benchmark"] is not None
    for strategy in result["strategies"].values():
        stats = strategy["stats"]
        assert "sortino" in stats and "profit_factor" in stats
        assert "max_consecutive_losses" in stats and "avg_exposure_pct" in stats
        assert len(strategy["equity"]) == result["days_simulated"]
    summary = evaluate(pd.Series([.01, -.02, .01]), pd.Series([1., 1., 0.]))["stats"]
    assert summary["num_trades"] >= 1

