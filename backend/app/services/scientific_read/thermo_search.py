"""Service implementation for /api/v1/scientific/thermo/search.

Chemistry-first thermo search composes the existing species discovery and
thermo retrieval services so callers do not have to chain ids manually.

Composition order (final response ordering):

1. Resolve species/species_entry candidates using the same identity rules
   as ``search_species`` (AND-combined identifiers, default trust posture
   on the species entry).
2. For each surviving species_entry, fetch its thermo records with
   ``get_species_thermo``.
3. Order the flat list of records by the RECORD's own review rank, then its
   own deposition time (``created_at`` DESC), then its own id DESC. The
   parent entry's review standing and id order nothing: a record is not
   ranked by who its parent is, and records of one entry are not kept
   together (``docs/specs/browse_order.md``).
4. Paginate the flat list. Every matching record is returned; none is picked.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    ReviewStatusSummary,
    browse_order,
)
from app.schemas.reads.scientific_species import SpeciesSearchRequest
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.schemas.reads.scientific_thermo_search import (
    RequestEcho,
    ScientificThermoSearchResponse,
    ThermoSearchRecord,
    ThermoSearchRequest,
    ThermoSearchSpeciesContext,
)
from app.services.scientific_read.common import (
    build_pagination,
    collect_bounded_pages,
    reject_client_sort,
    review_summary,
    slice_for_pagination,
    validate_includes,
    validate_pagination,
    validate_temperature_range,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.public_assessments import attach_thermo_trust
from app.services.scientific_read.species import search_species
from app.services.scientific_read.thermo import get_species_thermo

# ``trust`` is legal here, and it was not always. This surface used to
# declare ``records[*].thermo.trust`` — one level down, inside the ``thermo``
# wrapper — while rejecting ``include=trust`` with
# ``422 unknown_include_token``, so the field was structurally incapable of
# carrying a value and shipped as a permanent ``null`` on every record. The
# token is legal now, the value is built for the returned page (see
# :func:`attach_thermo_trust` below), and the route drops the key when the
# caller did not ask for it.
#
# It is **internal-tokenized**: ``include=all`` does not expand to it. The
# evaluator walks a 20-entry eager-load graph, and a convenience token must
# not buy that on a caller's behalf.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "provenance",
    "calculations",
    "artifacts",
    "review",
    "internal_ids",
    "all",
    "assessments",
    "trust",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids", "assessments", "trust"}

# Tokens forwarded to the inner thermo retrieval. The set matches the
# public legal tokens minus ``internal_ids`` / ``all`` — there are no
# v0 no-op tokens at this endpoint (a previous draft accepted
# ``statmech`` and ``conformers`` as accepted-but-no-op placeholders;
# they were removed in favor of returning ``unknown_include_token`` so
# the include grammar matches its semantics). If a future phase wires
# either token through, add it back here and to ``_LEGAL_INCLUDE_TOKENS``.
_THERMO_LEGAL_INCLUDES_PASSTHROUGH = {"provenance", "calculations", "review", "artifacts"}

_DEFAULT_SORT_ECHO = browse_order("review_rank", "created_at", "id")


def search_thermo(
    session: Session, request: ThermoSearchRequest
) -> ScientificThermoSearchResponse:
    """Chemistry-first thermo search.

    Returns thermo records along with the resolved species/species_entry
    identity context. Composes :func:`search_species` and
    :func:`get_species_thermo` — all filtering, ranking, evidence, and
    provenance is computed by the existing tested services.

    :param session: SQLAlchemy session.
    :param request: Parsed request model.
    :returns: ``ScientificThermoSearchResponse``.
    :raises ValueError: 422 for sort/pagination/include/temperature validation.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/thermo/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)
    validate_temperature_range(
        request.coverage_temperature_min_k, request.coverage_temperature_max_k,
        min_name="coverage_temperature_min_k",
        max_name="coverage_temperature_max_k",
    )

    if not any(
        v is not None
        for v in (
            request.smiles,
            request.inchi,
            request.inchi_key,
            request.formula,
            request.species_ref,
            request.species_entry_ref,
        )
    ):
        raise ValueError(
            "missing_identifier: at least one of {smiles, inchi, inchi_key, "
            "formula, species_ref, species_entry_ref} is required."
        )

    # 1) Resolve species + species_entries.
    def fetch_species_page(page_offset: int, page_limit: int):
        return search_species(
            session,
            SpeciesSearchRequest(
                smiles=request.smiles,
                inchi=request.inchi,
                inchi_key=request.inchi_key,
                formula=request.formula,
                charge=request.charge,
                multiplicity=request.multiplicity,
                electronic_state_kind=request.electronic_state_kind,
                species_entry_kind=request.species_entry_kind,
                species_ref=request.species_ref,
                species_entry_ref=request.species_entry_ref,
                min_review_status=None,
                include_rejected=request.include_rejected,
                include_deprecated=request.include_deprecated,
                offset=page_offset,
                limit=page_limit,
                include=[],
            ),
        )

    species_records = collect_bounded_pages(
        fetch_species_page,
        resource_name="species discovery candidates",
    )

    # Flatten species → entries with their species context.
    entry_contexts: list[tuple[ThermoSearchSpeciesContext, int]] = []
    for sp_record in species_records:
        for entry in sp_record.entries:
            ctx = ThermoSearchSpeciesContext(
                species_id=sp_record.species_id,
                species_ref=sp_record.species_ref,
                canonical_smiles=sp_record.canonical_smiles,
                inchi_key=sp_record.inchi_key,
                charge=sp_record.charge,
                multiplicity=sp_record.multiplicity,
                species_entry_id=entry.species_entry_id,
                species_entry_ref=entry.species_entry_ref,
                species_entry_label=entry.species_entry_label,
                species_entry_kind=entry.species_entry_kind,
                electronic_state_kind=entry.electronic_state_kind,
                species_entry_review=entry.review,
            )
            entry_contexts.append((ctx, entry.species_entry_id))

    if not entry_contexts:
        return _empty_response(request, includes, offset, limit)

    # 2) Per entry, retrieve thermo with the documented detail-endpoint
    # ordering already applied. The forwarded include set equals the
    # outer legal set minus ``internal_ids`` / ``all`` (which the inner
    # endpoint does not accept directly).
    inner_includes = sorted(includes & _THERMO_LEGAL_INCLUDES_PASSTHROUGH)

    flat: list[ThermoSearchRecord] = []
    for ctx, entry_id in entry_contexts:
        def fetch_thermo_page(
            page_offset: int,
            page_limit: int,
            species_entry_id: int = entry_id,
        ):
            return get_species_thermo(
                session,
                species_entry_id=species_entry_id,
                request=ThermoReadRequest(
                    coverage_temperature_min_k=request.coverage_temperature_min_k,
                    coverage_temperature_max_k=request.coverage_temperature_max_k,
                    model_kind=request.model_kind,
                    level_of_theory_id=request.level_of_theory_id,
                    level_of_theory_ref=request.level_of_theory_ref,
                    software=request.software,
                    min_review_status=request.min_review_status,
                    include_rejected=request.include_rejected,
                    include_deprecated=request.include_deprecated,
                    include=inner_includes,
                        offset=page_offset,
                    limit=page_limit,
                ),
            )

        thermo_records = collect_bounded_pages(
            fetch_thermo_page,
            resource_name="thermo candidates for one species entry",
        )
        for thermo_record in thermo_records:
            flat.append(ThermoSearchRecord(species=ctx, thermo=thermo_record))

    if not flat:
        return _empty_response(request, includes, offset, limit)

    # 3) Display order over the flat list: the record's own review rank, deposition time and id. Nothing about the
    # parent entry takes part.
    flat.sort(
        key=lambda rec: (
            REVIEW_RANK[rec.thermo.review.status],
            -rec.thermo.created_at.timestamp(),
            -rec.thermo.thermo_id,
        )
    )

    # 4) Pagination on the flat list.
    total = len(flat)
    returned = slice_for_pagination(flat, offset=offset, limit=limit)

    # Trust is resolved for the returned page and for nothing else.
    # Forwarding ``trust`` into ``get_species_thermo`` would have been the
    # shorter change and the wrong one: that call runs once per matched
    # species entry, over every candidate the search walked, so the evidence
    # graph would be loaded for records the caller never sees and the cost
    # would scale with the corpus instead of with ``limit``.
    if "trust" in includes:
        attach_thermo_trust(
            session,
            [(rec.thermo, rec.thermo.thermo_id) for rec in returned],
        )

    summary = review_summary(rec.thermo.review for rec in flat)

    return ScientificThermoSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(returned),
            total=total,
        ),
    )


def _filter_echo(request: ThermoSearchRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    for field in ("smiles", "inchi", "inchi_key", "formula", "charge", "multiplicity"):
        v = getattr(request, field)
        if v is not None:
            echo[field] = v
    if request.electronic_state_kind is not None:
        echo["electronic_state_kind"] = request.electronic_state_kind.value
    if request.species_entry_kind is not None:
        echo["species_entry_kind"] = request.species_entry_kind.value
    if request.coverage_temperature_min_k is not None:
        echo["coverage_temperature_min_k"] = request.coverage_temperature_min_k
    if request.coverage_temperature_max_k is not None:
        echo["coverage_temperature_max_k"] = request.coverage_temperature_max_k
    if request.model_kind is not None:
        echo["model_kind"] = request.model_kind.value
    if request.level_of_theory_id is not None:
        echo["level_of_theory_id"] = request.level_of_theory_id
    if request.level_of_theory_ref is not None:
        echo["level_of_theory_ref"] = request.level_of_theory_ref
    if request.species_ref is not None:
        echo["species_ref"] = request.species_ref
    if request.species_entry_ref is not None:
        echo["species_entry_ref"] = request.species_entry_ref
    if request.software is not None:
        echo["software"] = request.software
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_response(
    request: ThermoSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificThermoSearchResponse:
    return ScientificThermoSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=ReviewStatusSummary(),
        records=[],
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=0,
            total=0,
        ),
    )
