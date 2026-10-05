import math

import pandas as pd
import pytest

from src.backtest.policy_table import PolicyConfig, build_policy_table, wilson_lower_bound


def _trades(bucket: str, wins: int, losses: int, win_usd: float, loss_usd: float, premium: float = 1.5) -> list[dict[str, object]]:
    rows = []
    for index in range(wins + losses):
        pnl = win_usd if index < wins else -loss_usd
        rows.append({"time_bucket": bucket, "entry_grade": "A", "entry_price": premium, "exit_price": premium + pnl / 100, "pnl_usd": pnl})
    return rows


def test_wilson_lower_bound_is_below_the_point_estimate_and_tightens_with_sample_size():
    small = wilson_lower_bound(7, 10)
    large = wilson_lower_bound(70, 100)

    assert small < 0.7 and large < 0.7
    assert large > small
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(21, 30) == pytest.approx(0.626, abs=0.005)


def test_policy_table_trades_strong_buckets_skips_weak_ones_and_learns_small_ones():
    trades = pd.DataFrame(
        _trades("10:00-11:00", wins=21, losses=9, win_usd=60.0, loss_usd=45.0)
        + _trades("12:00-13:00", wins=12, losses=18, win_usd=45.0, loss_usd=45.0)
        + _trades("15:00-close", wins=4, losses=1, win_usd=60.0, loss_usd=45.0)
        + [{"time_bucket": "09:30-10:00", "entry_grade": "A", "entry_price": None, "exit_price": None, "pnl_usd": None}]
    )

    table = build_policy_table(trades, ["time_bucket"], PolicyConfig(min_trades=20, account_usd=500.0))
    by_bucket = table.set_index("time_bucket")

    strong = by_bucket.loc["10:00-11:00"]
    assert strong["action"] == "trade"
    assert strong["trades"] == 30
    assert strong["win_rate"] == pytest.approx(0.7)
    assert strong["win_rate_lb80"] < strong["win_rate"]
    assert strong["expectancy_usd"] == pytest.approx(0.7 * 60 - 0.3 * 45, abs=0.01)
    assert strong["expectancy_lb80_usd"] < strong["expectancy_usd"]
    assert strong["size_contracts"] >= 1

    weak = by_bucket.loc["12:00-13:00"]
    assert weak["action"] == "skip"
    assert weak["size_contracts"] == 0

    small = by_bucket.loc["15:00-close"]
    assert small["action"] == "learn"
    assert small["trades"] == 5

    assert "09:30-10:00" not in by_bucket.index


def test_policy_table_caps_size_and_handles_buckets_without_losses():
    trades = pd.DataFrame(_trades("10:00-11:00", wins=25, losses=0, win_usd=80.0, loss_usd=0.0, premium=0.5))

    table = build_policy_table(trades, ["time_bucket"], PolicyConfig(min_trades=20, account_usd=5000.0, max_contracts=2))
    row = table.iloc[0]

    assert row["action"] == "trade"
    assert math.isinf(row["profit_factor"])
    assert row["size_contracts"] == 2


def test_policy_table_with_no_filled_trades_is_empty():
    trades = pd.DataFrame([{"time_bucket": "x", "entry_price": None, "exit_price": None, "pnl_usd": None}])

    assert build_policy_table(trades, ["time_bucket"]).empty
