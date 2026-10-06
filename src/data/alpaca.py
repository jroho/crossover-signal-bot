from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from src.config import AppConfig
from src.data.base import MarketDataAdapter
from src.models import Candle, Timeframe

# The free Alpaca data plan withholds the most recent 15 minutes of consolidated (SIP) bars,
# so any window that reaches into that span has to come from the real-time IEX feed instead.
RECENT_WINDOW = timedelta(minutes=15)
# Minutes fetched from both feeds to measure the SIP/IEX volume ratio at the seam.
SEAM_OVERLAP = timedelta(minutes=30)


def stitch_session_candles(older: list[Candle], recent: list[Candle], cutoff: datetime, *, default_scale: float) -> list[Candle]:
    """Join consolidated bars (before the cutoff) with real-time bars (after it), scaling the latter's volume."""
    older_by_time = {candle.timestamp: candle for candle in older if candle.timestamp < cutoff}
    sip_volume = 0.0
    iex_volume = 0.0
    for candle in recent:
        match = older_by_time.get(candle.timestamp)
        if match is not None and candle.volume > 0:
            sip_volume += match.volume
            iex_volume += candle.volume
    scale = sip_volume / iex_volume if iex_volume > 0 and sip_volume > 0 else default_scale
    scale = max(1.0, scale)
    stitched = list(older_by_time.values())
    for candle in recent:
        if candle.timestamp >= cutoff:
            stitched.append(
                Candle(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe,
                    timestamp=candle.timestamp,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=round(candle.volume * scale, 2),
                )
            )
    stitched.sort(key=lambda candle: candle.timestamp)
    return stitched


class BarsClient(Protocol):
    def get_stock_bars(self, request: Any) -> Any: ...


class OptionBarsClient(Protocol):
    def get_option_bars(self, request: Any) -> Any: ...


class AlpacaAdapter(MarketDataAdapter):
    """Alpaca market data: consolidated bars for backfills, real-time IEX bars for the live window."""

    def __init__(
        self,
        config: AppConfig,
        client: BarsClient | None = None,
        option_client: OptionBarsClient | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._option_client = option_client
        self.market_timezone = ZoneInfo(config.app.market_timezone)

    @property
    def client(self) -> BarsClient:
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient

            self._client = StockHistoricalDataClient(
                api_key=self.config.alpaca.api_key_id,
                secret_key=self.config.alpaca.api_secret_key,
            )
        return self._client

    @property
    def option_client(self) -> OptionBarsClient:
        if self._option_client is None:
            from alpaca.data.historical import OptionHistoricalDataClient

            self._option_client = OptionHistoricalDataClient(
                api_key=self.config.alpaca.api_key_id,
                secret_key=self.config.alpaca.api_secret_key,
            )
        return self._option_client

    def get_option_bars(
        self,
        occ_symbols: list[str],
        start: datetime,
        end: datetime,
        timeframe: Timeframe = Timeframe.ONE_MINUTE,
    ) -> dict[str, list[Any]]:
        """Raw OPRA bars keyed by OCC symbol; contracts that never traded are simply absent."""
        if not occ_symbols:
            return {}
        from alpaca.data.requests import OptionBarsRequest

        request = OptionBarsRequest(
            symbol_or_symbols=list(occ_symbols),
            timeframe=self._alpaca_timeframe(timeframe),
            start=start,
            end=end,
        )
        bar_set = self.option_client.get_option_bars(request)
        return {symbol: sorted(bars, key=lambda bar: bar.timestamp) for symbol, bars in bar_set.data.items()}

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        feed: str | None = None,
    ) -> list[Candle]:
        bars = self._fetch_bars(symbol, timeframe, start, end, feed)
        return [self._bar_to_candle(symbol, timeframe, bar) for bar in bars]

    def get_session_candles(self, symbol: str, session_start: datetime, now: datetime) -> list[Candle]:
        """The whole session so far: consolidated bars up to 15 minutes ago, real-time IEX bars after that.

        The free plan withholds recent SIP data, but a sliding IEX-only window hides older crossovers and
        starves the SMA warm-up. IEX volume is a slice of the tape, so it is scaled by the SIP/IEX ratio
        measured on the overlap, keeping the volume grade comparable across the seam.
        """
        if now <= session_start:
            return []  # polling before the session opens: nothing to fetch, and Alpaca rejects end < start
        cutoff = now - RECENT_WINDOW - timedelta(minutes=1)
        if cutoff <= session_start:
            return self.get_historical_candles(symbol, Timeframe.ONE_MINUTE, session_start, now, feed=self.config.alpaca.live_feed)
        try:
            older = self.get_historical_candles(symbol, Timeframe.ONE_MINUTE, session_start, cutoff, feed=self.config.alpaca.historical_feed)
        except Exception:  # noqa: BLE001 - if SIP refuses the window, trade on IEX rather than stop
            return self.get_historical_candles(symbol, Timeframe.ONE_MINUTE, session_start, now, feed=self.config.alpaca.live_feed)
        overlap_start = max(session_start, cutoff - SEAM_OVERLAP)
        recent = self.get_historical_candles(symbol, Timeframe.ONE_MINUTE, overlap_start, now, feed=self.config.alpaca.live_feed)
        return stitch_session_candles(older, recent, cutoff, default_scale=self.config.alpaca.iex_volume_scale)

    def get_single_day_aggregate_rows(
        self,
        symbol: str,
        day: date,
        multiplier: int,
    ) -> list[dict[str, object]]:
        """One market day of bars (premarket through after-hours) in the aggregate row shape fetch-day exports."""
        timeframe = self._timeframe_for_multiplier(multiplier)
        start = datetime.combine(day, time.min, tzinfo=self.market_timezone)
        end = start + timedelta(days=1)
        bars = self._fetch_bars(symbol, timeframe, start, end, feed=self.config.alpaca.historical_feed)
        return [self._bar_to_aggregate_row(bar) for bar in bars]

    def get_latest_closed_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
    ) -> list[Candle]:
        now = datetime.now(tz=UTC)
        start = now - timedelta(minutes=max(limit * 5, 60))
        candles = self.get_historical_candles(symbol, timeframe, start, now)
        return candles[-limit:]

    def select_feed(self, end: datetime, now: datetime | None = None) -> str:
        current = now or datetime.now(tz=UTC)
        if end >= current - RECENT_WINDOW:
            return self.config.alpaca.live_feed
        return self.config.alpaca.historical_feed

    def _fetch_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        feed: str | None,
    ) -> list[Any]:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest

        normalized_symbol = symbol.upper()
        request = StockBarsRequest(
            symbol_or_symbols=normalized_symbol,
            timeframe=self._alpaca_timeframe(timeframe),
            start=start,
            end=end,
            feed=DataFeed(feed or self.select_feed(end)),
            adjustment=Adjustment(self.config.alpaca.adjustment),
        )
        bar_set = self.client.get_stock_bars(request)
        bars = list(bar_set.data.get(normalized_symbol, []))
        bars.sort(key=lambda bar: bar.timestamp)
        return bars

    @staticmethod
    def _alpaca_timeframe(timeframe: Timeframe) -> Any:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        if timeframe == Timeframe.ONE_MINUTE:
            return TimeFrame(1, TimeFrameUnit.Minute)
        if timeframe == Timeframe.FIVE_MINUTE:
            return TimeFrame(5, TimeFrameUnit.Minute)
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    @staticmethod
    def _timeframe_for_multiplier(multiplier: int) -> Timeframe:
        if multiplier == 1:
            return Timeframe.ONE_MINUTE
        if multiplier == 5:
            return Timeframe.FIVE_MINUTE
        raise ValueError("Multiplier must be 1 or 5 minutes.")

    @staticmethod
    def _bar_to_candle(symbol: str, timeframe: Timeframe, bar: Any) -> Candle:
        return Candle(
            symbol=symbol.upper(),
            timeframe=timeframe,
            timestamp=_as_utc(bar.timestamp),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )

    @staticmethod
    def _bar_to_aggregate_row(bar: Any) -> dict[str, object]:
        return {
            "t": int(_as_utc(bar.timestamp).timestamp() * 1000),
            "o": bar.open,
            "h": bar.high,
            "l": bar.low,
            "c": bar.close,
            "v": bar.volume,
            "vw": bar.vwap,
            "n": bar.trade_count,
        }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
