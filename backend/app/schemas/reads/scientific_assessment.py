"""Compact trust and reproducibility summaries for machine consumers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.db.models.common import ReproducibilityGrade


class MadeUnder(BaseModel):
    """Which rubric judged an assessment, at which version, and whether that is still the current one.

    ``state`` is the one word a reader needs: ``current`` (made under the rubric version in force, from the inputs there are
    now), ``stale`` (it is not), or ``unassessed`` (nobody has assessed this record, so there is no rubric to name). ``stale`` is not ``unassessed``: a stale assessment exists and says what it was made under.

    ``stale_reason`` says why a stale assessment is stale: ``rubric_version`` (the rubric has moved on, or the
    assessment was stamped before the 2026-10 versioning reset, as version 0, which is never current) or
    ``inputs_changed`` (same rubric version, but the inputs it was computed from differ now: new evidence, or a changed
    rule under the same version).

    ``assessed_version`` 0 is "stamped before the 2026-10 versioning reset". It is never a current version.
    Nothing here ranks a record: it names the rule that judged it, not how good the record is.
    """

    rubric: str | None = None
    assessed_version: int | None = None
    current_rubric: str | None = None
    current_version: int | None = None
    state: Literal["current", "stale", "unassessed"]
    stale_reason: Literal["rubric_version", "inputs_changed"] | None = None
    #: False when the response held several records and only the rubric name and version were compared: whether the
    #: inputs changed since was not checked, so ``current`` there means "the rubric is the one in force", nothing more.
    inputs_checked: bool = True


class DeterministicTrustSummary(BaseModel):
    """Current code-defined evidence assessment; not a quality claim.

    Evaluated at read time under the rubric version in force, so ``made_under`` is always ``current``.
    """

    rubric: str
    rubric_version: int
    grade: str
    hard_fail: str | None = None
    made_under: MadeUnder | None = None


class ReproducibilityAssessmentSummary(BaseModel):
    """Latest immutable reproducibility assessment and freshness state.

    ``assessment_ref`` identifies the stored immutable claim; it does not
    imply that the claim remains current or curator-approved.
    """

    state: Literal["current", "stale", "unassessed"]
    assessment_ref: str | None = None
    rubric: str | None = None
    rubric_version: int | None = None
    grade: ReproducibilityGrade | None = None
    assessed_at: datetime | None = None
    made_under: MadeUnder | None = None


class PublicAssessmentSummary(BaseModel):
    """Small opt-in assessment projection shared by scientific records."""

    deterministic_trust: DeterministicTrustSummary
    reproducibility: ReproducibilityAssessmentSummary


__all__ = [
    "DeterministicTrustSummary",
    "MadeUnder",
    "PublicAssessmentSummary",
    "ReproducibilityAssessmentSummary",
]
