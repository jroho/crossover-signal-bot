from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.context import DayContext, context_for_day, fetch_vix_history, load_vix_history, vix_regime
from src.config import AppConfig
from src.data import AlpacaAdapter

BIAS_VALUES = {"bull": 1, "bear": -1, "neutral": 0}


def build_live_context(
    config: AppConfig,
    adapter: AlpacaAdapter,
    symbol: str,
    today: date,
    *,
    vix_path: str | Path = "data/context/VIX_History.csv",
    refresh_vix: bool = True,
    override: str | None = None,
    session: Any | None = None,
) -> DayContext:
    """Yesterday-and-earlier daily closes give the data trend bias; the VIX file gives the regime.

    A manual override (bull / bear / neutral) replaces the data bias so a headline-driven call made before the
    open takes precedence, but the data bias is still logged for comparison.
    """
    end = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    start = end - timedelta(days=60)
    daily = _daily_closes(adapter, symbol, start, end, config)
    vix_file = Path(vix_path)
    if refresh_vix:
        try:
            fetch_vix_history(vix_file, session=session)
        except Exception:  # noqa: BLE001 - a stale VIX file is better than no context
            pass
    vix = load_vix_history(vix_file)
    context = context_for_day(daily, vix, today)
    if override:
        label = override.strip().lower()
        if label not in BIAS_VALUES:
            raise ValueError(f"daily_bias_override must be bull, bear or neutral, not '{override}'")
        context = DayContext(
            vix_prev_close=context.vix_prev_close,
            vix_open=context.vix_open,
            vix_regime=vix_regime(context.vix_prev_close),
            trend_bias=BIAS_VALUES[label],
            trend_label=label,
            opening_range_pct=None,
            opening_range_ratio=None,
            turbulence="unknown",
        )
    return context


def _daily_closes(adapter: AlpacaAdapter, symbol: str, start: datetime, end: datetime, config: AppConfig) -> pd.DataFrame:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    request = StockBarsRequest(
        symbol_or_symbols=symbol.upper(),
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed(config.alpaca.historical_feed),
        adjustment=Adjustment(config.alpaca.adjustment),
    )
    bars = adapter.client.get_stock_bars(request).data.get(symbol.upper(), [])
    rows = [{"date": bar.timestamp.date(), "open": float(bar.open), "high": float(bar.high), "low": float(bar.low), "close": float(bar.close), "opening_range_pct": None} for bar in bars]
    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "opening_range_pct"])
