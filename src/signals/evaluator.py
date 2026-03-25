from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

import pandas as pd

from src.config import AppConfig
from src.grading import grade_setup
from src.indicators import IndicatorBundle, build_indicator_bundle
from src.models import Candle, Direction, Grade, IndicatorState, OneMinuteConfirmation, SetupEvaluation, StrikeBias, Timeframe


def evaluate_symbol(candles: list[Candle], config: AppConfig) -> tuple[list[SetupEvaluation], IndicatorBundle, IndicatorBundle]:
    one_min_bundle, five_min_bundle = build_indicator_bundle(candles, config)
    evaluations: list[SetupEvaluation] = []
    five_min_by_symbol: dict[str, list[Candle]] = defaultdict(list)
    for candle in five_min_bundle.candles:
        five_min_by_symbol[candle.symbol].append(candle)

    for symbol, symbol_candles in five_min_by_symbol.items():
        one_min_frame = one_min_bundle.dataframe[one_min_bundle.dataframe["symbol"] == symbol].copy()
        for index, candle in enumerate(symbol_candles):
            state = five_min_bundle.states[(symbol, pd.Timestamp(candle.timestamp))]
            previous = None
            if index > 0:
                previous = five_min_bundle.states[(symbol, pd.Timestamp(symbol_candles[index - 1].timestamp))]
            sma_cross_signal = _sma_cross_signal(state, previous)

            for direction in (Direction.BULL, Direction.BEAR):
                one_min_confirmation = _derive_one_min_confirmation(
                    one_min_frame=one_min_frame,
                    timestamp=pd.Timestamp(candle.timestamp),
                    direction=direction,
                    config=config,
                )
                evaluation = SetupEvaluation(
                    symbol=symbol,
                    timestamp=candle.timestamp,
                    timeframe=Timeframe.FIVE_MINUTE,
                    direction=direction,
                    last_price=candle.close,
                    vwap_relation=_relation(candle.close, state.vwap),
                    ema9_relation=_relation(candle.close, state.ema9),
                    sma15_value=state.sma15,
                    sma30_value=state.sma30,
                    sma_trend_relation=_sma_relation(state.sma15, state.sma30),
                    sma_cross_signal=sma_cross_signal,
                    rvgi=state.rvgi,
                    rvgi_sma=state.rvgi_sma,
                    rvgi_vs_sma=_rvgi_relation(state.rvgi, state.rvgi_sma),
                    rvgi_sign=_sign_label(state.rvgi),
                    volume=candle.volume,
                    recent_volume_avg=state.recent_volume_avg,
                    rolling_volume_avg=state.rolling_volume_avg,
                    volume_grade=state.volume_grade.value,
                    one_min_agreement=one_min_confirmation.status,
                    grade=Grade.C,
                    strike_bias=StrikeBias.SKIP,
                    strike_bias_reason="",
                )
                evaluation = grade_setup(evaluation, state, previous, one_min_confirmation, config)
                _apply_forward_returns(evaluation, one_min_frame)
                evaluations.append(evaluation)
    evaluations.sort(key=lambda item: (item.symbol, item.timestamp, item.direction.value))
    return evaluations, one_min_bundle, five_min_bundle


# The 5m 15/30 crossover is the primary trigger; 1m remains confirmation-only.
def _sma_cross_signal(current: IndicatorState, previous: IndicatorState | None) -> str:
    if previous is None:
        return "none"
    if any(value is None for value in (current.sma15, current.sma30, previous.sma15, previous.sma30)):
        return "warmup"
    if float(previous.sma15) <= float(previous.sma30) and float(current.sma15) > float(current.sma30):
        return Direction.BULL.value
    if float(previous.sma15) >= float(previous.sma30) and float(current.sma15) < float(current.sma30):
        return Direction.BEAR.value
    return "none"


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


def _apply_forward_returns(evaluation: SetupEvaluation, one_min_frame: pd.DataFrame) -> None:
    current_rows = one_min_frame[one_min_frame["timestamp"] <= pd.Timestamp(evaluation.timestamp)]
    if current_rows.empty:
        return
    current_price = float(current_rows.iloc[-1]["close"])
    sign = 1.0 if evaluation.direction == Direction.BULL else -1.0
    for minutes in (3, 5, 10, 15):
        target = pd.Timestamp(evaluation.timestamp) + timedelta(minutes=minutes)
        future_rows = one_min_frame[one_min_frame["timestamp"] >= target]
        if future_rows.empty:
            continue
        future_price = float(future_rows.iloc[0]["close"])
        adjusted_return = sign * ((future_price - current_price) / current_price)
        setattr(evaluation, f"forward_return_{minutes}m", adjusted_return)


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
