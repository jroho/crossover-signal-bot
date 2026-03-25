from __future__ import annotations

from src.config import AppConfig
from src.grading.strike_bias import recommend_strike_bias
from src.models import Direction, Grade, IndicatorState, OneMinuteConfirmation, SetupEvaluation, StrikeBias, VolumeGrade


def grade_setup(
    evaluation: SetupEvaluation,
    indicator_state: IndicatorState,
    previous_indicator: IndicatorState | None,
    one_min_confirmation: OneMinuteConfirmation,
    config: AppConfig,
) -> SetupEvaluation:
    if any(
        value is None
        for value in (
            indicator_state.vwap,
            indicator_state.ema9,
            indicator_state.sma15,
            indicator_state.sma30,
            indicator_state.rvgi,
            indicator_state.rvgi_sma,
        )
    ):
        evaluation.grade = Grade.C
        evaluation.failed_conditions.append("indicator warmup incomplete")
        evaluation.rationale = "Indicator warmup is incomplete, so the setup is logged as low-confidence only."
        evaluation.strike_bias = StrikeBias.SKIP
        evaluation.strike_bias_reason = "Indicators are not fully initialized yet."
        return evaluation

    is_bull = evaluation.direction == Direction.BULL
    trigger_aligned = evaluation.sma_cross_signal == evaluation.direction.value
    close_above_vwap = evaluation.last_price > float(indicator_state.vwap)
    close_above_ema = evaluation.last_price > float(indicator_state.ema9)
    sma_bullish = float(indicator_state.sma15) > float(indicator_state.sma30)
    rvgi_zero_favorable = float(indicator_state.rvgi) > 0
    rvgi_signal_favorable = float(indicator_state.rvgi_sma) > 0
    rvgi_cross_favorable = float(indicator_state.rvgi) > float(indicator_state.rvgi_sma)

    if not is_bull:
        close_above_vwap = not close_above_vwap
        close_above_ema = not close_above_ema
        sma_bullish = not sma_bullish
        rvgi_zero_favorable = not rvgi_zero_favorable
        rvgi_signal_favorable = not rvgi_signal_favorable
        rvgi_cross_favorable = not rvgi_cross_favorable

    structure_aligned = close_above_vwap and close_above_ema and sma_bullish
    slopes_constructive = _constructive_rvgi_slope(indicator_state, previous_indicator, is_bull)
    momentum_aligned = rvgi_zero_favorable and rvgi_signal_favorable and rvgi_cross_favorable
    constructive_but_incomplete = rvgi_zero_favorable and rvgi_signal_favorable and slopes_constructive
    volume_supportive = indicator_state.volume_grade in {VolumeGrade.STRONG, VolumeGrade.ACCEPTABLE}

    _fill_condition_lists(
        evaluation=evaluation,
        trigger_aligned=trigger_aligned,
        structure_aligned=structure_aligned,
        close_above_vwap=close_above_vwap,
        close_above_ema=close_above_ema,
        sma_aligned=sma_bullish,
        rvgi_zero_favorable=rvgi_zero_favorable,
        rvgi_signal_favorable=rvgi_signal_favorable,
        rvgi_cross_favorable=rvgi_cross_favorable,
        slopes_constructive=slopes_constructive,
        volume_grade=indicator_state.volume_grade,
        one_min_confirmation=one_min_confirmation,
    )

    if not trigger_aligned:
        evaluation.grade = Grade.C
    elif structure_aligned and momentum_aligned and volume_supportive:
        if config.confirmation.require_one_min_confirmation and one_min_confirmation.status != "yes":
            evaluation.grade = Grade.B if one_min_confirmation.status == "mixed" else Grade.C
        else:
            evaluation.grade = Grade.A
    elif structure_aligned and volume_supportive and (momentum_aligned or constructive_but_incomplete):
        evaluation.grade = Grade.B
    elif structure_aligned and indicator_state.volume_grade == VolumeGrade.STRONG:
        evaluation.grade = Grade.B
    else:
        evaluation.grade = Grade.C

    if one_min_confirmation.status == "no" and evaluation.grade == Grade.A:
        evaluation.grade = Grade.B
    elif one_min_confirmation.status == "no" and evaluation.grade == Grade.B and config.confirmation.require_one_min_confirmation:
        evaluation.grade = Grade.C

    evaluation.strike_bias, evaluation.strike_bias_reason = recommend_strike_bias(
        evaluation.grade,
        config,
        structure_aligned=structure_aligned,
        momentum_aligned=momentum_aligned,
        volume_grade=indicator_state.volume_grade,
        one_min_agreement=one_min_confirmation.status,
    )
    evaluation.rationale = _build_rationale(
        grade=evaluation.grade,
        direction=evaluation.direction.value,
        sma_cross_signal=evaluation.sma_cross_signal,
        trigger_aligned=trigger_aligned,
        structure_aligned=structure_aligned,
        momentum_aligned=momentum_aligned,
        volume_grade=indicator_state.volume_grade,
        one_min_status=one_min_confirmation.status,
    )
    return evaluation


def _constructive_rvgi_slope(
    current: IndicatorState,
    previous: IndicatorState | None,
    is_bull: bool,
) -> bool:
    if previous is None or current.rvgi is None or current.rvgi_sma is None or previous.rvgi is None or previous.rvgi_sma is None:
        return False
    if is_bull:
        return current.rvgi > previous.rvgi and current.rvgi_sma > previous.rvgi_sma
    return current.rvgi < previous.rvgi and current.rvgi_sma < previous.rvgi_sma


def _fill_condition_lists(
    *,
    evaluation: SetupEvaluation,
    trigger_aligned: bool,
    structure_aligned: bool,
    close_above_vwap: bool,
    close_above_ema: bool,
    sma_aligned: bool,
    rvgi_zero_favorable: bool,
    rvgi_signal_favorable: bool,
    rvgi_cross_favorable: bool,
    slopes_constructive: bool,
    volume_grade: VolumeGrade,
    one_min_confirmation: OneMinuteConfirmation,
) -> None:
    if trigger_aligned:
        evaluation.passed_conditions.append("5m SMA 15/30 cross triggered")
    elif evaluation.sma_cross_signal == "warmup":
        evaluation.weak_conditions.append("5m SMA 15/30 trigger is still warming up")
    elif evaluation.sma_cross_signal == "none":
        evaluation.weak_conditions.append("5m SMA 15/30 cross has not triggered on this candle")
    else:
        evaluation.failed_conditions.append("5m SMA 15/30 cross triggered in the opposite direction")

    if close_above_vwap:
        evaluation.passed_conditions.append("price aligned with VWAP")
    else:
        evaluation.failed_conditions.append("price not aligned with VWAP")

    if close_above_ema:
        evaluation.passed_conditions.append("price aligned with EMA 9")
    else:
        evaluation.failed_conditions.append("price not aligned with EMA 9")

    if sma_aligned:
        evaluation.passed_conditions.append("SMA 15 and SMA 30 trend is aligned")
    else:
        evaluation.failed_conditions.append("SMA 15 and SMA 30 trend is not aligned")

    if rvgi_zero_favorable:
        evaluation.passed_conditions.append("RVGI has favorable sign")
    else:
        evaluation.weak_conditions.append("RVGI sign is not yet favorable")

    if rvgi_signal_favorable:
        evaluation.passed_conditions.append("RVGI SMA has favorable sign")
    else:
        evaluation.weak_conditions.append("RVGI SMA sign is not yet favorable")

    if rvgi_cross_favorable:
        evaluation.passed_conditions.append("RVGI is stronger than RVGI SMA")
    elif slopes_constructive:
        evaluation.weak_conditions.append("RVGI slope is constructive but crossover is incomplete")
    else:
        evaluation.weak_conditions.append("RVGI crossover is unfavorable")

    if volume_grade == VolumeGrade.STRONG:
        evaluation.passed_conditions.append("trigger volume is strong")
    elif volume_grade == VolumeGrade.ACCEPTABLE:
        evaluation.passed_conditions.append("trigger volume is acceptable")
    elif volume_grade == VolumeGrade.INSUFFICIENT:
        evaluation.weak_conditions.append("volume history is still warming up")
    else:
        evaluation.failed_conditions.append("trigger volume is weak")

    if one_min_confirmation.status == "yes":
        evaluation.passed_conditions.append("1m confirmation agrees")
    elif one_min_confirmation.status == "mixed":
        evaluation.weak_conditions.append("1m confirmation is mixed")
    elif one_min_confirmation.status == "no":
        evaluation.failed_conditions.append("1m confirmation disagrees")
    else:
        evaluation.weak_conditions.append(one_min_confirmation.details)

    if not structure_aligned:
        evaluation.failed_conditions.append("5m structure is not clean")


def _build_rationale(
    *,
    grade: Grade,
    direction: str,
    sma_cross_signal: str,
    trigger_aligned: bool,
    structure_aligned: bool,
    momentum_aligned: bool,
    volume_grade: VolumeGrade,
    one_min_status: str,
) -> str:
    if not trigger_aligned:
        if sma_cross_signal == "warmup":
            return f"{direction.capitalize()} setup is capped at Grade C because the 5m 15/30 trigger is still warming up."
        if sma_cross_signal in {Direction.BULL.value, Direction.BEAR.value}:
            return f"{direction.capitalize()} setup is capped at Grade C because the 5m 15/30 cross triggered for the opposite direction."
        return f"{direction.capitalize()} setup is capped at Grade C because the 5m 15/30 trigger did not fire on this candle."
    if grade == Grade.A:
        return (
            f"{direction.capitalize()} 5m structure is clean, the 5m 15/30 cross fired, momentum confirms, volume is {volume_grade.value}, "
            f"and 1m confirmation is {one_min_status}."
        )
    if grade == Grade.B:
        return (
            f"{direction.capitalize()} structure is constructive after a fresh 5m 15/30 cross, but at least one confirmation is incomplete. "
            f"Momentum aligned={momentum_aligned}, volume={volume_grade.value}, 1m={one_min_status}."
        )
    if not structure_aligned:
        return f"{direction.capitalize()} setup is capped at Grade C because the 5m structure is not fully aligned."
    return (
        f"{direction.capitalize()} setup is Grade C because confirmation quality is weak after the 5m trigger: "
        f"momentum aligned={momentum_aligned}, volume={volume_grade.value}, 1m={one_min_status}."
    )
