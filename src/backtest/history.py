from __future__ import annotations

import csv
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import AppConfig
from src.data import AlpacaAdapter, CsvReplayAdapter
from src.data.alpaca import _as_utc
from src.market_hours import is_within_market_hours, parse_clock_time
from src.models import Candle, Direction, Timeframe
from src.signals import evaluate_symbol

from .strikes import STRIKE_LABELS, StrikeChoice, select_strikes

UNDERLYING_FIELDNAMES = ["timestamp", "open", "high", "low", "close", "volume", "symbol"]
OPTION_FIELDNAMES = ["occ_symbol", "underlying", "expiration", "option_type", "strike", "timestamp", "open", "high", "low", "close", "volume", "trade_count", "vwap"]
EPISODE_FIELDNAMES = [
    "date",
    "symbol",
    "direction",
    "cross_time",
    "detection_time",
    "spot",
    "sma_cross_lag_min",
    "bar_minutes_elapsed",
    "volume_grade",
    *[f"{label.lower()}_symbol" for label in STRIKE_LABELS],
]
# QQQ/SPY options trade until 4:15 PM ET; bars are fetched from shortly before the first cross to that close.
OPTION_SESSION_END = dt_time(16, 15)
OPTION_LOOKBACK = timedelta(minutes=15)


@dataclass(frozen=True)
class CrossEpisode:
    day: date
    symbol: str
    direction: Direction
    cross_time: datetime
    detection_time: datetime
    spot: float
    sma_cross_lag_min: float | None
    bar_minutes_elapsed: int | None
    volume_grade: str
    strikes: tuple[StrikeChoice, ...]


def split_by_market_date(candles: list[Candle], market_timezone: ZoneInfo) -> dict[date, list[Candle]]:
    by_day: dict[date, list[Candle]] = {}
    for candle in sorted(candles, key=lambda item: item.timestamp):
        by_day.setdefault(candle.timestamp.astimezone(market_timezone).date(), []).append(candle)
    return by_day


def find_cross_episodes(candles: list[Candle], config: AppConfig) -> list[CrossEpisode]:
    """Every regular-hours 5m SMA 15/30 cross of the day, graded or not, with the five candidate 0DTE strikes."""
    if not candles:
        return []
    market_timezone = ZoneInfo(config.app.market_timezone)
    market_open = parse_clock_time(config.live.market_open_time, field_name="live.market_open_time")
    market_close = parse_clock_time(config.live.market_close_time, field_name="live.market_close_time")
    evaluations, _, _ = evaluate_symbol(candles, config)

    episodes: list[CrossEpisode] = []
    seen: set[tuple[str, datetime]] = set()
    for evaluation in evaluations:
        if evaluation.sma_cross_status != "fresh" or evaluation.sma_cross_time is None:
            continue
        # Both directions carry the same cross context; keep the row whose direction matches the signal.
        if evaluation.direction.value != evaluation.sma_cross_signal:
            continue
        key = (evaluation.symbol, evaluation.sma_cross_time)
        if key in seen or not is_within_market_hours(evaluation.timestamp, market_timezone, market_open, market_close):
            continue
        seen.add(key)
        day = evaluation.timestamp.astimezone(market_timezone).date()
        episodes.append(
            CrossEpisode(
                day=day,
                symbol=evaluation.symbol,
                direction=evaluation.direction,
                cross_time=evaluation.sma_cross_time,
                detection_time=evaluation.timestamp,
                spot=evaluation.last_price,
                sma_cross_lag_min=evaluation.sma_cross_lag_min,
                bar_minutes_elapsed=evaluation.bar_minutes_elapsed,
                volume_grade=evaluation.volume_grade,
                strikes=tuple(select_strikes(evaluation.symbol, day, evaluation.direction, evaluation.last_price)),
            )
        )
    return episodes


class HistoryPuller:
    """Pulls underlying 1m history and the 0DTE option bars behind each cross episode into per-day CSVs."""

    def __init__(
        self,
        config: AppConfig,
        adapter: AlpacaAdapter,
        out_dir: str | Path,
        *,
        sleep_seconds: float = 0.35,
        printer: Callable[[str], None] = print,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.adapter = adapter
        self.out_dir = Path(out_dir)
        self.sleep_seconds = sleep_seconds
        self.printer = printer
        self.sleeper = sleeper
        self.market_timezone = ZoneInfo(config.app.market_timezone)

    def underlying_path(self, symbol: str, day: date) -> Path:
        return self.out_dir / "underlying" / symbol.upper() / f"{symbol.upper()}_1minute_{day.isoformat()}.csv"

    def options_path(self, symbol: str, day: date) -> Path:
        return self.out_dir / "options" / symbol.upper() / f"{symbol.upper()}_options_{day.isoformat()}.csv"

    def episodes_path(self, symbol: str, day: date) -> Path:
        return self.out_dir / "episodes" / symbol.upper() / f"{symbol.upper()}_episodes_{day.isoformat()}.csv"

    def pull_underlying(self, symbol: str, start: date, end: date) -> list[Path]:
        written: list[Path] = []
        for chunk_start, chunk_end in _month_chunks(start, end):
            if all(self.underlying_path(symbol, day).exists() for day in _weekdays(chunk_start, chunk_end)):
                self.printer(f"{symbol} {chunk_start:%Y-%m}: already on disk, skipping")
                continue
            window_start = datetime.combine(chunk_start, dt_time.min, tzinfo=self.market_timezone)
            window_end = datetime.combine(chunk_end + timedelta(days=1), dt_time.min, tzinfo=self.market_timezone)
            # Consolidated bars inside the last 15 minutes are refused on the free plan; clamp the request.
            window_end = min(window_end, datetime.now(tz=UTC) - timedelta(minutes=16))
            candles = self.adapter.get_historical_candles(
                symbol,
                Timeframe.ONE_MINUTE,
                window_start,
                window_end,
                feed=self.config.alpaca.historical_feed,
            )
            by_day = split_by_market_date(candles, self.market_timezone)
            for day, day_candles in by_day.items():
                path = self.underlying_path(symbol, day)
                if path.exists():
                    continue
                _write_underlying_csv(path, day_candles)
                written.append(path)
            self.printer(f"{symbol} {chunk_start:%Y-%m}: {len(by_day)} days, {len(candles)} bars")
            self.sleeper(self.sleep_seconds)
        return written

    def pull_options(self, symbol: str, start: date, end: date) -> list[Path]:
        written: list[Path] = []
        loader = CsvReplayAdapter()
        for day in _weekdays(start, end):
            underlying_path = self.underlying_path(symbol, day)
            if not underlying_path.exists():
                continue
            options_path = self.options_path(symbol, day)
            if options_path.exists():
                continue
            try:
                candles = loader.load_candles(underlying_path, [symbol])
                episodes = find_cross_episodes(candles, self.config)
                _write_episodes_csv(self.episodes_path(symbol, day), episodes)
                choices = {choice.occ_symbol: choice for episode in episodes for choice in episode.strikes}
                bars_by_symbol: dict[str, list[object]] = {}
                if choices:
                    bars_start = min(episode.detection_time for episode in episodes) - OPTION_LOOKBACK
                    bars_end = datetime.combine(day, OPTION_SESSION_END, tzinfo=self.market_timezone)
                    bars_by_symbol = self.adapter.get_option_bars(sorted(choices), bars_start, bars_end)
                    self.sleeper(self.sleep_seconds)
                bar_count = _write_options_csv(options_path, symbol, day, choices, bars_by_symbol)
                written.append(options_path)
                self.printer(f"{symbol} {day}: {len(episodes)} episodes, {len(choices)} contracts, {bar_count} option bars")
            except Exception as exc:  # noqa: BLE001 - one bad day must not abort a multi-hour pull
                self._log_error(symbol, day, exc)
                self.printer(f"{symbol} {day}: FAILED ({type(exc).__name__}); see {self.out_dir / 'pull_errors.log'}")
        return written

    def _log_error(self, symbol: str, day: date, exc: Exception) -> None:
        log_path = self.out_dir / "pull_errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(tz=UTC).isoformat()} {symbol} {day}: {type(exc).__name__}: {exc}\n")
            handle.write(traceback.format_exc())


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        chunk_end = min(next_month - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        cursor = next_month
    return chunks


def _weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _format_timestamp(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _write_underlying_csv(path: Path, candles: list[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=UNDERLYING_FIELDNAMES)
        writer.writeheader()
        for candle in candles:
            writer.writerow(
                {
                    "timestamp": _format_timestamp(candle.timestamp),
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "symbol": candle.symbol,
                }
            )


def _write_episodes_csv(path: Path, episodes: list[CrossEpisode]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EPISODE_FIELDNAMES)
        writer.writeheader()
        for episode in episodes:
            row = {
                "date": episode.day.isoformat(),
                "symbol": episode.symbol,
                "direction": episode.direction.value,
                "cross_time": _format_timestamp(episode.cross_time),
                "detection_time": _format_timestamp(episode.detection_time),
                "spot": episode.spot,
                "sma_cross_lag_min": episode.sma_cross_lag_min,
                "bar_minutes_elapsed": episode.bar_minutes_elapsed,
                "volume_grade": episode.volume_grade,
            }
            for choice in episode.strikes:
                row[f"{choice.label.lower()}_symbol"] = choice.occ_symbol
            writer.writerow(row)


def _write_options_csv(
    path: Path,
    underlying: str,
    day: date,
    choices: dict[str, StrikeChoice],
    bars_by_symbol: dict[str, list[object]],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OPTION_FIELDNAMES)
        writer.writeheader()
        for occ_symbol in sorted(choices):
            choice = choices[occ_symbol]
            for bar in bars_by_symbol.get(occ_symbol, []):
                writer.writerow(
                    {
                        "occ_symbol": occ_symbol,
                        "underlying": underlying.upper(),
                        "expiration": day.isoformat(),
                        "option_type": choice.option_type,
                        "strike": choice.strike,
                        "timestamp": _format_timestamp(bar.timestamp),
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "trade_count": getattr(bar, "trade_count", None),
                        "vwap": getattr(bar, "vwap", None),
                    }
                )
                count += 1
    return count
