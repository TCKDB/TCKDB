"""Service implementation for /api/v1/scientific/calculations/search (MVP).

Composition:

1. Validate include / sort / pagination via shared helpers.
2. Reject the empty-filter request with 422 ``missing_filter`` so the
   public surface never accidentally serves a full-table scan.
3. Resolve owner/LoT refs to integer ids (422 on malformed/wrong-prefix
   refs; empty-result short-circuit when an unknown ref is supplied
   on a filter, matching Phase C semantics elsewhere).
4. Build a SQL query joining ``calculation`` to its provenance tables
   for the supplied scalar filters; emit only ``calculation.id`` so the
   filter pass stays cheap.
5. Bulk-load review badges for the candidate set; apply the visible-
   statuses gate (default trust posture). Build the ``review_summary``
   on this filtered, pre-pagination set.
6. Sort deterministically in Python (review rank → quality rank →
   created_at desc → id desc — ``evidence_completeness`` is deferred).
7. Slice for pagination, then materialize each page row via the shared
   :func:`build_record` helper so search and detail produce identical
   record shapes for the same include set.

See ``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationDependency,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationSCFStability,
    CalculationFreqResult,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationPathSearchResult,
    CalculationSPResult,
    CalculationScanResult,
)
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    SCFStabilityStatus,
    SubmissionRecordType,
    ValidationStatus,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import SpeciesEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_calculation import (
    ScientificCalculationRecord,
)
from app.schemas.reads.scientific_calculation_search import (
    CalculationOwnerKind,
    CalculationsSearchRequest,
    RequestEcho,
    ScientificCalculationsSearchResponse,
)
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.services.scientific_read.calculations import (
    _LEGAL_INCLUDE_TOKENS,
    _NOT_IMPLEMENTED_INCLUDE_TOKENS,
    _INTERNAL_INCLUDE_TOKENS,
    build_record,
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
    NO_MATCH,
    resolve_filter_ref,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)


# Filter knobs that count as "meaningful" for the at-least-one-filter rule.
# Pure pagination/include/review knobs are intentionally excluded so callers
# can't sidestep the rule by adding ``include_rejected=true``.
_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "species_entry_ref",
    "transition_state_entry_ref",
    "species_ref",
    "transition_state_ref",
    "owner_kind",
    "calculation_type",
    "quality",
    "has_result",
    "has_artifacts",
    "has_input_geometry",
    "has_output_geometry",
    "artifact_kind",
    "created_before",
    "created_after",
    "method",
    "basis",
    "lot_ref",
    "lot_hash",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
    "geometry_validation_status",
    "scf_stability_status",
    "dependency_role",
    "parent_calculation_ref",
    "child_calculation_ref",
    "parameter_key",
    "canonical_parameter_key",
)

# Quality rank: lower-is-better. ``rejected`` only appears when
# ``include_rejected_quality=true``; place it last regardless.
_QUALITY_RANK: dict[CalculationQuality, int] = {
    CalculationQuality.curated: 0,
    CalculationQuality.raw: 1,
    CalculationQuality.rejected: 2,
}


# Per-calc-type primary result table — drives the ``has_result`` filter.
_PRIMARY_RESULT_TABLE: dict[CalculationType, type] = {
    CalculationType.sp: CalculationSPResult,
    CalculationType.opt: CalculationOptResult,
    CalculationType.freq: CalculationFreqResult,
    CalculationType.scan: CalculationScanResult,
    CalculationType.irc: CalculationIRCResult,
    CalculationType.path_search: CalculationPathSearchResult,
}


_DEFAULT_SORT_ECHO = "review_rank,quality_rank,created_at,id"


def search_calculations(
    session: Session, request: CalculationsSearchRequest
) -> ScientificCalculationsSearchResponse:
    """Multi-axis chemistry/method/provenance calculation search (MVP).

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/calculations/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    requested_unimplemented = includes & _NOT_IMPLEMENTED_INCLUDE_TOKENS
    if requested_unimplemented:
        raise ValueError(
            "include_not_implemented_yet: include token(s) "
            f"{sorted(requested_unimplemented)!r} are reserved for "
            "/scientific/calculations/search but the v0 search "
            "endpoint has not implemented them yet. Drop the "
            "token(s) and re-issue. See "
            "backend/docs/specs/scientific_calculation_reads.md."
        )

    _enforce_parameter_value_pairs(request)
    _enforce_at_least_one_filter(request)

    # Resolve any ref filters to integer ids. Unknown ref → NO_MATCH
    # short-circuit (returns an empty page).
    species_entry_id, short_circuit = _resolve_filter_ref(
        session, SpeciesEntry, request.species_entry_ref, "species_entry"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    ts_entry_id, short_circuit = _resolve_filter_ref(
        session,
        TransitionStateEntry,
        request.transition_state_entry_ref,
        "transition_state_entry",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    species_id, short_circuit = _resolve_filter_ref(
        session, type(None), request.species_ref, "species"
    ) if False else (None, False)  # not in MVP filter list; reserved for future
    # The 'species_ref' / 'transition_state_ref' filters are not exposed
    # as scalar where-clauses in MVP — they would require a join to
    # species_entry / transition_state_entry. Treated as informational
    # for now; resolution attempts happen below for parity but only
    # narrow when the ref resolves to a single owning entry.
    if request.species_ref is not None:
        from app.db.models.species import Species

        species_id, short_circuit = _resolve_filter_ref(
            session, Species, request.species_ref, "species"
        )
        if short_circuit:
            return _empty_response(request, includes, offset, limit)
    if request.transition_state_ref is not None:
        ts_id, short_circuit = _resolve_filter_ref(
            session,
            TransitionState,
            request.transition_state_ref,
            "transition_state",
        )
        if short_circuit:
            return _empty_response(request, includes, offset, limit)
    else:
        ts_id = None

    # Dependency-graph endpoint refs.
    parent_calc_id, short_circuit = _resolve_filter_ref(
        session, Calculation, request.parent_calculation_ref, "calculation"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)
    child_calc_id, short_circuit = _resolve_filter_ref(
        session, Calculation, request.child_calculation_ref, "calculation"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    # Build the filter query.
    stmt = select(Calculation.id, Calculation.created_at)
    stmt = _apply_owner_filters(
        stmt,
        species_entry_id=species_entry_id,
        ts_entry_id=ts_entry_id,
        species_id=species_id,
        ts_id=ts_id,
        owner_kind=request.owner_kind,
    )
    stmt = _apply_calc_filters(stmt, request)
    stmt = _apply_lot_filters(stmt, request)
    stmt = _apply_software_filters(stmt, request)
    stmt = _apply_workflow_filters(stmt, request)
    stmt = _apply_validation_filters(stmt, request)
    stmt = _apply_dependency_filters(
        stmt,
        parent_calc_id=parent_calc_id,
        child_calc_id=child_calc_id,
        dependency_role=request.dependency_role,
    )
    stmt = _apply_parameter_filters(stmt, request)

    # CalculationQuality.rejected is opt-in and orthogonal to review-status.
    if not request.include_rejected_quality and request.quality is None:
        stmt = stmt.where(Calculation.quality != CalculationQuality.rejected)
    elif (
        not request.include_rejected_quality
        and request.quality is CalculationQuality.rejected
    ):
        # Caller asked for rejected-quality but didn't opt in — empty result.
        return _empty_response(request, includes, offset, limit)

    rows = session.execute(stmt).all()
    candidate_ids = [row.id for row in rows]
    created_at_by_id = {row.id: row.created_at for row in rows}

    if not candidate_ids:
        return _empty_response(request, includes, offset, limit)

    # Apply review-status visibility gate. Default trust posture excludes
    # rejected/deprecated unless explicitly opted in; min_review_status
    # narrows further (D5/D7).
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
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

    # Pre-pagination review summary (Phase 2.1: counts on the candidate set
    # *after* filtering, before pagination).
    summary = review_summary(badges[cid] for cid in visible_ids)

    # Load quality + created_at per visible id in one shot for sorting.
    quality_rows = session.execute(
        select(Calculation.id, Calculation.quality).where(
            Calculation.id.in_(visible_ids)
        )
    ).all()
    quality_by_id = {row.id: row.quality for row in quality_rows}

    visible_ids.sort(
        key=lambda cid: (
            REVIEW_RANK[badges[cid].status],
            _QUALITY_RANK[quality_by_id[cid]],
            -created_at_by_id[cid].timestamp(),
            -cid,
        )
    )

    total = len(visible_ids)
    page_ids = visible_ids[offset : offset + limit]

    # Materialize each page row via the shared record builder so search
    # records and detail records have identical shape.
    records: list[ScientificCalculationRecord] = []
    for cid in page_ids:
        calc = session.get(Calculation, cid)
        if calc is None:  # pragma: no cover — race with delete; skip
            continue
        records.append(
            build_record(session, calc, includes, badge=badges[cid])
        )

    return ScientificCalculationsSearchResponse(
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
# Filter rule + ref resolution helpers
# ---------------------------------------------------------------------------


def _enforce_parameter_value_pairs(
    request: CalculationsSearchRequest,
) -> None:
    """Reject ``*_value`` filters that lack their corresponding key.

    Values aren't independently meaningful — without a key the EAV
    EXISTS subquery would have to scan every parameter row. The 422
    error codes match the spec:
    ``parameter_value_requires_key`` and
    ``canonical_parameter_value_requires_key``.
    """
    if request.parameter_value is not None and request.parameter_key is None:
        raise ValueError(
            "parameter_value_requires_key: parameter_value=… requires "
            "parameter_key=… on the same request."
        )
    if (
        request.canonical_parameter_value is not None
        and request.canonical_parameter_key is None
    ):
        raise ValueError(
            "canonical_parameter_value_requires_key: "
            "canonical_parameter_value=… requires "
            "canonical_parameter_key=… on the same request."
        )


def _enforce_at_least_one_filter(request: CalculationsSearchRequest) -> None:
    """Reject requests with no meaningful filter.

    Pure pagination / include / review-trust knobs do not count.
    """
    if not any(
        getattr(request, name) is not None
        and getattr(request, name) is not False
        for name in _MEANINGFUL_FILTER_FIELDS
    ):
        raise ValueError(
            "missing_filter: at least one of "
            f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
            "/scientific/calculations/search."
        )


def _resolve_filter_ref(
    session: Session,
    model_cls: type,
    ref: str | None,
    kind_label: str,
) -> tuple[int | None, bool]:
    """Resolve an optional ``*_ref`` filter to (resolved_id, short_circuit).

    Returns:

    - ``(None, False)`` when *ref* is not supplied → no filter applied.
    - ``(id, False)`` when the ref resolves to a row.
    - ``(None, True)`` when the ref is well-formed but unknown
      (the empty-result short-circuit per Phase C semantics).

    Raises:

    - ``ValueError`` (422) for malformed / wrong-prefix refs.
    """
    if ref is None:
        return None, False
    resolved = resolve_filter_ref(session, model_cls, ref, kind_label=kind_label)
    if resolved is None:
        return None, True
    return resolved, False


# ---------------------------------------------------------------------------
# WHERE-clause builders
# ---------------------------------------------------------------------------


def _apply_owner_filters(
    stmt,
    *,
    species_entry_id: int | None,
    ts_entry_id: int | None,
    species_id: int | None,
    ts_id: int | None,
    owner_kind: CalculationOwnerKind | None,
):
    if species_entry_id is not None:
        stmt = stmt.where(Calculation.species_entry_id == species_entry_id)
    if ts_entry_id is not None:
        stmt = stmt.where(
            Calculation.transition_state_entry_id == ts_entry_id
        )
    if species_id is not None:
        # Narrow to calcs whose species_entry belongs to the species.
        stmt = stmt.join(
            SpeciesEntry,
            SpeciesEntry.id == Calculation.species_entry_id,
        ).where(SpeciesEntry.species_id == species_id)
    if ts_id is not None:
        # Narrow to calcs whose transition_state_entry belongs to the TS.
        stmt = stmt.join(
            TransitionStateEntry,
            TransitionStateEntry.id == Calculation.transition_state_entry_id,
        ).where(TransitionStateEntry.transition_state_id == ts_id)
    if owner_kind is CalculationOwnerKind.species_entry:
        stmt = stmt.where(Calculation.species_entry_id.is_not(None))
    elif owner_kind is CalculationOwnerKind.transition_state_entry:
        stmt = stmt.where(Calculation.transition_state_entry_id.is_not(None))
    return stmt


def _apply_calc_filters(stmt, request: CalculationsSearchRequest):
    if request.calculation_type is not None:
        stmt = stmt.where(Calculation.type == request.calculation_type)
    if request.quality is not None:
        stmt = stmt.where(Calculation.quality == request.quality)
    if request.created_before is not None:
        stmt = stmt.where(Calculation.created_at < request.created_before)
    if request.created_after is not None:
        stmt = stmt.where(Calculation.created_at >= request.created_after)
    if request.has_result is not None:
        stmt = _apply_has_result_filter(
            stmt, request.has_result, request.calculation_type
        )
    if request.has_artifacts is not None:
        stmt = _apply_exists_filter(
            stmt, CalculationArtifact, want=request.has_artifacts
        )
    if request.artifact_kind is not None:
        # ``artifact_kind`` implies artifact existence and is stricter
        # than ``has_artifacts=true``. AND-combines with ``has_artifacts``:
        # ``has_artifacts=false`` short-circuits this clause to empty
        # (the EXISTS is preserved either way; the prior ``~exists``
        # filter has already narrowed the candidate set).
        stmt = stmt.where(
            exists().where(
                and_(
                    CalculationArtifact.calculation_id == Calculation.id,
                    CalculationArtifact.kind == request.artifact_kind,
                )
            )
        )
    if request.has_input_geometry is not None:
        stmt = _apply_exists_filter(
            stmt, CalculationInputGeometry, want=request.has_input_geometry
        )
    if request.has_output_geometry is not None:
        stmt = _apply_exists_filter(
            stmt, CalculationOutputGeometry, want=request.has_output_geometry
        )
    return stmt


def _apply_has_result_filter(
    stmt,
    want: bool,
    calculation_type: CalculationType | None,
):
    """Apply ``has_result``: depends on the calc type's primary result table.

    When ``calculation_type`` is supplied, the EXISTS hits the matching
    table. When no type is supplied, the EXISTS is OR-combined across
    every primary result table so the filter still narrows correctly.
    """
    if calculation_type is not None:
        table = _PRIMARY_RESULT_TABLE.get(calculation_type)
        if table is None:
            # ``conf`` (or unknown type) has no primary result table.
            # ``has_result=True`` filters everything out; False is a no-op.
            if want:
                return stmt.where(False)
            return stmt
        ex = exists().where(table.calculation_id == Calculation.id)
        return stmt.where(ex if want else ~ex)

    clauses = [
        exists().where(table.calculation_id == Calculation.id)
        for table in _PRIMARY_RESULT_TABLE.values()
    ]
    combined = or_(*clauses)
    return stmt.where(combined if want else ~combined)


def _apply_exists_filter(stmt, table, *, want: bool):
    ex = exists().where(table.calculation_id == Calculation.id)
    return stmt.where(ex if want else ~ex)


def _apply_lot_filters(stmt, request: CalculationsSearchRequest):
    if (
        request.method is None
        and request.basis is None
        and request.lot_ref is None
        and request.lot_hash is None
    ):
        return stmt
    stmt = stmt.join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
    if request.method is not None:
        stmt = stmt.where(LevelOfTheory.method == request.method)
    if request.basis is not None:
        stmt = stmt.where(LevelOfTheory.basis == request.basis)
    if request.lot_ref is not None:
        stmt = stmt.where(LevelOfTheory.public_ref == request.lot_ref)
    if request.lot_hash is not None:
        stmt = stmt.where(LevelOfTheory.lot_hash == request.lot_hash)
    return stmt


def _apply_software_filters(stmt, request: CalculationsSearchRequest):
    if request.software is None and request.software_version is None:
        return stmt
    stmt = stmt.join(
        SoftwareRelease, SoftwareRelease.id == Calculation.software_release_id
    ).join(Software, Software.id == SoftwareRelease.software_id)
    if request.software is not None:
        stmt = stmt.where(Software.name == request.software)
    if request.software_version is not None:
        stmt = stmt.where(SoftwareRelease.version == request.software_version)
    return stmt


def _apply_workflow_filters(stmt, request: CalculationsSearchRequest):
    if (
        request.workflow_tool is None
        and request.workflow_tool_version is None
    ):
        return stmt
    stmt = stmt.join(
        WorkflowToolRelease,
        WorkflowToolRelease.id == Calculation.workflow_tool_release_id,
    ).join(
        WorkflowTool, WorkflowTool.id == WorkflowToolRelease.workflow_tool_id
    )
    if request.workflow_tool is not None:
        stmt = stmt.where(WorkflowTool.name == request.workflow_tool)
    if request.workflow_tool_version is not None:
        stmt = stmt.where(
            WorkflowToolRelease.version == request.workflow_tool_version
        )
    return stmt


def _apply_validation_filters(stmt, request: CalculationsSearchRequest):
    if request.geometry_validation_status is not None:
        gv_status = request.geometry_validation_status
        if gv_status == "not_present":
            stmt = stmt.where(
                ~exists().where(
                    CalculationGeometryValidation.calculation_id
                    == Calculation.id
                )
            )
        else:
            stmt = stmt.where(
                exists().where(
                    and_(
                        CalculationGeometryValidation.calculation_id
                        == Calculation.id,
                        CalculationGeometryValidation.validation_status
                        == ValidationStatus(gv_status),
                    )
                )
            )
    if request.scf_stability_status is not None:
        scf_status = request.scf_stability_status
        if scf_status == "not_present":
            stmt = stmt.where(
                ~exists().where(
                    CalculationSCFStability.calculation_id == Calculation.id
                )
            )
        else:
            stmt = stmt.where(
                exists().where(
                    and_(
                        CalculationSCFStability.calculation_id
                        == Calculation.id,
                        CalculationSCFStability.status
                        == SCFStabilityStatus(scf_status),
                    )
                )
            )
    return stmt


def _apply_dependency_filters(
    stmt,
    *,
    parent_calc_id: int | None,
    child_calc_id: int | None,
    dependency_role,
):
    """Apply dependency-graph filters.

    Semantics (documented in the request schema):

    - ``dependency_role=X`` alone: matches calcs that participate in any
      edge with role ``X`` (as parent OR child).
    - ``parent_calculation_ref`` resolved → returns child calcs of that
      parent (i.e. ``Calculation.id`` matches ``cd.child_calculation_id``
      for the edge whose parent is the resolved id).
    - ``child_calculation_ref`` resolved → returns parent calcs of that
      child.
    - Combining a ref with ``dependency_role`` narrows by role.
    - Combining BOTH refs returns the two endpoints of the exact edge if
      it exists, else empty (the chosen "preferred behavior" from the
      spec). The returned calcs are the parent and the child of that
      single edge.
    """
    role_clause = (
        (CalculationDependency.dependency_role == dependency_role)
        if dependency_role is not None
        else None
    )

    def _and(*clauses):
        clauses = [c for c in clauses if c is not None]
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return and_(*clauses)

    if parent_calc_id is not None and child_calc_id is not None:
        # Both refs supplied — match the two endpoints of the exact edge
        # if it exists, else empty. The EXISTS check on the exact edge
        # is parameterless w.r.t. Calculation.id; pair it with
        # ``Calculation.id IN (parent, child)`` to scope the result to
        # the two endpoints.
        exact_edge_exists = exists().where(
            _and(
                CalculationDependency.parent_calculation_id == parent_calc_id,
                CalculationDependency.child_calculation_id == child_calc_id,
                role_clause,
            )
        )
        return stmt.where(
            and_(
                Calculation.id.in_([parent_calc_id, child_calc_id]),
                exact_edge_exists,
            )
        )

    if parent_calc_id is not None:
        # Match child calcs of the resolved parent.
        return stmt.where(
            exists().where(
                _and(
                    CalculationDependency.parent_calculation_id
                    == parent_calc_id,
                    CalculationDependency.child_calculation_id
                    == Calculation.id,
                    role_clause,
                )
            )
        )

    if child_calc_id is not None:
        # Match parent calcs of the resolved child.
        return stmt.where(
            exists().where(
                _and(
                    CalculationDependency.child_calculation_id
                    == child_calc_id,
                    CalculationDependency.parent_calculation_id
                    == Calculation.id,
                    role_clause,
                )
            )
        )

    if dependency_role is not None:
        # Role-only filter: match any calc participating in an edge of
        # that role, on either side.
        return stmt.where(
            exists().where(
                and_(
                    CalculationDependency.dependency_role == dependency_role,
                    or_(
                        CalculationDependency.parent_calculation_id
                        == Calculation.id,
                        CalculationDependency.child_calculation_id
                        == Calculation.id,
                    ),
                )
            )
        )

    return stmt


def _apply_parameter_filters(stmt, request: CalculationsSearchRequest):
    """Apply raw / canonical ``calculation_parameter`` filters.

    Semantics (documented in the request schema):

    - ``parameter_key=K`` alone: matches calcs with at least one row
      where ``raw_key = K``.
    - ``parameter_key=K`` + ``parameter_value=V``: the value must be on
      the **same row** as the key.
    - Same rules apply to ``canonical_parameter_key`` + ``canonical_parameter_value``.
    - Raw and canonical filters AND-combine: when both sides are
      supplied, the calc must have a matching raw row AND a matching
      canonical row (not necessarily the same row).
    - Value-without-key validation lives in :func:`_enforce_parameter_value_pairs`,
      so by the time we get here either both value+key are set or only
      key is set.
    """
    if request.parameter_key is not None:
        clauses = [CalculationParameter.raw_key == request.parameter_key]
        if request.parameter_value is not None:
            clauses.append(
                CalculationParameter.raw_value == request.parameter_value
            )
        stmt = stmt.where(
            exists().where(
                and_(
                    CalculationParameter.calculation_id == Calculation.id,
                    *clauses,
                )
            )
        )
    if request.canonical_parameter_key is not None:
        clauses = [
            CalculationParameter.canonical_key
            == request.canonical_parameter_key
        ]
        if request.canonical_parameter_value is not None:
            clauses.append(
                CalculationParameter.canonical_value
                == request.canonical_parameter_value
            )
        stmt = stmt.where(
            exists().where(
                and_(
                    CalculationParameter.calculation_id == Calculation.id,
                    *clauses,
                )
            )
        )
    return stmt


# ---------------------------------------------------------------------------
# Empty + echo helpers
# ---------------------------------------------------------------------------


def _empty_response(
    request: CalculationsSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificCalculationsSearchResponse:
    return ScientificCalculationsSearchResponse(
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


def _request_filter_echo(request: CalculationsSearchRequest) -> dict[str, Any]:
    """Return the caller's filter inputs verbatim (post-parse).

    Echoes only the supplied filter fields so callers can confirm what
    the server saw. Pagination/include/sort live elsewhere on the
    envelope.
    """
    out: dict[str, Any] = {}
    for name in _MEANINGFUL_FILTER_FIELDS + (
        "include_rejected",
        "include_deprecated",
        "include_rejected_quality",
        "min_review_status",
    ):
        value = getattr(request, name)
        if value is None:
            continue
        if isinstance(value, datetime):
            out[name] = value.isoformat()
        else:
            out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = [
    "search_calculations",
]
