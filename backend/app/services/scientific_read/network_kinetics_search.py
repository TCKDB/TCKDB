"""Service implementation for /api/v1/scientific/network-kinetics/search.

Records reuse :class:`ScientificNetworkKineticsRecord` from the
network-kinetics detail endpoint via the shared
:func:`build_network_kinetics_record` helper, so search and detail
return byte-identical per-record payloads for the same include set.

Review state is inherited from the parent solve (``NetworkKinetics``
is not in ``SubmissionRecordType``); the search applies trust filters
against the parent solve's badge in the same way the detail surface
applies it to a single record.

See ``backend/docs/specs/scientific_network_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.network import Network
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolve,
    NetworkSolveSourceCalculation,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_network_kinetics import (
    ScientificNetworkKineticsRecord,
)
from app.schemas.reads.scientific_network_kinetics_search import (
    NetworkKineticsSearchRequest,
    RequestEcho,
    ScientificNetworkKineticsSearchResponse,
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
from app.services.scientific_read.network_channel_chemistry import (
    apply_channel_chemistry_filters,
)
from app.services.scientific_read.network_kinetics import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_network_kinetics_record,
)

# Filters that satisfy the at-least-one-filter guard. Trust knobs
# (``include_rejected`` / ``include_deprecated`` / ``min_review_status``)
# don't count — they tune visibility against a candidate set rather
# than narrow which kinetics records are candidates in the first
# place. Matches the convention used by the network and
# network-solve search surfaces.
_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "network_kinetics_ref",
    "network_ref",
    "network_solve_ref",
    "source_species_entry_refs",
    "sink_species_entry_refs",
    "source_smiles",
    "sink_smiles",
    "model_kind",
    "temperature_min",
    "temperature_max",
    "pressure_min",
    "pressure_max",
    "has_chebyshev",
    "has_plog",
    "has_points",
    "has_source_calculations",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
)


_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_network_kinetics(
    session: Session, request: NetworkKineticsSearchRequest
) -> ScientificNetworkKineticsSearchResponse:
    """Multi-axis network-kinetics search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/network-kinetics/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    # --- ref resolution -----------------------------------------------------
    nk_id, short = _resolve_ref(
        session,
        NetworkKinetics,
        request.network_kinetics_ref,
        "network_kinetics",
    )
    if short:
        return _empty_response(request, includes, offset, limit)
    network_id, short = _resolve_ref(
        session, Network, request.network_ref, "network"
    )
    if short:
        return _empty_response(request, includes, offset, limit)
    solve_id, short = _resolve_ref(
        session, NetworkSolve, request.network_solve_ref, "network_solve"
    )
    if short:
        return _empty_response(request, includes, offset, limit)

    # --- candidate query ----------------------------------------------------
    stmt = select(
        NetworkKinetics.id,
        NetworkKinetics.solve_id,
        NetworkKinetics.created_at,
    )
    stmt = _apply_identity_filters(
        stmt,
        nk_id=nk_id,
        network_id=network_id,
        solve_id=solve_id,
    )
    stmt = apply_channel_chemistry_filters(stmt, request)
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_envelope_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    if not rows:
        return _empty_response(request, includes, offset, limit)

    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    parent_solve_by_kinetics = {row.id: row.solve_id for row in rows}

    # Review badges are inherited from the parent solve.
    parent_solve_ids = list({row.solve_id for row in rows})
    solve_badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.network_solve,
        record_ids=parent_solve_ids,
    )
    badges: dict[int, RecordReviewBadge] = {
        cid: solve_badges[parent_solve_by_kinetics[cid]]
        for cid in candidate_ids
    }

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_ids = [
        cid for cid in candidate_ids if badges[cid].status in visible
    ]
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

    return ScientificNetworkKineticsSearchResponse(
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


def _enforce_at_least_one_filter(
    request: NetworkKineticsSearchRequest,
) -> None:
    """Reject requests with no meaningful filter. Only ``None`` skips;
    explicit ``False`` is meaningful."""
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None or value == []:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/network-kinetics/search."
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
    nk_id: int | None,
    network_id: int | None,
    solve_id: int | None,
):
    if nk_id is not None:
        stmt = stmt.where(NetworkKinetics.id == nk_id)
    if solve_id is not None:
        stmt = stmt.where(NetworkKinetics.solve_id == solve_id)
    if network_id is not None:
        # Network ↔ kinetics path goes through NetworkSolve.network_id.
        sub = (
            select(NetworkSolve.id)
            .where(NetworkSolve.network_id == network_id)
            .where(NetworkSolve.id == NetworkKinetics.solve_id)
        )
        stmt = stmt.where(sub.exists())
    return stmt


def _apply_scalar_filters(stmt, request: NetworkKineticsSearchRequest):
    if request.model_kind is not None:
        stmt = stmt.where(NetworkKinetics.model_kind == request.model_kind)
    return stmt


def _apply_envelope_filters(stmt, request: NetworkKineticsSearchRequest):
    """Overlap semantics — a kinetics record matches ``temperature_min=X``
    iff its own ``tmax_k >= X`` (and analogously for the other bound /
    for pressure). Matches the network and network-solve search surfaces.
    """
    if request.temperature_min is not None:
        stmt = stmt.where(
            and_(
                NetworkKinetics.tmax_k.is_not(None),
                NetworkKinetics.tmax_k >= request.temperature_min,
            )
        )
    if request.temperature_max is not None:
        stmt = stmt.where(
            and_(
                NetworkKinetics.tmin_k.is_not(None),
                NetworkKinetics.tmin_k <= request.temperature_max,
            )
        )
    if request.pressure_min is not None:
        stmt = stmt.where(
            and_(
                NetworkKinetics.pmax_bar.is_not(None),
                NetworkKinetics.pmax_bar >= request.pressure_min,
            )
        )
    if request.pressure_max is not None:
        stmt = stmt.where(
            and_(
                NetworkKinetics.pmin_bar.is_not(None),
                NetworkKinetics.pmin_bar <= request.pressure_max,
            )
        )
    return stmt


def _apply_evidence_filters(stmt, request: NetworkKineticsSearchRequest):
    if request.has_chebyshev is not None:
        ex = exists().where(
            NetworkKineticsChebyshev.network_kinetics_id == NetworkKinetics.id
        )
        stmt = stmt.where(ex if request.has_chebyshev else ~ex)
    if request.has_plog is not None:
        ex = exists().where(
            NetworkKineticsPlog.network_kinetics_id == NetworkKinetics.id
        )
        stmt = stmt.where(ex if request.has_plog else ~ex)
    if request.has_points is not None:
        ex = exists().where(
            NetworkKineticsPoint.network_kinetics_id == NetworkKinetics.id
        )
        stmt = stmt.where(ex if request.has_points else ~ex)
    if request.has_source_calculations is not None:
        ex = exists().where(
            NetworkSolveSourceCalculation.solve_id == NetworkKinetics.solve_id
        )
        stmt = stmt.where(ex if request.has_source_calculations else ~ex)
    return stmt


def _apply_method_basis_software_filters(
    stmt, request: NetworkKineticsSearchRequest
):
    """Filter to kinetics whose parent solve's source-calc graph carries
    at least one calculation matching the supplied provenance.

    Routes through ``NetworkKinetics.solve_id ->
    NetworkSolveSourceCalculation.calculation_id -> Calculation`` and
    then onto ``LevelOfTheory`` / ``SoftwareRelease`` /
    ``WorkflowToolRelease`` exactly the way the network-solve search
    does — so a record visible there for a given LoT/software/workflow
    triple is also visible here for the same triple.
    """
    method_or_basis = (
        request.method is not None or request.basis is not None
    )
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
        .where(
            NetworkSolveSourceCalculation.solve_id == NetworkKinetics.solve_id
        )
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
) -> list[ScientificNetworkKineticsRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(NetworkKinetics).where(NetworkKinetics.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificNetworkKineticsRecord] = []
    for cid in page_ids:
        nk = by_id.get(cid)
        if nk is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_network_kinetics_record(
                session, nk=nk, badge=badges[cid], includes=includes
            )
        )
    return out


def _empty_response(
    request: NetworkKineticsSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificNetworkKineticsSearchResponse:
    return ScientificNetworkKineticsSearchResponse(
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
    request: NetworkKineticsSearchRequest,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in (*_MEANINGFUL_FILTER_FIELDS, "include_rejected", "include_deprecated", "min_review_status"):
        value = getattr(request, name)
        if value is None:
            continue
        out[name] = value.value if hasattr(value, "value") else value
    return out


# Suppress unused-import warning for NetworkChannel — kept imported so
# future channel-ref filter (deferred until NetworkChannel gains a
# public_ref) plugs in here without churn.
_ = NetworkChannel


__all__ = ["search_network_kinetics"]
