"""Read schemas for /api/v1/scientific/conformers/search.

Search returns records at the **conformer-group grain** — one record
per `conformer_group` row that matches the filter set. Per-record shape
is the same `ScientificConformerGroupRecord` used by the group detail
endpoint, so a generic client can parse search and detail responses
with one set of code.

See ``backend/docs/specs/scientific_conformer_reads.md``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    ConformerSelectionKind,
    RecordReviewStatus,
    ScientificOriginKind,
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
from app.schemas.reads.scientific_common import (
    Pagination,
    ProfiledRequestEcho,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_conformer import (
    ScientificConformerGroupRecord,
)


class ConformerEvidenceMatch(str, Enum):
    """Quantifier applied to the ``has_*`` evidence filters.

    A conformer group holds several observations, so "does this group
    have frequency evidence?" has two honest readings. This parameter
    makes the caller pick one at the call site instead of hiding the
    choice inside a bare boolean.

    ``any_observation`` (default, and the historical behaviour)
        The group matches ``has_freq=true`` when **at least one** of its
        observations has a ``freq`` calculation. ``has_freq=false`` is
        the negation: **no** observation has one.

    ``all_observations``
        The group matches ``has_freq=true`` when it has at least one
        observation and **every** observation has a ``freq``
        calculation — the complete-coverage question the old boolean
        could not express. ``has_freq=false`` is again the negation of
        that: the group has at least one observation and **at least one
        of them lacks** ``freq`` — i.e. coverage is *incomplete*, which
        includes zero coverage.

    A group with no observations at all matches neither direction under
    ``all_observations``. "Every observation has freq" must not be
    vacuously true of a basin with nothing in it.

    The quantifier applies to the whole evidence family
    (``has_calculations``, ``has_opt``, ``has_freq``, ``has_sp``,
    ``has_geometries``, ``has_geometry_validation``,
    ``has_scf_stability``). It is a modifier, not a filter: supplying it
    alone does not satisfy the at-least-one-filter rule.
    """

    any_observation = "any_observation"
    all_observations = "all_observations"


class ConformersSearchRequest(BaseModel):
    """Service-layer request for /scientific/conformers/search.

    Filters AND-combine. At least one meaningful filter is required —
    requests with only pagination / include / review knobs are rejected
    with 422 ``missing_filter`` to avoid accidental public table scans.
    """

    # --- identity filters ------------------------------------------------
    species_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    species_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_group_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    conformer_observation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- curation filters ------------------------------------------------
    selection_kind: ConformerSelectionKind | None = None
    has_selection: bool | None = None
    assignment_scheme_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- evidence filters ------------------------------------------------
    has_observations: bool | None = None
    has_calculations: bool | None = None
    has_geometries: bool | None = None
    has_opt: bool | None = None
    has_freq: bool | None = None
    has_sp: bool | None = None
    has_geometry_validation: bool | None = None
    has_scf_stability: bool | None = None
    evidence_match: ConformerEvidenceMatch = (
        ConformerEvidenceMatch.any_observation
    )

    # --- provenance filters ----------------------------------------------
    scientific_origin: ScientificOriginKind | None = None
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
    limit: int = 50


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed request — surfaced in the response envelope."""

    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificConformersSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/conformers/search.

    Records are at the conformer-group grain — one record per
    matching ``conformer_group`` row.
    """

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificConformerGroupRecord]
    pagination: Pagination


__all__ = [
    "ConformerEvidenceMatch",
    "ConformersSearchRequest",
    "RequestEcho",
    "ScientificConformersSearchResponse",
]
