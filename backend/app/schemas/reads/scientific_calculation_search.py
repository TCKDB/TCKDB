"""Read schemas for /api/v1/scientific/calculations/search (MVP).

Request + response envelope for the calculation-search endpoint. The
per-record shape is the same ``ScientificCalculationRecord`` used by
the detail endpoint, so callers can parse search and detail results
with one set of code.

See ``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
)
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
    MAX_SMILES_LENGTH as _MAX_PARAMETER_VALUE_LENGTH,
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
    MAX_WORKFLOW_TOOL_LENGTH as _MAX_WORKFLOW_TOOL_LENGTH,
)

# Parameter keys are short tokens (route-line / canonical-vocab style);
# values can be long when they hold lists or formatted text. We reuse
# the SMILES bound (2048) for value lengths to avoid adding a new
# field-bounds constant for one filter.
_MAX_PARAMETER_KEY_LENGTH: int = _MAX_METHOD_LENGTH
from app.schemas.reads.scientific_calculation import (
    ScientificCalculationRecord,
)
from app.schemas.reads.scientific_common import (
    GeometryValidationStatus,
    Pagination,
    ReviewStatusSummary,
    SCFStabilityStatusValue,
)


class CalculationOwnerKind(str, Enum):
    """Discriminator for the ``owner_kind`` filter."""

    species_entry = "species_entry"
    transition_state_entry = "transition_state_entry"


class CalculationsSearchRequest(BaseModel):
    """Service-layer request for /scientific/calculations/search (MVP).

    Filters AND-combine. At least one meaningful filter is required —
    requests with only pagination / include / review knobs are rejected
    with 422 ``missing_filter`` to avoid accidental public table scans.
    """

    # --- owner filters ----------------------------------------------------
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transition_state_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transition_state_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    owner_kind: CalculationOwnerKind | None = None

    # --- calculation filters ---------------------------------------------
    calculation_type: CalculationType | None = None
    quality: CalculationQuality | None = None
    has_result: bool | None = None
    has_artifacts: bool | None = None
    has_input_geometry: bool | None = None
    has_output_geometry: bool | None = None
    # ``artifact_kind=X`` matches calcs that have at least one
    # ``calculation_artifact`` row of that kind (stricter than
    # ``has_artifacts=true`` and AND-combines with it). The ``include=artifacts``
    # heavy section continues to return *all* artifacts for the matching
    # calculation regardless of the filter — filter narrows calcs, include
    # selects child rows.
    artifact_kind: ArtifactKind | None = None
    created_before: datetime | None = None
    created_after: datetime | None = None

    # --- level-of-theory filters -----------------------------------------
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    lot_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    lot_hash: str | None = Field(default=None, max_length=128)

    # --- software / workflow filters -------------------------------------
    software: str | None = Field(
        default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH
    )
    software_version: str | None = Field(default=None, max_length=128)
    workflow_tool: str | None = Field(
        default=None, max_length=_MAX_WORKFLOW_TOOL_LENGTH
    )
    workflow_tool_version: str | None = Field(default=None, max_length=128)

    # --- validation filters ----------------------------------------------
    geometry_validation_status: GeometryValidationStatus | None = None
    scf_stability_status: SCFStabilityStatusValue | None = None

    # --- dependency-graph filters ----------------------------------------
    # ``dependency_role`` alone matches calcs that participate in any edge
    # of that role (as parent OR child). ``parent_calculation_ref`` returns
    # child calcs of that parent; ``child_calculation_ref`` returns parent
    # calcs of that child. Combining a ref with ``dependency_role`` narrows
    # by role. Combining both refs returns the two endpoints of an exact
    # edge if it exists, else empty.
    dependency_role: CalculationDependencyRole | None = None
    parent_calculation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    child_calculation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- calculation-parameter filters -----------------------------------
    # ``parameter_key`` / ``canonical_parameter_key`` filter calcs that
    # have at least one matching ``calculation_parameter`` row. Adding the
    # corresponding ``*_value`` requires the value to be on the **same
    # row** as the key. Supplying ``parameter_value`` without
    # ``parameter_key`` (or the canonical equivalent) is a 422 — values
    # alone aren't independently meaningful.
    parameter_key: str | None = Field(
        default=None, max_length=_MAX_PARAMETER_KEY_LENGTH
    )
    parameter_value: str | None = Field(
        default=None, max_length=_MAX_PARAMETER_VALUE_LENGTH
    )
    canonical_parameter_key: str | None = Field(
        default=None, max_length=_MAX_PARAMETER_KEY_LENGTH
    )
    canonical_parameter_value: str | None = Field(
        default=None, max_length=_MAX_PARAMETER_VALUE_LENGTH
    )

    # --- review filters --------------------------------------------------
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False
    # Orthogonal to review_status — opts in CalculationQuality.rejected.
    include_rejected_quality: bool = False

    # --- sort / include / pagination -------------------------------------
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class RequestEcho(BaseModel):
    """Echo of the parsed request — surfaced in the response envelope."""

    filter: dict[str, object]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificCalculationsSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/calculations/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificCalculationRecord]
    pagination: Pagination


__all__ = [
    "CalculationOwnerKind",
    "CalculationsSearchRequest",
    "RequestEcho",
    "ScientificCalculationsSearchResponse",
]
