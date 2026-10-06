"""Permutation test for rule_search: how many single/pair rules pass by chance when P&L is shuffled?

Usage: python scripts/rule_permutation.py <sim_out_dir> [--perms 300] [--split 2026-04-01]
Reads <sim_out_dir>/rule_search_trades.csv written by rule_search.py.
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rule_search import bucketize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--perms", type=int, default=300)
    ap.add_argument("--split", default="2026-04-01")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--min-pf", type=float, default=1.3)
    ap.add_argument("--min-win", type=float, default=55.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    t = pd.read_csv(Path(args.out_dir) / "rule_search_trades.csv")
    t = t.sort_values("entry_dt").reset_index(drop=True)
    feats = bucketize(t)
    names = list(feats)
    masks, labels = [], []
    for name in names:
        for value in feats[name].unique():
            masks.append((feats[name] == value).values)
            labels.append(f"{name}={value}")
    for a, b in itertools.combinations(names, 2):
        for va in feats[a].unique():
            ma = (feats[a] == va).values
            if ma.sum() < 2 * args.min_n:
                continue
            for vb in feats[b].unique():
                masks.append(ma & (feats[b] == vb).values)
                labels.append(f"{a}={va} & {b}={vb}")
    M = np.array(masks, dtype=float)  # rules x trades
    is_mask = (t.date < args.split).values
    M_is, M_oos = M * is_mask, M * ~is_mask
    n_is, n_oos = M_is.sum(1), M_oos.sum(1)
    eligible = (n_is >= args.min_n) & (n_oos >= args.min_n)
    print(f"{len(masks)} rules, {int(eligible.sum())} with n>={args.min_n} in both halves")

    def passes(pnl: np.ndarray) -> np.ndarray:
        pos, neg, win = np.clip(pnl, 0, None), np.clip(-pnl, 0, None), (pnl > 0).astype(float)
        out = np.ones(len(masks), dtype=bool) & eligible
        for Mh, nh in ((M_is, n_is), (M_oos, n_oos)):
            gw, gl, w = Mh @ pos, Mh @ neg, Mh @ win
            pf = np.where(gl > 0, gw / np.where(gl > 0, gl, 1), np.inf)
            winrate = 100.0 * w / np.where(nh > 0, nh, 1)
            out &= (pf >= args.min_pf) & (winrate >= args.min_win)
        return out

    pnl = t.pnl_usd.values.astype(float)
    real = passes(pnl)
    print(f"real labels: {int(real.sum())} rules pass")
    rng = np.random.default_rng(args.seed)
    counts = []
    for _ in range(args.perms):
        counts.append(int(passes(rng.permutation(pnl)).sum()))
    counts = np.array(counts)
    print(f"shuffled P&L ({args.perms} permutations): passes mean {counts.mean():.1f}, median {np.median(counts):.0f}, "
          f"90th pct {np.percentile(counts, 90):.0f}, 99th pct {np.percentile(counts, 99):.0f}, max {counts.max()}")
    print(f"share of permutations with >= {int(real.sum())} passes: {(counts >= real.sum()).mean():.3f}")
    # the same test restricted to the economically motivated subset of features
    core = [i for i, lab in enumerate(labels) if all(any(lab_part.split("=")[0] in {"grade", "alignment", "hour", "lag", "prior_x", "other", "ext", "lossbefore", "prem", "direction"} for lab_part in lab.split(" & ")) for _ in [0])]
    sub = np.zeros(len(masks), dtype=bool); sub[core] = True
    real_core = int((real & sub).sum())
    core_counts = []
    for _ in range(args.perms):
        core_counts.append(int((passes(rng.permutation(pnl)) & sub).sum()))
    core_counts = np.array(core_counts)
    print(f"core features only ({int(sub.sum())} rules): real {real_core} pass; shuffled mean {core_counts.mean():.1f}, 99th pct {np.percentile(core_counts, 99):.0f}, "
          f"share >= real {(core_counts >= real_core).mean():.3f}")


if __name__ == "__main__":
    main()
