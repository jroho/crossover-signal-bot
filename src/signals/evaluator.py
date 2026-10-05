from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import AppConfig
from src.config.settings import OutcomeConfig
from src.grading import grade_setup
from src.indicators import (
    IndicatorBundle,
    build_indicator_bundle,
    compute_indicator_states,
    five_minute_bucket_start,
    partial_bar_volume_context,
    resample_to_active_five_minute,
)
from src.models import Candle, Direction, Grade, IndicatorState, OneMinuteConfirmation, OutcomeGrade, OutcomeResult, SetupEvaluation, StrikeBias, Timeframe

OUTCOME_HORIZONS = (3, 5, 10, 15, 30)
FORWARD_RETURN_HORIZONS = (3, 5, 10, 15, 30)
# A 1m cross further back than this is not treated as the lead-in to the 5m cross.
CROSS_LAG_LOOKBACK = timedelta(minutes=90)
# The interpolated 5m cross time can land slightly before the 1m cross inside the same bar.
CROSS_LAG_LOOKAHEAD = timedelta(minutes=5)


@dataclass(frozen=True)
class SmaCrossContext:
    signal: str
    status: str
    cross_time: datetime | None
    sma15_slope: float | None
    sma30_slope: float | None
    age_bars: float | None = None


def evaluate_symbol(candles: list[Candle], config: AppConfig) -> tuple[list[SetupEvaluation], IndicatorBundle, IndicatorBundle]:
    one_min_bundle, five_min_bundle = build_indicator_bundle(candles, config)
    evaluations: list[SetupEvaluation] = []
    one_min_by_symbol: dict[str, list[Candle]] = defaultdict(list)
    for candle in sorted(candles, key=lambda item: (item.symbol, item.timestamp)):
        one_min_by_symbol[candle.symbol].append(candle)
    market_timezone = ZoneInfo(config.app.market_timezone)

    for symbol, symbol_candles in one_min_by_symbol.items():
        one_min_frame = one_min_bundle.dataframe[one_min_bundle.dataframe["symbol"] == symbol].copy()
        one_min_cross_events = _one_minute_cross_events(one_min_frame)
        last_cross_signal = "none"
        last_cross_time: datetime | None = None
        last_cross_bucket: datetime | None = None
        last_cross_lag: float | None = None
        previous_five_min_state: IndicatorState | None = None
        session_date = None

        for index, minute_candle in enumerate(symbol_candles):
            active_five_minute_candles = resample_to_active_five_minute(symbol_candles[: index + 1])
            if not active_five_minute_candles:
                continue

            bucket_start = five_minute_bucket_start(minute_candle.timestamp)
            candle_session = bucket_start.astimezone(market_timezone).date()
            if candle_session != session_date:
                # A crossover from an earlier session is not a trigger for this one.
                session_date = candle_session
                last_cross_signal = "none"
                last_cross_time = None
                last_cross_bucket = None
                last_cross_lag = None
                previous_five_min_state = None

            active_five_minute_bundle = compute_indicator_states(active_five_minute_candles, config)
            current_five_minute_candle = active_five_minute_bundle.candles[-1]
            state = active_five_minute_bundle.states[(symbol, pd.Timestamp(current_five_minute_candle.timestamp))]
            partial_volume = partial_bar_volume_context(symbol_candles[: index + 1], config)
            if partial_volume is not None:
                state = replace(
                    state,
                    recent_volume_avg=partial_volume.recent_volume_avg,
                    rolling_volume_avg=partial_volume.rolling_volume_avg,
                    volume_grade=partial_volume.volume_grade,
                )

            cross_context = _build_sma_cross_context(
                current=state,
                previous=previous_five_min_state,
                last_cross_signal=last_cross_signal,
                last_cross_time=last_cross_time,
            )
            if cross_context.status == "fresh":
                last_cross_signal = cross_context.signal
                last_cross_time = cross_context.cross_time
                last_cross_bucket = bucket_start
                # The lead-in lag is fixed at detection; later 1m crosses belong to the next episode.
                last_cross_lag = _cross_lag_minutes(cross_context, one_min_cross_events, as_of=minute_candle.timestamp)
            cross_context = _with_cross_age(cross_context, bucket_start, last_cross_bucket, config)
            cross_lag = last_cross_lag if cross_context.status in {"fresh", "active", "stale"} else None

            for direction in (Direction.BULL, Direction.BEAR):
                one_min_confirmation = _derive_one_min_confirmation(
                    one_min_frame=one_min_frame,
                    timestamp=pd.Timestamp(minute_candle.timestamp),
                    direction=direction,
                    config=config,
                )
                evaluation = SetupEvaluation(
                    symbol=symbol,
                    timestamp=minute_candle.timestamp,
                    timeframe=Timeframe.FIVE_MINUTE,
                    direction=direction,
                    last_price=minute_candle.close,
                    vwap_relation=_relation(current_five_minute_candle.close, state.vwap),
                    ema9_relation=_relation(current_five_minute_candle.close, state.ema9),
                    sma15_value=state.sma15,
                    sma30_value=state.sma30,
                    sma_trend_relation=_sma_relation(state.sma15, state.sma30),
                    sma_cross_signal=cross_context.signal,
                    sma_cross_status=cross_context.status,
                    sma_cross_time=cross_context.cross_time,
                    sma15_slope=cross_context.sma15_slope,
                    sma30_slope=cross_context.sma30_slope,
                    rvgi=state.rvgi,
                    rvgi_sma=state.rvgi_sma,
                    rvgi_vs_sma=_rvgi_relation(state.rvgi, state.rvgi_sma),
                    rvgi_sign=_sign_label(state.rvgi),
                    volume=current_five_minute_candle.volume,
                    recent_volume_avg=state.recent_volume_avg,
                    rolling_volume_avg=state.rolling_volume_avg,
                    volume_grade=state.volume_grade.value,
                    one_min_agreement=one_min_confirmation.status,
                    grade=Grade.C,
                    strike_bias=StrikeBias.SKIP,
                    strike_bias_reason="",
                    sma_cross_age_bars=cross_context.age_bars,
                    sma_cross_lag_min=cross_lag,
                    bar_minutes_elapsed=partial_volume.elapsed_minutes if partial_volume else None,
                )
                evaluation = grade_setup(evaluation, state, previous_five_min_state, one_min_confirmation, config)
                _apply_forward_returns(evaluation, one_min_frame, config)
                evaluations.append(evaluation)

            previous_five_min_state = state

    evaluations.sort(key=lambda item: (item.symbol, item.timestamp, item.direction.value))
    return evaluations, one_min_bundle, five_min_bundle


# The 5m 15/30 crossover owns the directional regime; 1m remains confirmation-only.
def _build_sma_cross_context(
    *,
    current: IndicatorState,
    previous: IndicatorState | None,
    last_cross_signal: str,
    last_cross_time: datetime | None,
) -> SmaCrossContext:
    if previous is None:
        return SmaCrossContext(signal="none", status="none", cross_time=None, sma15_slope=None, sma30_slope=None)
    if any(value is None for value in (current.sma15, current.sma30, previous.sma15, previous.sma30)):
        return SmaCrossContext(signal="none", status="warmup", cross_time=None, sma15_slope=None, sma30_slope=None)

    sma15_slope = float(current.sma15) - float(previous.sma15)
    sma30_slope = float(current.sma30) - float(previous.sma30)
    prev_delta = float(previous.sma15) - float(previous.sma30)
    curr_delta = float(current.sma15) - float(current.sma30)
    fresh_signal = _cross_direction(prev_delta, curr_delta)
    if fresh_signal != "none":
        cross_time = _interpolate_cross_time(previous.timestamp, current.timestamp, prev_delta, curr_delta)
        return SmaCrossContext(
            signal=fresh_signal,
            status="fresh",
            cross_time=cross_time,
            sma15_slope=sma15_slope,
            sma30_slope=sma30_slope,
        )

    if last_cross_signal in {Direction.BULL.value, Direction.BEAR.value}:
        return SmaCrossContext(
            signal=last_cross_signal,
            status="active",
            cross_time=last_cross_time,
            sma15_slope=sma15_slope,
            sma30_slope=sma30_slope,
        )

    # No crossover observed this session: the regime is inferred from SMA levels only and never triggers.
    derived_signal = _signal_from_delta(curr_delta)
    if derived_signal != "none":
        return SmaCrossContext(
            signal=derived_signal,
            status="derived",
            cross_time=None,
            sma15_slope=sma15_slope,
            sma30_slope=sma30_slope,
        )
    return SmaCrossContext(
        signal="none",
        status="none",
        cross_time=None,
        sma15_slope=sma15_slope,
        sma30_slope=sma30_slope,
    )


def _with_cross_age(
    context: SmaCrossContext,
    bucket_start: datetime,
    last_cross_bucket: datetime | None,
    config: AppConfig,
) -> SmaCrossContext:
    if context.status not in {"fresh", "active"} or last_cross_bucket is None:
        return context
    age_bars = (bucket_start - last_cross_bucket) / timedelta(minutes=5)
    status = context.status
    if status == "active" and age_bars > config.grading.fresh_cross_max_bars:
        status = "stale"
    return replace(context, status=status, age_bars=float(age_bars))


def _one_minute_cross_events(one_min_frame: pd.DataFrame, epsilon: float = 1e-9) -> list[tuple[datetime, str]]:
    events: list[tuple[datetime, str]] = []
    if one_min_frame.empty or "sma15" not in one_min_frame or "sma30" not in one_min_frame:
        return events
    frame = one_min_frame.sort_values("timestamp")
    previous_delta: float | None = None
    for timestamp, sma15, sma30 in zip(frame["timestamp"], frame["sma15"], frame["sma30"], strict=True):
        if pd.isna(sma15) or pd.isna(sma30):
            previous_delta = None
            continue
        delta = float(sma15) - float(sma30)
        if previous_delta is not None:
            direction = _cross_direction(previous_delta, delta, epsilon)
            if direction != "none":
                events.append((pd.Timestamp(timestamp).to_pydatetime(), direction))
        previous_delta = delta
    return events


def _cross_lag_minutes(
    context: SmaCrossContext,
    one_min_cross_events: list[tuple[datetime, str]],
    *,
    as_of: datetime,
) -> float | None:
    if context.cross_time is None or context.status not in {"fresh", "active", "stale"}:
        return None
    earliest = context.cross_time - CROSS_LAG_LOOKBACK
    latest = min(context.cross_time + CROSS_LAG_LOOKAHEAD, as_of)
    candidates = [
        timestamp
        for timestamp, direction in one_min_cross_events
        if direction == context.signal and earliest <= timestamp <= latest
    ]
    if not candidates:
        return None
    return round((context.cross_time - max(candidates)).total_seconds() / 60.0, 2)


def _cross_direction(previous_delta: float, current_delta: float, epsilon: float = 1e-9) -> str:
    if previous_delta < -epsilon and current_delta >= -epsilon:
        return Direction.BULL.value
    if previous_delta > epsilon and current_delta <= epsilon:
        return Direction.BEAR.value
    return "none"


def _signal_from_delta(delta: float, epsilon: float = 1e-9) -> str:
    if delta > epsilon:
        return Direction.BULL.value
    if delta < -epsilon:
        return Direction.BEAR.value
    return "none"


def _interpolate_cross_time(previous_time: datetime, current_time: datetime, previous_delta: float, current_delta: float) -> datetime:
    delta_change = current_delta - previous_delta
    if abs(delta_change) < 1e-12:
        return current_time
    fraction = -previous_delta / delta_change
    fraction = max(0.0, min(1.0, fraction))
    return previous_time + ((current_time - previous_time) * fraction)


def _derive_one_min_confirmation(
    *,
    one_min_frame: pd.DataFrame,
    timestamp: pd.Timestamp,
    direction: Direction,
    config: AppConfig,
) -> OneMinuteConfirmation:
    if not config.confirmation.enable_one_min_confirmation:
        return OneMinuteConfirmation(status="disabled", details="1m confirmation is disabled by config.")

    recent = one_min_frame[one_min_frame["timestamp"] <= timestamp].tail(1)
    if recent.empty:
        return OneMinuteConfirmation(status="disabled", details="1m confirmation unavailable for this candle.")

    row = recent.iloc[0]
    if pd.isna(row["vwap"]) or pd.isna(row["ema9"]):
        return OneMinuteConfirmation(status="mixed", details="1m indicators are still warming up.")

    if direction == Direction.BULL:
        price_aligned_vwap = row["close"] > row["vwap"]
        price_aligned_ema = row["close"] > row["ema9"]
    else:
        price_aligned_vwap = row["close"] < row["vwap"]
        price_aligned_ema = row["close"] < row["ema9"]

    if price_aligned_vwap and price_aligned_ema:
        return OneMinuteConfirmation(status="yes", details="1m price agrees with VWAP and EMA 9.")
    if not price_aligned_vwap and not price_aligned_ema:
        return OneMinuteConfirmation(status="no", details="1m price disagrees with VWAP and EMA 9.")
    return OneMinuteConfirmation(status="mixed", details="1m confirmation is mixed across VWAP and EMA 9.")


def _apply_forward_returns(evaluation: SetupEvaluation, one_min_frame: pd.DataFrame, config: AppConfig | None = None) -> None:
    current_rows = one_min_frame[one_min_frame["timestamp"] <= pd.Timestamp(evaluation.timestamp)]
    if current_rows.empty:
        return
    current_price = float(current_rows.iloc[-1]["close"])
    sign = 1.0 if evaluation.direction == Direction.BULL else -1.0
    for minutes in FORWARD_RETURN_HORIZONS:
        target = pd.Timestamp(evaluation.timestamp) + timedelta(minutes=minutes)
        future_rows = one_min_frame[one_min_frame["timestamp"] >= target]
        if future_rows.empty:
            continue
        future_price = float(future_rows.iloc[0]["close"])
        adjusted_return = sign * ((future_price - current_price) / current_price)
        setattr(evaluation, f"forward_return_{minutes}m", adjusted_return)

    _apply_pop_outcome(evaluation, config)


def _apply_pop_outcome(evaluation: SetupEvaluation, config: AppConfig | None = None) -> None:
    outcomes = config.outcomes if config is not None else OutcomeConfig()
    threshold = outcomes.pop_threshold_pct / 100.0
    outcome_returns = {minutes: getattr(evaluation, f"forward_return_{minutes}m") for minutes in OUTCOME_HORIZONS}
    if any(value is None for value in outcome_returns.values()):
        evaluation.pop_outcome = None
        evaluation.pop_outcome_horizon = None
        evaluation.pop_grade = None
        return

    for minutes in OUTCOME_HORIZONS:
        value = outcome_returns[minutes]
        if value is None:
            continue
        if value >= threshold:
            evaluation.pop_outcome = OutcomeResult.WIN
            evaluation.pop_outcome_horizon = f"{minutes}m"
            evaluation.pop_grade = _grade_pop_strength(max(outcome_returns.values()), outcomes)
            return
        if value <= -threshold:
            evaluation.pop_outcome = OutcomeResult.LOSS
            evaluation.pop_outcome_horizon = f"{minutes}m"
            evaluation.pop_grade = None
            return

    evaluation.pop_outcome = OutcomeResult.FLAT
    evaluation.pop_outcome_horizon = None
    evaluation.pop_grade = None


def _grade_pop_strength(max_favorable_return: float, outcomes: OutcomeConfig) -> OutcomeGrade:
    if max_favorable_return >= outcomes.pop_grade_a_pct / 100.0:
        return OutcomeGrade.A
    if max_favorable_return >= outcomes.pop_grade_b_pct / 100.0:
        return OutcomeGrade.B
    return OutcomeGrade.C


def _relation(price: float, reference: float | None) -> str:
    if reference is None:
        return "unknown"
    return "above" if price > reference else "below_or_equal"


def _sma_relation(sma15: float | None, sma30: float | None) -> str:
    if sma15 is None or sma30 is None:
        return "unknown"
    return "bullish" if sma15 > sma30 else "bearish_or_flat"


def _rvgi_relation(rvgi: float | None, rvgi_sma: float | None) -> str:
    if rvgi is None or rvgi_sma is None:
        return "unknown"
    return "above" if rvgi > rvgi_sma else "below_or_equal"


def _sign_label(value: float | None) -> str:
    if value is None:
        return "unknown"
    return "positive" if value > 0 else "negative_or_zero"
