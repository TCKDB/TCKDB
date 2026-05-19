"""Service implementation for /api/v1/scientific/network-solves/search.

Records reuse :class:`ScientificNetworkSolveRecord` from the
network-solve detail endpoint via the shared
:func:`build_network_solve_record` helper, so search and detail
return byte-identical per-record payloads for the same include set.

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    NetworkKineticsModelKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.network import Network
from app.db.models.network_pdep import (
    NetworkKinetics,
    NetworkSolve,
    NetworkSolveBathGas,
    NetworkSolveEnergyTransfer,
    NetworkSolveSourceCalculation,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_network import (
    ScientificNetworkSolveRecord,
)
from app.schemas.reads.scientific_network_solve_search import (
    NetworkSolveSearchRequest,
    RequestEcho,
    ScientificNetworkSolveSearchResponse,
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
    _SOLVE_INTERNAL_INCLUDE_TOKENS,
    _SOLVE_LEGAL_INCLUDE_TOKENS,
    build_network_solve_record,
)


_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "network_solve_ref",
    "network_ref",
    "solve_method",
    "temperature_min",
    "temperature_max",
    "pressure_min",
    "pressure_max",
    "has_bath_gas",
    "has_energy_transfer",
    "has_source_calculations",
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
)

_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_network_solves(
    session: Session, request: NetworkSolveSearchRequest
) -> ScientificNetworkSolveSearchResponse:
    """Multi-axis network-solve search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _SOLVE_LEGAL_INCLUDE_TOKENS,
        "/scientific/network-solves/search",
        internal_tokens=_SOLVE_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    # --- ref resolution -----------------------------------------------------
    solve_id, short = _resolve_ref(
        session, NetworkSolve, request.network_solve_ref, "network_solve"
    )
    if short:
        return _empty_response(request, includes, offset, limit)
    network_id, short = _resolve_ref(
        session, Network, request.network_ref, "network"
    )
    if short:
        return _empty_response(request, includes, offset, limit)

    # --- candidate query ----------------------------------------------------
    stmt = select(NetworkSolve.id, NetworkSolve.created_at)
    stmt = _apply_identity_filters(
        stmt, solve_id=solve_id, network_id=network_id
    )
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_envelope_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.network_solve,
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

    return ScientificNetworkSolveSearchResponse(
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


def _enforce_at_least_one_filter(request: NetworkSolveSearchRequest) -> None:
    """Reject requests with no meaningful filter. Only ``None`` skips;
    explicit ``False`` is meaningful."""
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/network-solves/search."
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
    solve_id: int | None,
    network_id: int | None,
):
    if solve_id is not None:
        stmt = stmt.where(NetworkSolve.id == solve_id)
    if network_id is not None:
        stmt = stmt.where(NetworkSolve.network_id == network_id)
    return stmt


def _apply_scalar_filters(stmt, request: NetworkSolveSearchRequest):
    if request.solve_method is not None:
        stmt = stmt.where(NetworkSolve.me_method == request.solve_method)
    return stmt


def _apply_envelope_filters(stmt, request: NetworkSolveSearchRequest):
    """Overlap semantics — matches the network surface's T/P filters.

    A solve matches ``temperature_min=X`` iff its own ``tmax_k >= X``;
    symmetrically for ``temperature_max``. Same for pressure.
    """
    if request.temperature_min is not None:
        stmt = stmt.where(
            and_(
                NetworkSolve.tmax_k.is_not(None),
                NetworkSolve.tmax_k >= request.temperature_min,
            )
        )
    if request.temperature_max is not None:
        stmt = stmt.where(
            and_(
                NetworkSolve.tmin_k.is_not(None),
                NetworkSolve.tmin_k <= request.temperature_max,
            )
        )
    if request.pressure_min is not None:
        stmt = stmt.where(
            and_(
                NetworkSolve.pmax_bar.is_not(None),
                NetworkSolve.pmax_bar >= request.pressure_min,
            )
        )
    if request.pressure_max is not None:
        stmt = stmt.where(
            and_(
                NetworkSolve.pmin_bar.is_not(None),
                NetworkSolve.pmin_bar <= request.pressure_max,
            )
        )
    return stmt


def _apply_evidence_filters(stmt, request: NetworkSolveSearchRequest):
    if request.has_bath_gas is not None:
        ex = exists().where(NetworkSolveBathGas.solve_id == NetworkSolve.id)
        stmt = stmt.where(ex if request.has_bath_gas else ~ex)
    if request.has_energy_transfer is not None:
        ex = exists().where(
            NetworkSolveEnergyTransfer.solve_id == NetworkSolve.id
        )
        stmt = stmt.where(ex if request.has_energy_transfer else ~ex)
    if request.has_source_calculations is not None:
        ex = exists().where(
            NetworkSolveSourceCalculation.solve_id == NetworkSolve.id
        )
        stmt = stmt.where(ex if request.has_source_calculations else ~ex)
    if request.has_kinetics is not None:
        ex = exists().where(NetworkKinetics.solve_id == NetworkSolve.id)
        stmt = stmt.where(ex if request.has_kinetics else ~ex)
    for want, model_kind in (
        (request.has_chebyshev, NetworkKineticsModelKind.chebyshev),
        (request.has_plog, NetworkKineticsModelKind.plog),
        (request.has_point_kinetics, NetworkKineticsModelKind.tabulated),
    ):
        if want is None:
            continue
        ex = exists().where(
            and_(
                NetworkKinetics.solve_id == NetworkSolve.id,
                NetworkKinetics.model_kind == model_kind,
            )
        )
        stmt = stmt.where(ex if want else ~ex)
    return stmt


def _apply_method_basis_software_filters(
    stmt, request: NetworkSolveSearchRequest
):
    """Match solves whose source-calc graph carries at least one calc
    matching the supplied provenance."""
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
        .where(NetworkSolveSourceCalculation.solve_id == NetworkSolve.id)
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
) -> list[ScientificNetworkSolveRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(NetworkSolve).where(NetworkSolve.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificNetworkSolveRecord] = []
    for cid in page_ids:
        s = by_id.get(cid)
        if s is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_network_solve_record(
                session, s=s, badge=badges[cid], includes=includes
            )
        )
    return out


def _empty_response(
    request: NetworkSolveSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificNetworkSolveSearchResponse:
    return ScientificNetworkSolveSearchResponse(
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


def _request_filter_echo(
    request: NetworkSolveSearchRequest,
) -> dict[str, Any]:
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


__all__ = ["search_network_solves"]
