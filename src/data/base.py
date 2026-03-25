from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.models import Candle, Timeframe


class MarketDataAdapter(ABC):
    @abstractmethod
    def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        raise NotImplementedError

    @abstractmethod
    def get_latest_closed_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
    ) -> list[Candle]:
        raise NotImplementedError
