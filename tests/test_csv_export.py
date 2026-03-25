from datetime import UTC, datetime

from src.models import AlertPayload, AlertRecord, Direction, Grade, OutcomeGrade, OutcomeResult, SetupEvaluation, StrikeBias, Timeframe
from src.storage.csv_export import export_alerts_to_csv, export_evaluations_to_csv


def _build_evaluation() -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=datetime(2026, 3, 24, 15, 35, tzinfo=UTC),
        timeframe=Timeframe.FIVE_MINUTE,
        direction=Direction.BULL,
        last_price=586.24,
        vwap_relation="above",
        ema9_relation="above",
        sma15_value=585.0,
        sma30_value=583.0,
        sma_trend_relation="bullish",
        sma_cross_signal="bull",
        sma_cross_status="active",
        sma_cross_time=datetime(2026, 3, 24, 15, 20, tzinfo=UTC),
        sma15_slope=0.35,
        sma30_slope=0.18,
        rvgi=0.22,
        rvgi_sma=0.18,
        rvgi_vs_sma="above",
        rvgi_sign="positive",
        volume=2200,
        recent_volume_avg=1800,
        rolling_volume_avg=1750,
        volume_grade="acceptable",
        one_min_agreement="yes",
        grade=Grade.B,
        strike_bias=StrikeBias.ATM,
        strike_bias_reason="default",
        passed_conditions=["price aligned with VWAP"],
        weak_conditions=["RVGI crossover is incomplete"],
        failed_conditions=[],
        rationale="Bullish structure is intact, but momentum confirmation is incomplete.",
        forward_return_3m=0.0019,
        forward_return_5m=0.0036,
        forward_return_10m=0.0028,
        forward_return_15m=0.0032,
        forward_return_30m=0.0034,
        pop_outcome=OutcomeResult.WIN,
        pop_outcome_horizon="3m",
        pop_grade=OutcomeGrade.B,
    )


def test_export_evaluations_to_csv_adds_market_time_columns(tmp_path):
    output_path = tmp_path / "evaluations.csv"

    export_evaluations_to_csv([_build_evaluation()], output_path, market_timezone="America/New_York")

    contents = output_path.read_text(encoding="utf-8")
    assert "datetime_market" in contents
    assert "sma_cross_time_market" in contents
    assert "market_timezone" in contents
    assert "sma_cross_signal" in contents
    assert "sma_cross_status" in contents
    assert "sma_cross_time" in contents
    assert "sma15_slope" in contents
    assert "sma30_slope" in contents
    assert "forward_return_30m" in contents
    assert "pop_outcome" in contents
    assert "pop_outcome_horizon" in contents
    assert "pop_grade" in contents
    assert "2026-03-24 11:35:00 AM EDT" in contents
    assert "2026-03-24 11:20:00 AM EDT" in contents
    assert "America/New_York" in contents
    assert "win" in contents
    assert ",3m," in contents
    assert ",B" in contents


def test_export_alerts_to_csv_adds_market_time_columns(tmp_path):
    output_path = tmp_path / "alerts.csv"
    evaluation = _build_evaluation()
    alert = AlertRecord(
        evaluation=evaluation,
        payload=AlertPayload(
            symbol="QQQ",
            timestamp=evaluation.timestamp,
            timeframe=evaluation.timeframe,
            direction=evaluation.direction,
            grade=evaluation.grade,
            strike_bias=evaluation.strike_bias,
            title="QQQ 5m BULL ALERT",
            message="sample alert",
        ),
        delivered=False,
        transport_message="replay send disabled",
    )

    export_alerts_to_csv([alert], output_path, market_timezone="America/New_York")

    contents = output_path.read_text(encoding="utf-8")
    assert "datetime_market" in contents
    assert "sma_cross_time_market" in contents
    assert "market_timezone" in contents
    assert "sma_cross_signal" in contents
    assert "sma_cross_status" in contents
    assert "sma_cross_time" in contents
    assert "sma15_slope" in contents
    assert "sma30_slope" in contents
    assert "forward_return_30m" in contents
    assert "pop_outcome" in contents
    assert "pop_outcome_horizon" in contents
    assert "pop_grade" in contents
    assert "2026-03-24 11:35:00 AM EDT" in contents
    assert "2026-03-24 11:20:00 AM EDT" in contents
    assert "America/New_York" in contents
    assert "win" in contents
    assert ",3m," in contents
    assert ",B" in contents

