from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from src.backtest.context import DayContext, alignment
from src.config import AppConfig
from src.models import GRADE_RANK, Direction, Grade, SetupEvaluation


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    contracts: int = 0


class TradingPolicy:
    """The rules that survived the backtest, applied to one live evaluation at a time."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.trading = config.trading
        self.market_timezone = ZoneInfo(config.app.market_timezone)
        self.last_entry = dt_time.fromisoformat(self.trading.last_entry_time)
        self.flat_at = dt_time.fromisoformat(self.trading.flat_time)

    def evaluate_entry(self, evaluation: SetupEvaluation, context: DayContext, now: datetime, premium: float | None) -> Decision:
        grade = evaluation.grade
        if GRADE_RANK[grade] < GRADE_RANK[Grade(self.trading.min_grade)]:
            return Decision(False, f"grade {grade.value} below {self.trading.min_grade}")
        if grade != Grade.A_PLUS and self.trading.plain_a_max_lag_min is not None:
            lag = evaluation.sma_cross_lag_min
            if lag is None or lag > self.trading.plain_a_max_lag_min:
                return Decision(False, f"plain A with lag {lag} > {self.trading.plain_a_max_lag_min} min")
        trade_alignment = alignment(evaluation.direction, context.trend_bias) if context.trend_label != "unknown" else "unknown"
        if self.trading.require_alignment and trade_alignment != "aligned":
            return Decision(False, f"{evaluation.direction.value} trade not aligned with {context.trend_label} bias")
        local_now = now.astimezone(self.market_timezone)
        if local_now.time() > self.last_entry:
            return Decision(False, f"after last entry time {self.trading.last_entry_time}")
        if evaluation.sma_cross_age_bars is not None and evaluation.sma_cross_age_bars > self.config.grading.fresh_cross_max_bars:
            return Decision(False, "crossover is stale")
        contracts = self.size(evaluation.direction, trade_alignment, premium)
        if contracts == 0:
            return Decision(False, f"premium {premium} too large for risk capital")
        return Decision(True, f"{grade.value} {evaluation.direction.value} {trade_alignment}", contracts)

    def size(self, direction: Direction, trade_alignment: str, premium: float | None) -> int:
        """Base contracts from risk capital, doubled for aligned bears, capped by max contracts and position cost."""
        base = max(1, math.floor(self.trading.risk_capital_usd / self.trading.usd_per_contract))
        contracts = base * (self.trading.bear_multiplier if direction == Direction.BEAR and trade_alignment == "aligned" else 1)
        contracts = min(contracts, self.trading.max_contracts)
        if premium is not None and premium > 0:
            affordable = math.floor(self.trading.risk_capital_usd * self.trading.max_position_cost_pct / 100.0 / (premium * 100.0))
            contracts = min(contracts, affordable)
        return max(0, contracts)

    def target_price(self, entry_price: float) -> float:
        return round(entry_price * (1 + self.trading.target_pct / 100.0), 2)

    def stop_price(self, entry_price: float) -> float:
        return round(entry_price * (1 - self.trading.stop_pct / 100.0), 2)

    def past_flat_time(self, now: datetime) -> bool:
        return now.astimezone(self.market_timezone).time() >= self.flat_at
