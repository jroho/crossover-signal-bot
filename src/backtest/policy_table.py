from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

# One-sided 80% lower bound: the win rate we plan around is the one we'd still clear four times in five.
WILSON_Z_80 = 0.8416


@dataclass(frozen=True)
class PolicyConfig:
    # Buckets with fewer filled trades stay in "learn" until the sample is large enough to trust.
    min_trades: int = 20
    account_usd: float = 500.0
    # Fraction of full Kelly to size with; quarter-Kelly keeps drawdowns survivable with one contract lots.
    kelly_fraction: float = 0.25
    max_contracts: int = 2
    z: float = WILSON_Z_80


def wilson_lower_bound(wins: int, trials: int, z: float = WILSON_Z_80) -> float:
    if trials <= 0:
        return 0.0
    p_hat = wins / trials
    denominator = 1 + z * z / trials
    centre = p_hat + z * z / (2 * trials)
    margin = z * math.sqrt(p_hat * (1 - p_hat) / trials + z * z / (4 * trials * trials))
    return max(0.0, (centre - margin) / denominator)


def build_policy_table(trades: pd.DataFrame, keys: list[str], config: PolicyConfig | None = None) -> pd.DataFrame:
    """Per-bucket reward summary and the action the live bot should take when a signal lands in that bucket.

    Expectancy is computed twice: from the raw win rate, and from the Wilson lower bound on the win rate.
    Only the conservative version decides whether a bucket trades.
    """
    cfg = config or PolicyConfig()
    filled = trades[trades["entry_price"].notna() & trades["exit_price"].notna()].copy()
    if filled.empty:
        return pd.DataFrame(columns=[*keys, "trades", "action"])

    rows: list[dict[str, object]] = []
    for bucket, group in filled.groupby(keys, dropna=False):
        bucket_values = bucket if isinstance(bucket, tuple) else (bucket,)
        pnl = group["pnl_usd"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl <= 0]
        n = int(len(group))
        win_count = int(len(wins))
        win_rate = win_count / n
        win_rate_lb = wilson_lower_bound(win_count, n, cfg.z)
        avg_win = float(wins.mean()) if win_count else 0.0
        avg_loss = float(-losses.mean()) if len(losses) else 0.0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
        expectancy_lb = win_rate_lb * avg_win - (1 - win_rate_lb) * avg_loss
        profit_factor = float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else math.inf
        avg_premium = float((group["entry_price"].astype(float) * 100).mean())

        if n < cfg.min_trades:
            action = "learn"
        elif expectancy_lb > 0:
            action = "trade"
        else:
            action = "skip"

        kelly = 0.0
        if avg_loss > 0 and avg_win > 0:
            payoff = avg_win / avg_loss
            kelly = max(0.0, win_rate_lb - (1 - win_rate_lb) / payoff)
        elif avg_win > 0 and avg_loss == 0:
            kelly = win_rate_lb
        contracts = 0
        if action == "trade" and avg_premium > 0:
            contracts = int(min(cfg.max_contracts, max(1, math.floor(kelly * cfg.kelly_fraction * cfg.account_usd / avg_premium))))

        row: dict[str, object] = dict(zip(keys, bucket_values, strict=True))
        row.update(
            {
                "trades": n,
                "wins": win_count,
                "win_rate": round(win_rate, 3),
                "win_rate_lb80": round(win_rate_lb, 3),
                "avg_win_usd": round(avg_win, 2),
                "avg_loss_usd": round(avg_loss, 2),
                "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else math.inf,
                "expectancy_usd": round(expectancy, 2),
                "expectancy_lb80_usd": round(expectancy_lb, 2),
                "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else math.inf,
                "avg_premium_usd": round(avg_premium, 2),
                "kelly": round(kelly, 3),
                "size_contracts": contracts,
                "action": action,
            }
        )
        rows.append(row)

    table = pd.DataFrame(rows)
    return table.sort_values(["action", "expectancy_lb80_usd"], ascending=[True, False]).reset_index(drop=True)
