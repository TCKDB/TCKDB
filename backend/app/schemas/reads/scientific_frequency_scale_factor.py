"""Read schemas for the scientific frequency-scale-factor surface.

Covers:

- ``GET /api/v1/scientific/frequency-scale-factors/{frequency_scale_factor_ref_or_id}``
- ``GET/POST /api/v1/scientific/frequency-scale-factors/search``

FrequencyScaleFactor is a content-derived reference table — same
(level_of_theory_id, software_id, scale_kind, value, source_literature_id,
workflow_tool_release_id) tuple always produces the same ``public_ref``
(prefix ``fsf_``). It is **not** in ``SubmissionRecordType``, so it has
no per-row review history; the response envelope still carries a
``review_summary`` block (always empty) to stay shape-compatible with
the other scientific detail endpoints.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import FrequencyScaleKind
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class FrequencyScaleFactorCoreBlock(BaseModel):
    """Direct frequency_scale_factor row metadata.

    The ORM has no ``model_kind`` column for FSF rows (the request-side
    ``model_kind`` filter is reserved for future use and currently
    unsupported). ``scale_kind`` is the canonical column.
    """

    frequency_scale_factor_id: int | None = None
    frequency_scale_factor_ref: str
    scale_kind: FrequencyScaleKind
    value: float
    note: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Used-by + evidence + available_sections
# ---------------------------------------------------------------------------


class FrequencyScaleFactorUsageSummary(BaseModel):
    """One inverse-link to a record that uses this scale factor.

    The ``endpoint`` field is a relative URL the client can follow for
    the full target record. Currently only ``record_type='statmech'`` is
    surfaced (direct FK ``statmech.frequency_scale_factor_id``).
    """

    record_type: str
    record_ref: str
    record_id: int | None = None
    endpoint: str


class FrequencyScaleFactorEvidenceSummary(BaseModel):
    """Bounded evidence projection for a frequency-scale-factor row."""

    has_literature_source: bool
    has_workflow_tool_source: bool
    has_software_dimension: bool
    statmech_usage_count: int
    has_statmech_usage: bool


class AvailableFrequencyScaleFactorSections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_used_by: bool
    has_literature: bool


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificFrequencyScaleFactorRecord(BaseModel):
    """One FSF row projected as a scientific record."""

    frequency_scale_factor: FrequencyScaleFactorCoreBlock
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    literature: LiteratureSummary | None = None
    evidence_summary: FrequencyScaleFactorEvidenceSummary
    available_sections: AvailableFrequencyScaleFactorSections

    # Optional include blocks
    used_by: list[FrequencyScaleFactorUsageSummary] | None = None


class ScientificFrequencyScaleFactorDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/frequency-scale-factors/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificFrequencyScaleFactorRecord


__all__ = [
    "AvailableFrequencyScaleFactorSections",
    "FrequencyScaleFactorCoreBlock",
    "FrequencyScaleFactorEvidenceSummary",
    "FrequencyScaleFactorUsageSummary",
    "RequestEcho",
    "ScientificFrequencyScaleFactorDetailResponse",
    "ScientificFrequencyScaleFactorRecord",
]
