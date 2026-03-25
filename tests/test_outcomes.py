from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.models import Direction, Grade, OutcomeGrade, OutcomeResult, SetupEvaluation, StrikeBias, Timeframe
from src.signals.evaluator import _apply_forward_returns, _apply_pop_outcome


START = datetime(2026, 3, 24, 15, 0, tzinfo=UTC)


def _evaluation(direction: Direction = Direction.BULL) -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=START,
        timeframe=Timeframe.FIVE_MINUTE,
        direction=direction,
        last_price=100.0,
        vwap_relation="above",
        ema9_relation="above",
        sma15_value=100.0,
        sma30_value=99.5,
        sma_trend_relation="bullish",
        sma_cross_signal=direction.value,
        sma_cross_status="fresh",
        sma_cross_time=START,
        sma15_slope=0.2,
        sma30_slope=0.1,
        rvgi=0.2,
        rvgi_sma=0.1,
        rvgi_vs_sma="above",
        rvgi_sign="positive",
        volume=1000.0,
        recent_volume_avg=900.0,
        rolling_volume_avg=850.0,
        volume_grade="acceptable",
        one_min_agreement="yes",
        grade=Grade.B,
        strike_bias=StrikeBias.ATM,
        strike_bias_reason="default",
    )


def _frame_from_prices(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["QQQ"] * len(prices),
            "timestamp": [pd.Timestamp(START + timedelta(minutes=index)) for index in range(len(prices))],
            "close": prices,
        }
    )


def test_apply_forward_returns_computes_signed_30m_for_bear_setup():
    evaluation = _evaluation(Direction.BEAR)
    prices = [100.0] + ([100.0] * 29) + [99.0]

    _apply_forward_returns(evaluation, _frame_from_prices(prices))

    assert evaluation.forward_return_30m == pytest.approx(0.01)
    assert evaluation.pop_outcome == OutcomeResult.WIN
    assert evaluation.pop_outcome_horizon == "30m"
    assert evaluation.pop_grade == OutcomeGrade.A


def test_pop_outcome_marks_win_on_first_positive_threshold_hit():
    evaluation = _evaluation(Direction.BULL)
    prices = [100.0, 100.0, 100.0, 100.2, 100.15, 99.7] + ([99.7] * 10) + [100.25] + ([100.25] * 14)

    _apply_forward_returns(evaluation, _frame_from_prices(prices))

    assert evaluation.pop_outcome == OutcomeResult.WIN
    assert evaluation.pop_outcome_horizon == "3m"
    assert evaluation.pop_grade == OutcomeGrade.C


def test_pop_outcome_marks_loss_when_negative_threshold_hits_first():
    evaluation = _evaluation(Direction.BULL)
    prices = [100.0, 100.0, 100.0, 99.8, 99.8, 100.4] + ([100.4] * 25)

    _apply_forward_returns(evaluation, _frame_from_prices(prices))

    assert evaluation.pop_outcome == OutcomeResult.LOSS
    assert evaluation.pop_outcome_horizon == "3m"
    assert evaluation.pop_grade is None


def test_pop_outcome_uses_first_threshold_hit_when_both_sides_trigger():
    evaluation = _evaluation(Direction.BULL)
    prices = [100.0, 100.0, 100.0, 100.05, 100.05, 99.8] + ([99.8] * 10) + [100.5] + ([100.5] * 14)

    _apply_forward_returns(evaluation, _frame_from_prices(prices))

    assert evaluation.pop_outcome == OutcomeResult.LOSS
    assert evaluation.pop_outcome_horizon == "5m"
    assert evaluation.pop_grade is None


def test_pop_outcome_marks_flat_when_all_horizons_exist_without_threshold_hit():
    evaluation = _evaluation(Direction.BULL)
    prices = [100.0, 100.0, 100.0, 100.10, 100.10, 100.16] + ([100.16] * 10) + [99.85] + ([99.85] * 14)

    _apply_forward_returns(evaluation, _frame_from_prices(prices))

    assert evaluation.pop_outcome == OutcomeResult.FLAT
    assert evaluation.pop_outcome_horizon is None
    assert evaluation.pop_grade is None


def test_pop_outcome_stays_blank_when_30m_window_is_missing():
    evaluation = _evaluation(Direction.BULL)
    prices = [100.0] * 20

    _apply_forward_returns(evaluation, _frame_from_prices(prices))

    assert evaluation.forward_return_15m == pytest.approx(0.0)
    assert evaluation.forward_return_30m is None
    assert evaluation.pop_outcome is None
    assert evaluation.pop_outcome_horizon is None
    assert evaluation.pop_grade is None


@pytest.mark.parametrize(
    ("returns", "expected_grade"),
    [
        ({"forward_return_3m": 0.0018, "forward_return_5m": 0.0019, "forward_return_15m": 0.0020, "forward_return_30m": 0.0021}, OutcomeGrade.C),
        ({"forward_return_3m": 0.0018, "forward_return_5m": 0.0035, "forward_return_15m": 0.0032, "forward_return_30m": 0.0031}, OutcomeGrade.B),
        ({"forward_return_3m": 0.0018, "forward_return_5m": 0.0052, "forward_return_15m": 0.0045, "forward_return_30m": 0.0049}, OutcomeGrade.A),
    ],
)
def test_pop_grade_buckets_win_strength(returns: dict[str, float], expected_grade: OutcomeGrade):
    evaluation = _evaluation(Direction.BULL)
    for field_name, value in returns.items():
        setattr(evaluation, field_name, value)

    _apply_pop_outcome(evaluation)

    assert evaluation.pop_outcome == OutcomeResult.WIN
    assert evaluation.pop_outcome_horizon == "3m"
    assert evaluation.pop_grade == expected_grade
