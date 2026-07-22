"""Read schemas for /api/v1/scientific/energy-correction-schemes/search.

Records reuse :class:`ScientificEnergyCorrectionSchemeRecord` from the
detail endpoint so search and detail callers parse responses with one
set of code.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import EnergyCorrectionSchemeKind
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_energy_correction_scheme import (
    ScientificEnergyCorrectionSchemeRecord,
)


class EnergyCorrectionSchemeSearchRequest(BaseModel):
    """Service-layer request for /scientific/energy-correction-schemes/search.

    Filters AND-combine; at least one meaningful filter is required
    (422 ``missing_filter`` otherwise). Bool filters default to
    ``None``; explicit ``False`` is meaningful — only ``None`` skips
    the filter gate.

    Declared fields without an enforceable backing path remain accepted
    by request validation for compatibility, then fail closed with 422
    ``unsupported_filter`` (see the spec).
    """

    # --- identity filters -------------------------------------------------
    energy_correction_scheme_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- scalar filters --------------------------------------------------
    name: str | None = Field(default=None, max_length=256)
    version: str | None = Field(default=None, max_length=128)
    scheme_kind: EnergyCorrectionSchemeKind | None = None

    # --- provenance filters ----------------------------------------------
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    literature_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- declared but fail-closed until wired to a column ----------------
    software: str | None = Field(default=None, max_length=256)
    software_version: str | None = Field(default=None, max_length=128)

    # --- evidence / usage filters ----------------------------------------
    has_corrections: bool | None = None
    used_by_thermo: bool | None = None
    used_by_calculation: bool | None = None

    # --- review filters (ECS is non-reviewable; kept for shape parity) --
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


class ScientificEnergyCorrectionSchemeSearchResponse(BaseModel):
    """Response envelope for the ECS search endpoint."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificEnergyCorrectionSchemeRecord]
    pagination: Pagination


__all__ = [
    "EnergyCorrectionSchemeSearchRequest",
    "RequestEcho",
    "ScientificEnergyCorrectionSchemeSearchResponse",
]
