"""Read schemas for /api/v1/scientific/species/search.

See docs/specs/read_api_mvp.md §Endpoint 1.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from app.schemas.reads._field_bounds import (
    MAX_BASIS_LENGTH as _MAX_BASIS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_ELEMENTS_LENGTH as _MAX_ELEMENTS_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_FORMULA_LENGTH as _MAX_FORMULA_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_INCHI_KEY_LENGTH as _MAX_INCHI_KEY_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_INCHI_LENGTH as _MAX_INCHI_LENGTH,
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
    CollapseMode,
    Pagination,
    ProfiledRequestEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_structure_search import StructureSearchMode

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ElementMatchMode(str, Enum):
    """How :attr:`SpeciesBrowseRequest.elements` combines more than one symbol.

    ``all``  every listed element must be present (composition superset).
    ``any``  at least one listed element must be present (composition union).

    Mirrors ``elem_mode`` on the ``HAb_DB`` species router this filter is
    modeled on. Default is ``all``, matching that precedent.
    """

    all = "all"
    any = "any"


class SpeciesFilterRequest(BaseModel):
    """Fields shared by species search and species browse.

    Everything here narrows a candidate set but never *is* one: a species
    identifier (``smiles`` / ``inchi_key`` / ``species_ref`` /
    ``species_entry_ref``) is what makes ``/species/search`` a lookup, and
    those live only on :class:`SpeciesSearchRequest`. ``/species/browse``
    (:class:`SpeciesBrowseRequest`) has none of them, by construction —
    not by a runtime check — which is what lets it list the corpus with no
    identifier at all.

    ``formula`` sits here rather than with the identifiers even though
    ``search_species`` treats it as one of the "at least one identifier"
    quartet: on a browse listing, "show me the C6H6 entries" is exactly
    the kind of narrowing a catalogue reader does *after* opening the
    page, not a lookup they arrived with. Its matching semantics (exact,
    case-sensitive, via the RDKit cartridge) are identical on both
    surfaces — see ``species.py::_formula_expr``.
    """

    # Matched via the RDKit cartridge's mol_formula(mol_from_smiles(...))
    # against the species' identity SMILES: Hill notation, e.g. "H2O",
    # "C3H6", with a trailing charge suffix for ions ("HO-", "H4N+").
    # Exact, case-sensitive match; isotopes are not distinguished.
    formula: str | None = Field(default=None, max_length=_MAX_FORMULA_LENGTH)

    charge: int | None = None
    multiplicity: int | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    species_entry_kind: StationaryPointKind | None = None

    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # v0 forbids client-supplied sort. The service rejects a non-None value.
    sort: str | None = None

    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)
    offset: int = 0
    limit: int = 50


class SpeciesSearchRequest(SpeciesFilterRequest):
    """Service-layer request model for species search.

    At least one of ``smiles``, ``inchi``, ``inchi_key``, ``formula`` must be
    supplied; multiple identifiers AND-combine. Inconsistent identifiers
    return an empty result set, not a validation error (per Phase 2.1 patch).
    """

    smiles: str | None = Field(default=None, max_length=_MAX_SMILES_LENGTH)
    # inchi has no stored/derivable column to filter against (see
    # species.py::search_species); an inchi-only query returns an empty
    # result set rather than the unfiltered species table.
    inchi: str | None = Field(default=None, max_length=_MAX_INCHI_LENGTH)
    inchi_key: str | None = Field(default=None, max_length=_MAX_INCHI_KEY_LENGTH)

    # Phase C: explicit handles (refs); ID siblings keep the existing
    # behavior. If both are supplied they must resolve to the same row.
    species_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)
    species_entry_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)


class SpeciesBrowseRequest(SpeciesFilterRequest):
    """Service-layer request model for the identifier-free species catalogue.

    See ``/scientific/species/browse``. Deliberately carries no identifier
    field at all (no ``smiles``, ``inchi``, ``inchi_key``, ``species_ref``,
    ``species_entry_ref``) — the route that constructs this model does not
    accept them as query parameters, so "browse with no identifier" is a
    property of the type, not a value a caller happened not to supply.

    Composition filters
    --------------------
    ``elements`` / ``elem_mode`` / ``max_heavy_atoms`` / ``min_heavy_atoms``
    are **browse-only**, deliberately absent from :class:`SpeciesFilterRequest`
    so ``/species/search`` cannot reach them. Modeled on
    ``HAb_DB``'s ``api/routers/species.py`` composition filters, whose own
    comment is the load-bearing design note: "Composition filters (used
    only when q is empty)" — composition is a browse concern (selecting
    from a bounded, paged listing), not a search one (a fuzzy ``C`` query
    that would match every carbon-containing species unboundedly). See
    ``species.py::_formula_has_element_expr`` and
    ``species.py::_heavy_atom_count_expr`` for what "element present" and
    "heavy atom" mean here, pinned against real archive SMILES shapes
    (radicals, charged species, explicit ``[H]``, isotopes) in
    ``tests/services/scientific_read/test_browse_species.py``.

    ``elements`` is a comma-separated list of element symbols (e.g.
    ``"C,N,S"``); an unrecognised symbol is refused with 422
    ``unknown_element_symbol`` rather than silently matching nothing (a
    typo must not read as "we hold none of that element"). ``max_heavy_atoms``
    / ``min_heavy_atoms`` bound the species' non-hydrogen atom count,
    inclusive on both ends.

    Provenance filters
    ------------------
    ``method`` / ``basis`` / ``software`` / ``software_version`` /
    ``workflow_tool`` / ``workflow_tool_version`` are also **browse-only**,
    matching the composition filters above and mirroring
    ``TransitionStatesBrowseRequest`` on the TS-browse sibling. A species
    can have many ``species_entry`` rows, each with many ``calculation``
    rows at potentially different levels of theory, so these are
    **OR-across-calculation, at the species grain**: a species passes if
    *at least one* of its calculations (belonging to *any* of its
    entries) matches every supplied constraint simultaneously — the same
    choice ``transition_states_search.py``'s
    ``_apply_method_basis_software_filters`` already made for TS entries,
    applied here one level up (species owns entries owns calculations,
    where a TS entry owns calculations directly). "All calculations must
    match" was rejected: a species with ten calculations at nine
    different levels of theory and one at ``b3lyp`` is exactly the kind
    of record a caller filtering by level of theory wants back, and
    "all" would silently exclude it.

    ``software_version`` requires ``software`` (and ``workflow_tool_version``
    requires ``workflow_tool``) — refused with 422 ``missing_version_parent``
    otherwise, matching ``/meta/software-versions`` and
    ``/meta/workflow-tool-versions`` (#304). A version string alone is
    ambiguous across packages (two software packages can each have a
    release called "16"), and this endpoint already has a coded refusal
    for exactly that shape of ambiguity elsewhere in the read API, so
    reusing it here keeps one policy instead of two. This is a stricter
    choice than ``TransitionStatesBrowseRequest`` currently makes (that
    endpoint does not enforce the parent) — see the species-browse PR
    description for why the two were allowed to diverge rather than
    loosening this one to match.

    See ``app.services.scientific_read.calculation_provenance_filters``
    for the join logic these six fields drive (shared with TS
    search/browse), and ``species.py::_apply_provenance_filters`` for how
    it is anchored at the species grain.
    """

    elements: str | None = Field(default=None, max_length=_MAX_ELEMENTS_LENGTH)
    elem_mode: ElementMatchMode = ElementMatchMode.all
    max_heavy_atoms: int | None = Field(default=None, ge=0)
    min_heavy_atoms: int | None = Field(default=None, ge=0)

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

    # Structure filter -- **browse-only**, added alongside the browse-page
    # structure search UI (frontend/src/components/BrowseFilterForm.tsx).
    # A bounded, additive projection of
    # /scientific/species/structure-search's own vocabulary
    # (query_smiles / query_smarts / mode / similarity_threshold -- see
    # app.schemas.reads.scientific_structure_search) onto the browse
    # candidate set, so a catalogue reader can narrow by chemical
    # structure the same way they narrow by formula/elements, in the SAME
    # request as every other filter here -- not a second, standalone
    # search that a caller has to run separately and intersect by hand.
    #
    # Only two of the standalone endpoint's four query fields are exposed
    # (no query_inchi / query_inchi_key): a browse reader is narrowing a
    # listing they are already looking at, not arriving with a foreign
    # identifier, and InChI/InChIKey narrowing is exactly what
    # /species/search already does. Supplying neither field is not an
    # error -- unlike the standalone endpoint's missing_structure_query
    # requirement, the structure filter is optional here, same as every
    # other field on this class. Supplying BOTH is still refused
    # (multiple_structure_queries), and a query_smarts under
    # mode=similarity/exact, or any query under a mode the cartridge does
    # not support for it, is refused (invalid_structure_query) -- see
    # app.services.scientific_read.structure_query, whose parsing and
    # mode/query-kind rules this filter reuses verbatim rather than
    # forking a second copy (see that module's docstring).
    #
    # similarity_threshold is read only when mode=similarity; a value
    # supplied under another mode is accepted but has no effect, the same
    # as the standalone endpoint's own handling (see
    # ScientificSpeciesStructureSearchRequest).
    query_smiles: str | None = Field(default=None, max_length=4096)
    query_smarts: str | None = Field(default=None, max_length=4096)
    mode: StructureSearchMode = StructureSearchMode.substructure
    similarity_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0
    )


# ---------------------------------------------------------------------------
# Per-record shapes
# ---------------------------------------------------------------------------


class SpeciesEntryAvailability(BaseModel):
    """Boolean availability flags + counts per L1 species/reaction-search policy."""

    has_thermo: bool
    has_statmech: bool
    has_transport: bool
    has_conformers: bool
    calculation_count: int


class SpeciesEntrySectionIds(BaseModel):
    """Lightweight section payload populated when an ``include=`` token requests it.

    v0 returns ID lists only; richer per-section read shapes are a future
    enhancement. Validation of the include token already happened upstream.
    """

    ids: list[int]


class SpeciesEntryScientificRecord(BaseModel):
    """Per-entry block embedded in a SpeciesScientificRecord.

    Phase B: ``species_entry_ref`` is the public stable handle alongside
    the integer ``species_entry_id``.

    Identity fields
    ---------------
    ``stereo_label``, ``electronic_state_label``, ``term_symbol`` and
    ``isotope_key`` are the columns of ``uq_species_entry_species_id``
    other than ``electronic_state_kind`` (already served above) and the
    species itself. Together those five are what makes one entry a
    different row from another under the same ``species``.

    They are in the **default** projection, not behind an ``include=``
    token, because their absence is not a missing convenience — it is a
    wrong answer. Two entries of one species that agree on every served
    field are indistinguishable on the wire, and a reader picking a ref
    from a list of two identical records has a 50% chance of citing the
    other molecule. The live database has exactly that shape: ``N=N``
    carries a ``Z`` entry (cis-diazene) and an ``E`` entry
    (trans-diazene), with different thermochemistry. A reader who does
    not know to ask for an include token is precisely the reader who gets
    the wrong molecule, so asking cannot be the price of correctness.

    ``species_entry_label`` is those five columns rendered as one short
    string — ``"E"``, ``"excited T1"`` — derived by
    :func:`app.services.scientific_read.species_identity.species_entry_label`,
    the same function the pressure-dependent network surface uses. One
    definition, so a species entry reads the same way in a species search,
    a structure search, and a network state label.

    Every one of these is ``None`` when the column is ``NULL``, never
    ``""``. 51 of the 60 entries on the deployed database have no stereo
    label at all, and "no label" must render as no label rather than as a
    label that happens to be blank.

    ``isotopologue_label`` is deliberately **not** served. It is a
    deprecated free-text column, is no longer part of the entry's unique
    identity, is never written by the application, and therefore cannot
    discriminate between two entries; serving it would advertise an
    identity component that is not one.
    """

    species_entry_id: int
    species_entry_ref: str
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind
    stereo_label: str | None = None
    electronic_state_label: str | None = None
    term_symbol: str | None = None
    isotope_key: str | None = None
    species_entry_label: str | None = None
    review: RecordReviewBadge
    availability: SpeciesEntryAvailability

    # Populated only when the corresponding include= token is set.
    thermo_summary: SpeciesEntrySectionIds | None = None
    statmech_summary: SpeciesEntrySectionIds | None = None
    transport_summary: SpeciesEntrySectionIds | None = None
    conformers_summary: SpeciesEntrySectionIds | None = None


class SpeciesScientificRecord(BaseModel):
    """One species row returned from /scientific/species/search.

    Phase B: ``species_ref`` is the public stable handle alongside
    the integer ``species_id``.

    ``stereo_kind`` is a property of the *species* graph, not of any one
    entry, and it is what makes a null ``stereo_label`` on an entry
    readable. ``achiral`` says the null means there is no stereochemistry
    to label; ``ez_isomer`` or ``enantiomer`` says stereoisomers exist and
    this entry simply has not been labelled — a materially different
    thing to know before citing it. Non-nullable in the schema, so it is
    populated on every species row.

    ``formula`` is not a stored column. It is derived at read time by the
    same RDKit-cartridge expression the ``formula=`` filter matches on
    (``species.py::_formula_expr``), so a caller who searched by formula
    reads back exactly the string that was matched. Hill notation with a
    trailing charge suffix for ions (``H2O``, ``CH3``, ``HO-``, ``H4N+``);
    isotopes are not distinguished. It stays nullable because a species
    whose SMILES will not parse has no formula to derive — that is the
    only case in which it is null.
    """

    species_id: int
    species_ref: str
    canonical_smiles: str
    inchi_key: str
    formula: str | None = None
    charge: int
    multiplicity: int
    stereo_kind: StereoKind
    entries: list[SpeciesEntryScientificRecord] = Field(default_factory=list)


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed query for debuggability and traceability."""

    filter: dict[str, object]
    sort: str
    collapse: CollapseMode
    include: list[str]


class ScientificSpeciesSearchResponse(BaseModel):
    """Response envelope for /api/v1/scientific/species/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[SpeciesScientificRecord]
    pagination: Pagination


class ScientificSpeciesBrowseResponse(ScientificSpeciesSearchResponse):
    """Response envelope for /api/v1/scientific/species/browse.

    Field-for-field identical to :class:`ScientificSpeciesSearchResponse`
    by design (see the endpoint's module docstring for why): a client's
    parser for one search-response schema works unmodified against a
    browse response. Declared as its own class, not a bare alias, so the
    OpenAPI document and generated clients name the two surfaces
    separately even though nothing about the shape differs.
    """
