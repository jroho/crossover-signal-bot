from datetime import UTC, datetime

from src.config.settings import AppConfig, AppSection, ConfirmationConfig, GradingConfig, IndicatorConfig, LiveConfig, PolygonConfig, ReplayConfig, StorageConfig, TelegramConfig, VolumeConfig
from src.grading import grade_setup
from src.models import Direction, Grade, IndicatorState, OneMinuteConfirmation, SetupEvaluation, StrikeBias, Timeframe, VolumeGrade


def _config(require_one_min: bool = False) -> AppConfig:
    return AppConfig(
        app=AppSection(symbols=["QQQ"], market_timezone="America/New_York"),
        indicators=IndicatorConfig(),
        volume=VolumeConfig(),
        confirmation=ConfirmationConfig(enable_one_min_confirmation=True, require_one_min_confirmation=require_one_min),
        grading=GradingConfig(alert_grades=["A", "B"], allow_grade_c_soft_alerts=False, allow_grade_b_itm=True, allow_grade_a_otm=True, allow_two_otm=False),
        storage=StorageConfig(),
        replay=ReplayConfig(),
        telegram=TelegramConfig(),
        polygon=PolygonConfig(),
        live=LiveConfig(),
    )


def _evaluation(
    direction: Direction,
    *,
    sma_cross_signal: str | None = None,
    sma_cross_status: str = "fresh",
    sma15_slope: float = 1.0,
    sma30_slope: float = 0.5,
    sma_cross_time: datetime | None = None,
) -> SetupEvaluation:
    return SetupEvaluation(
        symbol="QQQ",
        timestamp=datetime(2026, 3, 24, 15, 0, tzinfo=UTC),
        timeframe=Timeframe.FIVE_MINUTE,
        direction=direction,
        last_price=505.0,
        vwap_relation="above",
        ema9_relation="above",
        sma15_value=504.0,
        sma30_value=502.0,
        sma_trend_relation="bullish",
        sma_cross_signal=sma_cross_signal or direction.value,
        sma_cross_status=sma_cross_status,
        sma_cross_time=sma_cross_time or datetime(2026, 3, 24, 14, 58, tzinfo=UTC),
        sma15_slope=sma15_slope,
        sma30_slope=sma30_slope,
        rvgi=0.4,
        rvgi_sma=0.2,
        rvgi_vs_sma="above",
        rvgi_sign="positive",
        volume=2500,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade="strong",
        one_min_agreement="yes",
        grade=Grade.C,
        strike_bias=StrikeBias.SKIP,
        strike_bias_reason="",
    )


def test_grade_a_bullish_setup_with_fresh_five_min_cross():
    evaluation = _evaluation(Direction.BULL, sma_cross_status="fresh")
    current = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=500.0,
        ema9=501.0,
        sma15=504.0,
        sma30=502.0,
        rvgi=0.4,
        rvgi_sma=0.2,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade=VolumeGrade.STRONG,
    )
    previous = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=499.0,
        ema9=500.5,
        sma15=503.0,
        sma30=501.5,
        rvgi=0.3,
        rvgi_sma=0.1,
        recent_volume_avg=1700,
        rolling_volume_avg=1600,
        volume_grade=VolumeGrade.ACCEPTABLE,
    )

    graded = grade_setup(evaluation, current, previous, OneMinuteConfirmation("yes", "supportive"), _config())

    assert graded.grade == Grade.A
    assert graded.strike_bias in {StrikeBias.ATM, StrikeBias.ONE_OTM}
    assert "5m SMA 15/30 cross triggered within this candle" in graded.passed_conditions
    assert "5m SMA slopes support the current crossover direction" in graded.passed_conditions


def test_grade_b_when_one_min_is_required_and_mixed():
    evaluation = _evaluation(Direction.BULL, sma_cross_status="fresh")
    current = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=500.0,
        ema9=501.0,
        sma15=504.0,
        sma30=502.0,
        rvgi=0.4,
        rvgi_sma=0.2,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade=VolumeGrade.ACCEPTABLE,
    )

    graded = grade_setup(evaluation, current, None, OneMinuteConfirmation("mixed", "mixed"), _config(require_one_min=True))

    assert graded.grade == Grade.B
    assert graded.strike_bias == StrikeBias.ATM


def test_grade_c_bearish_when_structure_breaks():
    evaluation = _evaluation(Direction.BEAR, sma15_slope=-1.0, sma30_slope=-0.5)
    evaluation.last_price = 505.0
    evaluation.vwap_relation = "below_or_equal"
    evaluation.ema9_relation = "below_or_equal"
    evaluation.sma_trend_relation = "bearish_or_flat"
    current = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=500.0,
        ema9=501.0,
        sma15=504.0,
        sma30=502.0,
        rvgi=0.4,
        rvgi_sma=0.2,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade=VolumeGrade.WEAK,
    )

    graded = grade_setup(evaluation, current, None, OneMinuteConfirmation("no", "opposing"), _config(require_one_min=True))

    assert graded.grade == Grade.C
    assert graded.strike_bias == StrikeBias.SKIP
    assert any("trigger volume is weak" == item for item in graded.failed_conditions)


def test_grade_b_when_cross_regime_is_active_but_not_fresh():
    evaluation = _evaluation(Direction.BULL, sma_cross_status="active")
    current = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=500.0,
        ema9=501.0,
        sma15=504.0,
        sma30=502.0,
        rvgi=0.25,
        rvgi_sma=0.2,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade=VolumeGrade.ACCEPTABLE,
    )
    previous = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=499.0,
        ema9=500.5,
        sma15=503.7,
        sma30=501.7,
        rvgi=0.2,
        rvgi_sma=0.15,
        recent_volume_avg=1700,
        rolling_volume_avg=1600,
        volume_grade=VolumeGrade.ACCEPTABLE,
    )

    graded = grade_setup(evaluation, current, previous, OneMinuteConfirmation("yes", "supportive"), _config())

    assert graded.grade == Grade.A
    assert "5m SMA 15/30 crossover regime is still active" in graded.passed_conditions


def test_grade_c_when_cross_happened_premarket():
    evaluation = _evaluation(
        Direction.BEAR,
        sma_cross_status="active",
        sma_cross_time=datetime(2026, 3, 24, 11, 50, tzinfo=UTC),
        sma15_slope=-1.0,
        sma30_slope=-0.5,
    )
    evaluation.last_price = 505.0
    evaluation.vwap_relation = "below_or_equal"
    evaluation.ema9_relation = "below_or_equal"
    evaluation.sma_trend_relation = "bearish_or_flat"
    current = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=500.0,
        ema9=501.0,
        sma15=504.0,
        sma30=502.0,
        rvgi=-0.4,
        rvgi_sma=-0.2,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade=VolumeGrade.STRONG,
    )
    previous = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=499.0,
        ema9=500.5,
        sma15=505.0,
        sma30=502.5,
        rvgi=-0.3,
        rvgi_sma=-0.1,
        recent_volume_avg=1700,
        rolling_volume_avg=1600,
        volume_grade=VolumeGrade.ACCEPTABLE,
    )

    graded = grade_setup(evaluation, current, previous, OneMinuteConfirmation("yes", "supportive"), _config())

    assert graded.grade == Grade.C
    assert graded.strike_bias == StrikeBias.SKIP
    assert "5m SMA 15/30 crossover happened outside market hours" in graded.failed_conditions


def test_grade_c_when_five_min_cross_regime_points_elsewhere():
    evaluation = _evaluation(Direction.BULL, sma_cross_signal="bear", sma_cross_status="active")
    current = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=500.0,
        ema9=501.0,
        sma15=504.0,
        sma30=502.0,
        rvgi=0.4,
        rvgi_sma=0.2,
        recent_volume_avg=1800,
        rolling_volume_avg=1700,
        volume_grade=VolumeGrade.STRONG,
    )
    previous = IndicatorState(
        symbol="QQQ",
        timeframe=Timeframe.FIVE_MINUTE,
        timestamp=evaluation.timestamp,
        vwap=499.0,
        ema9=500.5,
        sma15=503.0,
        sma30=501.5,
        rvgi=0.3,
        rvgi_sma=0.1,
        recent_volume_avg=1700,
        rolling_volume_avg=1600,
        volume_grade=VolumeGrade.ACCEPTABLE,
    )

    graded = grade_setup(evaluation, current, previous, OneMinuteConfirmation("yes", "supportive"), _config())

    assert graded.grade == Grade.C
    assert graded.strike_bias == StrikeBias.SKIP
    assert "5m SMA 15/30 crossover regime points the opposite direction" in graded.failed_conditions
