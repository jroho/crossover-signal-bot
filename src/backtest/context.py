from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from src.models import Direction

VIX_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
VIX_REGIMES = ((15.0, "low"), (20.0, "mid"), (30.0, "high"), (float("inf"), "extreme"))
TURBULENCE_BUCKETS = ((0.75, "calm"), (1.5, "normal"), (float("inf"), "turbulent"))
MARKET_OPEN = dt_time(9, 30)
OPENING_RANGE_END = dt_time(9, 45)
MARKET_CLOSE = dt_time(16, 0)


@dataclass(frozen=True)
class DayContext:
    vix_prev_close: float | None = None
    vix_open: float | None = None
    vix_regime: str = "unknown"
    # +1 bull, -1 bear, 0 neutral; from prior days only, so it is known before the open.
    trend_bias: int = 0
    trend_label: str = "unknown"
    opening_range_pct: float | None = None
    opening_range_ratio: float | None = None
    turbulence: str = "unknown"


def vix_regime(level: float | None) -> str:
    if level is None or pd.isna(level):
        return "unknown"
    for upper, name in VIX_REGIMES:
        if level < upper:
            return name
    return "extreme"


def turbulence_bucket(ratio: float | None) -> str:
    if ratio is None or pd.isna(ratio):
        return "unknown"
    for upper, name in TURBULENCE_BUCKETS:
        if ratio < upper:
            return name
    return "turbulent"


def alignment(direction: Direction, trend_bias: int) -> str:
    if trend_bias == 0:
        return "neutral"
    is_bull_trade = direction == Direction.BULL
    return "aligned" if (trend_bias > 0) == is_bull_trade else "counter"


def fetch_vix_history(out_path: str | Path, session: Any | None = None) -> Path:
    """Download CBOE's daily VIX history (DATE,OPEN,HIGH,LOW,CLOSE) to a CSV."""
    http = session or requests.Session()
    response = http.get(VIX_HISTORY_URL, timeout=30)
    response.raise_for_status()
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(response.text, encoding="utf-8")
    return path


def load_vix_history(path: str | Path) -> pd.DataFrame | None:
    source = Path(path)
    if not source.exists():
        return None
    frame = pd.read_csv(source)
    frame.columns = [column.strip().lower() for column in frame.columns]
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame[["date", "open", "high", "low", "close"]].sort_values("date").reset_index(drop=True)


def build_daily_summary(data_dir: str | Path, symbol: str, market_timezone: ZoneInfo) -> pd.DataFrame:
    """Per-day open/high/low/close and opening-range width from the pulled 1m files, cached as CSV."""
    base = Path(data_dir)
    day_files = sorted((base / "underlying" / symbol.upper()).glob(f"{symbol.upper()}_1minute_*.csv"))
    cache_path = base / "context" / f"{symbol.upper()}_daily.csv"
    if cache_path.exists():
        cached = pd.read_csv(cache_path)
        if len(cached) == len(day_files):
            cached["date"] = pd.to_datetime(cached["date"]).dt.date
            return cached
    rows: list[dict[str, object]] = []
    for path in day_files:
        rows.append(_summarize_day_file(path, market_timezone))
    summary = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "opening_range_pct"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(cache_path, index=False)
    summary["date"] = pd.to_datetime(summary["date"]).dt.date
    return summary


def _summarize_day_file(path: Path, market_timezone: ZoneInfo) -> dict[str, object]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    regular: list[dict[str, str]] = []
    opening: list[dict[str, str]] = []
    day_value: date | None = None
    for row in rows:
        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).astimezone(market_timezone)
        day_value = day_value or timestamp.date()
        clock = timestamp.time()
        if MARKET_OPEN <= clock < MARKET_CLOSE:
            regular.append(row)
            if clock < OPENING_RANGE_END:
                opening.append(row)
    if not regular:
        return {"date": day_value, "open": None, "high": None, "low": None, "close": None, "opening_range_pct": None}
    open_price = float(regular[0]["open"])
    or_pct = None
    if opening:
        or_high = max(float(row["high"]) for row in opening)
        or_low = min(float(row["low"]) for row in opening)
        or_pct = (or_high - or_low) / open_price * 100.0 if open_price else None
    return {
        "date": day_value,
        "open": open_price,
        "high": max(float(row["high"]) for row in regular),
        "low": min(float(row["low"]) for row in regular),
        "close": float(regular[-1]["close"]),
        "opening_range_pct": or_pct,
    }


def context_for_day(
    daily: pd.DataFrame,
    vix: pd.DataFrame | None,
    day: date,
    *,
    sma_window: int = 20,
    return_window: int = 5,
    opening_range_window: int = 20,
) -> DayContext:
    """Everything here uses prior days only, except the opening range, which is known at 9:45."""
    history = daily[daily["date"] < day].dropna(subset=["close"])
    trend_bias = 0
    trend_label = "unknown"
    if len(history) >= sma_window and len(history) > return_window:
        closes = history["close"].astype(float)
        prev_close = float(closes.iloc[-1])
        sma = float(closes.tail(sma_window).mean())
        ret = prev_close / float(closes.iloc[-1 - return_window]) - 1.0
        if prev_close > sma and ret > 0:
            trend_bias, trend_label = 1, "bull"
        elif prev_close < sma and ret < 0:
            trend_bias, trend_label = -1, "bear"
        else:
            trend_label = "neutral"

    or_pct = None
    or_ratio = None
    today = daily[daily["date"] == day]
    if not today.empty and not pd.isna(today.iloc[0]["opening_range_pct"]):
        or_pct = float(today.iloc[0]["opening_range_pct"])
        prior_ranges = history["opening_range_pct"].dropna().astype(float).tail(opening_range_window)
        if len(prior_ranges) >= 5 and float(prior_ranges.median()) > 0:
            or_ratio = or_pct / float(prior_ranges.median())

    vix_prev = None
    vix_open = None
    if vix is not None and not vix.empty:
        earlier = vix[vix["date"] < day]
        if not earlier.empty:
            vix_prev = float(earlier.iloc[-1]["close"])
        same_day = vix[vix["date"] == day]
        if not same_day.empty:
            vix_open = float(same_day.iloc[0]["open"])

    return DayContext(
        vix_prev_close=vix_prev,
        vix_open=vix_open,
        vix_regime=vix_regime(vix_prev),
        trend_bias=trend_bias,
        trend_label=trend_label,
        opening_range_pct=round(or_pct, 4) if or_pct is not None else None,
        opening_range_ratio=round(or_ratio, 3) if or_ratio is not None else None,
        turbulence=turbulence_bucket(or_ratio),
    )
