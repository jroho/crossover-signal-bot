"""Summarize direction_test scores: proxy win rate (first-touch barrier) by era, year, VIX regime, alignment, grade."""
import sys

import numpy as np
import pandas as pd

KEYS = [["era"], ["year"], ["era", "vix_regime"], ["era", "alignment"], ["era", "direction"], ["era", "entry_grade"]]


def load(path: str) -> tuple[pd.DataFrame, str]:
    t = pd.read_csv(path)
    t = t[t["win_k0.2"].notna()]
    t["date"] = pd.to_datetime(t.date)
    t["year"] = t.date.dt.year
    t["era"] = np.select([t.date < "2024-02-01", t.date < "2025-10-01"], ["A 2019-2024Jan (proxy only)", "B 2024Feb-2025Sep"], "C 2025Oct-2026Oct (tuned)")
    o = t[t.exit_reason.isin(["target", "stop"])]
    ks = [c for c in t.columns if c.startswith("win_k")]
    best = max(ks, key=lambda c: (o[c] == (o.exit_reason == "target")).mean())
    print(f"barrier {best}: agrees with option outcome on {(o[best] == (o.exit_reason == 'target')).mean():.1%} of {len(o)} rows")
    return t, best


def stats(g: pd.DataFrame, k: str) -> pd.Series:
    n = len(g)
    p = g[k].mean()
    opt = g[g.exit_reason.isin(["target", "stop"])]
    return pd.Series({
        "n": n,
        "proxy_win%": round(p * 100, 1),
        "z_vs_50": round((p - 0.5) / np.sqrt(0.25 / n), 1) if n else np.nan,
        "ret30_bps": round(g.ret30_bps.mean(), 1),
        "option_win%": round((opt.exit_reason == "target").mean() * 100, 1) if len(opt) else np.nan,
    })


for path in sys.argv[1:]:
    t, k = load(path)
    print(f"===== {path}")
    for keys in KEYS:
        print(t.groupby(keys).apply(stats, k, include_groups=False).to_string(), "\n")
