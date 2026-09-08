"""Read schemas for /api/v1/scientific/level-of-theories/search.

Records reuse :class:`ScientificLevelOfTheoryRecord` from the detail
endpoint so search and detail callers parse responses with one set of
code -- same pattern as
``scientific_energy_correction_scheme_search.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import SpinTreatment
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_DISPERSION_LENGTH as _MAX_DISPERSION_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SOLVENT_LENGTH as _MAX_SOLVENT_LENGTH,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ProfiledRequestEcho,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_level_of_theory import (
    ScientificLevelOfTheoryRecord,
)


class LevelOfTheorySearchRequest(BaseModel):
    """Service-layer request for /scientific/level-of-theories/search.

    Filters AND-combine; at least one meaningful filter is required
    (422 ``missing_filter`` otherwise). Bool filters default to
    ``None``; explicit ``False`` is meaningful -- only ``None`` skips
    the filter gate. Same convention as the ECS/FSF search requests.

    ``include_rejected``/``include_deprecated``/``min_review_status``
    are accepted for shape parity with every other scientific search
    endpoint; a level of theory is non-reviewable, same as ECS/FSF, so
    they are no-ops here.
    """

    # --- identity filters -------------------------------------------------
    level_of_theory_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    lot_hash: str | None = Field(default=None, max_length=128)

    # --- scalar filters ----------------------------------------------------
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    dispersion: str | None = Field(
        default=None, max_length=_MAX_DISPERSION_LENGTH
    )
    solvent: str | None = Field(default=None, max_length=_MAX_SOLVENT_LENGTH)
    spin_treatment: SpinTreatment | None = None

    # --- evidence filters ----------------------------------------------------
    has_correction_schemes: bool | None = None
    has_frequency_scale_factors: bool | None = None

    # --- review filters (LOT is non-reviewable; kept for shape parity) ----
    include_rejected: bool = False
    include_deprecated: bool = False
    min_review_status: str | None = None

    # --- sort / include / pagination ---------------------------------------
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class LevelOfTheoryBrowseRequest(BaseModel):
    """Service-layer request for /scientific/level-of-theories/browse.

    Sibling to :class:`LevelOfTheorySearchRequest`, not a relaxation of
    it: no field here is required. Every field is duplicated verbatim
    from :class:`LevelOfTheorySearchRequest` rather than shared through
    a common base class -- same "identifier-free by construction, not by
    an unset field" argument
    ``TransitionStatesBrowseRequest``'s docstring gives for its own
    relationship to ``TransitionStatesSearchRequest``: a shared base is
    one edit away from a future required field silently reaching browse
    through inheritance.

    Unlike the reaction/transition-state/species browse siblings, this
    class keeps every filter :class:`LevelOfTheorySearchRequest` has,
    ``level_of_theory_ref`` included -- there is no owner/parent ref
    here to exclude the way ``reaction_ref`` is excluded from
    ``ReactionsBrowseRequest``: a level of theory is not owned by
    anything else, and narrowing an open listing down to one exact
    ``level_of_theory_ref`` is a legitimate page interaction (open the
    catalogue, then narrow), not a lookup that belongs on ``/search``
    instead.
    """

    # --- identity filters -------------------------------------------------
    level_of_theory_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    lot_hash: str | None = Field(default=None, max_length=128)

    # --- scalar filters ----------------------------------------------------
    method: str | None = Field(default=None, max_length=_MAX_METHOD_LENGTH)
    basis: str | None = Field(default=None, max_length=_MAX_BASIS_LENGTH)
    dispersion: str | None = Field(
        default=None, max_length=_MAX_DISPERSION_LENGTH
    )
    solvent: str | None = Field(default=None, max_length=_MAX_SOLVENT_LENGTH)
    spin_treatment: SpinTreatment | None = None

    # --- evidence filters ----------------------------------------------------
    has_correction_schemes: bool | None = None
    has_frequency_scale_factors: bool | None = None

    # --- review filters (LOT is non-reviewable; kept for shape parity) ----
    include_rejected: bool = False
    include_deprecated: bool = False
    min_review_status: str | None = None

    # --- sort / include / pagination ---------------------------------------
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed request -- surfaced in the response envelope."""

    filter: dict[str, Any]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificLevelOfTheorySearchResponse(BaseModel):
    """Response envelope for the level-of-theory search endpoint.

    Reused verbatim (not subclassed) for
    ``/scientific/level-of-theories/browse`` -- the same relationship
    :func:`app.services.scientific_read.reactions.browse_reactions` has
    to :class:`~app.schemas.reads.scientific_reactions.ScientificReactionSearchResponse`.
    A search response and a browse response are the same envelope over
    the same record type, so a caller's parser for one works unmodified
    against the other.
    """

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificLevelOfTheoryRecord]
    pagination: Pagination


__all__ = [
    "LevelOfTheoryBrowseRequest",
    "LevelOfTheorySearchRequest",
    "RequestEcho",
    "ScientificLevelOfTheorySearchResponse",
]
