from __future__ import annotations

from datetime import UTC, datetime, timedelta

import requests

from src.config import AppConfig
from src.data.base import MarketDataAdapter
from src.models import Candle, Timeframe


class PolygonAdapter(MarketDataAdapter):
    def __init__(self, config: AppConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        multiplier, timespan = self._polygon_timespan(timeframe)
        url = (
            f"{self.config.polygon.base_url}/v2/aggs/ticker/{symbol.upper()}/range/"
            f"{multiplier}/{timespan}/{start.date()}/{end.date()}"
        )
        response = self.session.get(
            url,
            params={
                "adjusted": "true",
                "sort": "asc",
                "limit": 50000,
                "apiKey": self.config.polygon.api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        return [self._result_to_candle(symbol, timeframe, result) for result in results]

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

    @staticmethod
    def _polygon_timespan(timeframe: Timeframe) -> tuple[int, str]:
        if timeframe == Timeframe.ONE_MINUTE:
            return 1, "minute"
        if timeframe == Timeframe.FIVE_MINUTE:
            return 5, "minute"
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    @staticmethod
    def _result_to_candle(symbol: str, timeframe: Timeframe, payload: dict[str, float]) -> Candle:
        return Candle(
            symbol=symbol.upper(),
            timeframe=timeframe,
            timestamp=datetime.fromtimestamp(payload["t"] / 1000, tz=UTC),
            open=float(payload["o"]),
            high=float(payload["h"]),
            low=float(payload["l"]),
            close=float(payload["c"]),
            volume=float(payload["v"]),
        )
