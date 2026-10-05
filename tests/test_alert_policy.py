from datetime import UTC, datetime, timedelta

from src.alerts import AlertDeduper
from src.grading import grade_is_alertable
from src.models import Direction, Grade, SetupEvaluation, StrikeBias, Timeframe


def _evaluation(grade: Grade, cross_time: datetime, direction: Direction = Direction.BULL) -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=cross_time + timedelta(minutes=1),
        timeframe=Timeframe.FIVE_MINUTE,
        direction=direction,
        last_price=500.0,
        vwap_relation="above",
        ema9_relation="above",
        sma15_value=1.0,
        sma30_value=0.9,
        sma_trend_relation="bullish",
        sma_cross_signal=direction.value,
        sma_cross_status="fresh",
        sma_cross_time=cross_time,
        sma15_slope=0.1,
        sma30_slope=0.05,
        rvgi=0.2,
        rvgi_sma=0.1,
        rvgi_vs_sma="above",
        rvgi_sign="positive",
        volume=1000,
        recent_volume_avg=900,
        rolling_volume_avg=850,
        volume_grade="strong",
        one_min_agreement="yes",
        grade=grade,
        strike_bias=StrikeBias.ATM,
        strike_bias_reason="",
    )


def test_grade_floor_covers_higher_grades():
    assert grade_is_alertable(Grade.A_PLUS, ["A", "B"])
    assert grade_is_alertable(Grade.A, ["A", "B"])
    assert grade_is_alertable(Grade.B, ["A", "B"])
    assert not grade_is_alertable(Grade.C, ["A", "B"])
    assert not grade_is_alertable(Grade.B, ["A"])
    assert grade_is_alertable(Grade.A_PLUS, ["A"])
    assert not grade_is_alertable(Grade.A, [])


def test_deduper_alerts_once_per_episode_and_again_only_on_upgrade():
    cross = datetime(2026, 3, 24, 15, 0, tzinfo=UTC)
    deduper = AlertDeduper()

    assert deduper.should_alert(_evaluation(Grade.B, cross))
    assert not deduper.should_alert(_evaluation(Grade.B, cross))
    assert deduper.should_alert(_evaluation(Grade.A, cross))
    assert not deduper.should_alert(_evaluation(Grade.B, cross))
    assert deduper.should_alert(_evaluation(Grade.A_PLUS, cross))
    assert not deduper.should_alert(_evaluation(Grade.A_PLUS, cross))


def test_deduper_treats_new_cross_or_other_direction_as_a_new_episode():
    cross = datetime(2026, 3, 24, 15, 0, tzinfo=UTC)
    deduper = AlertDeduper()

    assert deduper.should_alert(_evaluation(Grade.A, cross))
    assert deduper.should_alert(_evaluation(Grade.A, cross + timedelta(minutes=30)))
    assert deduper.should_alert(_evaluation(Grade.A, cross, direction=Direction.BEAR))
