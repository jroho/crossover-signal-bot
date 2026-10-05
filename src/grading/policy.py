from __future__ import annotations

from src.models import GRADE_RANK, Grade


def grade_is_alertable(grade: Grade, alert_grades: list[str]) -> bool:
    """The lowest configured grade is the alert floor, so ["A", "B"] also covers A+."""
    configured = [Grade(value) for value in alert_grades if value in {item.value for item in Grade}]
    if not configured:
        return False
    floor = min(GRADE_RANK[item] for item in configured)
    return GRADE_RANK[grade] >= floor
