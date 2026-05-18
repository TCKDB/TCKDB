"""Service implementation for /api/v1/scientific/networks/search."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    NetworkKineticsModelKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.network import Network, NetworkReaction, NetworkSpecies
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkSolve,
    NetworkSolveSourceCalculation,
    NetworkState,
)
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_network import (
    ScientificNetworkRecord,
)
from app.schemas.reads.scientific_network_search import (
    NetworkSearchRequest,
    RequestEcho,
    ScientificNetworkSearchResponse,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.handles import resolve_filter_ref
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.networks import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_network_record,
)


_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "network_ref",
    "species_ref",
    "species_entry_ref",
    "reaction_ref",
    "reaction_entry_ref",
    "has_species",
    "has_reactions",
    "has_states",
    "has_channels",
    "has_solves",
    "has_kinetics",
    "has_chebyshev",
    "has_plog",
    "has_point_kinetics",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
    "temperature_min",
    "temperature_max",
    "pressure_min",
    "pressure_max",
)

_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_networks(
    session: Session, request: NetworkSearchRequest
) -> ScientificNetworkSearchResponse:
    """Multi-axis network search (MVP)."""
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/networks/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    # --- ref resolution -----------------------------------------------------
    network_id, short = _resolve_ref(
        session, Network, request.network_ref, "network"
    )
    if short:
        return _empty_response(request, includes, offset, limit)
    species_id, short = _resolve_ref(
        session, Species, request.species_ref, "species"
    )
    if short:
        return _empty_response(request, includes, offset, limit)
    species_entry_id, short = _resolve_ref(
        session, SpeciesEntry, request.species_entry_ref, "species_entry"
    )
    if short:
        return _empty_response(request, includes, offset, limit)
    reaction_id, short = _resolve_ref(
        session, ChemReaction, request.reaction_ref, "reaction"
    )
    if short:
        return _empty_response(request, includes, offset, limit)
    reaction_entry_id, short = _resolve_ref(
        session, ReactionEntry, request.reaction_entry_ref, "reaction_entry"
    )
    if short:
        return _empty_response(request, includes, offset, limit)

    # --- candidate query ----------------------------------------------------
    stmt = select(Network.id, Network.created_at)
    stmt = _apply_identity_filters(
        stmt,
        network_id=network_id,
        species_id=species_id,
        species_entry_id=species_entry_id,
        reaction_id=reaction_id,
        reaction_entry_id=reaction_entry_id,
    )
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_envelope_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.network,
        record_ids=candidate_ids,
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_ids = [cid for cid in candidate_ids if badges[cid].status in visible]
    if not visible_ids:
        return _empty_response(request, includes, offset, limit)

    summary = review_summary(badges[cid] for cid in visible_ids)
    visible_ids.sort(
        key=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            -created_at_by_id[cid].timestamp(),
            -cid,
        )
    )
    total = len(visible_ids)
    page_ids = visible_ids[offset : offset + limit]
    records = _materialize_records(session, page_ids, badges, includes)

    return ScientificNetworkSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total
        ),
    )


# ---------------------------------------------------------------------------
# Filter rule + ref resolution
# ---------------------------------------------------------------------------


def _enforce_at_least_one_filter(request: NetworkSearchRequest) -> None:
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/networks/search."
    )


def _resolve_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    if ref is None:
        return None, False
    resolved = resolve_filter_ref(
        session, model_cls, ref, kind_label=kind_label
    )
    if resolved is None:
        return None, True
    return resolved, False


# ---------------------------------------------------------------------------
# WHERE-clause builders
# ---------------------------------------------------------------------------


def _apply_identity_filters(
    stmt,
    *,
    network_id: int | None,
    species_id: int | None,
    species_entry_id: int | None,
    reaction_id: int | None,
    reaction_entry_id: int | None,
):
    if network_id is not None:
        stmt = stmt.where(Network.id == network_id)
    if species_entry_id is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    NetworkSpecies.network_id == Network.id,
                    NetworkSpecies.species_entry_id == species_entry_id,
                )
            )
        )
    elif species_id is not None:
        stmt = stmt.where(
            exists()
            .where(NetworkSpecies.network_id == Network.id)
            .where(
                NetworkSpecies.species_entry_id.in_(
                    select(SpeciesEntry.id).where(
                        SpeciesEntry.species_id == species_id
                    )
                )
            )
        )
    if reaction_entry_id is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    NetworkReaction.network_id == Network.id,
                    NetworkReaction.reaction_entry_id == reaction_entry_id,
                )
            )
        )
    elif reaction_id is not None:
        stmt = stmt.where(
            exists()
            .where(NetworkReaction.network_id == Network.id)
            .where(
                NetworkReaction.reaction_entry_id.in_(
                    select(ReactionEntry.id).where(
                        ReactionEntry.reaction_id == reaction_id
                    )
                )
            )
        )
    return stmt


def _apply_evidence_filters(stmt, request: NetworkSearchRequest):
    if request.has_species is not None:
        ex = exists().where(NetworkSpecies.network_id == Network.id)
        stmt = stmt.where(ex if request.has_species else ~ex)
    if request.has_reactions is not None:
        ex = exists().where(NetworkReaction.network_id == Network.id)
        stmt = stmt.where(ex if request.has_reactions else ~ex)
    if request.has_states is not None:
        ex = exists().where(NetworkState.network_id == Network.id)
        stmt = stmt.where(ex if request.has_states else ~ex)
    if request.has_channels is not None:
        ex = exists().where(NetworkChannel.network_id == Network.id)
        stmt = stmt.where(ex if request.has_channels else ~ex)
    if request.has_solves is not None:
        ex = exists().where(NetworkSolve.network_id == Network.id)
        stmt = stmt.where(ex if request.has_solves else ~ex)
    if request.has_kinetics is not None:
        ex = (
            select(NetworkKinetics.id)
            .join(
                NetworkSolve, NetworkSolve.id == NetworkKinetics.solve_id
            )
            .where(NetworkSolve.network_id == Network.id)
            .exists()
        )
        stmt = stmt.where(ex if request.has_kinetics else ~ex)
    for want, model_kind in (
        (request.has_chebyshev, NetworkKineticsModelKind.chebyshev),
        (request.has_plog, NetworkKineticsModelKind.plog),
        (request.has_point_kinetics, NetworkKineticsModelKind.tabulated),
    ):
        if want is None:
            continue
        ex = (
            select(NetworkKinetics.id)
            .join(
                NetworkSolve, NetworkSolve.id == NetworkKinetics.solve_id
            )
            .where(
                and_(
                    NetworkSolve.network_id == Network.id,
                    NetworkKinetics.model_kind == model_kind,
                )
            )
            .exists()
        )
        stmt = stmt.where(ex if want else ~ex)
    return stmt


def _apply_envelope_filters(stmt, request: NetworkSearchRequest):
    """Filter networks whose solve-level T/P envelope at least touches
    the requested range (overlap semantics).

    A network matches `temperature_min=X` iff at least one of its
    solves has `tmax_k >= X`; symmetrically for `temperature_max`.
    Same for pressure_min/pressure_max. This is the cheapest useful
    semantic and matches how kinetics search elsewhere handles
    bounded ranges.
    """
    if request.temperature_min is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    NetworkSolve.network_id == Network.id,
                    NetworkSolve.tmax_k.is_not(None),
                    NetworkSolve.tmax_k >= request.temperature_min,
                )
            )
        )
    if request.temperature_max is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    NetworkSolve.network_id == Network.id,
                    NetworkSolve.tmin_k.is_not(None),
                    NetworkSolve.tmin_k <= request.temperature_max,
                )
            )
        )
    if request.pressure_min is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    NetworkSolve.network_id == Network.id,
                    NetworkSolve.pmax_bar.is_not(None),
                    NetworkSolve.pmax_bar >= request.pressure_min,
                )
            )
        )
    if request.pressure_max is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    NetworkSolve.network_id == Network.id,
                    NetworkSolve.pmin_bar.is_not(None),
                    NetworkSolve.pmin_bar <= request.pressure_max,
                )
            )
        )
    return stmt


def _apply_method_basis_software_filters(
    stmt, request: NetworkSearchRequest
):
    """Match networks whose solve source-calc graph carries at least
    one calculation matching the supplied provenance."""
    method_or_basis = request.method is not None or request.basis is not None
    sw_filter = (
        request.software is not None or request.software_version is not None
    )
    wf_filter = (
        request.workflow_tool is not None
        or request.workflow_tool_version is not None
    )
    if not (method_or_basis or sw_filter or wf_filter):
        return stmt

    sub = (
        select(Calculation.id)
        .join(
            NetworkSolveSourceCalculation,
            NetworkSolveSourceCalculation.calculation_id == Calculation.id,
        )
        .join(
            NetworkSolve,
            NetworkSolve.id == NetworkSolveSourceCalculation.solve_id,
        )
        .where(NetworkSolve.network_id == Network.id)
    )
    if method_or_basis:
        sub = sub.join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        if request.method is not None:
            sub = sub.where(LevelOfTheory.method == request.method)
        if request.basis is not None:
            sub = sub.where(LevelOfTheory.basis == request.basis)
    if sw_filter:
        sub = sub.join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
        ).join(Software, Software.id == SoftwareRelease.software_id)
        if request.software is not None:
            sub = sub.where(Software.name == request.software)
        if request.software_version is not None:
            sub = sub.where(
                SoftwareRelease.version == request.software_version
            )
    if wf_filter:
        sub = sub.join(
            WorkflowToolRelease,
            WorkflowToolRelease.id == Calculation.workflow_tool_release_id,
        ).join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        if request.workflow_tool is not None:
            sub = sub.where(WorkflowTool.name == request.workflow_tool)
        if request.workflow_tool_version is not None:
            sub = sub.where(
                WorkflowToolRelease.version == request.workflow_tool_version
            )
    return stmt.where(sub.exists())


# ---------------------------------------------------------------------------
# Materialization + helpers
# ---------------------------------------------------------------------------


def _materialize_records(
    session: Session,
    page_ids: list[int],
    badges: dict[int, RecordReviewBadge],
    includes: set[str],
) -> list[ScientificNetworkRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(Network).where(Network.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificNetworkRecord] = []
    for cid in page_ids:
        n = by_id.get(cid)
        if n is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_network_record(
                session, n=n, badge=badges[cid], includes=includes
            )
        )
    return out


def _empty_response(
    request: NetworkSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificNetworkSearchResponse:
    return ScientificNetworkSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request),
            sort=_DEFAULT_SORT_ECHO,
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


def _request_filter_echo(request: NetworkSearchRequest) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in _MEANINGFUL_FILTER_FIELDS + (
        "include_rejected",
        "include_deprecated",
        "min_review_status",
    ):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = ["search_networks"]
