"""Read schemas for the method and basis-set catalogue.

Covers:

- ``GET /api/v1/scientific/methods`` and ``GET /api/v1/scientific/methods/{handle}``
- ``GET /api/v1/scientific/basis-sets`` and ``GET /api/v1/scientific/basis-sets/{handle}``
- the ``catalogue`` section of a level-of-theory record (``include=catalogue``)

A catalogue entry is curated reference data: what a stored spelling names, in TCKDB's words, and the articles that
define it. Family and DFT rung are derived from :mod:`app.chemistry.method_families`, never stored. Usage counts are
derived at read time from the calculations the read profile shows. Nothing here orders, ranks or recommends an entry:
lists are in key order. A browse returns the whole catalogue, a curated list of tens of entries, so it is not paged.
A browse also lists the identity keys that shown levels of theory use and no entry names (``uncatalogued``), with the
same usage counts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.chemistry.method_families import MethodFamily
from app.db.models.common import (
    BasisSetRole,
    CatalogueMethodKind,
    CatalogueReferenceRole,
    LiteratureTitleSource,
)
from app.schemas.reads.scientific_common import (
    ProfiledRequestEcho,
    ReviewStatusSummary,
    ScientificResponse,
)


class CatalogueRequestEcho(ProfiledRequestEcho):
    """Echo of the parsed request."""

    include: list[str] = Field(default_factory=list)


class CatalogueCitation(BaseModel):
    """One article a catalogue entry cites.

    ``title`` is served with its ``title_source``, as every literature summary is. ``doi`` is served only when the
    row's metadata was resolved from the DOI registry (``title_source = resolved_metadata``).
    """

    literature_ref: str
    role: CatalogueReferenceRole
    position: int
    title: str
    title_source: LiteratureTitleSource | None = None
    journal: str | None = None
    year: int | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    authors: list[str] = Field(default_factory=list, description="Recorded author names, in author order.")
    doi: str | None = Field(
        default=None,
        description=(
            "The DOI, only when the row's metadata was resolved from the DOI registry "
            "(title_source = resolved_metadata); null otherwise."
        ),
    )


class CatalogueUsage(BaseModel):
    """How much stored data an entry names, derived at read time.

    ``level_usage_count`` counts levels of theory with at least one calculation the read profile shows;
    ``calculation_usage_count`` counts those calculations.
    """

    level_usage_count: int = 0
    calculation_usage_count: int = 0


class MethodCatalogueCard(BaseModel):
    """A method or dispersion correction: its names, definition and references."""

    catalogue_ref: str
    key: str
    keys: list[str] = Field(description="Every identity key that names this entry, its own key included.")
    kind: CatalogueMethodKind
    short_name: str
    full_name: str
    description: str
    family: MethodFamily | None = Field(
        default=None,
        description="Derived from the entry's keys by the method-family classification; null for a dispersion "
        "correction. A classification, not a measure of accuracy.",
    )
    dft_rung: int | None = None
    references: list[CatalogueCitation] = Field(default_factory=list)
    spellings_seen: list[str] = Field(
        default_factory=list,
        description="The distinct stored spellings, on levels of theory counted in usage, that key to this entry.",
    )
    usage: CatalogueUsage = Field(default_factory=CatalogueUsage)


class BasisSetCatalogueCard(BaseModel):
    """A basis set: its names, role, family, definition and references."""

    catalogue_ref: str
    key: str
    keys: list[str] = Field(description="Every identity key that names this entry, its own key included.")
    role: BasisSetRole
    family: str
    short_name: str
    full_name: str
    description: str
    references: list[CatalogueCitation] = Field(default_factory=list)
    spellings_seen: list[str] = Field(
        default_factory=list,
        description="The distinct stored spellings, on levels of theory counted in usage, that key to this entry.",
    )
    usage: CatalogueUsage = Field(default_factory=CatalogueUsage)


class UncataloguedMethod(BaseModel):
    """A method or dispersion identity key that levels of theory use and no catalogue entry names."""

    key: str = Field(description="The identity key the stored spellings share.")
    kind: CatalogueMethodKind
    family: MethodFamily | None = Field(
        default=None,
        description="Derived from the key by the method-family classification; null for a dispersion correction.",
    )
    dft_rung: int | None = None
    spellings_seen: list[str] = Field(
        default_factory=list, description="The distinct stored spellings, on levels of theory counted in usage."
    )
    usage: CatalogueUsage = Field(default_factory=CatalogueUsage)


class UncataloguedBasisSet(BaseModel):
    """A basis-set identity key that levels of theory use (as basis, auxiliary or CABS basis) and no entry names."""

    key: str = Field(description="The identity key the stored spellings share.")
    spellings_seen: list[str] = Field(
        default_factory=list, description="The distinct stored spellings, on levels of theory counted in usage."
    )
    usage: CatalogueUsage = Field(default_factory=CatalogueUsage)


class LevelOfTheoryCatalogue(BaseModel):
    """The catalogue entries a level of theory's spellings key to; ``null`` where a spelling keys to none."""

    method: MethodCatalogueCard | None = None
    dispersion: MethodCatalogueCard | None = None
    basis: BasisSetCatalogueCard | None = None
    aux_basis: BasisSetCatalogueCard | None = None
    cabs_basis: BasisSetCatalogueCard | None = None


class CatalogueLevelUsage(BaseModel):
    """One level of theory that uses a catalogue entry."""

    level_of_theory_ref: str
    label: str
    calculation_usage_count: int


class MethodCatalogueDetailRecord(BaseModel):
    method: MethodCatalogueCard
    levels: list[CatalogueLevelUsage] = Field(default_factory=list)


class BasisSetCatalogueDetailRecord(BaseModel):
    basis_set: BasisSetCatalogueCard
    levels: list[CatalogueLevelUsage] = Field(default_factory=list)


class MethodCatalogueBrowseResponse(ScientificResponse):
    """Response envelope for ``GET /scientific/methods``."""

    request: CatalogueRequestEcho
    review_summary: ReviewStatusSummary
    records: list[MethodCatalogueCard]
    uncatalogued: list[UncataloguedMethod] = Field(
        default_factory=list,
        description="Keys used by at least one level of theory counted in usage that no entry names, in key order. "
        "The kind and family filters apply to them as to the entries.",
    )


class MethodCatalogueDetailResponse(ScientificResponse):
    """Response envelope for ``GET /scientific/methods/{handle}``."""

    request: CatalogueRequestEcho
    review_summary: ReviewStatusSummary
    record: MethodCatalogueDetailRecord


class BasisSetCatalogueBrowseResponse(ScientificResponse):
    """Response envelope for ``GET /scientific/basis-sets``."""

    request: CatalogueRequestEcho
    review_summary: ReviewStatusSummary
    records: list[BasisSetCatalogueCard]
    uncatalogued: list[UncataloguedBasisSet] = Field(
        default_factory=list,
        description="Keys used by at least one level of theory counted in usage that no entry names, in key order. "
        "They have no role or family, so a role or family filter leaves this list empty.",
    )


class BasisSetCatalogueDetailResponse(ScientificResponse):
    """Response envelope for ``GET /scientific/basis-sets/{handle}``."""

    request: CatalogueRequestEcho
    review_summary: ReviewStatusSummary
    record: BasisSetCatalogueDetailRecord


__all__ = [
    "BasisSetCatalogueBrowseResponse",
    "BasisSetCatalogueCard",
    "BasisSetCatalogueDetailRecord",
    "BasisSetCatalogueDetailResponse",
    "CatalogueCitation",
    "CatalogueLevelUsage",
    "CatalogueRequestEcho",
    "CatalogueUsage",
    "LevelOfTheoryCatalogue",
    "MethodCatalogueBrowseResponse",
    "MethodCatalogueCard",
    "MethodCatalogueDetailRecord",
    "MethodCatalogueDetailResponse",
    "UncataloguedBasisSet",
    "UncataloguedMethod",
]
