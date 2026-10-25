"""The integers a select answer and its manifest may hold for the filter and eligibility steps, by exact path.

They are counts of candidates (how many each step removed, kept out or admitted), never ids. Tests that pin every
integer of a selection document at an exact path extend their allowed set with these.
"""

from __future__ import annotations

DEFAULT_NAMES = (
    "rejected_review_status",
    "deprecated_review_status",
    "evidence_hard_fail",
    "confirmed_blocking_findings",
    "rejected_calculation_quality",
)

#: In a response.
RESPONSE_STEP_COUNT_PATHS = frozenset(
    {
        "funnel.considered",
        "funnel.removed_by_filters",
        "funnel.removed_by_floor",
        "funnel.removed_by_eligibility",
        "funnel.remaining",
        "filters.removed",
        "eligibility[].excluded",
        "eligibility[].admitted_by_override",
    }
)

#: In a manifest (the funnel sits in the population block).
MANIFEST_STEP_COUNT_PATHS = frozenset(
    {
        "population.funnel.considered",
        "population.funnel.removed_by_filters",
        "population.funnel.removed_by_floor",
        "population.funnel.removed_by_eligibility",
        "population.funnel.remaining",
        *(f"population.funnel.excluded_by_default.{name}" for name in DEFAULT_NAMES),
        *(f"population.funnel.admitted_by_override.{name}" for name in DEFAULT_NAMES),
    }
)
