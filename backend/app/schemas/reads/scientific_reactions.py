"""Read schemas for /api/v1/scientific/reactions/search.

See docs/specs/read_api_mvp.md §Endpoint 2.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator
from tckdb_schemas.coded_error import CodedValidationError

from app.db.models.common import RecordReviewStatus
from app.schemas.reads._field_bounds import (
    MAX_FAMILY_LENGTH as _MAX_FAMILY_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PARTICIPANTS_PER_REACTION as _MAX_PARTICIPANTS_PER_REACTION,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SMILES_LENGTH as _MAX_SMILES_LENGTH,
)
from app.schemas.reads.scientific_common import (
    CollapseMode,
    Pagination,
    ProfiledRequestEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
)


class ReactionDirectionQuery(str, Enum):
    """v0 reaction-search direction enum.

    ``forward``  — query reactants/products match in stored orientation.
    ``reverse``  — query reactants/products match the swapped orientation.
    ``either``   — match in either orientation.

    ``exact`` is **not** in v0; the service rejects it with a deterministic
    422 error (per Phase 2.1 patch).
    """

    forward = "forward"
    reverse = "reverse"
    either = "either"


class ReactionSearchRequest(BaseModel):
    """Service-layer request model for reaction search."""

    reactants: list[str] = Field(
        default_factory=list,
        max_length=_MAX_PARTICIPANTS_PER_REACTION,
    )
    products: list[str] = Field(
        default_factory=list,
        max_length=_MAX_PARTICIPANTS_PER_REACTION,
    )
    direction: ReactionDirectionQuery = ReactionDirectionQuery.either
    family: str | None = Field(default=None, max_length=_MAX_FAMILY_LENGTH)

    # Phase C: explicit handles (refs) — useful when a caller already has
    # a reaction/reaction_entry ref from a previous response.
    reaction_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)
    reaction_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    sort: str | None = None  # rejected non-None per v0 sort policy.

    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50

    @field_validator("reactants", "products")
    @classmethod
    def _bound_participant_lengths(cls, value: list[str]) -> list[str]:
        """Reject participant SMILES that exceed the public free-text cap.

        ``smiles_too_long`` is a relationship code as of 2026-08-18: a
        supplied length against a configured maximum, neither named by
        the code. ``context`` carries both, and both are safe under the
        disclosure line in ``app.api.code_catalogue.Shape`` — the
        maximum is TCKDB's own constant, and the length is the caller's
        own string measured back to them. The string itself is *not*
        echoed: it adds nothing the length does not and would grow the
        body by up to the cap.

        ``CodedValidationError`` rather than the backend's
        ``CodedValueError`` because ``app.schemas.reads`` is on the wire
        side of the schema layer; both are caught by the same handler.
        """
        for item in value:
            if len(item) > _MAX_SMILES_LENGTH:
                raise CodedValidationError(
                    "smiles_too_long",
                    "participant SMILES exceeds "
                    f"the maximum length of {_MAX_SMILES_LENGTH}.",
                    context={
                        "max_length": _MAX_SMILES_LENGTH,
                        "length": len(item),
                    },
                )
        return value


# ---------------------------------------------------------------------------
# Per-record shapes
# ---------------------------------------------------------------------------


class ReactionParticipantSummary(BaseModel):
    """Reactant or product participant within a reaction-entry record.

    Phase B: ``species_entry_ref`` is the public stable handle for the
    participant species entry.
    """

    species_entry_id: int
    species_entry_ref: str
    smiles: str
    participant_index: int


class ReactionAvailability(BaseModel):
    """Boolean availability flags + counts per L1.

    ``has_atom_map`` says whether the record states which atom of the
    reactants and products is which atom of the transition state (ADR 0011).
    ``false`` is the ordinary state for everything deposited before atom
    mapping existed and for every barrierless channel; it means the record is
    incomplete, not wrong. It is here rather than only on the deep read so a
    consumer never has to fetch a reaction to find out that it cannot answer
    the question they came with.
    """

    has_kinetics: bool
    has_transition_state: bool
    has_path_search: bool
    has_atom_map: bool = False
    kinetics_count: int


class ReactionScientificRecord(BaseModel):
    """One reaction-entry row from /scientific/reactions/search.

    Phase B: ``reaction_ref`` and ``reaction_entry_ref`` are the public
    stable handles for the chem-reaction-level identity and the
    reaction-entry event, respectively.
    """

    reaction_id: int
    reaction_ref: str
    reaction_entry_id: int
    reaction_entry_ref: str
    equation: str
    matched_direction: ReactionDirectionQuery
    reversible: bool
    family: str | None = None
    review: RecordReviewBadge
    reactants: list[ReactionParticipantSummary]
    products: list[ReactionParticipantSummary]
    availability: ReactionAvailability


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificReactionSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/reactions/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ReactionScientificRecord]
    pagination: Pagination
