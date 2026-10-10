"""Direction-only backtest for years without option data (Alpaca option bars start Feb 2024).

episodes: derive each day's cross episodes from the evaluation cache (same rule as find_cross_episodes) so the
          simulator can make its entry decisions; with no option file every accepted entry comes back as no_fill.
score:    for every accepted entry in a simulate trades.csv, score the underlying from the fill minute: a first-touch
          barrier scaled to the expected move to the close (k calibrated on 2024+ rows that have real option
          outcomes) plus signed returns at fixed horizons.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from src.config import load_config
from src.market_hours import is_within_market_hours, parse_clock_time

DATA = Path("data")
EPISODE_COLS = ["date", "symbol", "direction", "cross_time", "detection_time", "spot", "sma_cross_lag_min",
                "bar_minutes_elapsed", "volume_grade", "itm2_symbol", "itm1_symbol", "atm_symbol", "otm1_symbol", "otm2_symbol"]


def episodes_for_day(symbol: str, day: date) -> pd.DataFrame | None:
    config = load_config("config.toml")
    tz = ZoneInfo(config.app.market_timezone)
    open_t = parse_clock_time(config.live.market_open_time, field_name="open")
    close_t = parse_clock_time(config.live.market_close_time, field_name="close")
    cache = DATA / "evaluations" / symbol / f"{symbol}_evaluations_{day.isoformat()}.csv"
    if not cache.exists():
        return None
    ev = pd.read_csv(cache, usecols=["datetime", "symbol", "direction", "last_price", "sma_cross_signal", "sma_cross_status",
                                     "sma_cross_time", "sma_cross_lag_min", "bar_minutes_elapsed", "volume_grade"])
    ev = ev[(ev.sma_cross_status == "fresh") & ev.sma_cross_time.notna() & (ev.direction == ev.sma_cross_signal)]
    rows = []
    for r in ev.itertuples():
        if not is_within_market_hours(pd.Timestamp(r.datetime).to_pydatetime(), tz, open_t, close_t):
            continue
        rows.append([day.isoformat(), symbol, r.direction, pd.Timestamp(r.sma_cross_time).isoformat(), pd.Timestamp(r.datetime).isoformat(),
                     r.last_price, r.sma_cross_lag_min, r.bar_minutes_elapsed, r.volume_grade, "", "", "", "", ""])
    return pd.DataFrame(rows, columns=EPISODE_COLS).drop_duplicates(["symbol", "cross_time"])


def write_episodes(symbol: str, start: date, end: date) -> int:
    written = 0
    day = start
    while day <= end:
        out = DATA / "episodes" / symbol / f"{symbol}_episodes_{day.isoformat()}.csv"
        frame = None if out.exists() else episodes_for_day(symbol, day)
        if frame is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(out, index=False)
            written += 1
        day += timedelta(days=1)
    return written


_bars: dict[tuple[str, str], pd.DataFrame] = {}


def bars(symbol: str, day: str) -> pd.DataFrame:
    key = (symbol, day)
    if key not in _bars:
        d = pd.read_csv(DATA / "underlying" / symbol / f"{symbol}_1minute_{day}.csv")
        d["ts"] = pd.to_datetime(d["timestamp"], utc=True)
        _bars[key] = d.set_index("ts").sort_index()
        if len(_bars) > 64:
            _bars.pop(next(iter(_bars)))
    return _bars[key]


def score_row(row: pd.Series, ks: list[float]) -> dict[str, float]:
    sign = 1.0 if row.direction == "bull" else -1.0
    fill = pd.Timestamp(row.detection_time) + pd.Timedelta(minutes=float(row.entry_delay_min))
    b = bars(row.symbol, row.date[:10])
    day_close = fill.tz_convert("America/New_York").normalize().tz_localize(None).tz_localize("America/New_York") + pd.Timedelta(hours=16)
    flat = day_close - pd.Timedelta(minutes=25)
    before = b[b.index < fill].tail(31)
    after = b[(b.index >= fill) & (b.index < flat)]
    if after.empty or len(before) < 10:
        return {}
    entry = float(after.iloc[0]["open"])
    sigma = np.log(before["close"]).diff().std()
    expected = sigma * np.sqrt(max((day_close - fill).total_seconds() / 60.0, 1.0))
    fav = sign * ((after["high"] if sign > 0 else after["low"]) / entry - 1.0)
    adv = -sign * ((after["low"] if sign > 0 else after["high"]) / entry - 1.0)
    out: dict[str, float] = {"expected_move": expected}
    for k in ks:
        barrier = k * expected
        hit_f = np.flatnonzero(fav.values >= barrier)
        hit_a = np.flatnonzero(adv.values >= barrier)
        first_f = hit_f[0] if len(hit_f) else 10**6
        first_a = hit_a[0] if len(hit_a) else 10**6
        if first_f == first_a == 10**6:
            out[f"win_k{k}"] = float(sign * (after.iloc[-1]["close"] / entry - 1.0) > 0)
        else:
            out[f"win_k{k}"] = float(first_f < first_a)  # same bar counts as a loss
    for h in (15, 30, 60):
        part = after[after.index < fill + pd.Timedelta(minutes=h)]
        out[f"ret{h}_bps"] = sign * (float(part.iloc[-1]["close"]) / entry - 1.0) * 1e4
    return out


def score(trades_csv: str, out_csv: str) -> None:
    t = pd.read_csv(trades_csv)
    blocked = {"no_entry", "filtered_grade", "after_cutoff", "filtered_time", "filtered_lag", "filtered_direction",
               "filtered_turbulence", "filtered_late_follower"}
    t = t[~t.exit_reason.isin(blocked)].copy()
    ks = [0.1, 0.15, 0.2, 0.25, 0.3]
    scored = pd.DataFrame([score_row(r, ks) for _, r in t.iterrows()], index=t.index)
    t = t.join(scored)
    t.to_csv(out_csv, index=False)
    print(f"scored {len(t)} entries -> {out_csv}")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "episodes":
        n = write_episodes(sys.argv[2], date.fromisoformat(sys.argv[3]), date.fromisoformat(sys.argv[4]))
        print(f"{sys.argv[2]}: wrote {n} episode files")
    elif mode == "score":
        score(sys.argv[2], sys.argv[3])
