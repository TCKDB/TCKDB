"""Compact trust and reproducibility summaries for machine consumers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.db.models.common import ReproducibilityGrade


class DeterministicTrustSummary(BaseModel):
    """Current code-defined evidence assessment; not a quality claim."""

    rubric: str
    rubric_version: str
    grade: str
    hard_fail: str | None = None


class ReproducibilityAssessmentSummary(BaseModel):
    """Latest immutable reproducibility assessment and freshness state."""

    state: Literal["current", "stale", "unassessed"]
    rubric: str | None = None
    rubric_version: str | None = None
    grade: ReproducibilityGrade | None = None
    assessed_at: datetime | None = None


class PublicAssessmentSummary(BaseModel):
    """Small opt-in assessment projection shared by scientific records."""

    deterministic_trust: DeterministicTrustSummary
    reproducibility: ReproducibilityAssessmentSummary


__all__ = [
    "DeterministicTrustSummary",
    "PublicAssessmentSummary",
    "ReproducibilityAssessmentSummary",
]
