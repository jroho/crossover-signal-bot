from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from src.models import Candle, Timeframe


class CsvReplayAdapter:
    """Loads local 1m candles for deterministic replay."""

    def load_candles(self, path: str | Path, symbols: list[str] | None = None) -> list[Candle]:
        selected = {symbol.upper() for symbol in symbols or []}
        candles: list[Candle] = []
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                symbol = row["symbol"].upper()
                if selected and symbol not in selected:
                    continue
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=Timeframe.ONE_MINUTE,
                        timestamp=self._parse_timestamp(row["timestamp"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                )
        candles.sort(key=lambda candle: (candle.symbol, candle.timestamp))
        return candles

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        cleaned = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
