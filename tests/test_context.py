from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.backtest.context import (
    alignment,
    build_daily_summary,
    context_for_day,
    fetch_vix_history,
    load_vix_history,
    turbulence_bucket,
    vix_regime,
)
from src.models import Direction

ET = ZoneInfo("America/New_York")


def _daily(closes: list[float], ranges: list[float] | None = None, start: date = date(2026, 1, 2)) -> pd.DataFrame:
    rows = []
    day = start
    for index, close in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append({"date": day, "open": close, "high": close + 1, "low": close - 1, "close": close, "opening_range_pct": (ranges or [0.4] * len(closes))[index]})
        day += timedelta(days=1)
    return pd.DataFrame(rows)


def test_vix_regime_and_turbulence_buckets():
    assert vix_regime(None) == "unknown"
    assert vix_regime(12.0) == "low"
    assert vix_regime(17.5) == "mid"
    assert vix_regime(24.0) == "high"
    assert vix_regime(35.0) == "extreme"
    assert turbulence_bucket(0.5) == "calm"
    assert turbulence_bucket(1.0) == "normal"
    assert turbulence_bucket(2.0) == "turbulent"
    assert turbulence_bucket(None) == "unknown"


def test_alignment_matches_trade_direction_to_bias():
    assert alignment(Direction.BULL, 1) == "aligned"
    assert alignment(Direction.BEAR, 1) == "counter"
    assert alignment(Direction.BEAR, -1) == "aligned"
    assert alignment(Direction.BULL, 0) == "neutral"


def test_context_uses_prior_days_only_and_flags_a_bull_trend():
    closes = [100.0 + index for index in range(25)]  # steady uptrend
    daily = _daily(closes, ranges=[0.4] * 24 + [0.8])
    target_day = daily.iloc[-1]["date"]
    vix = pd.DataFrame({"date": daily["date"], "open": 16.0, "high": 17.0, "low": 15.0, "close": [14.0] * 24 + [25.0]})

    context = context_for_day(daily, vix, target_day)

    assert context.trend_label == "bull" and context.trend_bias == 1
    # The day's own VIX close must not leak in; the prior close drives the regime.
    assert context.vix_prev_close == 14.0 and context.vix_regime == "low"
    assert context.vix_open == 16.0
    assert context.opening_range_pct == 0.8
    assert context.opening_range_ratio == 2.0 and context.turbulence == "turbulent"


def test_context_is_neutral_when_signals_disagree_and_unknown_without_history():
    # Prior close 125 sits above its 20-day average (113.75) but the 5-day return is negative (125 vs 130).
    mixed = _daily([100.0] * 15 + [130.0] * 5 + [125.0] * 6)
    assert context_for_day(mixed, None, mixed.iloc[-1]["date"]).trend_label == "neutral"

    short = _daily([100.0] * 5)
    context = context_for_day(short, None, short.iloc[-1]["date"])
    assert context.trend_label == "unknown" and context.vix_regime == "unknown"


def test_build_daily_summary_reads_regular_hours_and_opening_range(tmp_path: Path):
    day_dir = tmp_path / "underlying" / "QQQ"
    day_dir.mkdir(parents=True)
    lines = ["timestamp,open,high,low,close,volume,symbol"]
    # 09:25 premarket bar must be ignored; 09:30-09:44 is the opening range; 15:59 is the close.
    lines.append("2026-03-24T13:25:00+00:00,99,99.5,98.5,99,100,QQQ")
    for minute in range(0, 15):
        lines.append(f"2026-03-24T13:{30 + minute:02d}:00+00:00,100,101,99,100,100,QQQ")
    lines.append("2026-03-24T16:00:00+00:00,100,103,97,102,100,QQQ")
    lines.append("2026-03-24T19:59:00+00:00,102,102.5,101.5,102.2,100,QQQ")
    lines.append("2026-03-24T20:05:00+00:00,110,110,110,110,100,QQQ")
    (day_dir / "QQQ_1minute_2026-03-24.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary = build_daily_summary(tmp_path, "QQQ", ET)

    row = summary.iloc[0]
    assert row["date"] == date(2026, 3, 24)
    assert row["open"] == 100.0 and row["close"] == 102.2
    assert row["high"] == 103.0 and row["low"] == 97.0
    assert row["opening_range_pct"] == 2.0
    assert (tmp_path / "context" / "QQQ_daily.csv").exists()


def test_fetch_and_load_vix_history(tmp_path: Path):
    class _Response:
        text = "DATE,OPEN,HIGH,LOW,CLOSE\n03/23/2026,18.1,19.0,17.5,18.4\n03/24/2026,18.6,20.2,18.0,19.9\n"

        def raise_for_status(self) -> None:
            return None

    class _Session:
        def get(self, url: str, timeout: int) -> _Response:
            assert "VIX_History" in url
            return _Response()

    path = fetch_vix_history(tmp_path / "VIX_History.csv", session=_Session())
    frame = load_vix_history(path)

    assert list(frame["date"]) == [date(2026, 3, 23), date(2026, 3, 24)]
    assert frame.iloc[1]["close"] == 19.9
    assert load_vix_history(tmp_path / "missing.csv") is None
