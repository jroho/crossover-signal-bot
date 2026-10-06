"""Evaluate candidate higher-volume policies on the enriched trade universe from rule_search.py.

Usage: python scripts/policy_eval.py <sim_out_dir> [--split 2026-04-01]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

pd.set_option("display.width", 250)


def perf(g: pd.DataFrame) -> str:
    n = len(g)
    if n == 0:
        return "n=0"
    w = int((g.pnl_usd > 0).sum())
    gw = g.loc[g.pnl_usd > 0, "pnl_usd"].sum()
    gl = -g.loc[g.pnl_usd < 0, "pnl_usd"].sum()
    eq = g.sort_values("entry_dt").pnl_usd.cumsum()
    dd = (eq - eq.cummax()).min()
    pf = gw / gl if gl > 0 else np.inf
    return f"n={n:<4d} win={100 * w / n:5.1f}% avg={g.pnl_usd.mean():6.1f} total={g.pnl_usd.sum():8.0f} PF={pf:4.2f} DD={dd:6.0f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--split", default="2026-04-01")
    args = ap.parse_args()
    t = pd.read_csv(Path(args.out_dir) / "rule_search_trades.csv")
    t["entry_dt"] = pd.to_datetime(t.entry_dt, utc=True)
    t = t.sort_values("entry_dt").reset_index(drop=True)
    is_mask = t.date < args.split
    mod = t.minute_of_day
    bull = t.direction == "bull"
    a_plus = t.entry_grade == "A+"
    a_or_better = t.entry_grade.isin(["A", "A+"])
    before_13 = mod < 780
    time_rule = (mod >= 585) & ~(bull & (mod >= 720))
    aligned = t.alignment == "aligned"
    lag5 = t.lag_min <= 5
    first_cross = t.prior_crosses_today.fillna(0) == 0
    other = t.other_index_confirmed == "yes"
    ext20 = t.ext_sma30_bps.abs() >= 20
    prem1 = t.entry_price >= 1.0
    # "no loss yet today" defined three ways: over every B+ cross (needs shadow tracking live), over A-or-better crosses, and over the policy's own trades
    loss_before_all = t.loss_before_today.astype(bool)
    t["lb_a"] = t[a_or_better].groupby("date").pnl_usd.transform(lambda s: (s < 0).cumsum().shift(1).fillna(0) > 0).reindex(t.index).fillna(False).astype(bool)

    def own_loss_before(mask: pd.Series) -> pd.Series:
        sub = t[mask]
        flag = sub.groupby("date").pnl_usd.transform(lambda s: (s < 0).cumsum().shift(1).fillna(0) > 0)
        return flag.reindex(t.index).fillna(False).astype(bool)

    live = aligned & before_13 & (a_plus | lag5)
    policies = {
        "P0 current live policy": live,
        "P0 + time rule": live & time_rule,
        "P1 A+ first cross of day, <13:00": a_plus & first_cross & before_13,
        "P1 + time rule": a_plus & first_cross & before_13 & time_rule,
        "P2 A+ & (ext>20 or other index confirmed), <13:00": a_plus & (ext20 | other) & before_13,
        "P2 + time rule + prem>=1": a_plus & (ext20 | other) & before_13 & time_rule & prem1,
        "P3 A-or-better & other index confirmed, <13:00": a_or_better & other & before_13,
        "P3 + time rule": a_or_better & other & before_13 & time_rule,
        "P4 A+ & (first cross or other confirmed or aligned), <13:00, time rule": a_plus & (first_cross | other | aligned) & before_13 & time_rule,
        "P5 union: live policy OR (A+ & first cross) OR (A-or-better & other), <13:00, time rule": (live | (a_plus & first_cross) | (a_or_better & other)) & before_13 & time_rule,
        "P6 A-or-better & aligned, <13:00, time rule (no lag filter)": a_or_better & aligned & before_13 & time_rule,
        "P7 A+ all, <13:00, time rule": a_plus & before_13 & time_rule,
        "P8 A-or-better, first cross, <13:00, time rule": a_or_better & first_cross & before_13 & time_rule,
    }
    for name, mask in policies.items():
        print(f"\n{name}")
        print(f"   all  {perf(t[mask])}")
        print(f"   IS   {perf(t[mask & is_mask])}")
        print(f"   OOS  {perf(t[mask & ~is_mask])}")
        q = t[mask].groupby("quarter").pnl_usd.agg(["size", "sum"])
        print("   quarters: " + ", ".join(f"{idx} {int(r['size'])}t ${r['sum']:.0f}" for idx, r in q.iterrows()))
        by_dir = t[mask].groupby("direction").pnl_usd.agg(["size", lambda s: 100 * (s > 0).mean(), "sum"])
        print("   by direction: " + ", ".join(f"{d} {int(r.iloc[0])}t {r.iloc[1]:.0f}% ${r.iloc[2]:.0f}" for d, r in by_dir.iterrows()))
        own = own_loss_before(mask)
        print(f"   + stop after own first loss of day: {perf(t[mask & ~own])}")
        print(f"   + stop after any A-or-better shadow loss: {perf(t[mask & ~t.lb_a])}")
        overlap = int((mask & live).sum())
        print(f"   overlap with current policy: {overlap} of {int(live.sum())} current trades; adds {int((mask & ~live).sum())} new trades: {perf(t[mask & ~live])}")


if __name__ == "__main__":
    main()
