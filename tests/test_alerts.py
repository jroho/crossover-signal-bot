from datetime import UTC, datetime

from src.alerts import format_alert
from src.models import Direction, Grade, SetupEvaluation, StrikeBias, Timeframe


def test_alert_payload_contains_required_sections():
    evaluation = SetupEvaluation(
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
        passed_conditions=["price aligned with VWAP", "price aligned with EMA 9"],
        weak_conditions=["RVGI crossover is incomplete"],
        failed_conditions=[],
        rationale="Bullish structure is intact, but momentum confirmation is incomplete.",
    )

    payload = format_alert(evaluation)

    assert payload.title == "QQQ 5m BULL ALERT"
    assert "Strike bias: ATM" in payload.message
    assert "Reason: Bullish structure is intact" in payload.message
    assert "15/30 Cross: bull (active)" in payload.message
    assert "15 SMA slope: 0.35" in payload.message
    assert "1m agreement: yes" in payload.message
