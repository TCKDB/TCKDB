"""GET /api/v1/scientific/species/browse.

A public, unauthenticated, identifier-free catalogue read over the
species corpus. See :mod:`app.services.scientific_read.species` for why
this is a separate endpoint rather than an opt-in relaxation of
``/species/search``'s ``missing_identifier`` refusal, and for the shared
ordering / visibility / pagination machinery this route inherits from
its sibling.

Deliberately does **not** accept ``smiles``, ``inchi``, ``inchi_key``,
``species_ref`` or ``species_entry_ref`` as query parameters. A caller
who has one of those wants ``/species/search``; this route is for the
caller who does not.

``query_smiles`` / ``query_smarts`` / ``mode`` / ``similarity_threshold``
are a bounded, additive structure filter -- the browse-page counterpart
of ``/species/structure-search``'s own vocabulary, composed with every
other filter here in one query rather than run as a second, separate
search. See :class:`~app.schemas.reads.scientific_species.SpeciesBrowseRequest`
for the full contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import (
    ANYWHERE_SCOPE,
    SPECIES_BROWSE_SECTIONS,
    omit_unrequested_sections,
)
from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_species import (
    ElementMatchMode,
    ScientificSpeciesBrowseResponse,
    SpeciesBrowseRequest,
)
from app.schemas.reads.scientific_structure_search import StructureSearchMode
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.species import browse_species

router = APIRouter(prefix="/species")


@router.get("/browse", response_model=ScientificSpeciesBrowseResponse)
def species_browse(
    session: Session = Depends(get_db),
    formula: str | None = Query(None),
    charge: int | None = Query(None),
    multiplicity: int | None = Query(None),
    electronic_state_kind: SpeciesEntryStateKind | None = Query(None),
    species_entry_kind: StationaryPointKind | None = Query(None),
    elements: str | None = Query(
        None,
        description=(
            "Comma-separated element symbols, e.g. 'C,N,S'. Browse-only -- "
            "see SpeciesBrowseRequest for why this is not on /species/search."
        ),
    ),
    elem_mode: ElementMatchMode = Query(ElementMatchMode.all),
    max_heavy_atoms: int | None = Query(None, ge=0),
    min_heavy_atoms: int | None = Query(None, ge=0),
    method: str | None = Query(
        None,
        description=(
            "Level-of-theory method, e.g. 'b3lyp'. Matches a species with "
            "at least one calculation (across any of its entries) at this "
            "method -- OR-across-calculation, not 'every calculation must "
            "match'. Browse-only, mirrors /transition-states/browse."
        ),
    ),
    basis: str | None = Query(
        None,
        description=(
            "Level-of-theory basis set, e.g. 'def2-tzvp'. Same "
            "OR-across-calculation semantics as method=; AND-combines "
            "with method= when both are supplied."
        ),
    ),
    software: str | None = Query(
        None,
        description=(
            "Software package name, e.g. 'Gaussian'. OR-across-calculation, "
            "same as method=/basis=."
        ),
    ),
    software_version: str | None = Query(
        None,
        description=(
            "Software release version, e.g. '16'. Requires software= -- a "
            "version string alone is ambiguous across packages. Refused "
            "with 422 missing_version_parent if software= is absent, "
            "matching /meta/software-versions."
        ),
    ),
    workflow_tool: str | None = Query(
        None,
        description=(
            "Workflow tool name, e.g. 'ARC'. OR-across-calculation, same "
            "as method=/basis=/software=."
        ),
    ),
    workflow_tool_version: str | None = Query(
        None,
        description=(
            "Workflow tool release version. Requires workflow_tool= -- "
            "refused with 422 missing_version_parent otherwise, matching "
            "/meta/workflow-tool-versions."
        ),
    ),
    query_smiles: str | None = Query(
        None,
        description=(
            "Structure filter: species with >=1 entry whose stored "
            "molecule matches this SMILES under mode=. Browse-only, a "
            "bounded projection of /species/structure-search's own "
            "vocabulary -- see SpeciesBrowseRequest for the full "
            "rationale. Mutually exclusive with query_smarts "
            "(422 multiple_structure_queries if both are supplied)."
        ),
    ),
    query_smarts: str | None = Query(
        None,
        description=(
            "Structure filter: species with >=1 entry whose stored "
            "molecule contains this SMARTS pattern as a substructure. "
            "Only valid under mode=substructure -- 422 "
            "invalid_structure_query otherwise. Mutually exclusive with "
            "query_smiles."
        ),
    ),
    mode: StructureSearchMode = Query(
        StructureSearchMode.substructure,
        description=(
            "Structure-filter algorithm: substructure (RDKit cartridge "
            "'@>', SMILES or SMARTS), similarity (Tanimoto over Morgan "
            "fingerprints, SMILES only), or exact (canonical InChIKey, "
            "SMILES only). Unused unless query_smiles/query_smarts is "
            "also supplied."
        ),
    ),
    similarity_threshold: float | None = Query(
        None,
        ge=0.0,
        le=1.0,
        description=(
            "Tanimoto similarity floor, 0.0-1.0. Read only when "
            "mode=similarity; ignored otherwise. Defaults to "
            "DEFAULT_SIMILARITY_THRESHOLD (see "
            "scientific_structure_search.py) when mode=similarity and "
            "this is omitted."
        ),
    ),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    collapse: CollapseMode = Query(CollapseMode.all),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificSpeciesBrowseResponse:
    """List species with no identifier required, for a catalogue page.

    Every secondary filter ``/species/search`` accepts alongside an
    identifier is accepted here too, so a listing can be narrowed after
    it is opened. ``elements`` / ``elem_mode`` / ``max_heavy_atoms`` /
    ``min_heavy_atoms`` are the exception: composition filters exist only
    here, not on ``/species/search`` — see
    :class:`~app.schemas.reads.scientific_species.SpeciesBrowseRequest`
    for why. ``method`` / ``basis`` / ``software`` / ``software_version`` /
    ``workflow_tool`` / ``workflow_tool_version`` narrow by calculation
    provenance the same way ``/transition-states/browse`` does —
    OR-across-calculation (see the per-parameter descriptions below and
    :class:`~app.schemas.reads.scientific_species.SpeciesBrowseRequest`
    for the full rationale); the two ``*_version`` fields require their
    named parent and 422 with ``missing_version_parent`` otherwise.
    ``limit`` is capped at 200 (default 50, same as
    ``/species/search``); ``offset`` is capped by the hosted
    ``public_max_offset`` setting via the shared pagination validator.
    Ordering is ``review_rank ASC, has_entries DESC, created_at DESC,
    id DESC`` — the same default as ``/species/search`` — which is what
    keeps pagination stable across requests. Client-supplied ``sort=`` is
    rejected with 422 (``client_sort_not_supported``), matching the
    sibling endpoint.
    """
    request = SpeciesBrowseRequest(
        formula=formula,
        charge=charge,
        multiplicity=multiplicity,
        electronic_state_kind=electronic_state_kind,
        species_entry_kind=species_entry_kind,
        elements=elements,
        elem_mode=elem_mode,
        max_heavy_atoms=max_heavy_atoms,
        min_heavy_atoms=min_heavy_atoms,
        method=method,
        basis=basis,
        software=software,
        software_version=software_version,
        workflow_tool=workflow_tool,
        workflow_tool_version=workflow_tool_version,
        query_smiles=query_smiles,
        query_smarts=query_smarts,
        mode=mode,
        similarity_threshold=similarity_threshold,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        collapse=collapse,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = browse_species(session, request)
    visibility = apply_internal_ids_visibility(payload)
    # Same reasoning as /species/search: the four summaries sit at
    # ``records[*].entries[*]``, two levels below the record root, so no
    # record-shaped scope reaches them.
    return omit_unrequested_sections(
        visibility,
        payload,
        table=SPECIES_BROWSE_SECTIONS,
        scope=ANYWHERE_SCOPE,
    )
