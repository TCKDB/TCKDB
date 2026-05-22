"""Service implementation for /api/v1/scientific/artifacts/search.

Standalone artifact-metadata search surface. Composes ``calculation_artifact``
filters with owning-calculation provenance filters (LoT / software / workflow /
owner / conformer observation) and applies the project's standard
review-status visibility gate against the **owning calculation** — artifacts
themselves do not carry a review state, so trust gating is anchored on the
calc.

Returns the artifact metadata projection (``CalculationArtifactSummary``)
plus an ``ArtifactCalculationContext`` block carrying the owning
calculation's identity, level-of-theory, software, workflow-tool, and
review badge. Owner (species/TS entry) and review history are heavy
optional includes.

See ``backend/docs/specs/scientific_artifact_reads.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import (
    CalculationQuality,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import ConformerObservation, SpeciesEntry
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_artifact_search import (
    ArtifactCalculationContext,
    AvailableArtifactSections,
    RequestEcho,
    ScientificArtifactRecord,
    ScientificArtifactSearchRequest,
    ScientificArtifactSearchResponse,
)
from app.schemas.reads.scientific_calculation import CalculationArtifactSummary
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.services.scientific_read.calculations import (
    _build_lot_summary,
    _build_owner,
    _build_review_history,
    _build_software_summary,
    _build_workflow_summary,
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


_LEGAL_INCLUDE_TOKENS: set[str] = {
    "calculation",
    "owner",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


# Filter knobs that count as "meaningful" for the at-least-one-filter rule.
# Trust/pagination/include knobs are intentionally excluded so callers
# can't sidestep the rule by setting ``include_rejected=true``.
_MEANINGFUL_FILTER_FIELDS: tuple[str, ...] = (
    "artifact_kind",
    "filename",
    "filename_contains",
    "sha256",
    "has_sha256",
    "has_bytes",
    "bytes_min",
    "bytes_max",
    "calculation_ref",
    "calculation_type",
    "quality",
    "method",
    "basis",
    "software",
    "software_version",
    "workflow_tool",
    "workflow_tool_version",
    "species_entry_ref",
    "transition_state_entry_ref",
    "conformer_observation_ref",
    "created_after",
    "created_before",
)


_DEFAULT_SORT_ECHO = "review_rank,created_at,artifact_id"


def search_artifacts(
    session: Session, request: ScientificArtifactSearchRequest
) -> ScientificArtifactSearchResponse:
    """Run the standalone scientific artifact search.

    :raises ValueError: 422 for sort, pagination, include, malformed
        handle / handle-type-mismatch, or missing-filter violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/artifacts/search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    _enforce_at_least_one_filter(request)

    # Resolve ref filters to integer ids. Unknown ref of the right
    # prefix → empty short-circuit (Phase C semantics).
    calc_id, short_circuit = _resolve_filter_ref(
        session, Calculation, request.calculation_ref, "calculation"
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

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

    conf_obs_id, short_circuit = _resolve_filter_ref(
        session,
        ConformerObservation,
        request.conformer_observation_ref,
        "conformer_observation",
    )
    if short_circuit:
        return _empty_response(request, includes, offset, limit)

    # Build the filter query. We need both the artifact row and the
    # owning calculation row for downstream materialization; emit ids
    # only in the filter pass to keep it cheap.
    stmt = select(
        CalculationArtifact.id.label("artifact_id"),
        CalculationArtifact.calculation_id.label("calculation_id"),
        CalculationArtifact.created_at.label("artifact_created_at"),
    ).join(Calculation, Calculation.id == CalculationArtifact.calculation_id)

    stmt = _apply_artifact_filters(stmt, request)
    stmt = _apply_calc_filters(
        stmt,
        request,
        calculation_id=calc_id,
        species_entry_id=species_entry_id,
        ts_entry_id=ts_entry_id,
        conformer_observation_id=conf_obs_id,
    )
    stmt = _apply_lot_filters(stmt, request)
    stmt = _apply_software_filters(stmt, request)
    stmt = _apply_workflow_filters(stmt, request)

    rows = session.execute(stmt).all()
    if not rows:
        return _empty_response(request, includes, offset, limit)

    # Owning-calc review badges drive the visibility gate. Bulk-load
    # badges for the unique set of owning calcs, then keep only the
    # artifact rows whose owner badge passes the trust posture.
    owning_calc_ids = list({row.calculation_id for row in rows})
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=owning_calc_ids,
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_rows = [
        row for row in rows if badges[row.calculation_id].status in visible
    ]
    if not visible_rows:
        return _empty_response(request, includes, offset, limit)

    # Pre-pagination review summary counts owning-calc review states
    # across the filtered artifact records (matching artifact-row
    # cardinality, not unique-calc cardinality — one artifact row, one
    # vote, as documented).
    summary = review_summary(badges[row.calculation_id] for row in visible_rows)

    # Deterministic ordering: owning-calc review rank ASC, then
    # artifact created_at DESC NULLS LAST, then artifact_id DESC.
    def sort_key(row) -> tuple:
        created_at = row.artifact_created_at
        nulls_last = created_at is None
        # negate created_at so DESC sort works lexicographically
        ts = -created_at.timestamp() if created_at is not None else 0.0
        return (
            REVIEW_RANK[badges[row.calculation_id].status],
            nulls_last,  # False (has value) before True (null)
            ts,
            -row.artifact_id,
        )

    visible_rows.sort(key=sort_key)
    total = len(visible_rows)
    page_rows = visible_rows[offset : offset + limit]

    # Materialize each page row. Bulk-load the calculation rows we
    # actually need for the page (not the full visible set).
    page_calc_ids = list({row.calculation_id for row in page_rows})
    calc_by_id: dict[int, Calculation] = {}
    if page_calc_ids:
        for calc in session.execute(
            select(Calculation).where(Calculation.id.in_(page_calc_ids))
        ).scalars():
            calc_by_id[calc.id] = calc

    page_artifact_ids = [row.artifact_id for row in page_rows]
    artifact_by_id: dict[int, CalculationArtifact] = {}
    if page_artifact_ids:
        for art in session.execute(
            select(CalculationArtifact).where(
                CalculationArtifact.id.in_(page_artifact_ids)
            )
        ).scalars():
            artifact_by_id[art.id] = art

    records: list[ScientificArtifactRecord] = []
    for row in page_rows:
        art = artifact_by_id.get(row.artifact_id)
        calc = calc_by_id.get(row.calculation_id)
        if art is None or calc is None:  # pragma: no cover — race with delete
            continue
        records.append(
            _build_record(
                session,
                artifact=art,
                calculation=calc,
                badge=badges[calc.id],
                includes=includes,
            )
        )

    return ScientificArtifactSearchResponse(
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
# Record builder
# ---------------------------------------------------------------------------


def _build_record(
    session: Session,
    *,
    artifact: CalculationArtifact,
    calculation: Calculation,
    badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificArtifactRecord:
    """Build a public ``ScientificArtifactRecord`` for *artifact*.

    ``include=calculation`` (the default expansion) populates the LoT /
    software / workflow summaries on the calculation context; absent
    fields stay ``None``. ``include=owner`` adds the species/TS entry
    owner block. ``include=review`` populates the artifact's
    ``calculation.review`` badge from the owning calc's review history
    (the badge itself is always present).
    """
    artifact_summary = CalculationArtifactSummary(
        artifact_id=artifact.id,
        artifact_ref=None,  # no public_ref column on calculation_artifact yet
        kind=artifact.kind,
        uri=artifact.uri,
        filename=artifact.filename,
        sha256=artifact.sha256,
        bytes=artifact.bytes,
        created_at=artifact.created_at,
    )

    # Calculation context — always populated; LoT/software/workflow
    # summaries only when the ``calculation`` include token is in effect
    # (default expansion, but the caller can drop it to keep responses
    # smaller).
    want_calc = "calculation" in includes
    lot_summary = (
        _build_lot_summary(session, calculation.lot_id) if want_calc else None
    )
    software_summary = (
        _build_software_summary(session, calculation.software_release_id)
        if want_calc
        else None
    )
    workflow_summary = (
        _build_workflow_summary(session, calculation.workflow_tool_release_id)
        if want_calc
        else None
    )

    calc_context = ArtifactCalculationContext(
        calculation_id=calculation.id,
        calculation_ref=calculation.public_ref,
        calculation_type=calculation.type,
        quality=calculation.quality,
        created_at=calculation.created_at,
        level_of_theory=lot_summary,
        software_release=software_summary,
        workflow_tool_release=workflow_summary,
        review=badge,
    )

    owner_block = None
    has_owner_section = False
    if "owner" in includes:
        owner_block = _build_owner(session, calculation)
        has_owner_section = True

    # ``include=review`` is reserved for the calc's review history. The
    # detail loader already shapes per-event entries. We don't expose
    # review history inline on the artifact record (the badge already
    # rides on the calculation context). Reserved for a future
    # extension; the include token is accepted today as a no-op so the
    # grammar matches the spec's documented surface.
    has_review_history = False
    if "review" in includes:
        # Defer per-event review history to a future surface. Today this
        # is a no-op (the badge is already on the calculation context),
        # but keeping the token legal lets clients pre-wire to it.
        _ = _build_review_history(session, calculation.id)
        has_review_history = True

    return ScientificArtifactRecord(
        artifact=artifact_summary,
        calculation=calc_context,
        owner=owner_block,
        available_sections=AvailableArtifactSections(
            has_calculation=want_calc,
            has_owner=has_owner_section,
            has_review_history=has_review_history,
        ),
    )


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def _enforce_at_least_one_filter(
    request: ScientificArtifactSearchRequest,
) -> None:
    """Reject requests with no meaningful filter.

    Explicit boolean ``False`` values (``has_sha256=false``) count as
    meaningful — only ``None`` (not supplied) is filtered out.
    """
    for name in _MEANINGFUL_FILTER_FIELDS:
        value = getattr(request, name)
        if value is not None:
            return
    raise ValueError(
        "missing_filter: at least one of "
        f"{sorted(_MEANINGFUL_FILTER_FIELDS)!r} must be supplied to "
        "/scientific/artifacts/search."
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
    resolved = resolve_filter_ref(session, model_cls, ref, kind_label=kind_label)
    if resolved is None:
        return None, True
    return resolved, False


def _apply_artifact_filters(stmt, request: ScientificArtifactSearchRequest):
    if request.artifact_kind is not None:
        stmt = stmt.where(CalculationArtifact.kind == request.artifact_kind)
    if request.filename is not None:
        stmt = stmt.where(CalculationArtifact.filename == request.filename)
    if request.filename_contains is not None:
        pattern = f"%{request.filename_contains}%"
        stmt = stmt.where(
            func.lower(CalculationArtifact.filename).like(
                func.lower(pattern)
            )
        )
    if request.sha256 is not None:
        stmt = stmt.where(CalculationArtifact.sha256 == request.sha256)
    if request.has_sha256 is not None:
        if request.has_sha256:
            stmt = stmt.where(CalculationArtifact.sha256.is_not(None))
        else:
            stmt = stmt.where(CalculationArtifact.sha256.is_(None))
    if request.has_bytes is not None:
        if request.has_bytes:
            stmt = stmt.where(CalculationArtifact.bytes.is_not(None))
        else:
            stmt = stmt.where(CalculationArtifact.bytes.is_(None))
    if request.bytes_min is not None:
        stmt = stmt.where(CalculationArtifact.bytes >= request.bytes_min)
    if request.bytes_max is not None:
        stmt = stmt.where(CalculationArtifact.bytes <= request.bytes_max)
    if request.created_after is not None:
        stmt = stmt.where(
            CalculationArtifact.created_at >= request.created_after
        )
    if request.created_before is not None:
        stmt = stmt.where(
            CalculationArtifact.created_at < request.created_before
        )
    return stmt


def _apply_calc_filters(
    stmt,
    request: ScientificArtifactSearchRequest,
    *,
    calculation_id: int | None,
    species_entry_id: int | None,
    ts_entry_id: int | None,
    conformer_observation_id: int | None,
):
    if calculation_id is not None:
        stmt = stmt.where(Calculation.id == calculation_id)
    if species_entry_id is not None:
        stmt = stmt.where(Calculation.species_entry_id == species_entry_id)
    if ts_entry_id is not None:
        stmt = stmt.where(
            Calculation.transition_state_entry_id == ts_entry_id
        )
    if conformer_observation_id is not None:
        stmt = stmt.where(
            Calculation.conformer_observation_id == conformer_observation_id
        )
    if request.calculation_type is not None:
        stmt = stmt.where(Calculation.type == request.calculation_type)
    if request.quality is not None:
        stmt = stmt.where(Calculation.quality == request.quality)
    # Default trust posture for calc.quality mirrors the calculations
    # search: hide rejected-quality unless the caller explicitly opted
    # in via the matching review knob. We piggy-back on
    # ``include_rejected`` here since the artifact endpoint does not
    # expose a separate ``include_rejected_quality`` knob — the simple
    # rule is "if the caller is opting into rejected review state, also
    # show rejected-quality calcs".
    if not request.include_rejected and request.quality is None:
        stmt = stmt.where(Calculation.quality != CalculationQuality.rejected)
    return stmt


def _apply_lot_filters(stmt, request: ScientificArtifactSearchRequest):
    if request.method is None and request.basis is None:
        return stmt
    stmt = stmt.join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
    if request.method is not None:
        stmt = stmt.where(LevelOfTheory.method == request.method)
    if request.basis is not None:
        stmt = stmt.where(LevelOfTheory.basis == request.basis)
    return stmt


def _apply_software_filters(stmt, request: ScientificArtifactSearchRequest):
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


def _apply_workflow_filters(stmt, request: ScientificArtifactSearchRequest):
    if request.workflow_tool is None and request.workflow_tool_version is None:
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


# ---------------------------------------------------------------------------
# Empty + echo helpers
# ---------------------------------------------------------------------------


def _empty_response(
    request: ScientificArtifactSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
) -> ScientificArtifactSearchResponse:
    return ScientificArtifactSearchResponse(
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
    request: ScientificArtifactSearchRequest,
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
        # ``include_rejected`` / ``include_deprecated`` default to False —
        # only echo when explicitly set.
        if name in ("include_rejected", "include_deprecated") and value is False:
            continue
        if isinstance(value, datetime):
            out[name] = value.isoformat()
        else:
            out[name] = value.value if hasattr(value, "value") else value
    return out


__all__ = [
    "search_artifacts",
]
