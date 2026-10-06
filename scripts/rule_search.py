"""Search simulator trades for entry rules that stay profitable in both halves of the sample.

Every feature is known at detection time (the evaluation row the live engine acts on) or from prior days.

Usage: python scripts/rule_search.py <sim_out_dir> [--split 2026-04-01] [--min-n 30] [--data data]
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pattern_scan import add_day_context, add_entry_features, add_episode_context, load_trades, wilson_lb  # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 200)


def perf(g: pd.DataFrame) -> dict:
    n = len(g)
    if n == 0:
        return dict(n=0, win=np.nan, total=0.0, pf=np.nan, avg=np.nan)
    wins = int((g.pnl_usd > 0).sum())
    gw = g.loc[g.pnl_usd > 0, "pnl_usd"].sum()
    gl = -g.loc[g.pnl_usd < 0, "pnl_usd"].sum()
    return dict(n=n, win=100.0 * wins / n, total=float(g.pnl_usd.sum()), pf=(gw / gl) if gl > 0 else np.inf, avg=float(g.pnl_usd.mean()))


def add_intraday_features(t: pd.DataFrame, data: Path) -> pd.DataFrame:
    frames = []
    for sym in t.symbol.unique():
        d = pd.read_csv(data / "context" / f"{sym}_daily.csv", parse_dates=["date"]).sort_values("date")
        d["prev_close"] = d.close.shift(1)
        d["symbol"] = sym
        d["date"] = d.date.dt.strftime("%Y-%m-%d")
        frames.append(d[["symbol", "date", "open", "prev_close"]].rename(columns={"open": "day_open"}))
    t = t.merge(pd.concat(frames), on=["symbol", "date"], how="left")
    # detection-time spot comes from the episodes file (merged in add_episode_context as `spot`)
    spot = t.spot.astype(float)
    t["spot_vs_open_bps"] = 1e4 * (spot / t.day_open - 1)
    t["spot_vs_prev_close_bps"] = 1e4 * (spot / t.prev_close - 1)
    is_bull = t.direction == "bull"
    t["with_day_move"] = np.where(t.spot_vs_open_bps.abs() < 5, "flat", np.where((t.spot_vs_open_bps > 0) == is_bull, "with", "against"))
    t["with_prev_close"] = np.where(t.spot_vs_prev_close_bps.abs() < 5, "flat", np.where((t.spot_vs_prev_close_bps > 0) == is_bull, "with", "against"))
    # other index crossed the same way in the 10 minutes before this entry
    eps = []
    for sym in t.symbol.unique():
        for path in (data / "episodes" / sym).glob(f"{sym}_episodes_*.csv"):
            e = pd.read_csv(path)
            if not e.empty:
                eps.append(e)
    e = pd.concat(eps)
    e["cross_dt"] = pd.to_datetime(e.cross_time, utc=True)
    flags = []
    for _, tr in t.iterrows():
        other = "SPY" if tr.symbol == "QQQ" else "QQQ"
        m = e[(e.symbol == other) & (e.direction == tr.direction) & (e.date == tr.date) & (e.cross_dt <= tr.entry_dt) & (e.cross_dt >= tr.entry_dt - pd.Timedelta(minutes=10))]
        flags.append("yes" if len(m) else "no")
    t["other_index_confirmed"] = flags
    t["trade_num_today"] = t.groupby("date").cumcount() + 1
    t["loss_before_today"] = t.groupby("date").apply(lambda g: (g.pnl_usd < 0).cumsum().shift(1).fillna(0) > 0, include_groups=False).reset_index(level=0, drop=True)
    return t


def bucketize(t: pd.DataFrame) -> dict[str, pd.Series]:
    mod = t.minute_of_day
    cut = lambda s, edges, labels: pd.cut(s, bins=edges, labels=labels, include_lowest=True).astype(str)  # noqa: E731
    f = {
        "direction": t.direction,
        "symbol": t.symbol,
        "grade": t.entry_grade,
        "alignment": t.alignment,
        "trend": t.trend_label,
        "vix": t.vix_regime,
        "turb": t.turbulence,
        "weekday": t.day_of_week,
        "hour": cut(mod, [569, 585, 600, 630, 660, 720, 780, 840, 960], ["9:30", "9:45", "10:00", "10:30", "11:00", "12:00", "13:00", "14:00+"]),
        "lag": cut(t.lag_min.fillna(999), [-999, 0, 2, 5, 10, 998, 1000], ["<=0", "0-2", "2-5", "5-10", ">10", "na"]),
        "prem": cut(t.entry_price, [0, 1.0, 1.5, 2.0, 2.5, 99], ["<1", "1-1.5", "1.5-2", "2-2.5", ">2.5"]),
        "prior_x": cut(t.prior_crosses_today.fillna(0), [-1, 0, 1, 2, 50], ["0", "1", "2", "3+"]),
        "since_prev": cut(t.min_since_prev_cross.fillna(999), [0, 20, 40, 80, 1000], ["<20", "20-40", "40-80", "80+"]),
        "rvgi_vs": t.rvgi_vs_sma.astype(str),
        "rvgi_sign": t.rvgi_sign.astype(str),
        "volgrade": t.volume_grade.astype(str),
        "volr": cut(t.vol_ratio, [0, 0.9, 1.2, 1.6, 99], ["<0.9", "0.9-1.2", "1.2-1.6", ">1.6"]),
        "ext": cut(t.ext_sma30_bps.abs(), [0, 5, 10, 20, 1000], ["<5", "5-10", "10-20", ">20"]),
        "onemin": t.one_min_agreement.astype(str),
        "barmin": t.bar_minutes_elapsed.astype(str),
        "gap": cut(t.gap_pct, [-99, -0.5, -0.15, 0.15, 0.5, 99], ["<-0.5", "-0.5..-0.15", "flat", "0.15..0.5", ">0.5"]),
        "prevret": cut(t.prev_day_ret_pct, [-99, -1, -0.3, 0.3, 1, 99], ["<-1", "-1..-0.3", "flat", "0.3..1", ">1"]),
        "dist20": cut(t.dist_sma20_pct, [-99, -2, 0, 1, 2, 99], ["<-2", "-2..0", "0..1", "1..2", ">2"]),
        "ret5": cut(t.ret5_pct, [-99, -2, -0.5, 0.5, 2, 99], ["<-2", "-2..-0.5", "flat", "0.5..2", ">2"]),
        "vixgap": cut(t.vix_open_vs_prev_pct, [-99, -5, -1, 1, 5, 99], ["<-5", "-5..-1", "flat", "1..5", ">5"]),
        "vix5d": cut(t.vix_chg_5d, [-99, -2, 0, 2, 99], ["fall>2", "fall", "rise", "rise>2"]),
        "orange": cut(t.opening_range_pct, [0, 0.3, 0.45, 0.6, 9], ["<0.3", "0.3-0.45", "0.45-0.6", ">0.6"]),
        "daymove": t.with_day_move,
        "vsprev": t.with_prev_close,
        "other": t.other_index_confirmed,
        "tradenum": cut(t.trade_num_today, [0, 1, 2, 50], ["1", "2", "3+"]),
        "lossbefore": t.loss_before_today.astype(str),
    }
    return {k: v.astype(str) for k, v in f.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--data", default="data")
    ap.add_argument("--split", default="2026-04-01")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--min-pf", type=float, default=1.3)
    ap.add_argument("--min-win", type=float, default=55.0)
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()
    out, data = Path(args.out_dir), Path(args.data)

    t = load_trades(out, "ATM")
    t = add_day_context(t, data)
    t = add_episode_context(t, data)
    t = add_entry_features(t, data)
    t = add_intraday_features(t, data)
    t = t.sort_values("entry_dt").reset_index(drop=True)
    is_mask = t.date < args.split
    print(f"universe: {len(t)} trades, IS {int(is_mask.sum())} / OOS {int((~is_mask).sum())}; base {perf(t)}")
    print(f"  IS {perf(t[is_mask])}\n  OOS {perf(t[~is_mask])}")

    feats = bucketize(t)
    names = list(feats)
    quarter = t.quarter

    def evaluate(mask: np.ndarray) -> dict | None:
        a, b = t[mask & is_mask.values], t[mask & ~is_mask.values]
        pa, pb = perf(a), perf(b)
        if pa["n"] < args.min_n or pb["n"] < args.min_n:
            return None
        if pa["pf"] < args.min_pf or pb["pf"] < args.min_pf or pa["win"] < args.min_win or pb["win"] < args.min_win:
            return None
        q = t[mask].groupby(quarter[mask]).pnl_usd.sum()
        wins_all = int((t[mask].pnl_usd > 0).sum())
        return dict(n=pa["n"] + pb["n"], win=100.0 * wins_all / (pa["n"] + pb["n"]), wlb80=100.0 * wilson_lb(wins_all, pa["n"] + pb["n"]),
                    total=pa["total"] + pb["total"], pf_is=pa["pf"], pf_oos=pb["pf"], win_is=pa["win"], win_oos=pb["win"],
                    n_is=pa["n"], n_oos=pb["n"], q_pos=f"{int((q > 0).sum())}/{len(q)}", score=min(pa["pf"], pb["pf"]) * np.sqrt(pa["n"] + pb["n"]))

    results = []
    tested = 0
    for name in names:
        for value in feats[name].unique():
            tested += 1
            r = evaluate((feats[name] == value).values)
            if r:
                results.append(dict(rule=f"{name}={value}", **r))
    for a, b in itertools.combinations(names, 2):
        for va in feats[a].unique():
            ma = (feats[a] == va).values
            if ma.sum() < 2 * args.min_n:
                continue
            for vb in feats[b].unique():
                tested += 1
                r = evaluate(ma & (feats[b] == vb).values)
                if r:
                    results.append(dict(rule=f"{a}={va} & {b}={vb}", **r))
    res = pd.DataFrame(results)
    print(f"\ntested {tested} single/pair rules; {len(res)} pass (n>={args.min_n} per half, PF>={args.min_pf} and win>={args.min_win}% in both halves)")
    if res.empty:
        return
    res = res.sort_values("score", ascending=False)
    cols = ["rule", "n", "win", "wlb80", "total", "pf_is", "pf_oos", "win_is", "win_oos", "n_is", "n_oos", "q_pos", "score"]
    print("\n== top rules by min(PF) x sqrt(n) ==")
    print(res[cols].head(args.top).round(2).to_string(index=False))
    print("\n== largest passing rules by trade count ==")
    print(res.sort_values("n", ascending=False)[cols].head(15).round(2).to_string(index=False))

    # current live policy for reference
    live = (t.alignment == "aligned") & (t.minute_of_day < 780) & ((t.entry_grade == "A+") | (t.lag_min <= 5))
    print("\n== reference: current live policy subset ==")
    print(f"  all {perf(t[live])}\n  IS {perf(t[live & is_mask])}\n  OOS {perf(t[live & ~is_mask])}")
    t.to_csv(out / "rule_search_trades.csv", index=False)
    res.to_csv(out / "rule_search_results.csv", index=False)
    print(f"\nwrote {out / 'rule_search_trades.csv'} and {out / 'rule_search_results.csv'}")


if __name__ == "__main__":
    main()
