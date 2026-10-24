"""Compact trust and reproducibility summaries for machine consumers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from tckdb_schemas.coded_error import CodedValidationError

from app.db.models.common import ReproducibilityGrade


def refuse_check_inputs_in_body(data: object) -> object:
    """A search body that names ``check_inputs`` at its top level is refused: it is a query parameter of the thermo and
    kinetics entry-scoped lists only. Judged on the parsed object, so a string value or a nested key that merely contains
    the text is not touched."""
    if isinstance(data, dict) and "check_inputs" in data:
        raise CodedValidationError(
            "check_inputs_not_supported",
            "check_inputs is supported only on the thermo and kinetics entry-scoped lists (with include=assessments) and, "
            "implicitly, on by-ref statmech and transport reads; this route does not compare inputs.",
        )
    return data


class MadeUnder(BaseModel):
    """Which rubric judged an assessment, at which version, and whether that is still the current one.

    ``state`` is the one word a reader needs: ``current`` (made under the rubric version in force, and the inputs were
    compared with today's and match), ``stale`` (it is not), ``inputs_unchecked`` (the rubric version is the one in force
    but this route does not compare the inputs, so nothing is claimed about them) or ``unassessed`` (nobody has assessed
    this record, so there is no rubric to name). ``stale`` is not ``unassessed``: a stale assessment exists and says what it was made under.

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
    state: Literal["current", "stale", "unassessed", "inputs_unchecked"]
    stale_reason: Literal["rubric_version", "prompt_version", "inputs_changed"] | None = None
    #: Whether the inputs were compared with today's: true only on a route that checks them. ``null`` when there is
    #: nothing to check (``unassessed``).
    inputs_checked: bool | None = None
    #: Why the inputs were not compared although the route asked: ``curated_profile`` (a profile that hides records
    #: would let a hidden record's edit move a visible verdict).
    inputs_unchecked_reason: Literal["curated_profile"] | None = None


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

    state: Literal["current", "stale", "unassessed", "inputs_unchecked"]
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
    "refuse_check_inputs_in_body",
]
