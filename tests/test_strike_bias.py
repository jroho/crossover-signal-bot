from src.config.settings import AppConfig, AppSection, ConfirmationConfig, GradingConfig, IndicatorConfig, LiveConfig, PolygonConfig, ReplayConfig, StorageConfig, TelegramConfig, VolumeConfig
from src.grading import recommend_strike_bias
from src.models import Grade, StrikeBias, VolumeGrade


def _config(allow_two_otm: bool = False, soft_c: bool = False) -> AppConfig:
    return AppConfig(
        app=AppSection(),
        indicators=IndicatorConfig(),
        volume=VolumeConfig(),
        confirmation=ConfirmationConfig(),
        grading=GradingConfig(alert_grades=["A", "B"], allow_grade_c_soft_alerts=soft_c, allow_grade_b_itm=True, allow_grade_a_otm=True, allow_two_otm=allow_two_otm),
        storage=StorageConfig(),
        replay=ReplayConfig(),
        telegram=TelegramConfig(),
        polygon=PolygonConfig(),
        live=LiveConfig(),
    )


def test_grade_a_defaults_to_one_otm_only_when_expansion_is_strong():
    bias, reason = recommend_strike_bias(
        Grade.A,
        _config(),
        structure_aligned=True,
        momentum_aligned=True,
        volume_grade=VolumeGrade.STRONG,
        one_min_agreement="yes",
    )

    assert bias == StrikeBias.ONE_OTM
    assert "expansion" in reason.lower()


def test_grade_b_can_fall_back_to_itm():
    bias, _ = recommend_strike_bias(
        Grade.B,
        _config(),
        structure_aligned=True,
        momentum_aligned=False,
        volume_grade=VolumeGrade.ACCEPTABLE,
        one_min_agreement="mixed",
    )

    assert bias == StrikeBias.ONE_ITM


def test_grade_c_prefers_skip_without_soft_alerts():
    bias, _ = recommend_strike_bias(
        Grade.C,
        _config(soft_c=False),
        structure_aligned=False,
        momentum_aligned=False,
        volume_grade=VolumeGrade.WEAK,
        one_min_agreement="no",
    )

    assert bias == StrikeBias.SKIP


def test_grade_a_can_use_two_otm_only_when_enabled():
    bias, _ = recommend_strike_bias(
        Grade.A,
        _config(allow_two_otm=True),
        structure_aligned=True,
        momentum_aligned=True,
        volume_grade=VolumeGrade.STRONG,
        one_min_agreement="yes",
    )

    assert bias == StrikeBias.TWO_OTM
