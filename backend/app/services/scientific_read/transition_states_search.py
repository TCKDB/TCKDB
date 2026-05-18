"""Service implementation for /api/v1/scientific/transition-states/search.

Returns records at the transition-state-entry grain (one record per
``transition_state_entry`` row that matches the filter set). Shares the
``ScientificTransitionStateEntryRecord`` shape with the TS-entry detail
endpoint so search and detail callers can parse responses with one set
of code.

Composition mirrors the calculations-search service:

1. Validate include / sort / pagination via shared helpers.
2. Reject the empty-filter request with 422 ``missing_filter``.
3. Resolve owner/parent refs to integer ids (422 on malformed /
   wrong-prefix refs; empty short-circuit on unknown refs).
4. Build the candidate SQL query joining ``transition_state_entry`` to
   its parent ``transition_state``, ``reaction_entry``, and the
   ``calculation`` evidence tables.
5. Bulk-load review badges; apply the visible-statuses gate.
6. Sort deterministically (review rank → created_at desc → id desc).
7. Slice for pagination, then materialize each page row via the shared
   :func:`build_entry_record` helper.

See ``backend/docs/specs/scientific_transition_state_reads.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
)
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_transition_state import (
    ScientificTransitionStateEntryRecord,
)
from app.schemas.reads.scientific_transition_state_search import (
    RequestEcho,
    ScientificTransitionStatesSearchResponse,
    TransitionStatesSearchRequest,
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
from app.services.scientific_read.handles import (
    resolve_filter_ref,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.transition_states import (
    _INTERNAL_INCLUDE_TOKENS,
    _LEGAL_INCLUDE_TOKENS,
    build_entry_record,
    _build_reaction_context,
    _build_ts_core_block,
)


# Filter knobs that count as "meaningful" for the at-least-one-filter rule.
# Pure pagination / include / review knobs are deliberately excluded.
_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "reaction_ref",
    "reaction_entry_ref",
    "transition_state_ref",
    "transition_state_entry_ref",
    "status",
    "charge",
    "multiplicity",
    "has_calculations",
    "has_opt",
    "has_freq",
    "has_sp",
    "has_irc",
    "has_path_search",
    "has_geometry_validation",
    "has_scf_stability",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
)


_DEFAULT_SORT_ECHO = "review_rank,created_at,id"


def search_transition_states(
    session: Session, request: TransitionStatesSearchRequest
) -> ScientificTransitionStatesSearchResponse:
    """Multi-axis transition-state-entry search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/transition-states/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)
    # ``include=entries`` is meaningful only on the TS-concept detail
    # endpoint. On search it would be a no-op (each record IS an entry),
    # so silently drop it without raising — keeps a generic client able
    # to pass the same include set to both surfaces.
    includes.discard("entries")

    _enforce_at_least_one_filter(request)

    # --- ref resolution -----------------------------------------------------
    reaction_id, short_circuit = _resolve_filter_ref(
        session, ChemReaction, request.reaction_ref, "reaction"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    reaction_entry_id, short_circuit = _resolve_filter_ref(
        session, ReactionEntry, request.reaction_entry_ref, "reaction_entry"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    ts_id, short_circuit = _resolve_filter_ref(
        session, TransitionState, request.transition_state_ref,
        "transition_state",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    tse_id, short_circuit = _resolve_filter_ref(
        session,
        TransitionStateEntry,
        request.transition_state_entry_ref,
        "transition_state_entry",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    # --- candidate query ----------------------------------------------------
    stmt = select(
        TransitionStateEntry.id,
        TransitionStateEntry.created_at,
    )
    stmt = _apply_parent_filters(
        stmt,
        reaction_id=reaction_id,
        reaction_entry_id=reaction_entry_id,
        ts_id=ts_id,
        tse_id=tse_id,
    )
    stmt = _apply_scalar_filters(stmt, request)
    stmt = _apply_evidence_filters(stmt, request)
    stmt = _apply_method_basis_software_filters(stmt, request)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}

    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    # --- review filter ------------------------------------------------------
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_ids=candidate_ids,
    )
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

    # --- deterministic sort -------------------------------------------------
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

    return ScientificTransitionStatesSearchResponse(
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
    request: TransitionStatesSearchRequest,
) -> None:
    """Reject requests with no meaningful filter.

    Bool filters in :class:`TransitionStatesSearchRequest` default to
    ``None``; an explicit ``False`` from the caller is a meaningful
    filter (e.g. ``has_opt=false`` selects TS entries *without* opt
    evidence), so only ``None`` skips here. ``include_rejected`` and
    friends are not in ``_MEANINGFUL_FILTER_FIELDS``, so their
    ``bool = False`` defaults can't accidentally satisfy the gate.
    """
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is None:
            continue
        return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/transition-states/search."
    )


def _resolve_filter_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    """Resolve an optional ``*_ref`` filter to (resolved_id, short_circuit)."""
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


def _apply_parent_filters(
    stmt,
    *,
    reaction_id: int | None,
    reaction_entry_id: int | None,
    ts_id: int | None,
    tse_id: int | None,
):
    if tse_id is not None:
        stmt = stmt.where(TransitionStateEntry.id == tse_id)
    if ts_id is not None:
        stmt = stmt.where(TransitionStateEntry.transition_state_id == ts_id)
    if reaction_entry_id is not None:
        stmt = stmt.join(
            TransitionState,
            TransitionState.id == TransitionStateEntry.transition_state_id,
        ).where(TransitionState.reaction_entry_id == reaction_entry_id)
    elif reaction_id is not None:
        # Narrow to entries whose TS belongs to a reaction_entry that
        # belongs to the given chem_reaction.
        stmt = (
            stmt.join(
                TransitionState,
                TransitionState.id
                == TransitionStateEntry.transition_state_id,
            )
            .join(
                ReactionEntry,
                ReactionEntry.id == TransitionState.reaction_entry_id,
            )
            .where(ReactionEntry.reaction_id == reaction_id)
        )
    return stmt


def _apply_scalar_filters(
    stmt, request: TransitionStatesSearchRequest
):
    if request.status is not None:
        stmt = stmt.where(TransitionStateEntry.status == request.status)
    if request.charge is not None:
        stmt = stmt.where(TransitionStateEntry.charge == request.charge)
    if request.multiplicity is not None:
        stmt = stmt.where(
            TransitionStateEntry.multiplicity == request.multiplicity
        )
    return stmt


def _apply_evidence_filters(
    stmt, request: TransitionStatesSearchRequest
):
    if request.has_calculations is not None:
        ex = exists().where(
            Calculation.transition_state_entry_id == TransitionStateEntry.id
        )
        stmt = stmt.where(ex if request.has_calculations else ~ex)

    type_filters: list[tuple[bool | None, CalculationType]] = [
        (request.has_opt, CalculationType.opt),
        (request.has_freq, CalculationType.freq),
        (request.has_sp, CalculationType.sp),
        (request.has_irc, CalculationType.irc),
        (request.has_path_search, CalculationType.path_search),
    ]
    for want, calc_type in type_filters:
        if want is None:
            continue
        ex = exists().where(
            and_(
                Calculation.transition_state_entry_id
                == TransitionStateEntry.id,
                Calculation.type == calc_type,
            )
        )
        stmt = stmt.where(ex if want else ~ex)

    if request.has_geometry_validation is not None:
        ex = exists().where(
            and_(
                CalculationGeometryValidation.calculation_id
                == Calculation.id,
                Calculation.transition_state_entry_id
                == TransitionStateEntry.id,
            )
        )
        stmt = stmt.where(ex if request.has_geometry_validation else ~ex)

    if request.has_scf_stability is not None:
        ex = exists().where(
            and_(
                CalculationSCFStability.calculation_id == Calculation.id,
                Calculation.transition_state_entry_id
                == TransitionStateEntry.id,
            )
        )
        stmt = stmt.where(ex if request.has_scf_stability else ~ex)

    return stmt


def _apply_method_basis_software_filters(
    stmt, request: TransitionStatesSearchRequest
):
    """Method/basis/software/workflow filters narrow TS entries to those
    whose calculation evidence includes a row matching the supplied
    provenance. The match is an OR-across-calc set: a TS entry passes
    if at least one of its calculations matches.
    """
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

    sub_clauses = [
        Calculation.transition_state_entry_id == TransitionStateEntry.id,
    ]
    sub_select = select(Calculation.id).where(*sub_clauses)
    if method_or_basis:
        sub_select = sub_select.join(
            LevelOfTheory, LevelOfTheory.id == Calculation.lot_id
        )
        if request.method is not None:
            sub_select = sub_select.where(
                LevelOfTheory.method == request.method
            )
        if request.basis is not None:
            sub_select = sub_select.where(
                LevelOfTheory.basis == request.basis
            )
    if sw_filter:
        sub_select = sub_select.join(
            SoftwareRelease,
            SoftwareRelease.id == Calculation.software_release_id,
        ).join(Software, Software.id == SoftwareRelease.software_id)
        if request.software is not None:
            sub_select = sub_select.where(Software.name == request.software)
        if request.software_version is not None:
            sub_select = sub_select.where(
                SoftwareRelease.version == request.software_version
            )
    if wf_filter:
        sub_select = sub_select.join(
            WorkflowToolRelease,
            WorkflowToolRelease.id == Calculation.workflow_tool_release_id,
        ).join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        if request.workflow_tool is not None:
            sub_select = sub_select.where(
                WorkflowTool.name == request.workflow_tool
            )
        if request.workflow_tool_version is not None:
            sub_select = sub_select.where(
                WorkflowToolRelease.version == request.workflow_tool_version
            )
    return stmt.where(sub_select.exists())


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _materialize_records(
    session: Session,
    page_ids: list[int],
    badges: dict[int, RecordReviewBadge],
    includes: set[str],
) -> list[ScientificTransitionStateEntryRecord]:
    """Materialize TS-entry records via the shared per-entry builder."""
    if not page_ids:
        return []
    # Bulk-load entries, parent TS rows, and TS review badges. Each page
    # is small (limit <= 200), so per-entry parent lookups are bounded.
    entries = session.scalars(
        select(TransitionStateEntry).where(
            TransitionStateEntry.id.in_(page_ids)
        )
    ).all()
    entry_by_id = {e.id: e for e in entries}

    ts_ids = {e.transition_state_id for e in entries}
    ts_rows = session.scalars(
        select(TransitionState).where(TransitionState.id.in_(ts_ids))
    ).all()
    ts_by_id = {t.id: t for t in ts_rows}

    ts_badges = (
        fetch_review_badges(
            session,
            record_type=SubmissionRecordType.transition_state,
            record_ids=list(ts_ids),
        )
        if ts_ids
        else {}
    )

    # Cache reaction context per reaction_entry_id to avoid re-querying
    # the participants when several TS entries share a parent.
    reaction_cache: dict[int | None, Any] = {}

    records: list[ScientificTransitionStateEntryRecord] = []
    for cid in page_ids:
        entry = entry_by_id.get(cid)
        if entry is None:  # pragma: no cover — race with delete
            continue
        ts = ts_by_id.get(entry.transition_state_id)
        if ts is None:  # pragma: no cover — FK guarantees existence
            continue
        ts_badge = ts_badges.get(
            ts.id,
            RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
        )
        ts_core = _build_ts_core_block(ts, ts_badge)
        re_id = ts.reaction_entry_id
        if re_id not in reaction_cache:
            reaction_cache[re_id] = _build_reaction_context(session, re_id)
        reaction = reaction_cache[re_id]
        records.append(
            build_entry_record(
                session,
                entry=entry,
                ts_core=ts_core,
                reaction=reaction,
                entry_badge=badges[cid],
                includes=includes,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Empty + echo helpers
# ---------------------------------------------------------------------------


def _empty_response(
    request: TransitionStatesSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificTransitionStatesSearchResponse:
    return ScientificTransitionStatesSearchResponse(
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


def _request_filter_echo(request: TransitionStatesSearchRequest) -> dict[str, Any]:
    """Return the caller's filter inputs verbatim (post-parse)."""
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


__all__ = [
    "search_transition_states",
]
