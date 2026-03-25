from __future__ import annotations

from zoneinfo import ZoneInfo

from src.config import AppConfig
from src.grading.strike_bias import recommend_strike_bias
from src.market_hours import is_within_market_hours, parse_clock_time
from src.models import Direction, Grade, IndicatorState, OneMinuteConfirmation, SetupEvaluation, StrikeBias, VolumeGrade


ACTIVE_CROSS_STATUSES = {"fresh", "active", "derived"}


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
    cross_in_market_hours = _cross_is_during_market_hours(evaluation, config)
    trigger_aligned = (
        evaluation.sma_cross_signal == evaluation.direction.value
        and evaluation.sma_cross_status in ACTIVE_CROSS_STATUSES
        and cross_in_market_hours
    )
    slopes_supportive = _cross_slopes_supportive(evaluation, is_bull)
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
        cross_in_market_hours=cross_in_market_hours,
        slopes_supportive=slopes_supportive,
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

    if evaluation.grade == Grade.A and not slopes_supportive:
        evaluation.grade = Grade.B

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
        trigger_aligned=trigger_aligned,
        cross_in_market_hours=cross_in_market_hours,
        slopes_supportive=slopes_supportive,
        structure_aligned=structure_aligned,
        momentum_aligned=momentum_aligned,
        volume_grade=indicator_state.volume_grade,
        one_min_status=one_min_confirmation.status,
        cross_status=evaluation.sma_cross_status,
        cross_time=evaluation.sma_cross_time.isoformat() if evaluation.sma_cross_time else None,
    )
    return evaluation


def _cross_is_during_market_hours(evaluation: SetupEvaluation, config: AppConfig) -> bool:
    if evaluation.sma_cross_signal == "none":
        return False
    if evaluation.sma_cross_time is None:
        return True
    market_timezone = ZoneInfo(config.app.market_timezone)
    market_open = parse_clock_time(config.live.market_open_time, field_name="live.market_open_time")
    market_close = parse_clock_time(config.live.market_close_time, field_name="live.market_close_time")
    return is_within_market_hours(evaluation.sma_cross_time, market_timezone, market_open, market_close)


def _cross_slopes_supportive(evaluation: SetupEvaluation, is_bull: bool) -> bool:
    if evaluation.sma15_slope is None or evaluation.sma30_slope is None:
        return False
    if is_bull:
        return evaluation.sma15_slope >= evaluation.sma30_slope
    return evaluation.sma15_slope <= evaluation.sma30_slope


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
    cross_in_market_hours: bool,
    slopes_supportive: bool,
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
        if evaluation.sma_cross_status == "fresh":
            evaluation.passed_conditions.append("5m SMA 15/30 cross triggered within this candle")
        elif evaluation.sma_cross_status == "active":
            evaluation.passed_conditions.append("5m SMA 15/30 crossover regime is still active")
        else:
            evaluation.passed_conditions.append("5m SMA 15/30 crossover regime is aligned from available 5m history")
    elif evaluation.sma_cross_status == "warmup":
        evaluation.weak_conditions.append("5m SMA 15/30 trigger is still warming up")
    elif evaluation.sma_cross_signal == "none":
        evaluation.weak_conditions.append("5m SMA 15/30 crossover regime is not established yet")
    elif not cross_in_market_hours:
        evaluation.failed_conditions.append("5m SMA 15/30 crossover happened outside market hours")
    else:
        evaluation.failed_conditions.append("5m SMA 15/30 crossover regime points the opposite direction")

    if slopes_supportive:
        evaluation.passed_conditions.append("5m SMA slopes support the current crossover direction")
    elif evaluation.sma15_slope is not None and evaluation.sma30_slope is not None:
        evaluation.weak_conditions.append("5m SMA slopes are not expanding in the same direction")

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
    trigger_aligned: bool,
    cross_in_market_hours: bool,
    slopes_supportive: bool,
    structure_aligned: bool,
    momentum_aligned: bool,
    volume_grade: VolumeGrade,
    one_min_status: str,
    cross_status: str,
    cross_time: str | None,
) -> str:
    cross_detail = f"status={cross_status}"
    if cross_time:
        cross_detail += f", intersection={cross_time}"
    if not trigger_aligned:
        if cross_status == "warmup":
            return f"{direction.capitalize()} setup is capped at Grade C because the 5m 15/30 trigger is still warming up."
        if not cross_in_market_hours:
            return f"{direction.capitalize()} setup is capped at Grade C because the 5m 15/30 crossover happened outside market hours ({cross_detail})."
        return f"{direction.capitalize()} setup is capped at Grade C because the 5m 15/30 crossover regime does not support this direction ({cross_detail})."
    if grade == Grade.A:
        return (
            f"{direction.capitalize()} 5m structure is clean, the 5m 15/30 crossover regime is aligned ({cross_detail}), "
            f"SMA slopes are {'supportive' if slopes_supportive else 'mixed'}, momentum confirms, volume is {volume_grade.value}, and 1m confirmation is {one_min_status}."
        )
    if grade == Grade.B:
        return (
            f"{direction.capitalize()} structure is constructive with an aligned 5m 15/30 crossover regime ({cross_detail}), "
            f"but at least one confirmation is incomplete. Momentum aligned={momentum_aligned}, volume={volume_grade.value}, 1m={one_min_status}."
        )
    if not structure_aligned:
        return f"{direction.capitalize()} setup is capped at Grade C because the 5m structure is not fully aligned."
    return (
        f"{direction.capitalize()} setup is Grade C because confirmation quality is weak even though the 5m 15/30 crossover regime is aligned ({cross_detail}): "
        f"momentum aligned={momentum_aligned}, volume={volume_grade.value}, 1m={one_min_status}."
    )
