"""Read schemas for /api/v1/scientific/transition-states/search.

Request + response envelope for the transition-state search endpoint.
Search returns records at the transition-state-entry grain because
entries are the concrete objects carrying charge/multiplicity/status
and the actual calculation evidence; the parent TS-concept context
travels along on each record.

See ``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    RecordReviewStatus,
    TransitionStateEntryStatus,
)
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_FAMILY_LENGTH as _MAX_FAMILY_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_METHOD_LENGTH as _MAX_METHOD_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SMILES_LENGTH as _MAX_SMILES_LENGTH,
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
from app.schemas.reads.scientific_transition_state import (
    ScientificTransitionStateEntryRecord,
)


class TransitionStatesSearchRequest(BaseModel):
    """Service-layer request for /scientific/transition-states/search.

    Filters AND-combine. At least one meaningful filter is required —
    requests with only pagination / include / review knobs are rejected
    with 422 ``missing_filter`` to avoid accidental public table scans.
    """

    # --- owner / parent filters ------------------------------------------
    reaction_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    reaction_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transition_state_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    transition_state_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # --- TS-entry scalar filters -----------------------------------------
    status: TransitionStateEntryStatus | None = None
    charge: int | None = None
    multiplicity: int | None = Field(default=None, ge=1)

    # --- evidence filters ------------------------------------------------
    has_calculations: bool | None = None
    has_opt: bool | None = None
    has_freq: bool | None = None
    has_sp: bool | None = None
    has_irc: bool | None = None
    has_path_search: bool | None = None
    has_geometry_validation: bool | None = None
    has_scf_stability: bool | None = None

    # --- level-of-theory / software / workflow filters -------------------
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

    # --- reaction context filters -----------------------------------------
    #: Exact match against the seeded ``reaction_family`` vocabulary
    #: (``ReactionFamily.name``), same semantics as
    #: ``ReactionSearchRequest.family`` / ``KineticsSearchRequest.family``:
    #: an unknown value returns an empty result set, not a 422 -- the
    #: discoverable set of valid tokens is ``/meta/reaction-families``.
    family: str | None = Field(default=None, max_length=_MAX_FAMILY_LENGTH)
    #: Exact-match structural filter over the *reaction's* participants
    #: (either side, reactant or product), not the TS entry itself -- a
    #: transition state has no molecular graph of its own (see the browse
    #: request's docstring). Matched the same way
    #: ``SpeciesBrowseRequest``'s ``mode=exact`` does: the query SMILES is
    #: parsed via RDKit to its InChIKey
    #: (``app.services.scientific_read.structure_query.inchi_key_from_query``)
    #: and compared against ``species.inchi_key`` -- no substructure or
    #: similarity mode here, deliberately narrower than the species filter.
    participant_smiles: str | None = Field(
        default=None, max_length=_MAX_SMILES_LENGTH
    )

    # --- review filters --------------------------------------------------
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # --- sort / include / pagination -------------------------------------
    sort: str | None = None  # rejected non-None per v0 sort policy
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class TransitionStatesBrowseRequest(BaseModel):
    """Service-layer request for the identifier-free ``/transition-states/browse``.

    Structurally has no ``reaction_ref`` / ``reaction_entry_ref`` /
    ``transition_state_ref`` / ``transition_state_entry_ref`` field — the
    four owner/parent filters :class:`TransitionStatesSearchRequest`
    carries. Those are handles: a caller who already has one of them wants
    an exact lookup on ``/transition-states/search``, not a catalogue
    listing. Mirrors ``SpeciesBrowseRequest`` beside
    ``SpeciesSearchRequest`` in ``scientific_species.py`` -- same
    "identifier-free by construction, not by an unset field" argument.

    Every other field is duplicated verbatim from
    :class:`TransitionStatesSearchRequest` rather than shared through a
    common base class. That is deliberate, matching
    ``species.py::_browse_filter_echo``'s documented reasoning: a shared
    base that both classes inherit is one edit away from a future
    identifier field silently reaching browse through inheritance, and
    reordering ``TransitionStatesSearchRequest``'s fields to make room for
    a base class would perturb that route's request-body schema for no
    behavioural gain. The only way ``/transition-states/browse`` gains an
    identifier field is a change written on *this* class.

    ``elements`` / ``max_heavy_atoms`` / ``min_heavy_atoms`` still do not
    exist here, and for the reason the old docstring gave: a transition
    state has no formula of its own. But "no composition axis to browse
    by" no longer holds as a blanket claim — it conflated "a TS entry has
    no molecular graph" with "nothing reachable from a TS entry has one",
    and the second is false: every TS entry belongs to exactly one
    reaction, and a reaction's participants are ordinary species with
    ordinary structures. ``participant_smiles`` filters on *that* graph
    (the reaction's reactants/products), not a TS graph that does not
    exist, which is why it is a structural filter without being a
    composition filter in the ``elements``/heavy-atom sense above. See
    ``docs/specs/scientific_transition_state_reads.md``.
    """

    # --- TS-entry scalar filters -----------------------------------------
    status: TransitionStateEntryStatus | None = None
    charge: int | None = None
    multiplicity: int | None = Field(default=None, ge=1)

    # --- evidence filters ------------------------------------------------
    has_calculations: bool | None = None
    has_opt: bool | None = None
    has_freq: bool | None = None
    has_sp: bool | None = None
    has_irc: bool | None = None
    has_path_search: bool | None = None
    has_geometry_validation: bool | None = None
    has_scf_stability: bool | None = None

    # --- level-of-theory / software / workflow filters -------------------
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

    # --- reaction context filters -----------------------------------------
    # Same fields, same semantics, as ``TransitionStatesSearchRequest``
    # above -- see that class for the full rationale.
    family: str | None = Field(default=None, max_length=_MAX_FAMILY_LENGTH)
    participant_smiles: str | None = Field(
        default=None, max_length=_MAX_SMILES_LENGTH
    )

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


class ScientificTransitionStatesSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/transition-states/search.

    Records are at the transition-state-entry grain.
    """

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificTransitionStateEntryRecord]
    pagination: Pagination


class ScientificTransitionStatesBrowseResponse(ScientificTransitionStatesSearchResponse):
    """Response envelope for /api/v1/scientific/transition-states/browse.

    Field-for-field identical to
    :class:`ScientificTransitionStatesSearchResponse` (same reason
    ``ScientificSpeciesBrowseResponse`` subclasses its search sibling in
    ``scientific_species.py``): a client's parser for the search response
    works unmodified against a browse response. Declared as its own class
    so the OpenAPI document and generated clients name the two surfaces
    separately even though nothing about the shape differs.
    """


__all__ = [
    "RequestEcho",
    "ScientificTransitionStatesBrowseResponse",
    "ScientificTransitionStatesSearchResponse",
    "TransitionStatesBrowseRequest",
    "TransitionStatesSearchRequest",
]
