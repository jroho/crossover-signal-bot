"""Pre-registered tests of four published intraday strategies on 1m SPY/QQQ bars (Jan 2019 - Oct 2026).

Rules and parameters were fixed before any result was seen and are the published ones, not tuned here.
Returns are on the ETF itself (unlevered, per trade) net of COST_BPS per round trip. Primary symbol per
strategy is the one its paper used; the other is reported as a robustness check.

S1 noise-area momentum (Zarattini, Barbon & Aziz 2024), primary SPY: sigma(m) = mean over the prior 14 days of
   |close(m)/open - 1|; UB = max(open, prev_close)*(1+sigma), LB = min(open, prev_close)*(1-sigma). At each :00/:30
   from 10:00 to 15:30: flat -> long above UB / short below LB; long exits below max(UB, VWAP), short exits above
   min(LB, VWAP); flatten at the close.
S2 5-minute opening range breakout (Zarattini & Aziz 2023), primary QQQ: first 5m candle up -> long at the 9:35
   open, down -> short, doji -> no trade; stop at the candle's opposite extreme, target 10R, else exit at the close.
   A stop and target touched in the same minute counts as the stop.
S3 intraday momentum (Gao, Han, Li & Zhou 2018), primary SPY: sign of the prev close -> 10:00 return, traded
   15:30 -> 16:00. Half days skipped.
S4 variance-premium proxy, SPY: realized |close/open - 1| vs the VIX1D-implied expected absolute move
   0.8 * VIX1D_open / 100 / sqrt(252). Not a P&L test (no option prices): it measures whether same-day options were
   priced above the moves that followed. VIX1D history starts May 2022.
"""
from __future__ import annotations

import glob
import sys

import numpy as np
import pandas as pd

COST_BPS = 1.0
ET = "America/New_York"


def load(sym: str) -> dict:
    frames = [pd.read_csv(p, usecols=["timestamp", "open", "high", "low", "close", "volume"])
              for p in sorted(glob.glob(f"data/underlying/{sym}/{sym}_1minute_*.csv"))]
    d = pd.concat(frames, ignore_index=True)
    ts = pd.to_datetime(d.timestamp, utc=True).dt.tz_convert(ET)
    d["day"] = ts.dt.date
    d["m"] = ts.dt.hour * 60 + ts.dt.minute - 570
    d = d[(d.m >= 0) & (d.m < 390)]
    days = {}
    for day, g in d.groupby("day"):
        a = np.full((390, 5), np.nan)
        a[g.m.values] = g[["open", "high", "low", "close", "volume"]].values
        days[day] = a
    return days


def session(a: np.ndarray):
    c = pd.Series(a[:, 3]).ffill().bfill().values
    o = a[np.flatnonzero(~np.isnan(a[:, 0]))[0], 0]
    last = np.flatnonzero(~np.isnan(a[:, 3]))[-1]
    return o, c, last, last >= 385


def s1_noise(days: dict, sym: str) -> list[dict]:
    keys, rows, moves, prev_close = sorted(days), [], {}, None
    for i, day in enumerate(keys):
        a = days[day]
        o, c, last, full = session(a)
        moves[day] = np.abs(c / o - 1)
        hist = [moves[k] for k in keys[max(0, i - 14):i]]
        if full and prev_close is not None and len(hist) == 14:
            sigma = np.nanmean(hist, axis=0)
            ub, lb = max(o, prev_close) * (1 + sigma), min(o, prev_close) * (1 - sigma)
            hi, lo = np.where(np.isnan(a[:, 1]), c, a[:, 1]), np.where(np.isnan(a[:, 2]), c, a[:, 2])
            vol = np.nan_to_num(a[:, 4])
            vwap = np.cumsum((hi + lo + c) / 3 * vol) / np.maximum(np.cumsum(vol), 1)
            pos, entry = 0, 0.0
            for t in range(30, 361, 30):
                p, k = c[t - 1], t - 1
                if (pos == 1 and p < max(ub[k], vwap[k])) or (pos == -1 and p > min(lb[k], vwap[k])):
                    rows.append({"sym": sym, "day": day, "dir": pos, "bps": pos * (p / entry - 1) * 1e4 - COST_BPS})
                    pos = 0
                if pos == 0 and (p > ub[k] or p < lb[k]):
                    pos, entry = (1 if p > ub[k] else -1), p
            if pos:
                rows.append({"sym": sym, "day": day, "dir": pos, "bps": pos * (c[last] / entry - 1) * 1e4 - COST_BPS})
        prev_close = c[last]
    return rows


def s2_orb(days: dict, sym: str) -> list[dict]:
    rows = []
    for day in sorted(days):
        a = days[day]
        o, c, last, _ = session(a)
        c5, h5, l5 = c[4], np.nanmax(a[:5, 1]), np.nanmin(a[:5, 2])
        if c5 == o:
            continue
        d = 1 if c5 > o else -1
        entry = a[5, 0] if not np.isnan(a[5, 0]) else c[4]
        stop = l5 if d == 1 else h5
        risk = d * (entry - stop)
        if risk <= 0:
            continue
        target, exit_px = entry + d * 10 * risk, None
        for t in range(5, last + 1):
            bo, bh, bl = a[t, 0], a[t, 1], a[t, 2]
            if np.isnan(bh):
                continue
            adverse = bl if d == 1 else bh
            favorable = bh if d == 1 else bl
            if d * (adverse - stop) <= 0:
                exit_px = bo if d * (bo - stop) <= 0 else stop
                break
            if d * (favorable - target) >= 0:
                exit_px = target
                break
        exit_px = c[last] if exit_px is None else exit_px
        rows.append({"sym": sym, "day": day, "dir": d, "bps": d * (exit_px / entry - 1) * 1e4 - COST_BPS,
                     "R": d * (exit_px - entry) / risk})
    return rows


def s3_momentum(days: dict, sym: str) -> list[dict]:
    rows, prev_close = [], None
    for day in sorted(days):
        o, c, last, full = session(days[day])
        if full and prev_close is not None and c[29] != prev_close:
            d = 1 if c[29] > prev_close else -1
            rows.append({"sym": sym, "day": day, "dir": d, "bps": d * (c[389] / c[359] - 1) * 1e4 - COST_BPS})
        prev_close = c[last]
    return rows


def s4_premium(days: dict) -> pd.DataFrame:
    v = pd.read_csv("data/context/VIX1D_History.csv")
    v["day"] = pd.to_datetime(v.DATE, format="%m/%d/%Y").dt.date
    vix = dict(zip(v.day, v.OPEN))
    rows = []
    for day in sorted(days):
        o, c, last, full = session(days[day])
        if full and day in vix and vix[day] > 0:
            implied = 0.8 * vix[day] / 100 / np.sqrt(252) * 1e4
            realized = abs(c[last] / o - 1) * 1e4
            rows.append({"day": day, "implied_bps": implied, "realized_bps": realized, "edge_bps": implied - realized})
    return pd.DataFrame(rows)


def summarize(rows: list[dict], name: str) -> None:
    t = pd.DataFrame(rows)
    t["year"] = pd.to_datetime(t.day).dt.year
    for sym, g in t.groupby("sym"):
        daily = g.groupby("day").bps.sum()
        all_days = pd.Series(0.0, index=sorted(set(t.day)))
        all_days.loc[daily.index] = daily
        sharpe = all_days.mean() / all_days.std() * np.sqrt(252)
        dd = (all_days.cumsum() - all_days.cumsum().cummax()).min()
        print(f"\n{name} {sym}: {len(g)} trades, win {np.mean(g.bps > 0):.1%}, mean {g.bps.mean():+.1f} bps/trade, "
              f"{all_days.mean() * 252 / 100:+.1f}%/yr unlevered, Sharpe {sharpe:.2f}, max DD {dd / 100:.1f}%"
              + (f", mean {g.R.mean():+.2f}R" if "R" in g else ""))
        by = g.groupby("year").agg(n=("bps", "size"), win=("bps", lambda x: round(np.mean(x > 0) * 100, 1)),
                                   bps=("bps", lambda x: round(x.mean(), 1)), total_pct=("bps", lambda x: round(x.sum() / 100, 1)))
        print(by.T.to_string())


if __name__ == "__main__":
    data = {s: load(s) for s in ("SPY", "QQQ")}
    first = min(min(d) for d in data.values())
    print(f"bars {first} .. {max(max(d) for d in data.values())}; cost {COST_BPS} bp per round trip")
    for name, fn in (("S1 noise-area momentum", s1_noise), ("S2 5m ORB", s2_orb), ("S3 last-half-hour momentum", s3_momentum)):
        rows = [r for s in data for r in fn(data[s], s) if str(r["day"]) >= "2019-01-02"]
        pd.DataFrame(rows).to_csv(f"logs/sim/strat_{name.split()[0]}.csv", index=False)
        summarize(rows, name)
    p = s4_premium(data["SPY"])
    p["year"] = pd.to_datetime(p.day).dt.year
    p.to_csv("logs/sim/strat_S4.csv", index=False)
    print(f"\nS4 SPY variance premium proxy ({len(p)} days): implied {p.implied_bps.mean():.0f} bps vs realized "
          f"{p.realized_bps.mean():.0f} bps; realized > implied on {np.mean(p.edge_bps < 0):.0%} of days; "
          f"worst day realized/implied {(p.realized_bps / p.implied_bps).max():.1f}x")
    print(p.groupby("year").agg(days=("edge_bps", "size"), edge_bps=("edge_bps", lambda x: round(x.mean(), 1)),
                                realized_gt_implied=("edge_bps", lambda x: round(np.mean(x < 0) * 100, 1))).T.to_string())
