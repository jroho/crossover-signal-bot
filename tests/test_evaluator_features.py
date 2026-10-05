from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd

from src.config.settings import AppConfig, AppSection, ConfirmationConfig, GradingConfig, IndicatorConfig, VolumeConfig
from src.models import Candle, Timeframe
from src.signals import evaluate_symbol
from src.signals.evaluator import SmaCrossContext, _cross_lag_minutes, _one_minute_cross_events


def _config(fresh_cross_max_bars: int = 3) -> AppConfig:
    return AppConfig(
        app=AppSection(symbols=["QQQ"], market_timezone="America/New_York"),
        indicators=IndicatorConfig(ema_length=3, sma_fast_length=2, sma_slow_length=3, rvgi_length=2, rvgi_signal_length=2),
        volume=VolumeConfig(),
        confirmation=ConfirmationConfig(enable_one_min_confirmation=False, require_one_min_confirmation=False),
        grading=GradingConfig(fresh_cross_max_bars=fresh_cross_max_bars),
    )


def _candles(start: datetime, closes: list[float]) -> list[Candle]:
    return [
        Candle("QQQ", Timeframe.ONE_MINUTE, start + timedelta(minutes=index), close - 0.1, close + 0.2, close - 0.2, close, 1000 + index)
        for index, close in enumerate(closes)
    ]


def test_cross_ages_out_to_stale_after_the_fresh_window():
    start = datetime(2026, 3, 24, 13, 30, tzinfo=UTC)
    # Bull cross prints in the 13:45 bar, then price keeps drifting up so no new cross appears
    # (a perfectly flat series would make the SMAs exactly equal, which reads as a cross).
    closes = [10.0] * 5 + [9.0] * 5 + [8.0] * 5 + [12.0 + 0.1 * index for index in range(20)]
    evaluations, _, _ = evaluate_symbol(_candles(start, closes), _config(fresh_cross_max_bars=1))
    bull = {item.timestamp: item for item in evaluations if item.direction.value == "bull"}

    at_cross = bull[datetime(2026, 3, 24, 13, 45, tzinfo=UTC)]
    one_bar_later = bull[datetime(2026, 3, 24, 13, 52, tzinfo=UTC)]
    two_bars_later = bull[datetime(2026, 3, 24, 13, 57, tzinfo=UTC)]

    assert at_cross.sma_cross_status == "fresh"
    assert at_cross.sma_cross_age_bars == 0.0
    assert one_bar_later.sma_cross_status == "active"
    assert one_bar_later.sma_cross_age_bars == 1.0
    assert two_bars_later.sma_cross_status == "stale"
    assert two_bars_later.sma_cross_age_bars == 2.0
    assert two_bars_later.grade.value == "C"


def test_cross_state_resets_at_the_start_of_a_new_session():
    day_one = datetime(2026, 3, 24, 13, 30, tzinfo=UTC)
    day_two = datetime(2026, 3, 25, 13, 30, tzinfo=UTC)
    closes_day_one = [10.0] * 5 + [9.0] * 5 + [8.0] * 5 + [12.0] * 10
    closes_day_two = [12.0] * 15
    candles = _candles(day_one, closes_day_one) + _candles(day_two, closes_day_two)

    evaluations, _, _ = evaluate_symbol(candles, _config())
    day_two_rows = [item for item in evaluations if item.timestamp >= day_two]

    assert day_two_rows
    assert all(item.sma_cross_time is None or item.sma_cross_time >= day_two for item in day_two_rows)
    assert all(item.sma_cross_status not in {"active", "stale"} for item in day_two_rows[:2])


def test_one_minute_cross_events_detects_sign_changes():
    timestamps = [pd.Timestamp(datetime(2026, 3, 24, 13, 30, tzinfo=UTC)) + pd.Timedelta(minutes=index) for index in range(6)]
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "sma15": [float("nan"), 9.0, 9.0, 11.0, 11.0, 9.0],
            "sma30": [float("nan"), 10.0, 10.0, 10.0, 10.0, 10.0],
        }
    )

    events = _one_minute_cross_events(frame)

    assert events == [
        (timestamps[3].to_pydatetime(), "bull"),
        (timestamps[5].to_pydatetime(), "bear"),
    ]


def test_cross_lag_uses_the_latest_same_direction_one_minute_cross_already_seen():
    cross_time = datetime(2026, 3, 24, 15, 0, tzinfo=UTC)
    context = SmaCrossContext(signal="bull", status="fresh", cross_time=cross_time, sma15_slope=0.1, sma30_slope=0.05, age_bars=0.0)
    events = [
        (cross_time - timedelta(minutes=40), "bear"),
        (cross_time - timedelta(minutes=30), "bull"),
        (cross_time - timedelta(minutes=12), "bull"),
        (cross_time + timedelta(minutes=10), "bull"),
    ]

    lag = _cross_lag_minutes(context, events, as_of=cross_time + timedelta(minutes=1))

    assert lag == 12.0


def test_cross_lag_is_none_without_a_recent_one_minute_cross():
    cross_time = datetime(2026, 3, 24, 15, 0, tzinfo=UTC)
    context = SmaCrossContext(signal="bull", status="fresh", cross_time=cross_time, sma15_slope=0.1, sma30_slope=0.05, age_bars=0.0)
    events = [(cross_time - timedelta(minutes=120), "bull")]

    assert _cross_lag_minutes(context, events, as_of=cross_time) is None
    assert _cross_lag_minutes(replace(context, cross_time=None, status="derived"), events, as_of=cross_time) is None
