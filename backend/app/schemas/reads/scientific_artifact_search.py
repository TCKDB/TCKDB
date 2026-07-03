"""Read schemas for /api/v1/scientific/artifacts/search.

Standalone artifact-metadata search surface. Returns artifact rows
with their owning calculation's identity, level-of-theory, software,
workflow-tool, and review badge attached so callers can answer
"which artifacts exist for X" without first chaining a calculation
search.

The per-record ``artifact`` block reuses ``CalculationArtifactSummary``
from ``scientific_calculation`` exactly — the same shape served by
``GET /scientific/calculations/{handle}?include=artifacts`` and the
reaction-full artifacts section — so clients have one artifact-metadata
schema regardless of how they discovered it.

**Metadata only.** No body bytes, no presigned download URLs, no
geometry/coordinate payloads. The persisted ``uri`` is exposed verbatim
(typically ``s3://bucket/key``) for parity with the existing artifact
surface; resolving it to a download is an upload-side/artifact-service
responsibility outside this read.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    ArtifactKind,
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
)
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_WORKFLOW_TOOL_LENGTH as _MAX_WORKFLOW_TOOL_LENGTH,
)
from app.schemas.reads.scientific_calculation import (
    CalculationArtifactSummary,
    CalculationOwnerSummary,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)

# ---------------------------------------------------------------------------
# Per-record building blocks
# ---------------------------------------------------------------------------


class ArtifactCalculationContext(BaseModel):
    """Owning-calculation context attached to every artifact record.

    Always present (calculation context is the default include).
    Carries the calculation's public ref plus minimal core metadata so
    callers can identify, attribute, and (optionally) follow up with
    ``/scientific/calculations/{ref}`` for the full record. The
    ``review`` badge mirrors the owning calculation's review state.

    LoT / software / workflow-tool summaries are populated only when
    ``include=calculation`` (the default) is in effect; absent fields
    are explicit ``null`` so callers can distinguish "no LoT on this
    calc" from "did not ask".
    """

    calculation_id: int | None = None
    calculation_ref: str
    calculation_type: CalculationType
    quality: CalculationQuality
    created_at: datetime | None = None
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    review: RecordReviewBadge


class AvailableArtifactSections(BaseModel):
    """Boolean map describing which optional artifact-record sections
    have data. Mirrors the ``available_sections`` pattern used by other
    scientific read shapes; lets clients introspect availability
    without fetching the heavy sections.
    """

    has_calculation: bool = True
    has_owner: bool = False
    has_review_history: bool = False


class ScientificArtifactRecord(BaseModel):
    """One artifact-metadata row with its owning-calculation context.

    The ``artifact`` block is the same ``CalculationArtifactSummary``
    surface as the calculation detail's ``include=artifacts`` section,
    so artifact metadata is identical regardless of the discovery path.

    ``owner`` (calculation's species_entry / transition_state_entry) is
    populated only when ``include=owner`` was supplied; it is the same
    ``CalculationOwnerSummary`` produced by the calculation detail
    endpoint.
    """

    artifact: CalculationArtifactSummary
    calculation: ArtifactCalculationContext
    owner: CalculationOwnerSummary | None = None
    available_sections: AvailableArtifactSections


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ScientificArtifactSearchRequest(BaseModel):
    """Service-layer request for /scientific/artifacts/search.

    Filters AND-combine. At least one meaningful filter is required —
    pure pagination / include / review knobs are rejected with 422
    ``missing_filter`` to avoid accidental public table scans.
    """

    # --- artifact filters ------------------------------------------------
    artifact_kind: ArtifactKind | None = None
    filename: str | None = Field(default=None, max_length=512)
    filename_contains: str | None = Field(default=None, max_length=512)
    sha256: str | None = Field(default=None, max_length=64)
    has_sha256: bool | None = None
    has_bytes: bool | None = None
    bytes_min: int | None = Field(default=None, ge=0)
    bytes_max: int | None = Field(default=None, ge=0)

    # --- calculation filters --------------------------------------------
    calculation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    calculation_type: CalculationType | None = None
    quality: CalculationQuality | None = None
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    software: str | None = Field(
        default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH
    )
    software_version: str | None = Field(default=None, max_length=128)
    workflow_tool: str | None = Field(
        default=None, max_length=_MAX_WORKFLOW_TOOL_LENGTH
    )
    workflow_tool_version: str | None = Field(default=None, max_length=128)

    # --- owner filters ---------------------------------------------------
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transition_state_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_observation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- time filters ----------------------------------------------------
    created_after: datetime | None = None
    created_before: datetime | None = None

    # --- review/trust filters -------------------------------------------
    # Review state lives on the owning calculation, not on the artifact
    # itself. The trust gate hides artifacts whose owning calculation is
    # rejected/deprecated by default; the opt-in flags restore them.
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # --- sort / include / pagination ------------------------------------
    # v0 forbids client-supplied sort; the default deterministic order
    # always applies.
    sort: str | None = None
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class RequestEcho(BaseModel):
    """Echo of the parsed request — surfaced in the response envelope."""

    filter: dict[str, object]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificArtifactSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/artifacts/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificArtifactRecord]
    pagination: Pagination


__all__ = [
    "ArtifactCalculationContext",
    "AvailableArtifactSections",
    "RequestEcho",
    "ScientificArtifactRecord",
    "ScientificArtifactSearchRequest",
    "ScientificArtifactSearchResponse",
]
