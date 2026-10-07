import csv
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src.backtest.context import DayContext
from src.config.settings import AlpacaConfig, AppConfig, AppSection, GradingConfig, TradingConfig
from src.execution.parity import (
    ParityResult,
    append_log,
    compare_rows,
    parse_contexts,
    parse_decisions,
    replay_decisions,
    rows_from_evaluations,
    summary_line,
    write_report,
)
from src.models import Direction, Grade, SetupEvaluation, StrikeBias, Timeframe

ET = "America/New_York"
T0 = datetime(2026, 10, 6, 14, 5, tzinfo=UTC)  # 10:05 ET


def _config() -> AppConfig:
    return AppConfig(
        app=AppSection(symbols=["QQQ"], market_timezone=ET),
        grading=GradingConfig(alert_grades=["A", "B"]),
        alpaca=AlpacaConfig(api_key_id="k", api_secret_key="s"),
        trading=TradingConfig(enabled=True, paper=True, risk_capital_usd=1000.0),
    )


def _evaluation(timestamp: datetime, cross_time: datetime, grade: Grade = Grade.A_PLUS) -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=timestamp,
        timeframe=Timeframe.FIVE_MINUTE,
        direction=Direction.BEAR,
        last_price=585.19,
        vwap_relation="below_or_equal",
        ema9_relation="below_or_equal",
        sma15_value=584.0,
        sma30_value=585.0,
        sma_trend_relation="bearish_or_flat",
        sma_cross_signal="bear",
        sma_cross_status="fresh",
        sma_cross_time=cross_time,
        sma15_slope=-0.1,
        sma30_slope=-0.05,
        rvgi=-0.2,
        rvgi_sma=-0.1,
        rvgi_vs_sma="below_or_equal",
        rvgi_sign="negative_or_zero",
        volume=1000,
        recent_volume_avg=900,
        rolling_volume_avg=850,
        volume_grade="strong",
        one_min_agreement="yes",
        grade=grade,
        strike_bias=StrikeBias.ATM,
        strike_bias_reason="",
        sma_cross_age_bars=0.0,
        sma_cross_lag_min=3.0,
        bar_minutes_elapsed=1.0,
    )


def _row(minute: int, grade: str, volume_grade: str, direction: str = "bull") -> dict:
    return {
        "symbol": "QQQ",
        "direction": direction,
        "datetime": T0 + timedelta(minutes=minute),
        "grade": grade,
        "volume_grade": volume_grade,
        "volume": 100.0 + minute,
        "recent_volume_avg": 90.0,
        "sma_cross_status": "active",
        "bar_minutes_elapsed": 1.0,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    return frame


def test_compare_rows_counts_grade_volume_and_coverage_differences():
    live = _frame([_row(0, "A+", "strong"), _row(1, "C", "weak"), _row(2, "B", "acceptable"), _row(3, "C", "weak"), _row(0, "C", "strong", "bear")])
    sip = _frame([_row(0, "A", "acceptable"), _row(1, "C", "acceptable"), _row(2, "B", "acceptable"), _row(4, "C", "weak"), _row(0, "C", "strong", "bear")])

    mismatches, volume_only, relevant, live_only, sip_only = compare_rows(live, sip)

    assert len(mismatches) == 1
    assert mismatches.iloc[0][["grade_live", "grade_sip", "volume_grade_live", "volume_grade_sip"]].tolist() == ["A+", "A", "strong", "acceptable"]
    assert volume_only == 1  # minute 1: both C, weak vs acceptable
    assert relevant == 1  # the A+/A disagreement is at or above the entry floor
    assert live_only == 1 and sip_only == 1  # minute 3 only live, minute 4 only SIP


def test_rows_from_evaluations_matches_the_live_row_shape():
    frame = rows_from_evaluations([_evaluation(T0, T0 - timedelta(minutes=4))])
    assert frame.columns.tolist() == ["symbol", "direction", "datetime", "grade", "volume_grade", "volume", "recent_volume_avg", "sma_cross_status", "bar_minutes_elapsed"]
    assert frame.iloc[0]["grade"] == "A+" and frame.iloc[0]["direction"] == "bear" and frame.iloc[0]["datetime"] == pd.Timestamp(T0)
    assert rows_from_evaluations([]).empty


def test_parse_decisions_and_contexts_from_engine_messages():
    messages = [
        "QQQ context: bias=bull vix=mid (15.01)",
        "SPY context: bias=neutral vix=unknown (None)",
        "ENTRY QQQ bull A+ QQQ261006C00762000 x1 limit 1.46 (A+ bull aligned)",
        "FILLED QQQ261006C00762000 x1 @ 1.42; target 1.85 stop 0.99",
        "skip QQQ bear B: grade B below A",
        "skip SPY bull: max open positions",
        "skip SPY bear A+: halted (daily loss limit (realized -150.00, losses 2))",
        "EXIT QQQ261006C00762000 stop @ 0.96 pnl -46.00 (day -2.00)",
    ]
    decisions = parse_decisions(messages)
    assert [str(item) for item in decisions] == [
        "ENTRY QQQ bull A+: A+ bull aligned",
        "skip QQQ bear B: grade B below A",
        "skip SPY bull: max open positions",
        "skip SPY bear A+: halted (daily loss limit (realized -150.00, losses 2))",
    ]
    assert [item.artifact for item in decisions] == [False, False, True, True]

    contexts = parse_contexts(messages)
    assert contexts["QQQ"].trend_bias == 1 and contexts["QQQ"].trend_label == "bull"
    assert contexts["QQQ"].vix_regime == "mid" and contexts["QQQ"].vix_prev_close == 15.01
    assert contexts["SPY"].trend_bias == 0 and contexts["SPY"].trend_label == "neutral" and contexts["SPY"].vix_prev_close is None


def test_decision_diff_ignores_skips_caused_by_unmodelled_exit_state():
    result = ParityResult(
        day=date(2026, 10, 6),
        live_decisions=parse_decisions(["ENTRY QQQ bull A+ X x1 limit 1.46 (A+ bull aligned)", "skip SPY bull: max open positions"]),
        sip_decisions=parse_decisions(["ENTRY QQQ bull A+ Y x1 limit 1.52 (A+ bull aligned)", "skip SPY bull A: plain A with lag 7.0 > 5.0 min"]),
    )
    live_only, sip_only = result.decision_diff()
    assert live_only == []
    assert [str(item) for item in sip_only] == ["skip SPY bull A: plain A with lag 7.0 > 5.0 min"]
    assert result.entries_match

    result.sip_decisions = parse_decisions(["skip QQQ bull A: plain A with lag 8.2 > 5.0 min"])
    assert not result.entries_match


def test_replay_decisions_runs_the_real_engine_on_sip_evaluations(tmp_path: Path):
    day = date(2026, 10, 6)
    cross = datetime(2026, 10, 6, 15, 1, 30, tzinfo=UTC)
    evaluations = [_evaluation(datetime(2026, 10, 6, 15, 5, tzinfo=UTC), cross)]  # 11:05 ET, inside the entry window
    bear_day = DayContext(trend_bias=-1, trend_label="bear", vix_regime="mid", vix_prev_close=18.0)

    lines = replay_decisions(_config(), evaluations, {"QQQ": bear_day}, day, tmp_path)

    decisions = parse_decisions([line.split("] ", 1)[-1] for line in lines])
    assert [str(item) for item in decisions] == ["ENTRY QQQ bear A+: A+ bear aligned"]
    assert any("FILLED" in line for line in lines)
    assert (tmp_path / "parity_journal.sqlite3").exists()

    # On a bull day the same evaluation is declined, and the decline is reported the same way the live loop logs it.
    bull_day = DayContext(trend_bias=1, trend_label="bull", vix_regime="mid", vix_prev_close=18.0)
    lines = replay_decisions(_config(), evaluations, {"QQQ": bull_day}, day, tmp_path / "bull")
    assert [str(item) for item in parse_decisions([line.split("] ", 1)[-1] for line in lines])] == ["skip QQQ bear A+: bear trade not aligned with bull bias"]


def test_report_and_log_accumulate_per_day(tmp_path: Path):
    from zoneinfo import ZoneInfo

    mismatches, *_ = compare_rows(_frame([_row(0, "A+", "strong")]), _frame([_row(0, "A", "acceptable")]))
    result = ParityResult(
        day=date(2026, 10, 6),
        minutes_compared=10,
        grade_mismatches=mismatches,
        volume_grade_mismatches=2,
        decision_relevant_mismatches=1,
        live_decisions=parse_decisions(["ENTRY QQQ bull A+ X x1 limit 1.46 (A+ bull aligned)"]),
        sip_decisions=parse_decisions(["skip QQQ bull A: plain A with lag 8.2 > 5.0 min"]),
        live_modes=["trade-paper"],
        sip_end=datetime(2026, 10, 6, 19, 45, tzinfo=UTC),
    )
    report = write_report(result, tmp_path, ZoneInfo(ET))
    text = report.read_text(encoding="utf-8")
    assert "| Entries match | NO |" in text and "| 10:05 | QQQ | bull | A+ | A | strong | acceptable |" in text
    assert "Only SIP replay: skip QQQ bull A: plain A with lag 8.2 > 5.0 min" in text
    assert "1 grade mismatches (1 decision-relevant)" in summary_line(result) and "DIFFER" in summary_line(result)

    append_log(result, tmp_path)
    result.minutes_compared = 12
    path = append_log(result, tmp_path)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]["minutes_compared"] == "12" and rows[0]["entries_match"] == "False"

    result.day = date(2026, 10, 7)
    append_log(result, tmp_path)
    with path.open(newline="", encoding="utf-8") as handle:
        assert [row["date"] for row in csv.DictReader(handle)] == ["2026-10-06", "2026-10-07"]
