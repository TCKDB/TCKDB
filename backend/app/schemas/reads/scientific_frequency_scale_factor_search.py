"""Read schemas for /api/v1/scientific/frequency-scale-factors/search.

Records reuse :class:`ScientificFrequencyScaleFactorRecord` from the
detail endpoint so search and detail callers parse responses with one
set of code.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import FrequencyScaleKind
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_frequency_scale_factor import (
    ScientificFrequencyScaleFactorRecord,
)


class FrequencyScaleFactorSearchRequest(BaseModel):
    """Service-layer request for /scientific/frequency-scale-factors/search.

    Filters AND-combine; at least one meaningful filter is required
    (422 ``missing_filter`` otherwise). Bool filters default to
    ``None``; explicit ``False`` is meaningful — only ``None`` skips
    the filter gate.

    The FSF row has no ``model_kind`` column; the request field is
    accepted for forward compatibility but currently unsupported and
    documented as deferred (see the spec).
    """

    # --- identity filters -------------------------------------------------
    frequency_scale_factor_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- scalar value filters --------------------------------------------
    value: float | None = None
    value_min: float | None = None
    value_max: float | None = None
    scale_kind: FrequencyScaleKind | None = None

    # --- deferred (not yet wired to a column) ----------------------------
    model_kind: str | None = Field(default=None, max_length=64)

    # --- provenance filters ----------------------------------------------
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    software: str | None = Field(
        default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH
    )
    software_version: str | None = Field(default=None, max_length=128)
    literature_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- usage filters ---------------------------------------------------
    used_by_statmech: bool | None = None

    # --- review filters (FSF is non-reviewable; kept for shape parity) --
    include_rejected: bool = False
    include_deprecated: bool = False
    min_review_status: str | None = None

    # --- sort / include / pagination -------------------------------------
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class RequestEcho(BaseModel):
    """Echo of the parsed request — surfaced in the response envelope."""

    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificFrequencyScaleFactorSearchResponse(BaseModel):
    """Response envelope for the FSF search endpoint."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificFrequencyScaleFactorRecord]
    pagination: Pagination


__all__ = [
    "FrequencyScaleFactorSearchRequest",
    "RequestEcho",
    "ScientificFrequencyScaleFactorSearchResponse",
]
