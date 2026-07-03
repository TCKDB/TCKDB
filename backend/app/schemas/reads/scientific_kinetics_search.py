"""Read schemas for /api/v1/scientific/kinetics/search.

Chemistry-first kinetics lookup: callers supply reactants/products and get
back fully-shaped kinetics records with the resolved reaction/reaction_entry
identity attached. Reuses ``KineticsRecord`` from ``scientific_kinetics`` so
workflow tools see the same kinetics block as the entry-id detail endpoint.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.db.models.common import KineticsModelKind, RecordReviewStatus
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
from app.schemas.reads._field_bounds import (
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
)
from app.schemas.reads.scientific_common import (
    CollapseMode,
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_kinetics import KineticsRecord
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionParticipantSummary,
)

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class KineticsSearchRequest(BaseModel):
    """Service-layer request for chemistry-first kinetics search.

    Reactants and/or products are required; matching uses the same
    species-multiset semantics as ``search_reactions``. ``direction=exact``
    is **not** supported in v0.
    """

    # Reaction identity filters
    reactants: list[str] = Field(
        default_factory=list, max_length=_MAX_PARTICIPANTS_PER_REACTION
    )
    products: list[str] = Field(
        default_factory=list, max_length=_MAX_PARTICIPANTS_PER_REACTION
    )
    direction: ReactionDirectionQuery = ReactionDirectionQuery.either
    family: str | None = Field(default=None, max_length=_MAX_FAMILY_LENGTH)

    # Phase C: optional explicit reaction/reaction-entry handles.
    reaction_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)
    reaction_entry_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )

    # Kinetics filters
    temperature_min: float | None = None
    temperature_max: float | None = None
    pressure: float | None = None
    model_kind: KineticsModelKind | None = None
    level_of_theory_id: int | None = None
    level_of_theory_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    software: str | None = Field(default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH)

    # Trust filters
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # v0 forbids client-supplied sort.
    sort: str | None = None

    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50

    @field_validator("reactants", "products")
    @classmethod
    def _bound_participant_lengths(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > _MAX_SMILES_LENGTH:
                raise ValueError(
                    "smiles_too_long: participant SMILES exceeds "
                    f"the maximum length of {_MAX_SMILES_LENGTH}."
                )
        return value


# ---------------------------------------------------------------------------
# Per-record + envelope
# ---------------------------------------------------------------------------


class KineticsSearchReactionContext(BaseModel):
    """Resolved reaction/reaction-entry identity context for a kinetics record.

    Phase B: ``reaction_ref`` and ``reaction_entry_ref`` are the public
    stable handles alongside the integer IDs.
    """

    reaction_id: int
    reaction_ref: str
    reaction_entry_id: int
    reaction_entry_ref: str
    equation: str
    reversible: bool
    family: str | None = None
    matched_direction: ReactionDirectionQuery
    reactants: list[ReactionParticipantSummary]
    products: list[ReactionParticipantSummary]
    reaction_entry_review: RecordReviewBadge


class KineticsSearchRecord(BaseModel):
    """One result row: resolved reaction context + the kinetics record itself."""

    reaction: KineticsSearchReactionContext
    kinetics: KineticsRecord


class RequestEcho(BaseModel):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificKineticsSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/kinetics/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[KineticsSearchRecord]
    pagination: Pagination
