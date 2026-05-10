"""Service implementation for /api/v1/scientific/kinetics/search.

Chemistry-first kinetics search composes the existing reaction discovery
and kinetics retrieval services so callers do not have to chain ids
manually.

Composition order (final response ordering):

1. Resolve reaction/reaction_entry candidates using the same multiset
   matching rules as ``search_reactions`` (direction handling included).
2. For each surviving reaction_entry, fetch kinetics records using the
   same per-record D9 ordering as ``get_reaction_kinetics``.
3. Group across reaction_entries deterministically: outer key is the
   reaction_entry's review rank then id; inner order is the kinetics
   D9 chain already applied.
4. Apply collapse and pagination to the flat list.

Non-TS-backed kinetics surface here exactly as in the detail endpoint —
TS-chain provenance fields are ``null`` and the records are not hidden.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    Pagination,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_kinetics_search import (
    KineticsSearchReactionContext,
    KineticsSearchRecord,
    KineticsSearchRequest,
    RequestEcho,
    ScientificKineticsSearchResponse,
)
from app.schemas.reads.scientific_reactions import ReactionSearchRequest
from app.services.scientific_read.common import (
    build_pagination,
    reject_client_sort,
    review_summary,
    validate_includes,
    validate_pagination,
    validate_temperature_range,
)
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.reactions import search_reactions

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "provenance",
    "calculations",
    "artifacts",
    "review",
    "species",
    "transition_states",
    "path_search",
    "irc",
    "all",
}

# Tokens passed through to the kinetics detail endpoint (it has its own
# legal set; intersection prevents 422 noise from cross-endpoint tokens).
_KINETICS_LEGAL_INCLUDES_PASSTHROUGH = {
    "provenance",
    "calculations",
    "transition_states",
    "path_search",
    "irc",
    "review",
    "artifacts",
}

_DEFAULT_SORT_ECHO = (
    "reaction_entry_review_rank,reaction_entry_id;"
    "covers_requested_range,extrapolation_distance_k,review_rank,"
    "evidence_completeness,created_at,id"
)


def search_kinetics(
    session: Session, request: KineticsSearchRequest
) -> ScientificKineticsSearchResponse:
    """Chemistry-first kinetics search.

    Returns kinetics records along with the resolved reaction/reaction_entry
    identity context. Composes :func:`search_reactions` and
    :func:`get_reaction_kinetics` — non-TS-backed kinetics surface with null
    TS-chain provenance per Phase 2.2; nothing is fabricated.

    :param session: SQLAlchemy session.
    :param request: Parsed request model.
    :returns: ``ScientificKineticsSearchResponse``.
    :raises ValueError: 422 for sort/pagination/include/temperature validation.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include, _LEGAL_INCLUDE_TOKENS, "/scientific/kinetics/search"
    )
    validate_temperature_range(request.temperature_min, request.temperature_max)

    # 1) Resolve reaction_entries.
    reactions_request = ReactionSearchRequest(
        reactants=request.reactants,
        products=request.products,
        direction=request.direction,
        family=request.family,
        # Default trust posture on entry; kinetics-level review filter is
        # applied below so it stays shallow per D7.
        min_review_status=None,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
        offset=0,
        limit=200,
        collapse=request.collapse,
        include=[],
    )
    reactions_resp = search_reactions(session, reactions_request)

    # Flatten reaction records into reaction context tuples.
    reaction_contexts: list[tuple[KineticsSearchReactionContext, int]] = []
    for r_record in reactions_resp.records:
        ctx = KineticsSearchReactionContext(
            reaction_id=r_record.reaction_id,
            reaction_entry_id=r_record.reaction_entry_id,
            equation=r_record.equation,
            reversible=r_record.reversible,
            family=r_record.family,
            matched_direction=r_record.matched_direction,
            reactants=r_record.reactants,
            products=r_record.products,
            reaction_entry_review=r_record.review,
        )
        reaction_contexts.append((ctx, r_record.reaction_entry_id))

    if not reaction_contexts:
        return _empty_response(request, includes, offset, limit)

    # 2) Per entry, retrieve kinetics with D9 ordering already applied.
    inner_includes = sorted(includes & _KINETICS_LEGAL_INCLUDES_PASSTHROUGH)

    flat: list[KineticsSearchRecord] = []
    for ctx, entry_id in reaction_contexts:
        # Always fetch the full candidate set from the inner detail
        # endpoint; collapse is applied once at the outer level below.
        from app.schemas.reads.scientific_common import CollapseMode

        kinetics_request = KineticsReadRequest(
            temperature_min=request.temperature_min,
            temperature_max=request.temperature_max,
            pressure=request.pressure,
            model_kind=request.model_kind,
            level_of_theory_id=request.level_of_theory_id,
            software=request.software,
            min_review_status=request.min_review_status,
            include_rejected=request.include_rejected,
            include_deprecated=request.include_deprecated,
            include=inner_includes,
            collapse=CollapseMode.all,
            offset=0,
            limit=200,
        )
        kinetics_resp = get_reaction_kinetics(
            session, reaction_entry_id=entry_id, request=kinetics_request
        )
        for kinetics_record in kinetics_resp.records:
            flat.append(
                KineticsSearchRecord(reaction=ctx, kinetics=kinetics_record)
            )

    if not flat:
        return _empty_response(request, includes, offset, limit)

    # 3) Group by reaction_entry deterministically.
    def sort_key(rec: KineticsSearchRecord) -> tuple:
        return (
            REVIEW_RANK[rec.reaction.reaction_entry_review.status],
            -rec.reaction.reaction_entry_id,
            -int(
                rec.kinetics.temperature_coverage.covers_requested_range
                if rec.kinetics.temperature_coverage is not None
                else 0
            ),
            rec.kinetics.temperature_coverage.extrapolation_distance_k
            if rec.kinetics.temperature_coverage is not None
            else 0.0,
            REVIEW_RANK[rec.kinetics.review.status],
            -rec.kinetics.evidence_completeness.score,
            -rec.kinetics.kinetics_id,
        )

    flat.sort(key=sort_key)

    # 4) Collapse + pagination.
    pre_collapse_total = len(flat)
    collapse_first = request.collapse.value == "first"
    if collapse_first:
        returned = flat[:1]
    else:
        returned = flat[offset : offset + limit]

    summary = review_summary(rec.kinetics.review for rec in flat)

    return ScientificKineticsSearchResponse(
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


def _filter_echo(request: KineticsSearchRequest) -> dict[str, object]:
    echo: dict[str, object] = {}
    if request.reactants:
        echo["reactants"] = list(request.reactants)
    if request.products:
        echo["products"] = list(request.products)
    echo["direction"] = request.direction.value
    if request.family is not None:
        echo["family"] = request.family
    if request.temperature_min is not None:
        echo["temperature_min"] = request.temperature_min
    if request.temperature_max is not None:
        echo["temperature_max"] = request.temperature_max
    if request.pressure is not None:
        echo["pressure"] = request.pressure
    if request.model_kind is not None:
        echo["model_kind"] = request.model_kind.value
    if request.level_of_theory_id is not None:
        echo["level_of_theory_id"] = request.level_of_theory_id
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
    request: KineticsSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificKineticsSearchResponse:
    return ScientificKineticsSearchResponse(
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
