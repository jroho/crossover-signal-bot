"""Summarize simulator trades by period, half-year, direction, grade and alignment.

Usage: python scripts/ext_report.py <run> [<run> ...]   (runs are folders under logs/sim/)
"""
import sys, numpy as np, pandas as pd
def load(run):
    t = pd.read_csv(f"logs/sim/{run}/trades.csv")
    t = t[t.entry_time.notna()].copy()
    t["date"] = pd.to_datetime(t.date); t = t.sort_values("entry_time")
    t["period"] = np.where(t.date < "2025-10-01", "NEW 2024-02..2025-09", "SEEN 2025-10..2026-10")
    t["half"] = t.date.dt.year.astype(str) + np.where(t.date.dt.month <= 6, "H1", "H2")
    return t
def stats(g):
    p = g.pnl_usd.values; eq = p.cumsum(); dd = (eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min() if len(p) else 0
    gw, gl = p[p > 0].sum(), -p[p <= 0].sum()
    return pd.Series({"n": len(p), "win%": round((p > 0).mean() * 100, 1), "exp$": round(p.mean(), 1), "PF": round(gw / gl, 2) if gl else np.inf, "total$": round(p.sum()), "maxDD$": round(dd)})
for run in sys.argv[1:]:
    t = load(run); print(f"===== {run}")
    for keys in (["period"], ["half"], ["period", "direction"], ["period", "entry_grade"], ["period", "alignment"]):
        print(t.groupby(keys).apply(stats, include_groups=False).to_string(), "\n")
