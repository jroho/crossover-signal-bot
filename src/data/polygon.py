from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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
        results = self._get_aggregate_results(
            symbol=symbol,
            multiplier=multiplier,
            timespan=timespan,
            start_date=start.date(),
            end_date=end.date(),
        )
        return [self._result_to_candle(symbol, timeframe, result) for result in results]

    def get_single_day_aggregate_rows(
        self,
        symbol: str,
        day: date,
        multiplier: int,
    ) -> list[dict[str, object]]:
        if multiplier < 1:
            raise ValueError("Multiplier must be greater than or equal to 1.")

        results = self._get_aggregate_results(
            symbol=symbol,
            multiplier=multiplier,
            timespan="minute",
            start_date=day,
            end_date=day,
        )
        return [self._result_to_aggregate_row(result) for result in results]

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

    def _get_aggregate_results(
        self,
        symbol: str,
        multiplier: int,
        timespan: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, object]]:
        url = (
            f"{self.config.polygon.base_url}/v2/aggs/ticker/{symbol.upper()}/range/"
            f"{multiplier}/{timespan}/{start_date}/{end_date}"
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
        return payload.get("results", [])

    @staticmethod
    def _result_to_candle(symbol: str, timeframe: Timeframe, payload: dict[str, object]) -> Candle:
        return Candle(
            symbol=symbol.upper(),
            timeframe=timeframe,
            timestamp=datetime.fromtimestamp(float(payload["t"]) / 1000, tz=UTC),
            open=float(payload["o"]),
            high=float(payload["h"]),
            low=float(payload["l"]),
            close=float(payload["c"]),
            volume=float(payload["v"]),
        )

    @staticmethod
    def _result_to_aggregate_row(payload: dict[str, object]) -> dict[str, object]:
        return {
            "t": payload.get("t"),
            "o": payload.get("o"),
            "h": payload.get("h"),
            "l": payload.get("l"),
            "c": payload.get("c"),
            "v": payload.get("v"),
            "vw": payload.get("vw"),
            "n": payload.get("n"),
        }
