from __future__ import annotations

from src.models import GRADE_RANK, SetupEvaluation


class AlertDeduper:
    """One alert per crossover episode and direction; a later upgrade to a higher grade may alert once more.

    Without this, a setup that stays A for ten minutes re-alerts on every 1m evaluation.
    """

    def __init__(self) -> None:
        self._best_rank: dict[tuple[str, str, str], int] = {}

    def should_alert(self, evaluation: SetupEvaluation) -> bool:
        key = (
            evaluation.symbol,
            evaluation.direction.value,
            evaluation.sma_cross_time.isoformat() if evaluation.sma_cross_time else "none",
        )
        rank = GRADE_RANK[evaluation.grade]
        if rank <= self._best_rank.get(key, -1):
            return False
        self._best_rank[key] = rank
        return True
