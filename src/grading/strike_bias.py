from __future__ import annotations

from src.config import AppConfig
from src.models import Grade, StrikeBias, VolumeGrade


def recommend_strike_bias(
    grade: Grade,
    config: AppConfig,
    *,
    structure_aligned: bool,
    momentum_aligned: bool,
    volume_grade: VolumeGrade,
    one_min_agreement: str,
) -> tuple[StrikeBias, str]:
    strong_expansion = (
        structure_aligned
        and momentum_aligned
        and volume_grade == VolumeGrade.STRONG
        and one_min_agreement in {"yes", "disabled"}
    )

    # OTM is reserved for A+; a plain A stays ATM no matter how strong the expansion looks.
    if grade == Grade.A_PLUS:
        if config.grading.allow_two_otm and strong_expansion:
            return StrikeBias.TWO_OTM, "Exceptional A+ expansion setup with strong structure, momentum, and volume."
        if config.grading.allow_grade_a_otm:
            return StrikeBias.ONE_OTM, "A+ expansion setup supports a modest OTM bias."
        return StrikeBias.ATM, "A+ setup keeps ATM because OTM bias is disabled."

    if grade == Grade.A:
        return StrikeBias.ATM, "Grade A setup is valid; ATM is the default and OTM is reserved for A+."

    if grade == Grade.B:
        if config.grading.allow_grade_b_itm and (not momentum_aligned or one_min_agreement == "no"):
            return StrikeBias.ONE_ITM, "Confirmation is incomplete, so a conservative ITM bias is safer."
        return StrikeBias.ATM, "Constructive Grade B setup keeps ATM as the default advisory."

    if config.grading.allow_grade_c_soft_alerts:
        if structure_aligned:
            return StrikeBias.ONE_ITM, "Soft Grade C alert keeps risk conservative with a small ITM bias."
        return StrikeBias.ATM, "Soft Grade C review alert uses ATM instead of forcing OTM."
    return StrikeBias.SKIP, "Setup quality is weak or conflicted, so skipping is the default."
