"""Service implementation for /api/v1/scientific/transport/search.

Records reuse :class:`ScientificTransportRecord` from the detail
endpoint via the shared :func:`build_transport_record` helper.

See ``backend/docs/specs/scientific_transport_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transport import Transport, TransportSourceCalculation
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_transport import (
    ScientificTransportRecord,
)
from app.schemas.reads.scientific_transport_search import (
    RequestEcho,
    ScientificTransportSearchResponse,
    TransportSearchRequest,
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
from app.services.scientific_read.transport import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_transport_record,
)


_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "species_ref",
    "species_entry_ref",
    "transport_ref",
    "model_kind",
    "has_source_calculations",
    "has_lj_parameters",
    "has_dipole_moment",
    "has_polarizability",
    "has_rotational_relaxation",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
)


_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_transport(
    session: Session, request: TransportSearchRequest
) -> ScientificTransportSearchResponse:
    """Multi-axis transport search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/transport/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    species_id, short_circuit = _resolve_filter_ref(
        session, Species, request.species_ref, "species"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    species_entry_id, short_circuit = _resolve_filter_ref(
        session, SpeciesEntry, request.species_entry_ref, "species_entry"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    transport_id, short_circuit = _resolve_filter_ref(
        session, Transport, request.transport_ref, "transport"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    stmt = select(Transport.id, Transport.created_at)
    stmt = _apply_identity_filters(
        stmt,
        species_id=species_id,
        species_entry_id=species_entry_id,
        transport_id=transport_id,
    )
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}
    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transport,
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

    return ScientificTransportSearchResponse(
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


def _enforce_at_least_one_filter(request: TransportSearchRequest) -> None:
    """Reject requests with no meaningful filter. ``None`` skips;
    explicit ``False`` is meaningful."""
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/transport/search."
    )


def _resolve_filter_ref(
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
    species_id: int | None,
    species_entry_id: int | None,
    transport_id: int | None,
):
    if transport_id is not None:
        stmt = stmt.where(Transport.id == transport_id)
    if species_entry_id is not None:
        stmt = stmt.where(Transport.species_entry_id == species_entry_id)
    elif species_id is not None:
        stmt = stmt.join(
            SpeciesEntry, SpeciesEntry.id == Transport.species_entry_id
        ).where(SpeciesEntry.species_id == species_id)
    return stmt


def _apply_scalar_filters(stmt, request: TransportSearchRequest):
    if request.model_kind is not None:
        # The ORM has no ``model_kind`` column; ``scientific_origin``
        # is the closest model-class signal. The request schema's
        # ``model_kind`` field is typed ``ScientificOriginKind`` so the
        # value is already in the right enum space.
        stmt = stmt.where(Transport.scientific_origin == request.model_kind)
    return stmt


def _apply_evidence_filters(stmt, request: TransportSearchRequest):
    if request.has_source_calculations is not None:
        ex = exists().where(
            TransportSourceCalculation.transport_id == Transport.id
        )
        stmt = stmt.where(ex if request.has_source_calculations else ~ex)
    if request.has_lj_parameters is not None:
        # ``lj_pair_both_or_neither`` constraint ensures sigma and
        # epsilon are populated together; checking either suffices.
        cond = Transport.sigma_angstrom.is_not(None)
        stmt = stmt.where(cond if request.has_lj_parameters else ~cond)
    if request.has_dipole_moment is not None:
        cond = Transport.dipole_debye.is_not(None)
        stmt = stmt.where(cond if request.has_dipole_moment else ~cond)
    if request.has_polarizability is not None:
        cond = Transport.polarizability_angstrom3.is_not(None)
        stmt = stmt.where(cond if request.has_polarizability else ~cond)
    if request.has_rotational_relaxation is not None:
        cond = Transport.rotational_relaxation.is_not(None)
        stmt = stmt.where(cond if request.has_rotational_relaxation else ~cond)
    return stmt


def _apply_method_basis_software_filters(
    stmt, request: TransportSearchRequest
):
    """Provenance filters narrow transport rows to those whose
    source-calculation graph carries at least one row matching the
    supplied method / basis / software / workflow."""
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
            TransportSourceCalculation,
            TransportSourceCalculation.calculation_id == Calculation.id,
        )
        .where(TransportSourceCalculation.transport_id == Transport.id)
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
) -> list[ScientificTransportRecord]:
    if not page_ids:
        return []
    rows = session.scalars(
        select(Transport).where(Transport.id.in_(page_ids))
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[ScientificTransportRecord] = []
    for cid in page_ids:
        tr = by_id.get(cid)
        if tr is None:  # pragma: no cover — race with delete
            continue
        out.append(
            build_transport_record(
                session, tr=tr, badge=badges[cid], includes=includes
            )
        )
    return out


def _empty_response(
    request: TransportSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificTransportSearchResponse:
    return ScientificTransportSearchResponse(
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


def _request_filter_echo(request: TransportSearchRequest) -> dict[str, Any]:
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


__all__ = ["search_transport"]
