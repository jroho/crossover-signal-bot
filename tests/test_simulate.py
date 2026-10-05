from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.backtest import SimConfig, simulate_episode, summarize
from src.backtest.simulate import OptionBar, lag_bucket

ET = ZoneInfo("America/New_York")
DETECTION = datetime(2026, 3, 24, 15, 2, tzinfo=UTC)  # 11:02 ET, inside the default entry window
CROSS = datetime(2026, 3, 24, 15, 1, 30, tzinfo=UTC)
OCC = "QQQ260324P00585000"


def _episode() -> dict[str, object]:
    return {
        "date": "2026-03-24",
        "symbol": "QQQ",
        "direction": "bear",
        "cross_time": CROSS.isoformat(),
        "detection_time": DETECTION.isoformat(),
        "spot": 585.19,
        "sma_cross_lag_min": 23.5,
        "bar_minutes_elapsed": 3,
        "volume_grade": "weak",
        "atm_symbol": OCC,
        "itm1_symbol": "QQQ260324P00586000",
    }


def _evaluations(grades: dict[int, str], *, price_path: dict[int, float] | None = None, one_min: str = "yes") -> pd.DataFrame:
    """Bear-direction rows at DETECTION + offset minutes; grade C unless listed."""
    rows = []
    for offset in range(0, 60):
        timestamp = DETECTION + timedelta(minutes=offset)
        rows.append(
            {
                "timestamp": timestamp,
                "direction": "bear",
                "cross_time_s": CROSS,
                "grade": grades.get(offset, "C"),
                "last_price": (price_path or {}).get(offset, 585.0),
                "vwap_relation": "below_or_equal",
                "ema9_relation": "below_or_equal",
                "one_min_agreement": one_min,
                "sma15_value": 584.0,
                "sma30_value": 585.0 + 0.01 * offset,
                "volume_grade": "strong",
            }
        )
    return pd.DataFrame(rows)


def _bars(path: list[tuple[int, float, float, float, float]], start: datetime) -> list[OptionBar]:
    return [OptionBar(start + timedelta(minutes=offset), o, h, l, c) for offset, o, h, l, c in path]


def test_entry_waits_for_first_alertable_grade_and_fills_next_bar_open():
    evaluations = _evaluations({2: "A"})
    fill_time = DETECTION + timedelta(minutes=3)
    bars = _bars([(0, 2.00, 2.10, 1.95, 2.05), (1, 2.05, 2.80, 2.00, 2.70)], fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=30, stop_pct=30), ET)

    assert trade.entry_grade == "A"
    assert trade.entry_time == fill_time.isoformat()
    assert trade.entry_price == pytest.approx(2.02)
    assert trade.entry_delay_min == 3.0
    assert trade.exit_reason == "target"
    assert trade.exit_price == pytest.approx(2.02 * 1.30)
    assert trade.pnl_usd == pytest.approx((2.02 * 1.30 - 2.02) * 100, abs=0.01)
    assert trade.lag_bucket == "16-30"
    assert trade.entry_hour_et == 11
    assert trade.entry_time_et == "11:05"
    assert trade.time_bucket == "11:00-12:00"
    assert trade.day_of_week == "Tue"
    assert trade.strike == 585.0


def test_no_entry_when_grade_never_reaches_minimum():
    [trade] = simulate_episode(_episode(), _evaluations({5: "B"}), {}, SimConfig(min_grade="A"), ET)

    assert trade.exit_reason == "no_entry"
    assert trade.entry_price is None


def test_no_fill_when_option_bars_are_missing():
    [trade] = simulate_episode(_episode(), _evaluations({0: "A"}), {}, SimConfig(), ET)

    assert trade.exit_reason == "no_fill"
    assert trade.entry_grade == "A"


def test_stop_wins_when_stop_and_target_print_in_the_same_bar():
    evaluations = _evaluations({0: "A"})
    fill_time = DETECTION + timedelta(minutes=1)
    bars = _bars([(0, 2.00, 3.00, 1.00, 2.00)], fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=30, stop_pct=30), ET)

    assert trade.exit_reason == "stop"
    assert trade.exit_price == pytest.approx(2.02 * 0.70 - 0.02)
    assert trade.mfe_pct > 0 and trade.mae_pct < 0


def test_time_rule_exits_when_option_is_flat_and_trend_has_faded():
    # Underlying rallied against the bear trade and 1m disagrees: only structure could hold, so 1 of 3.
    evaluations = _evaluations({0: "A"}, price_path={offset: 586.0 for offset in range(60)}, one_min="no")
    fill_time = DETECTION + timedelta(minutes=1)
    flat = [(offset, 2.00, 2.05, 1.95, 2.00) for offset in range(0, 30)]
    bars = _bars(flat, fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=50, stop_pct=50, time_rule_mode="option"), ET)

    assert trade.exit_reason == "time_rule"
    assert trade.exit_time == (fill_time + timedelta(minutes=15)).isoformat()
    assert trade.hold_minutes == 15.0


def test_time_rule_off_lets_a_flat_trade_run_to_max_hold():
    evaluations = _evaluations({0: "A"}, price_path={offset: 586.0 for offset in range(60)}, one_min="no")
    fill_time = DETECTION + timedelta(minutes=1)
    bars = _bars([(offset, 2.00, 2.05, 1.95, 2.00) for offset in range(0, 50)], fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=50, stop_pct=50, time_rule_mode="off"), ET)

    assert trade.exit_reason == "max_hold"


def test_underlying_time_rule_exits_even_when_the_option_is_up():
    # Option is above entry, but the underlying has moved against a bear trade and 1m disagrees.
    evaluations = _evaluations({0: "A"}, price_path={offset: 586.0 for offset in range(60)}, one_min="no")
    fill_time = DETECTION + timedelta(minutes=1)
    bars = _bars([(offset, 2.00, 2.30, 1.98, 2.20) for offset in range(0, 50)], fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=50, stop_pct=50, time_rule_mode="underlying"), ET)

    assert trade.exit_reason == "time_rule"
    assert trade.pnl_usd > 0


def test_entries_after_the_cutoff_are_recorded_but_not_traded():
    evaluations = _evaluations({0: "A"})
    late_detection = datetime(2026, 3, 24, 17, 30, tzinfo=UTC)  # 13:30 ET
    evaluations["timestamp"] = evaluations["timestamp"] + (late_detection - DETECTION)
    episode = _episode()
    episode["detection_time"] = late_detection.isoformat()
    bars = _bars([(offset, 2.00, 2.90, 1.95, 2.80) for offset in range(0, 5)], late_detection + timedelta(minutes=1))

    [trade] = simulate_episode(episode, evaluations, {OCC: bars}, SimConfig(target_pct=30, last_entry_time="13:00"), ET)

    assert trade.exit_reason == "after_cutoff"
    assert trade.entry_grade == "A"
    assert trade.time_bucket == "13:00-14:00"
    assert trade.entry_price is None

    [allowed] = simulate_episode(episode, evaluations, {OCC: bars}, SimConfig(target_pct=30, last_entry_time=None), ET)
    assert allowed.exit_reason == "target"


def test_time_rule_holds_when_trend_still_supports_the_trade_then_max_hold_exits():
    # Underlying below entry for a bear and 1m agrees: 2 of 3, so the 15- and 25-minute checks hold.
    evaluations = _evaluations({0: "A"}, price_path={offset: 584.0 for offset in range(60)}, one_min="yes")
    fill_time = DETECTION + timedelta(minutes=1)
    flat = [(offset, 2.00, 2.05, 1.95, 2.00) for offset in range(0, 50)]
    bars = _bars(flat, fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=50, stop_pct=50, time_rule_mode="option"), ET)

    assert trade.exit_reason == "max_hold"
    assert trade.hold_minutes == 45.0


def test_eod_flat_closes_before_the_forced_sellout():
    evaluations = _evaluations({0: "A"})
    late_detection = datetime(2026, 3, 24, 19, 30, tzinfo=UTC)  # 15:30 ET
    evaluations["timestamp"] = evaluations["timestamp"] + (late_detection - DETECTION)
    episode = _episode()
    episode["detection_time"] = late_detection.isoformat()
    fill_time = late_detection + timedelta(minutes=1)
    bars = _bars([(offset, 2.00, 2.05, 1.95, 2.00) for offset in range(0, 20)], fill_time)

    [trade] = simulate_episode(episode, evaluations, {OCC: bars}, SimConfig(target_pct=50, stop_pct=50, last_entry_time=None), ET)

    assert trade.exit_reason == "eod_flat"
    assert trade.exit_time == datetime(2026, 3, 24, 19, 35, tzinfo=UTC).isoformat()


def test_multiple_labels_simulate_each_contract():
    evaluations = _evaluations({0: "A+"})
    fill_time = DETECTION + timedelta(minutes=1)
    bars = {OCC: _bars([(0, 2.00, 2.70, 1.95, 2.60)], fill_time), "QQQ260324P00586000": _bars([(0, 2.50, 2.60, 2.40, 2.55)] + [(o, 2.5, 2.5, 2.5, 2.5) for o in range(1, 50)], fill_time)}

    trades = simulate_episode(_episode(), evaluations, bars, SimConfig(target_pct=30, stop_pct=30, labels=("ATM", "ITM1")), ET)

    assert [(trade.label, trade.exit_reason) for trade in trades] == [("ATM", "target"), ("ITM1", "max_hold")]
    assert all(trade.entry_grade == "A+" for trade in trades)


def test_trailing_stop_locks_in_a_gain_after_the_trigger():
    evaluations = _evaluations({0: "A"}, price_path={offset: 584.0 for offset in range(60)})
    fill_time = DETECTION + timedelta(minutes=1)
    # Up 25% (arms the trail at +20%), then the pullback breaks the trail but never the fixed stop.
    bars = _bars([(0, 2.00, 2.10, 1.98, 2.05), (1, 2.05, 2.50, 2.04, 2.45), (2, 2.45, 2.46, 2.05, 2.10), (3, 2.10, 2.12, 2.00, 2.05)], fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=50, stop_pct=30, trail_trigger_pct=20, trail_pct=15), ET)

    assert trade.exit_reason == "trail_stop"
    assert trade.exit_time == (fill_time + timedelta(minutes=2)).isoformat()
    assert trade.exit_price == pytest.approx(2.50 * 0.85 - 0.02)
    assert trade.pnl_usd > 0


def test_trailing_stop_does_not_arm_before_the_trigger():
    evaluations = _evaluations({0: "A"}, price_path={offset: 584.0 for offset in range(60)})
    fill_time = DETECTION + timedelta(minutes=1)
    bars = _bars([(0, 2.00, 2.30, 1.98, 2.25), (1, 2.25, 2.26, 1.90, 1.95)] + [(o, 1.95, 1.96, 1.94, 1.95) for o in range(2, 50)], fill_time)

    [trade] = simulate_episode(_episode(), evaluations, {OCC: bars}, SimConfig(target_pct=50, stop_pct=30, trail_trigger_pct=20, trail_pct=15), ET)

    assert trade.exit_reason == "max_hold"


def test_entry_filters_decline_plain_a_with_long_lag_but_pass_a_plus():
    from src.backtest.context import DayContext

    evaluations = _evaluations({0: "A"})
    fill_time = DETECTION + timedelta(minutes=1)
    bars = _bars([(0, 2.00, 2.80, 1.95, 2.70)], fill_time)
    cfg = SimConfig(target_pct=30, plain_a_max_lag_min=5.0)

    [declined] = simulate_episode(_episode(), evaluations, {OCC: bars}, cfg, ET)  # lag 23.5 min
    assert declined.exit_reason == "filtered_lag" and declined.entry_price is None

    short_lag = dict(_episode(), sma_cross_lag_min=3.0)
    [taken] = simulate_episode(short_lag, evaluations, {OCC: bars}, cfg, ET)
    assert taken.exit_reason == "target"

    [a_plus] = simulate_episode(_episode(), _evaluations({0: "A+"}), {OCC: bars}, cfg, ET)
    assert a_plus.exit_reason == "target"


def test_entry_filters_for_direction_and_turbulence_use_day_context():
    from src.backtest.context import DayContext

    bull_episode = dict(_episode(), direction="bull", atm_symbol="QQQ260324C00585000")
    evaluations = _evaluations({0: "A+"})
    evaluations["direction"] = "bull"
    fill_time = DETECTION + timedelta(minutes=1)
    bars = {"QQQ260324C00585000": _bars([(0, 2.00, 2.80, 1.95, 2.70)], fill_time)}
    cfg = SimConfig(target_pct=30, bull_requires_alignment=True, skip_turbulent=True)

    bear_day = DayContext(trend_bias=-1, trend_label="bear", turbulence="normal")
    [counter] = simulate_episode(bull_episode, evaluations, bars, cfg, ET, bear_day)
    assert counter.exit_reason == "filtered_direction" and counter.alignment == "counter"

    bull_day = DayContext(trend_bias=1, trend_label="bull", turbulence="normal")
    [aligned] = simulate_episode(bull_episode, evaluations, bars, cfg, ET, bull_day)
    assert aligned.exit_reason == "target" and aligned.alignment == "aligned"

    wild_day = DayContext(trend_bias=1, trend_label="bull", turbulence="turbulent")
    [skipped] = simulate_episode(bull_episode, evaluations, bars, cfg, ET, wild_day)
    assert skipped.exit_reason == "filtered_turbulence" and skipped.turbulence == "turbulent"

    bear_episode = _episode()
    bear_evaluations = _evaluations({0: "A+"})
    bear_bars = {OCC: _bars([(0, 2.00, 2.80, 1.95, 2.70)], fill_time)}
    [bear_on_bull_day] = simulate_episode(bear_episode, bear_evaluations, bear_bars, cfg, ET, bull_day)
    assert bear_on_bull_day.exit_reason == "target"

    strict = SimConfig(target_pct=30, require_alignment=True)
    [counter_bear] = simulate_episode(bear_episode, bear_evaluations, bear_bars, strict, ET, bull_day)
    assert counter_bear.exit_reason == "filtered_direction"
    [aligned_bear] = simulate_episode(bear_episode, bear_evaluations, bear_bars, strict, ET, bear_day)
    assert aligned_bear.exit_reason == "target"
    neutral_day = DayContext(trend_bias=0, trend_label="neutral", turbulence="normal")
    [no_bias] = simulate_episode(bear_episode, bear_evaluations, bear_bars, strict, ET, neutral_day)
    assert no_bias.exit_reason == "filtered_direction" and no_bias.alignment == "neutral"


def test_time_bucket_edges():
    from src.backtest.simulate import time_bucket

    assert time_bucket(datetime(2026, 3, 24, 9, 31, tzinfo=ET)) == "09:30-10:00"
    assert time_bucket(datetime(2026, 3, 24, 10, 0, tzinfo=ET)) == "10:00-11:00"
    assert time_bucket(datetime(2026, 3, 24, 12, 30, tzinfo=ET)) == "12:00-13:00"
    assert time_bucket(datetime(2026, 3, 24, 15, 10, tzinfo=ET)) == "15:00-close"


def test_lag_bucket_edges():
    assert lag_bucket(None) == "none"
    assert lag_bucket(5.0) == "<=5"
    assert lag_bucket(5.01) == "6-15"
    assert lag_bucket(30.0) == "16-30"
    assert lag_bucket(31.0) == ">30"


def test_summarize_reports_hit_rates_and_expectancy():
    trades = pd.DataFrame(
        [
            {"target_pct": 30, "stop_pct": 30, "label": "ATM", "entry_grade": "A", "entry_price": 2.0, "exit_price": 2.6, "exit_reason": "target", "pnl_usd": 60.0, "return_pct": 30.0, "hold_minutes": 5},
            {"target_pct": 30, "stop_pct": 30, "label": "ATM", "entry_grade": "A", "entry_price": 2.0, "exit_price": 1.4, "exit_reason": "stop", "pnl_usd": -60.0, "return_pct": -30.0, "hold_minutes": 9},
            {"target_pct": 30, "stop_pct": 30, "label": "ATM", "entry_grade": "A", "entry_price": 2.0, "exit_price": 2.1, "exit_reason": "time_rule", "pnl_usd": 10.0, "return_pct": 5.0, "hold_minutes": 15},
            {"target_pct": 30, "stop_pct": 30, "label": "ATM", "entry_grade": None, "entry_price": None, "exit_price": None, "exit_reason": "no_entry", "pnl_usd": None, "return_pct": None, "hold_minutes": None},
        ]
    )

    summary = summarize(trades, ["entry_grade"])

    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["trades"] == 3
    assert row["win_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert row["target_rate"] == pytest.approx(1 / 3, abs=0.001)
    assert row["expectancy_usd"] == pytest.approx(10.0 / 3, abs=0.01)
    assert row["profit_factor"] == pytest.approx(70 / 60, abs=0.01)
