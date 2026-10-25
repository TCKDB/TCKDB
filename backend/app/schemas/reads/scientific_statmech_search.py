"""Read schemas for /api/v1/scientific/statmech/search.

Records reuse :class:`ScientificStatmechRecord` from the detail
endpoint so search and detail callers parse responses with one set
of code.

See ``backend/docs/specs/scientific_statmech_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models.common import (
    RecordReviewStatus,
    StatmechTreatmentKind,
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
from app.schemas.reads.scientific_assessment import refuse_check_inputs_in_body
from app.schemas.reads.scientific_common import (
    CursorToken,
    OrderEcho,
    Pagination,
    ProfiledRequestEcho,
    ReviewStatusSummary,
    ReviewVisibilityEcho,
    ScientificResponse,
    WatermarkEcho,
)
from app.schemas.reads.scientific_statmech import (
    ScientificStatmechRecord,
)


class StatmechSearchRequest(BaseModel):
    """Service-layer request for /scientific/statmech/search.

    Filters AND-combine. At least one meaningful filter is required —
    requests with only pagination / include / review knobs are
    rejected with 422 ``missing_filter``.

    A key the model does not declare is a 422 (``extra_forbidden``), never
    ignored: a misspelled filter would otherwise quietly widen the result.
    """

    @model_validator(mode="before")
    @classmethod
    def _refuse_check_inputs(cls, data):
        return refuse_check_inputs_in_body(data)


    model_config = ConfigDict(extra="forbid")

    # --- identity filters ------------------------------------------------
    species_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    statmech_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_group_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_observation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    #: Statmech records owned by this transition-state entry (``tse_...``).
    transition_state_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    #: Records whose OWN model declaration is scoped to exactly this basin
    #: (``cg_...``). Unlike ``conformer_group_ref`` (owning-entry context), it
    #: never matches by owning entry, undeclared rows, or ensemble members.
    declared_conformer_group_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- statmech scalar filters -----------------------------------------
    model_kind: StatmechTreatmentKind | None = None

    # --- evidence filters ------------------------------------------------
    has_source_calculations: bool | None = None
    has_freq_calculation: bool | None = None
    has_rotor_scans: bool | None = None
    has_torsions: bool | None = None

    # --- provenance filters ----------------------------------------------
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

    # --- review filters --------------------------------------------------
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # --- sort / include / pagination -------------------------------------
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    cursor: CursorToken = None
    limit: int = 50


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed request — surfaced in the response envelope.

    Both the broad search and the per-species read return every matching record in ``sort`` order; neither picks one.
    """

    filter: dict[str, Any]
    sort: OrderEcho
    include: list[str] = Field(default_factory=list)
    #: Review statuses this read showed and excluded, as computed.
    review_visibility: ReviewVisibilityEcho | None = None


class ScientificStatmechSearchResponse(ScientificResponse):
    """Response envelope for /api/v1/scientific/statmech/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificStatmechRecord]
    pagination: Pagination
    #: Where the next page starts: pass it back as ``cursor``. ``null`` on the last page. See ``Pagination``.
    next_cursor: str | None = None
    #: The snapshot a cursor traversal is pinned to (set on every page that can continue a traversal).
    watermark: WatermarkEcho | None = None


__all__ = [
    "RequestEcho",
    "ScientificStatmechSearchResponse",
    "StatmechSearchRequest",
]
