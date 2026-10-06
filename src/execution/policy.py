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
        self.first_entry = dt_time.fromisoformat(self.trading.first_entry_time) if self.trading.first_entry_time else None
        self.last_entry = dt_time.fromisoformat(self.trading.last_entry_time)
        self.bull_last_entry = dt_time.fromisoformat(self.trading.bull_last_entry_time) if self.trading.bull_last_entry_time else None
        self.flat_at = dt_time.fromisoformat(self.trading.flat_time)
        self.press_rank = GRADE_RANK[Grade(self.trading.press_min_grade)]

    def evaluate_entry(
        self,
        evaluation: SetupEvaluation,
        context: DayContext,
        now: datetime,
        premium: float | None,
        *,
        first_cross: bool = False,
        other_confirmed: bool = False,
    ) -> Decision:
        """Decide one entry. `first_cross` and `other_confirmed` describe the session so far (see ExecutionEngine)."""
        grade = evaluation.grade
        if GRADE_RANK[grade] < GRADE_RANK[Grade(self.trading.min_grade)]:
            return Decision(False, f"grade {grade.value} below {self.trading.min_grade}")
        if grade != Grade.A_PLUS and self.trading.plain_a_max_lag_min is not None:
            lag = evaluation.sma_cross_lag_min
            if lag is None or lag > self.trading.plain_a_max_lag_min:
                return Decision(False, f"plain A with lag {lag} > {self.trading.plain_a_max_lag_min} min")
        trade_alignment = alignment(evaluation.direction, context.trend_bias) if context.trend_label != "unknown" else "unknown"
        confirmation = self._confirmation(grade, trade_alignment, first_cross, other_confirmed)
        if self.trading.require_alignment and confirmation is None:
            return Decision(False, f"{evaluation.direction.value} trade not aligned with {context.trend_label} bias")
        local_now = now.astimezone(self.market_timezone)
        if self.first_entry is not None and local_now.time() < self.first_entry:
            return Decision(False, f"before first entry time {self.trading.first_entry_time}")
        if local_now.time() > self.last_entry:
            return Decision(False, f"after last entry time {self.trading.last_entry_time}")
        if evaluation.direction == Direction.BULL and self.bull_last_entry is not None and local_now.time() > self.bull_last_entry:
            return Decision(False, f"bull entry after {self.trading.bull_last_entry_time}")
        if evaluation.sma_cross_age_bars is not None and evaluation.sma_cross_age_bars > self.config.grading.fresh_cross_max_bars:
            return Decision(False, "crossover is stale")
        contracts = self.size(evaluation.direction, trade_alignment, premium, grade=grade)
        if contracts == 0:
            return Decision(False, f"premium {premium} too large for risk capital")
        return Decision(True, f"{grade.value} {evaluation.direction.value} {confirmation or trade_alignment}", contracts)

    def _confirmation(self, grade: Grade, trade_alignment: str, first_cross: bool, other_confirmed: bool) -> str | None:
        """What satisfies the alignment requirement: the day's bias, or for A+ an intraday confirmation."""
        if trade_alignment == "aligned":
            return "aligned"
        if self.trading.a_plus_confirmation and grade == Grade.A_PLUS:
            if first_cross:
                return "first-cross"
            if other_confirmed:
                return "other-index"
        return None

    def size(self, direction: Direction, trade_alignment: str, premium: float | None, grade: Grade | None = None) -> int:
        """Base contracts from risk capital, multiplied for aligned bears at or above press_min_grade, capped by
        max contracts and position cost."""
        base = max(1, math.floor(self.trading.risk_capital_usd / self.trading.usd_per_contract))
        press = direction == Direction.BEAR and trade_alignment == "aligned" and (grade is None or GRADE_RANK[grade] >= self.press_rank)
        contracts = base * (self.trading.bear_multiplier if press else 1)
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
