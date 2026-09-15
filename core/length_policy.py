"""Shared prose-length policy for AI writing workflows."""

from __future__ import annotations

from dataclasses import dataclass


PREFERRED_MIN_RATIO = 0.95
PREFERRED_MAX_RATIO = 1.05
REVIEW_MIN_RATIO = 0.90
REVIEW_MAX_RATIO = 1.10

LENGTH_SEVERELY_UNDER = "severely_under"
LENGTH_UNDER = "under"
LENGTH_QUALIFIED = "qualified"
LENGTH_OVER = "over"
LENGTH_SEVERELY_OVER = "severely_over"


@dataclass(frozen=True)
class LengthAssessment:
    """One deterministic assessment against a prose target."""

    target_chars: int
    preferred_min: int
    preferred_max: int
    review_min: int
    review_max: int
    actual_chars: int
    missing_to_target: int
    status: str

    @property
    def is_qualified(self) -> bool:
        return self.status == LENGTH_QUALIFIED

    @property
    def is_under(self) -> bool:
        return self.status in {LENGTH_SEVERELY_UNDER, LENGTH_UNDER}

    @property
    def is_over(self) -> bool:
        return self.status in {LENGTH_OVER, LENGTH_SEVERELY_OVER}

    @property
    def requires_strong_confirmation(self) -> bool:
        return self.status in {LENGTH_SEVERELY_UNDER, LENGTH_SEVERELY_OVER}


def assess_length(actual_chars: int, target_chars: int) -> LengthAssessment:
    """Classify a prose length using preferred and review ranges."""
    target = max(1, int(target_chars))
    actual = max(0, int(actual_chars))
    preferred_min = round(target * PREFERRED_MIN_RATIO)
    preferred_max = round(target * PREFERRED_MAX_RATIO)
    review_min = round(target * REVIEW_MIN_RATIO)
    review_max = round(target * REVIEW_MAX_RATIO)
    if actual < review_min:
        status = LENGTH_SEVERELY_UNDER
    elif actual < preferred_min:
        status = LENGTH_UNDER
    elif actual <= preferred_max:
        status = LENGTH_QUALIFIED
    elif actual <= review_max:
        status = LENGTH_OVER
    else:
        status = LENGTH_SEVERELY_OVER
    return LengthAssessment(
        target_chars=target,
        preferred_min=preferred_min,
        preferred_max=preferred_max,
        review_min=review_min,
        review_max=review_max,
        actual_chars=actual,
        missing_to_target=max(0, target - actual),
        status=status,
    )


def length_status_label(status: str) -> str:
    return {
        LENGTH_SEVERELY_UNDER: "明显低于目标",
        LENGTH_UNDER: "低于理想范围",
        LENGTH_QUALIFIED: "长度达标",
        LENGTH_OVER: "高于理想范围",
        LENGTH_SEVERELY_OVER: "明显高于目标",
    }.get(str(status), "长度待审阅")
