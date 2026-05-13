"""Service implementation for /api/v1/scientific/thermo/search.

Chemistry-first thermo search composes the existing species discovery and
thermo retrieval services so callers do not have to chain ids manually.

Composition order (final response ordering):

1. Resolve species/species_entry candidates using the same identity rules
   as ``search_species`` (AND-combined identifiers, default trust posture
   on the species entry).
2. For each surviving species_entry, fetch thermo records using the same
   per-record ordering as ``get_species_thermo`` (D8/L3: temperature
   coverage, extrapolation distance, review_rank, evidence_completeness,
   created_at, id).
3. Group across species_entries deterministically: outer key is the
   species_entry's review rank then created_at then id; inner order is the
   thermo per-record ordering already applied above.
4. Apply collapse and pagination to the flat list.

This means ``collapse="first"`` returns the best-thermo-of-the-best-entry
candidate. The full ordering is documented in code comments and tests.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    Pagination,
    ReviewStatusSummary,
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
    reject_client_sort,
    review_summary,
    validate_includes,
    validate_pagination,
    validate_temperature_range,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.species import search_species
from app.services.scientific_read.thermo import get_species_thermo

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "provenance",
    "calculations",
    "artifacts",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

# Tokens forwarded to the inner thermo retrieval. The set matches the
# public legal tokens minus ``internal_ids`` / ``all`` — there are no
# v0 no-op tokens at this endpoint (a previous draft accepted
# ``statmech`` and ``conformers`` as accepted-but-no-op placeholders;
# they were removed in favor of returning ``unknown_include_token`` so
# the include grammar matches its semantics). If a future phase wires
# either token through, add it back here and to ``_LEGAL_INCLUDE_TOKENS``.
_THERMO_LEGAL_INCLUDES_PASSTHROUGH = {"provenance", "calculations", "review", "artifacts"}

_DEFAULT_SORT_ECHO = (
    "species_review_rank,species_created_at,species_id;"
    "covers_requested_temperature_range,extrapolation_distance_k,"
    "review_rank,evidence_completeness,created_at,id"
)


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
    validate_temperature_range(request.temperature_min, request.temperature_max)

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
    species_request = SpeciesSearchRequest(
        smiles=request.smiles,
        inchi=request.inchi,
        inchi_key=request.inchi_key,
        formula=request.formula,
        charge=request.charge,
        multiplicity=request.multiplicity,
        electronic_state_kind=request.electronic_state_kind,
        species_entry_kind=request.species_entry_kind,
        # Phase C: pass through explicit refs as identity filters.
        species_ref=request.species_ref,
        species_entry_ref=request.species_entry_ref,
        # Pass through the trust posture so entry-level filtering is
        # consistent across the two layers.
        min_review_status=None,  # entry filtering only — thermo filter is shallow
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
        # We need every entry in this discovery pass; pagination is final
        # only after the inner thermo expansion.
        offset=0,
        limit=200,
        collapse=request.collapse,
        include=[],
    )
    species_resp = search_species(session, species_request)

    # Flatten species → entries with their species context.
    entry_contexts: list[tuple[ThermoSearchSpeciesContext, int]] = []
    for sp_record in species_resp.records:
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
        # Always fetch the full candidate set from the inner detail
        # endpoint so the outer ``pagination.total`` reflects every
        # candidate. Collapse is applied once at the outer level below.
        from app.schemas.reads.scientific_common import CollapseMode

        thermo_request = ThermoReadRequest(
            temperature_min=request.temperature_min,
            temperature_max=request.temperature_max,
            model_kind=request.model_kind,
            level_of_theory_id=request.level_of_theory_id,
            level_of_theory_ref=request.level_of_theory_ref,
            software=request.software,
            min_review_status=request.min_review_status,
            include_rejected=request.include_rejected,
            include_deprecated=request.include_deprecated,
            include=inner_includes,
            collapse=CollapseMode.all,
            offset=0,
            limit=200,
        )
        thermo_resp = get_species_thermo(
            session, species_entry_id=entry_id, request=thermo_request
        )
        for thermo_record in thermo_resp.records:
            flat.append(ThermoSearchRecord(species=ctx, thermo=thermo_record))

    if not flat:
        return _empty_response(request, includes, offset, limit)

    # 3) Group by species_entry deterministically. Outer key uses the
    # species_entry's review rank, then a stable falling-id tiebreaker —
    # we don't have created_at on the species record here, so id desc is
    # the documented L3 fallback already used elsewhere. Inner thermo
    # ordering is already applied by ``get_species_thermo``.
    def sort_key(rec: ThermoSearchRecord) -> tuple:
        return (
            REVIEW_RANK[rec.species.species_entry_review.status],
            -rec.species.species_entry_id,
            # Inner thermo ordering — keep the per-entry order from the
            # detail call by stable-sorting on the per-record sort keys.
            -int(
                rec.thermo.temperature_coverage.covers_requested_range
                if rec.thermo.temperature_coverage is not None
                else 0
            ),
            rec.thermo.temperature_coverage.extrapolation_distance_k
            if rec.thermo.temperature_coverage is not None
            else 0.0,
            REVIEW_RANK[rec.thermo.review.status],
            -rec.thermo.evidence_completeness.score,
            -rec.thermo.thermo_id,
        )

    flat.sort(key=sort_key)

    # 4) Collapse + pagination on the flat list.
    pre_collapse_total = len(flat)
    collapse_first = request.collapse.value == "first"
    if collapse_first:
        returned = flat[:1]
    else:
        returned = flat[offset : offset + limit]

    summary = review_summary(rec.thermo.review for rec in flat)

    return ScientificThermoSearchResponse(
        request=RequestEcho(
            filter=_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=returned,
        pagination=build_pagination(
            offset=offset,
            limit=limit,
            returned=len(returned),
            total=pre_collapse_total,
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
    if request.temperature_min is not None:
        echo["temperature_min"] = request.temperature_min
    if request.temperature_max is not None:
        echo["temperature_max"] = request.temperature_max
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
            collapse=request.collapse,
            include=sorted(includes),
        ),
        review_summary=ReviewStatusSummary(),
        records=[],
        pagination=Pagination(offset=offset, limit=limit, returned=0, total=0),
    )
