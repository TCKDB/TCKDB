"""Read schemas for the scientific composite-scheme surface (ADR 0021).

Covers ``GET /api/v1/scientific/composite-schemes/{composite_scheme_ref}``.

A composite scheme is the recipe behind a composite level of theory (CBS-QB3,
a CCSD(T)/CBS extrapolation, a focal-point sum). It is a deduplicated identity
row, so like ``level_of_theory`` it is not in ``SubmissionRecordType`` and has
no review history; the envelope still carries an empty ``review_summary`` for
shape parity with the rest of the scientific surface.

Refs only. Every level of theory the record mentions is a
:class:`LevelOfTheorySummary`, which carries ``level_of_theory_ref`` and (behind
``include=internal_ids``) the integer id like everywhere else; nothing in this
module names a database row any other way.

For a ``named_method`` scheme ``terms`` is empty: the catalogue does not hold a
term list, and a term the source does not state is not invented. An empty list
means "none recorded", never "the recipe has no steps".
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    CompositeBindingSource,
    CompositeExtrapolationFormula,
    CompositeInputSlot,
    CompositeSchemeKind,
    CompositeTermLinearity,
    CompositeTermOperation,
    EnergyComponentKind,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    ProfiledRequestEcho,
    ReviewStatusSummary,
)


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


class CompositeSchemeCoreBlock(BaseModel):
    """Direct ``composite_scheme`` row metadata.

    ``geometry_level_of_theory`` and ``frequency_level_of_theory`` are the
    levels the *recipe* runs internally; ``None`` means the source does not
    state it, never "the same as the energy level". ``recipe_zpe_scale_factor``
    is ``None`` unless a source is cited, and a reader must not substitute 1.0.
    """

    composite_scheme_ref: str
    kind: CompositeSchemeKind
    name: str
    definition_hash: str
    geometry_level_of_theory: LevelOfTheorySummary | None = None
    frequency_level_of_theory: LevelOfTheorySummary | None = None
    recipe_zpe_scale_factor: float | None = None
    source_literature_ref: str | None = None
    note: str | None = None
    created_at: datetime


class CompositeSchemeTermInputRecord(BaseModel):
    """One level-of-theory input of a term."""

    slot: CompositeInputSlot
    cardinal_number: int | None = None
    level_of_theory: LevelOfTheorySummary
    #: The weight of this input's ``energy_component`` in the term's value, for a term whose
    #: ``linearity`` is ``linear``: ``+1`` for a ``base`` / ``value`` input, ``+1`` (``high``) and
    #: ``-1`` (``low``) for a ``difference``, and the closed-form weight of the extrapolation
    #: for an extrapolated input (the two weights sum to 1). ``null`` when the term is not
    #: linear: no coefficient is invented for a non-linear formula. Derived on read from the
    #: stored operation, formula, exponent and cardinal numbers; never stored.
    coefficient: float | None = None


class CompositeSchemeTermRecord(BaseModel):
    """One term of the recipe, in order."""

    position: int
    operation: CompositeTermOperation
    energy_component: EnergyComponentKind
    formula: CompositeExtrapolationFormula | None = None
    exponent: float | None = None
    #: Whether the term is a fixed linear combination of its inputs' energies, so that
    #: ``coefficient`` is meaningful on each input. Derived on read.
    linearity: CompositeTermLinearity
    inputs: list[CompositeSchemeTermInputRecord]


class CompositeSchemeBoundLevel(BaseModel):
    """A level of theory whose energy this scheme names."""

    level_of_theory: LevelOfTheorySummary
    binding_source: CompositeBindingSource


class ScientificCompositeSchemeRecord(BaseModel):
    """One ``composite_scheme`` row projected as a scientific record."""

    composite_scheme: CompositeSchemeCoreBlock
    terms: list[CompositeSchemeTermRecord]
    #: ``true`` when the scheme has terms and every term computed from inputs is linear, so
    #: the total is a fixed linear combination of the input energies; ``false`` when any term is
    #: non-linear (``exponential_three_point``); ``null`` when the scheme states no terms (a
    #: named method), where nothing can be said. Derived on read.
    linear_in_energies: bool | None = None
    bound_levels_of_theory: list[CompositeSchemeBoundLevel]


class ScientificCompositeSchemeDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/composite-schemes/{ref}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificCompositeSchemeRecord


__all__ = [
    "CompositeSchemeBoundLevel",
    "CompositeSchemeCoreBlock",
    "CompositeSchemeTermInputRecord",
    "CompositeSchemeTermRecord",
    "RequestEcho",
    "ScientificCompositeSchemeDetailResponse",
    "ScientificCompositeSchemeRecord",
]
