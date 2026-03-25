from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

from src.config import AppConfig
from src.models import Candle, IndicatorState, Timeframe, VolumeGrade


IndicatorKey = tuple[str, pd.Timestamp]


@dataclass(frozen=True)
class IndicatorBundle:
    candles: list[Candle]
    states: dict[IndicatorKey, IndicatorState]
    dataframe: pd.DataFrame


def candles_to_dataframe(candles: list[Candle]) -> pd.DataFrame:
    rows = [
        {
            "symbol": candle.symbol,
            "timeframe": candle.timeframe.value,
            "timestamp": pd.Timestamp(candle.timestamp),
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in candles
    ]
    if not rows:
        return pd.DataFrame(columns=["symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows).sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def resample_to_five_minute(candles: list[Candle]) -> list[Candle]:
    frame = candles_to_dataframe(candles)
    if frame.empty:
        return []

    result_frames: list[pd.DataFrame] = []
    for symbol, group in frame.groupby("symbol", sort=True):
        resampled = (
            group.set_index("timestamp")
            .resample("5min", label="right", closed="left")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
                sample_count=("close", "count"),
            )
            .dropna(subset=["open", "high", "low", "close"])
        )
        resampled = resampled[resampled["sample_count"] == 5].reset_index()
        resampled["symbol"] = symbol
        result_frames.append(resampled)

    if not result_frames:
        return []

    merged = pd.concat(result_frames, ignore_index=True).sort_values(["symbol", "timestamp"])
    return [
        Candle(
            symbol=row.symbol,
            timeframe=Timeframe.FIVE_MINUTE,
            timestamp=row.timestamp.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in merged.itertuples(index=False)
    ]


def resample_to_active_five_minute(candles: list[Candle]) -> list[Candle]:
    frame = candles_to_dataframe(candles)
    if frame.empty:
        return []

    result_frames: list[pd.DataFrame] = []
    for symbol, group in frame.groupby("symbol", sort=True):
        grouped = group.copy()
        grouped["bucket_start"] = grouped["timestamp"].dt.floor("5min")
        snapshots = (
            grouped.groupby("bucket_start", sort=True)
            .agg(
                timestamp=("timestamp", "max"),
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .reset_index(drop=True)
        )
        snapshots["symbol"] = symbol
        result_frames.append(snapshots)

    if not result_frames:
        return []

    merged = pd.concat(result_frames, ignore_index=True).sort_values(["symbol", "timestamp"])
    return [
        Candle(
            symbol=row.symbol,
            timeframe=Timeframe.FIVE_MINUTE,
            timestamp=row.timestamp.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in merged.itertuples(index=False)
    ]


def compute_indicator_states(candles: list[Candle], config: AppConfig) -> IndicatorBundle:
    frame = candles_to_dataframe(candles)
    if frame.empty:
        return IndicatorBundle(candles=[], states={}, dataframe=frame)

    prepared_frames: list[pd.DataFrame] = []
    for _, group in frame.groupby("symbol", sort=True):
        prepared_frames.append(_compute_symbol_indicators(group.copy(), config))

    merged = pd.concat(prepared_frames, ignore_index=True).sort_values(["symbol", "timestamp"])
    states: dict[IndicatorKey, IndicatorState] = {}
    final_candles: list[Candle] = []
    for row in merged.itertuples(index=False):
        timestamp = pd.Timestamp(row.timestamp)
        key = (row.symbol, timestamp)
        state = IndicatorState(
            symbol=row.symbol,
            timeframe=Timeframe(row.timeframe),
            timestamp=timestamp.to_pydatetime(),
            vwap=_nullable_float(row.vwap),
            ema9=_nullable_float(row.ema9),
            sma15=_nullable_float(row.sma15),
            sma30=_nullable_float(row.sma30),
            rvgi=_nullable_float(row.rvgi),
            rvgi_sma=_nullable_float(row.rvgi_sma),
            recent_volume_avg=_nullable_float(row.recent_volume_avg),
            rolling_volume_avg=_nullable_float(row.rolling_volume_avg),
            volume_grade=VolumeGrade(row.volume_grade),
        )
        states[key] = state
        final_candles.append(
            Candle(
                symbol=row.symbol,
                timeframe=Timeframe(row.timeframe),
                timestamp=timestamp.to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
        )
    return IndicatorBundle(candles=final_candles, states=states, dataframe=merged)


def build_indicator_bundle(one_minute_candles: list[Candle], config: AppConfig) -> tuple[IndicatorBundle, IndicatorBundle]:
    one_minute_bundle = compute_indicator_states(one_minute_candles, config)
    five_minute_candles = resample_to_five_minute(one_minute_candles)
    five_minute_bundle = compute_indicator_states(five_minute_candles, config)
    return one_minute_bundle, five_minute_bundle


def _compute_symbol_indicators(frame: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    timeframe = frame["timeframe"].iloc[0]
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["market_date"] = frame["timestamp"].dt.tz_convert(ZoneInfo(config.app.market_timezone)).dt.date

    typical_price = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    cumulative_pv = (typical_price * frame["volume"]).groupby(frame["market_date"]).cumsum()
    cumulative_volume = frame["volume"].groupby(frame["market_date"]).cumsum()
    frame["vwap"] = cumulative_pv / cumulative_volume.where(cumulative_volume != 0)

    frame["ema9"] = frame["close"].ewm(span=config.indicators.ema_length, adjust=False).mean()
    frame["sma15"] = frame["close"].rolling(config.indicators.sma_fast_length).mean()
    frame["sma30"] = frame["close"].rolling(config.indicators.sma_slow_length).mean()

    price_delta = frame["close"] - frame["open"]
    range_delta = (frame["high"] - frame["low"]).where((frame["high"] - frame["low"]) != 0)
    frame["rvgi"] = (
        price_delta.rolling(config.indicators.rvgi_length).mean()
        / range_delta.rolling(config.indicators.rvgi_length).mean()
    )
    frame["rvgi_sma"] = frame["rvgi"].rolling(config.indicators.rvgi_signal_length).mean()

    frame["recent_volume_avg"] = frame["volume"].shift(1).rolling(config.volume.prior_window).mean()
    if config.volume.use_rolling_average and config.volume.rolling_window > 0:
        frame["rolling_volume_avg"] = frame["volume"].shift(1).rolling(config.volume.rolling_window).mean()
    else:
        frame["rolling_volume_avg"] = pd.NA

    volume_grades: list[str] = []
    for index in range(len(frame)):
        prior_window = frame["volume"].iloc[max(0, index - config.volume.prior_window) : index].tolist()
        current_volume = float(frame["volume"].iloc[index])
        rolling_avg = _nullable_float(frame["rolling_volume_avg"].iloc[index])
        volume_grades.append(
            _classify_volume(
                current_volume=current_volume,
                prior_volumes=prior_window,
                rolling_avg=rolling_avg,
                config=config,
            ).value
        )
    frame["volume_grade"] = volume_grades
    frame["timeframe"] = timeframe
    return frame


def _classify_volume(
    current_volume: float,
    prior_volumes: list[float],
    rolling_avg: float | None,
    config: AppConfig,
) -> VolumeGrade:
    if len(prior_volumes) < config.volume.prior_window:
        return VolumeGrade.INSUFFICIENT

    sorted_prior = sorted(prior_volumes, reverse=True)
    threshold_index = min(config.volume.top_n_strong - 1, len(sorted_prior) - 1)
    strong_from_prior = current_volume >= sorted_prior[threshold_index]

    ratios = []
    recent_avg = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0
    if recent_avg > 0:
        ratios.append(current_volume / recent_avg)
    if rolling_avg is not None and rolling_avg > 0:
        ratios.append(current_volume / rolling_avg)

    if strong_from_prior or any(ratio > config.volume.strong_ratio for ratio in ratios):
        return VolumeGrade.STRONG
    if any(ratio >= config.volume.acceptable_ratio for ratio in ratios):
        return VolumeGrade.ACCEPTABLE
    return VolumeGrade.WEAK


def _nullable_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
