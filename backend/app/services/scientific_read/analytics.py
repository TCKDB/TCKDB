"""The bounded analytics surface: four indexed endpoints, not forty filters.

Why this module exists
----------------------
Building a quantitative dataset from TCKDB means asking numeric questions —
"every Arrhenius fit with 800 K ≤ T and Ea below 40 kJ/mol", "every statmech
record with an external symmetry of 3 and at least two electronic levels".
The transactional searches under ``/scientific/*`` answer identity questions
("which records belong to this species"), and bolting a dozen optional numeric
filters onto each of the ~40 of them would have produced a surface nobody could
index, document, or reason about: every filter would be optional, every
combination would be a new query plan, and the ones with no supporting index
would be indistinguishable from the ones with.

So the numeric axes live here instead, on four endpoints whose query shapes are
finite, written down in ``backend/docs/specs/scientific_analytics_surface.md``,
and backed by measured index evidence.

Everything happens in SQL
-------------------------
Filtering, review visibility, review ranking, ordering and slicing are all
expressed as SQL over one candidate subquery; only the page is materialized.
This is not a performance preference, it is a correctness requirement: the
Python-side pattern this surface deliberately avoids calls
:func:`~app.services.scientific_read.common.fetch_review_badges` with one bind
parameter per candidate id, and PostgreSQL caps a statement at 65,535
parameters — so a broad analytics query would not have been slow, it would have
returned ``503``. Review visibility and rank come from
:mod:`app.services.scientific_read.sql_review`; the page projection is a second
query over at most ``limit`` ids.

Two pagination contracts
------------------------
``offset``/``limit`` keeps working unchanged. ``cursor=`` opts into keyset
traversal with a snapshot watermark (:mod:`app.services.scientific_read.keyset`),
which is what makes a multi-page dataset build reproducible against a live
database. The watermark bounds insertions; it does **not** freeze curation, and
that limitation is documented rather than papered over — see the keyset module
docstring and the spec.

The read profile
----------------
Nothing here threads a profile argument. Visibility goes through
:func:`~app.services.scientific_read.common.visible_statuses`, which applies the
curated floor, and every route returns through
:func:`~app.services.scientific_read.internal_ids.apply_internal_ids_visibility`,
which stamps the resolved profile into the ``request`` echo. Those are the two
seams :mod:`app.services.scientific_read.profile` documents, and using them is
what makes the profile a property of this code rather than a convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, exists, func, select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.calculation import (
    Calculation,
    CalculationFreqResult,
    CalculationOptResult,
    CalculationSpinDiagnostic,
    CalculationSPResult,
    CalculationWavefunctionDiagnostic,
)
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import Kinetics, KineticsInterpretationAssignment
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import SpeciesEntry
from app.db.models.statmech import (
    Statmech,
    StatmechElectronicLevel,
    StatmechTorsion,
)
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_analytics import (
    AnalyticsPagingRequest,
    AnalyticsRequestEcho,
    CalculationAnalyticsRecord,
    CalculationAnalyticsRequest,
    CalculationAnalyticsResponse,
    KineticsAnalyticsRecord,
    KineticsAnalyticsRequest,
    KineticsAnalyticsResponse,
    StatmechAnalyticsRecord,
    StatmechAnalyticsRequest,
    StatmechAnalyticsResponse,
    ThermoAnalyticsRecord,
    ThermoAnalyticsRequest,
    ThermoAnalyticsResponse,
    WatermarkEcho,
)
from app.schemas.reads.scientific_common import ReviewStatusSummary
from app.services.scientific_read.common import (
    build_pagination,
    reject_client_sort,
    validate_includes,
    validate_pagination,
    validate_temperature_range,
    visible_statuses,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.keyset import (
    Cursor,
    InvalidCursorError,
    Watermark,
    decode_cursor,
    encode_cursor,
    keyset_predicate,
    query_signature,
    watermark_predicate,
)
from app.services.scientific_read.profile import current_read_profile
from app.services.scientific_read.sql_review import (
    join_review,
    review_rank_expr,
    review_status_expr,
    status_from_sql,
    summary_from_sql,
    visible_review_filter,
)

#: The one sort every analytics endpoint uses. Best-reviewed first, then
#: newest id first. The trailing key is the primary key, which is what makes
#: the order a total order and therefore keyset-safe: without a unique final
#: key the "strictly after" predicate would skip rows tied on every other key.
DEFAULT_SORT = "review_rank,id"

#: Analytics responses carry no nested sub-resources, so the only include
#: token that means anything is the internal-id opt-in.
_LEGAL_INCLUDE_TOKENS: set[str] = {"internal_ids", "all"}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


# ---------------------------------------------------------------------------
# Request validation shared by all four endpoints
# ---------------------------------------------------------------------------


def _check_range(min_name: str, max_name: str, low: Any, high: Any) -> None:
    """Reject an inverted range with a stable code instead of an empty page.

    ``a_min=10&a_max=1`` is a typo, not a query. Returning zero rows would
    let it pass for "TCKDB has no such data", which for a database people
    build datasets from is the more expensive failure.

    Both parameter names are passed in rather than derived from a stem: the
    filters are not uniformly ``<stem>_min``/``<stem>_max``
    (``electronic_energy_min_hartree`` keeps its unit suffix last), and an
    error message naming a parameter that does not exist is worse than none.
    """
    if low is not None and high is not None and low > high:
        raise CodedValueError(
            "invalid_range",
            f"{min_name} must be <= {max_name} "
            f"(got {min_name}={low}, {max_name}={high}).",
            context={"min_filter": min_name, "max_filter": max_name, "min": low, "max": high},
        )


def _resolve_includes(request: AnalyticsPagingRequest, endpoint: str) -> set[str]:
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        endpoint,
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    return filter_internal_ids_from_resolved(includes)


def _validate_paging(request: AnalyticsPagingRequest) -> tuple[int, int]:
    """Shared sort / pagination / cursor-conflict validation."""
    reject_client_sort(request.sort)
    if request.cursor is not None and request.offset:
        raise CodedValueError(
            "cursor_offset_conflict",
            "cursor= and a non-zero offset= cannot be combined; a cursor "
            "already encodes the traversal position. Re-issue with the "
            "cursor alone, or drop the cursor to page by offset.",
            context={"offset": request.offset},
        )
    # ``limit`` is validated even in cursor mode; ``offset`` is zero there.
    return validate_pagination(request.offset, request.limit)


# ---------------------------------------------------------------------------
# The shared page engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    """One materialized page plus everything the envelope needs."""

    ids: list[int]
    statuses: dict[int, RecordReviewStatus]
    total: int
    summary: ReviewStatusSummary
    offset: int
    limit: int
    mode: str
    next_cursor: str | None
    watermark: WatermarkEcho


def _take_watermark(session: Session, id_column: Any) -> Watermark:
    """Establish the snapshot bound for a new traversal.

    Record ids are monotonic, so ``max(id)`` at this instant is a complete
    bound on insertions without needing a clock or a serializable snapshot.
    ``max(id)`` over a primary key is an index scan of one row.
    """
    max_id = session.scalar(select(func.max(id_column)))
    return Watermark(
        max_id=int(max_id) if max_id is not None else 0,
        taken_at=datetime.now(timezone.utc),
    )


def _signature(endpoint: str, filter_echo: dict[str, object]) -> str:
    """Bind a cursor to this endpoint, these filters, and this read profile.

    The profile is part of the signature because it changes the visible set:
    a cursor issued mid-traversal under ``exploratory`` and then replayed
    under ``curated`` would silently traverse a different corpus. Refusing it
    is the honest outcome.
    """
    resolved = current_read_profile()
    return query_signature(
        endpoint, {**filter_echo, "__profile__": resolved.profile.value}
    )


def _run_page(
    session: Session,
    *,
    endpoint: str,
    record_type: SubmissionRecordType,
    id_column: Any,
    candidate_stmt: Select,
    request: AnalyticsPagingRequest,
    filter_echo: dict[str, object],
    offset: int,
    limit: int,
) -> _Page:
    """Filter, gate, rank, order and slice — all in SQL; return one page.

    ``candidate_stmt`` must select exactly the primary-key column of the
    record table, with every content filter already applied. Everything from
    review visibility onwards is identical for all four endpoints and lives
    here, so a new analytics endpoint cannot accidentally implement the
    visibility gate differently from the other three.
    """
    signature = _signature(endpoint, filter_echo)

    cursor: Cursor | None = None
    if request.cursor is not None:
        cursor = decode_cursor(request.cursor, expected_signature=signature)
        watermark = cursor.watermark
        mode = "cursor"
    else:
        watermark = _take_watermark(session, id_column)
        mode = "offset"

    # The watermark applies to the count as well as to the page, so ``total``
    # is stable across a traversal rather than drifting upward under
    # concurrent uploads.
    candidate_stmt = candidate_stmt.where(watermark_predicate(id_column, watermark))

    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )

    candidates = candidate_stmt.subquery("analytics_candidates")
    ranked_base = select(candidates.c.id)
    ranked_base, review = join_review(ranked_base, candidates.c.id, record_type)
    ranked = (
        ranked_base.add_columns(
            review_rank_expr(review).label("review_rank"),
            review_status_expr(review).label("review_status"),
        )
        .where(visible_review_filter(review, visible))
        .subquery("analytics_visible")
    )

    total = session.scalar(select(func.count()).select_from(ranked)) or 0
    watermark_echo = WatermarkEcho(
        taken_at=watermark.taken_at, release_ref=watermark.release_ref
    )
    if total == 0:
        return _Page(
            ids=[],
            statuses={},
            total=0,
            summary=ReviewStatusSummary(),
            offset=offset,
            limit=limit,
            mode=mode,
            next_cursor=None,
            watermark=watermark_echo,
        )

    summary = summary_from_sql(session, ranked)

    page_stmt = select(
        ranked.c.id, ranked.c.review_rank, ranked.c.review_status
    ).order_by(ranked.c.review_rank.asc(), ranked.c.id.desc())
    if cursor is not None:
        page_stmt = page_stmt.where(
            keyset_predicate(
                [(ranked.c.review_rank, "asc"), (ranked.c.id, "desc")],
                cursor.last_values,
            )
        )
    else:
        page_stmt = page_stmt.offset(offset)
    rows = session.execute(page_stmt.limit(limit)).all()

    ids = [int(row.id) for row in rows]
    statuses = {
        int(row.id): status_from_sql(row.review_status) for row in rows
    }

    next_cursor: str | None = None
    if rows and len(rows) == limit:
        last = rows[-1]
        next_cursor = encode_cursor(
            Cursor(
                watermark=watermark,
                last_values=[int(last.review_rank), int(last.id)],
                query_signature=signature,
            )
        )

    return _Page(
        ids=ids,
        statuses=statuses,
        total=total,
        summary=summary,
        offset=offset,
        limit=limit,
        mode=mode,
        next_cursor=next_cursor,
        watermark=watermark_echo,
    )


def _echo(
    filter_echo: dict[str, object], includes: set[str], mode: str
) -> AnalyticsRequestEcho:
    return AnalyticsRequestEcho(
        filter=filter_echo,
        sort=DEFAULT_SORT,
        include=sorted(includes),
        pagination_mode=mode,
    )


def _filter_echo(request: AnalyticsPagingRequest, fields: tuple[str, ...]) -> dict[str, object]:
    """Echo only the filters the caller actually supplied.

    Defaults are omitted rather than echoed, so the echo doubles as the
    canonical description of the query — which is also what the cursor's
    signature is computed over, so an omitted default cannot change it.
    """
    echo: dict[str, object] = {}
    for name in fields:
        value = getattr(request, name)
        if value is None:
            continue
        echo[name] = value.value if hasattr(value, "value") else value
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _ordered(ids: list[int], rows: list[Any], key: str) -> list[Any]:
    """Restore the SQL page order over a projection fetched by ``id IN (...)``.

    The ordering decision was made in SQL; this only reassembles the page in
    that order after a second, bounded (``<= limit`` rows) projection query.
    """
    by_id = {getattr(row, key): row for row in rows}
    return [by_id[i] for i in ids if i in by_id]


# ---------------------------------------------------------------------------
# Kinetics
# ---------------------------------------------------------------------------

_KINETICS_ENDPOINT = "/scientific/analytics/kinetics"

_KINETICS_FILTER_FIELDS: tuple[str, ...] = (
    "scientific_origin",
    "direction",
    "model_kind",
    "tunneling_model",
    "pressure_context",
    "degeneracy_min",
    "degeneracy_max",
    "pressure_min_bar",
    "pressure_max_bar",
    "a_min",
    "a_max",
    "n_min",
    "n_max",
    "ea_min_kj_mol",
    "ea_max_kj_mol",
    "has_uncertainty",
    "ea_uncertainty_min_kj_mol",
    "ea_uncertainty_max_kj_mol",
    "temperature_min_k",
    "temperature_max_k",
    "has_literature",
    "workflow_tool",
    "has_transition_state_provenance",
    "has_statmech_provenance",
)

#: A kinetics record has statmech provenance when an interpretation
#: assignment names the exact statmech treatment used for a rate role;
#: ``kinetics_interpretation_assignment.statmech_id`` is NOT NULL, so the
#: existence of any assignment is the signal. TS provenance is the same
#: table restricted to assignments that name a transition-state entry.
def _kinetics_has_statmech():
    return exists().where(
        KineticsInterpretationAssignment.kinetics_id == Kinetics.id
    )


def _kinetics_has_ts():
    return exists().where(
        KineticsInterpretationAssignment.kinetics_id == Kinetics.id,
        KineticsInterpretationAssignment.transition_state_entry_id.is_not(None),
    )


def search_kinetics_analytics(
    session: Session, request: KineticsAnalyticsRequest
) -> KineticsAnalyticsResponse:
    """Quantitative kinetics search over Arrhenius / pressure / trust axes.

    :raises ValueError: 422 for sort, pagination, include, range or cursor
        violations.
    """
    offset, limit = _validate_paging(request)
    includes = _resolve_includes(request, _KINETICS_ENDPOINT)

    _check_range(
        "degeneracy_min", "degeneracy_max",
        request.degeneracy_min, request.degeneracy_max,
    )
    _check_range(
        "pressure_min_bar", "pressure_max_bar",
        request.pressure_min_bar, request.pressure_max_bar,
    )
    _check_range("a_min", "a_max", request.a_min, request.a_max)
    _check_range("n_min", "n_max", request.n_min, request.n_max)
    _check_range(
        "ea_min_kj_mol", "ea_max_kj_mol",
        request.ea_min_kj_mol, request.ea_max_kj_mol,
    )
    _check_range(
        "ea_uncertainty_min_kj_mol",
        "ea_uncertainty_max_kj_mol",
        request.ea_uncertainty_min_kj_mol,
        request.ea_uncertainty_max_kj_mol,
    )
    validate_temperature_range(request.temperature_min_k, request.temperature_max_k)

    stmt = select(Kinetics.id)

    if request.scientific_origin is not None:
        stmt = stmt.where(Kinetics.scientific_origin == request.scientific_origin)
    if request.direction is not None:
        stmt = stmt.where(Kinetics.direction == request.direction)
    if request.model_kind is not None:
        stmt = stmt.where(Kinetics.model_kind == request.model_kind)
    if request.tunneling_model is not None:
        stmt = stmt.where(Kinetics.tunneling_model == request.tunneling_model)
    if request.pressure_context is not None:
        stmt = stmt.where(Kinetics.pressure_context == request.pressure_context)

    stmt = _between(stmt, Kinetics.degeneracy, request.degeneracy_min, request.degeneracy_max)
    stmt = _between(
        stmt, Kinetics.pressure_bar, request.pressure_min_bar, request.pressure_max_bar
    )
    stmt = _between(stmt, Kinetics.a, request.a_min, request.a_max)
    stmt = _between(stmt, Kinetics.n, request.n_min, request.n_max)
    stmt = _between(
        stmt, Kinetics.ea_kj_mol, request.ea_min_kj_mol, request.ea_max_kj_mol
    )
    stmt = _between(
        stmt,
        Kinetics.ea_uncertainty_kj_mol,
        request.ea_uncertainty_min_kj_mol,
        request.ea_uncertainty_max_kj_mol,
    )

    if request.has_uncertainty is not None:
        any_uncertainty = (
            Kinetics.a_uncertainty.is_not(None)
            | Kinetics.n_uncertainty.is_not(None)
            | Kinetics.ea_uncertainty_kj_mol.is_not(None)
        )
        stmt = stmt.where(any_uncertainty if request.has_uncertainty else ~any_uncertainty)

    # Coverage semantics: the record's own validity window must contain the
    # requested one. A record with no stated window does not match — "not
    # stated" is not "valid everywhere".
    if request.temperature_min_k is not None:
        stmt = stmt.where(Kinetics.tmin_k.is_not(None), Kinetics.tmin_k <= request.temperature_min_k)
    if request.temperature_max_k is not None:
        stmt = stmt.where(Kinetics.tmax_k.is_not(None), Kinetics.tmax_k >= request.temperature_max_k)

    if request.has_literature is not None:
        stmt = stmt.where(
            Kinetics.literature_id.is_not(None)
            if request.has_literature
            else Kinetics.literature_id.is_(None)
        )
    if request.workflow_tool is not None:
        stmt = (
            stmt.join(
                WorkflowToolRelease,
                Kinetics.workflow_tool_release_id == WorkflowToolRelease.id,
            )
            .join(WorkflowTool, WorkflowToolRelease.workflow_tool_id == WorkflowTool.id)
            .where(WorkflowTool.name == request.workflow_tool)
        )

    if request.has_statmech_provenance is not None:
        predicate = _kinetics_has_statmech()
        stmt = stmt.where(predicate if request.has_statmech_provenance else ~predicate)
    if request.has_transition_state_provenance is not None:
        predicate = _kinetics_has_ts()
        stmt = stmt.where(
            predicate if request.has_transition_state_provenance else ~predicate
        )

    filter_echo = _filter_echo(request, _KINETICS_FILTER_FIELDS)
    page = _run_page(
        session,
        endpoint=_KINETICS_ENDPOINT,
        record_type=SubmissionRecordType.kinetics,
        id_column=Kinetics.id,
        candidate_stmt=stmt,
        request=request,
        filter_echo=filter_echo,
        offset=offset,
        limit=limit,
    )

    records = _project_kinetics(session, page) if page.ids else []
    return KineticsAnalyticsResponse(
        request=_echo(filter_echo, includes, page.mode),
        review_summary=page.summary,
        records=records,
        pagination=build_pagination(
            offset=page.offset,
            limit=page.limit,
            returned=len(records),
            total=page.total,
        ),
        next_cursor=page.next_cursor,
        watermark=page.watermark,
    )


def _project_kinetics(session: Session, page: _Page) -> list[KineticsAnalyticsRecord]:
    rows = session.execute(
        select(
            Kinetics.id,
            Kinetics.public_ref,
            Kinetics.reaction_entry_id,
            ReactionEntry.public_ref.label("reaction_entry_ref"),
            Kinetics.scientific_origin,
            Kinetics.model_kind,
            Kinetics.direction,
            Kinetics.is_third_body,
            Kinetics.a,
            Kinetics.a_units,
            Kinetics.n,
            Kinetics.ea_kj_mol,
            Kinetics.a_uncertainty,
            Kinetics.a_uncertainty_kind,
            Kinetics.n_uncertainty,
            Kinetics.ea_uncertainty_kj_mol,
            Kinetics.tmin_k,
            Kinetics.tmax_k,
            Kinetics.degeneracy,
            Kinetics.degeneracy_convention,
            Kinetics.tunneling_model,
            Kinetics.pressure_context,
            Kinetics.pressure_bar,
            Kinetics.literature_id.is_not(None).label("has_literature"),
            Kinetics.workflow_tool_release_id.is_not(None).label("has_workflow_tool"),
            _kinetics_has_ts().label("has_ts"),
            _kinetics_has_statmech().label("has_statmech"),
            Kinetics.created_at,
        )
        .join(ReactionEntry, Kinetics.reaction_entry_id == ReactionEntry.id)
        .where(Kinetics.id.in_(page.ids))
    ).all()
    return [
        KineticsAnalyticsRecord(
            kinetics_id=row.id,
            kinetics_ref=row.public_ref,
            reaction_entry_id=row.reaction_entry_id,
            reaction_entry_ref=row.reaction_entry_ref,
            scientific_origin=row.scientific_origin,
            model_kind=row.model_kind,
            direction=row.direction,
            is_third_body=row.is_third_body,
            a=row.a,
            a_units=row.a_units,
            n=row.n,
            ea_kj_mol=row.ea_kj_mol,
            a_uncertainty=row.a_uncertainty,
            a_uncertainty_kind=row.a_uncertainty_kind,
            n_uncertainty=row.n_uncertainty,
            ea_uncertainty_kj_mol=row.ea_uncertainty_kj_mol,
            tmin_k=row.tmin_k,
            tmax_k=row.tmax_k,
            degeneracy=row.degeneracy,
            degeneracy_convention=row.degeneracy_convention,
            tunneling_model=row.tunneling_model,
            pressure_context=row.pressure_context,
            pressure_bar=row.pressure_bar,
            has_literature=row.has_literature,
            has_workflow_tool=row.has_workflow_tool,
            has_transition_state_provenance=row.has_ts,
            has_statmech_provenance=row.has_statmech,
            review_status=page.statuses[row.id],
            created_at=row.created_at,
        )
        for row in _ordered(page.ids, rows, "id")
    ]


# ---------------------------------------------------------------------------
# Thermo
# ---------------------------------------------------------------------------

_THERMO_ENDPOINT = "/scientific/analytics/thermo"

_THERMO_FILTER_FIELDS: tuple[str, ...] = (
    "scientific_origin",
    "phase",
    "model_kind",
    "reference_pressure_min_bar",
    "reference_pressure_max_bar",
    "h298_min_kj_mol",
    "h298_max_kj_mol",
    "s298_min_j_mol_k",
    "s298_max_j_mol_k",
    "enthalpy_formation_0k_min_kj_mol",
    "enthalpy_formation_0k_max_kj_mol",
    "has_uncertainty",
    "h298_uncertainty_min_kj_mol",
    "h298_uncertainty_max_kj_mol",
    "has_literature",
    "workflow_tool",
    "has_statmech_provenance",
)


def search_thermo_analytics(
    session: Session, request: ThermoAnalyticsRequest
) -> ThermoAnalyticsResponse:
    """Quantitative thermo search over H/S/ΔfH(0 K), state and trust axes.

    :raises ValueError: 422 for sort, pagination, include, range or cursor
        violations.
    """
    offset, limit = _validate_paging(request)
    includes = _resolve_includes(request, _THERMO_ENDPOINT)

    _check_range(
        "reference_pressure_min_bar",
        "reference_pressure_max_bar",
        request.reference_pressure_min_bar,
        request.reference_pressure_max_bar,
    )
    _check_range(
        "h298_min_kj_mol", "h298_max_kj_mol",
        request.h298_min_kj_mol, request.h298_max_kj_mol,
    )
    _check_range(
        "s298_min_j_mol_k", "s298_max_j_mol_k",
        request.s298_min_j_mol_k, request.s298_max_j_mol_k,
    )
    _check_range(
        "enthalpy_formation_0k_min_kj_mol",
        "enthalpy_formation_0k_max_kj_mol",
        request.enthalpy_formation_0k_min_kj_mol,
        request.enthalpy_formation_0k_max_kj_mol,
    )
    _check_range(
        "h298_uncertainty_min_kj_mol",
        "h298_uncertainty_max_kj_mol",
        request.h298_uncertainty_min_kj_mol,
        request.h298_uncertainty_max_kj_mol,
    )

    stmt = select(Thermo.id)

    if request.scientific_origin is not None:
        stmt = stmt.where(Thermo.scientific_origin == request.scientific_origin)
    if request.phase is not None:
        stmt = stmt.where(Thermo.phase == request.phase)
    if request.model_kind is not None:
        stmt = stmt.where(Thermo.model_kind == request.model_kind)

    stmt = _between(
        stmt,
        Thermo.reference_pressure_bar,
        request.reference_pressure_min_bar,
        request.reference_pressure_max_bar,
    )
    stmt = _between(
        stmt, Thermo.h298_kj_mol, request.h298_min_kj_mol, request.h298_max_kj_mol
    )
    stmt = _between(
        stmt, Thermo.s298_j_mol_k, request.s298_min_j_mol_k, request.s298_max_j_mol_k
    )
    stmt = _between(
        stmt,
        Thermo.enthalpy_formation_0k_kj_mol,
        request.enthalpy_formation_0k_min_kj_mol,
        request.enthalpy_formation_0k_max_kj_mol,
    )
    stmt = _between(
        stmt,
        Thermo.h298_uncertainty_kj_mol,
        request.h298_uncertainty_min_kj_mol,
        request.h298_uncertainty_max_kj_mol,
    )

    if request.has_uncertainty is not None:
        any_uncertainty = (
            Thermo.h298_uncertainty_kj_mol.is_not(None)
            | Thermo.s298_uncertainty_j_mol_k.is_not(None)
            | Thermo.enthalpy_formation_0k_uncertainty_kj_mol.is_not(None)
        )
        stmt = stmt.where(any_uncertainty if request.has_uncertainty else ~any_uncertainty)

    if request.has_literature is not None:
        stmt = stmt.where(
            Thermo.literature_id.is_not(None)
            if request.has_literature
            else Thermo.literature_id.is_(None)
        )
    if request.workflow_tool is not None:
        stmt = (
            stmt.join(
                WorkflowToolRelease,
                Thermo.workflow_tool_release_id == WorkflowToolRelease.id,
            )
            .join(WorkflowTool, WorkflowToolRelease.workflow_tool_id == WorkflowTool.id)
            .where(WorkflowTool.name == request.workflow_tool)
        )
    if request.has_statmech_provenance is not None:
        stmt = stmt.where(
            Thermo.statmech_id.is_not(None)
            if request.has_statmech_provenance
            else Thermo.statmech_id.is_(None)
        )

    filter_echo = _filter_echo(request, _THERMO_FILTER_FIELDS)
    page = _run_page(
        session,
        endpoint=_THERMO_ENDPOINT,
        record_type=SubmissionRecordType.thermo,
        id_column=Thermo.id,
        candidate_stmt=stmt,
        request=request,
        filter_echo=filter_echo,
        offset=offset,
        limit=limit,
    )

    records = _project_thermo(session, page) if page.ids else []
    return ThermoAnalyticsResponse(
        request=_echo(filter_echo, includes, page.mode),
        review_summary=page.summary,
        records=records,
        pagination=build_pagination(
            offset=page.offset,
            limit=page.limit,
            returned=len(records),
            total=page.total,
        ),
        next_cursor=page.next_cursor,
        watermark=page.watermark,
    )


def _project_thermo(session: Session, page: _Page) -> list[ThermoAnalyticsRecord]:
    rows = session.execute(
        select(
            Thermo.id,
            Thermo.public_ref,
            Thermo.species_entry_id,
            SpeciesEntry.public_ref.label("species_entry_ref"),
            Thermo.scientific_origin,
            Thermo.model_kind,
            Thermo.phase,
            Thermo.reference_pressure_bar,
            Thermo.h298_kj_mol,
            Thermo.s298_j_mol_k,
            Thermo.enthalpy_formation_0k_kj_mol,
            Thermo.h298_uncertainty_kj_mol,
            Thermo.s298_uncertainty_j_mol_k,
            Thermo.enthalpy_formation_0k_uncertainty_kj_mol,
            Thermo.tmin_k,
            Thermo.tmax_k,
            Thermo.literature_id.is_not(None).label("has_literature"),
            Thermo.workflow_tool_release_id.is_not(None).label("has_workflow_tool"),
            Thermo.statmech_id.is_not(None).label("has_statmech"),
            Thermo.created_at,
        )
        .join(SpeciesEntry, Thermo.species_entry_id == SpeciesEntry.id)
        .where(Thermo.id.in_(page.ids))
    ).all()
    return [
        ThermoAnalyticsRecord(
            thermo_id=row.id,
            thermo_ref=row.public_ref,
            species_entry_id=row.species_entry_id,
            species_entry_ref=row.species_entry_ref,
            scientific_origin=row.scientific_origin,
            model_kind=row.model_kind,
            phase=row.phase,
            reference_pressure_bar=row.reference_pressure_bar,
            h298_kj_mol=row.h298_kj_mol,
            s298_j_mol_k=row.s298_j_mol_k,
            enthalpy_formation_0k_kj_mol=row.enthalpy_formation_0k_kj_mol,
            h298_uncertainty_kj_mol=row.h298_uncertainty_kj_mol,
            s298_uncertainty_j_mol_k=row.s298_uncertainty_j_mol_k,
            enthalpy_formation_0k_uncertainty_kj_mol=(
                row.enthalpy_formation_0k_uncertainty_kj_mol
            ),
            tmin_k=row.tmin_k,
            tmax_k=row.tmax_k,
            has_literature=row.has_literature,
            has_workflow_tool=row.has_workflow_tool,
            has_statmech_provenance=row.has_statmech,
            review_status=page.statuses[row.id],
            created_at=row.created_at,
        )
        for row in _ordered(page.ids, rows, "id")
    ]


# ---------------------------------------------------------------------------
# Statmech
# ---------------------------------------------------------------------------

_STATMECH_ENDPOINT = "/scientific/analytics/statmech"

_STATMECH_FILTER_FIELDS: tuple[str, ...] = (
    "scientific_origin",
    "external_symmetry",
    "is_linear",
    "point_group",
    "statmech_treatment",
    "rigid_rotor_kind",
    "optical_isomers",
    "rotational_constant_a_min_cm1",
    "rotational_constant_a_max_cm1",
    "rotational_constant_b_min_cm1",
    "rotational_constant_b_max_cm1",
    "rotational_constant_c_min_cm1",
    "rotational_constant_c_max_cm1",
    "has_frequency_scale_factor",
    "has_torsions",
    "has_electronic_levels",
    "electronic_level_count_min",
    "electronic_level_count_max",
)


def _electronic_level_count():
    """Correlated count of electronic levels for the current statmech row."""
    return (
        select(func.count())
        .select_from(StatmechElectronicLevel)
        .where(StatmechElectronicLevel.statmech_id == Statmech.id)
        .scalar_subquery()
    )


def _torsion_count():
    return (
        select(func.count())
        .select_from(StatmechTorsion)
        .where(StatmechTorsion.statmech_id == Statmech.id)
        .scalar_subquery()
    )


def search_statmech_analytics(
    session: Session, request: StatmechAnalyticsRequest
) -> StatmechAnalyticsResponse:
    """Quantitative statmech search over rotor, symmetry and treatment axes.

    :raises ValueError: 422 for sort, pagination, include, range or cursor
        violations.
    """
    offset, limit = _validate_paging(request)
    includes = _resolve_includes(request, _STATMECH_ENDPOINT)

    _check_range(
        "rotational_constant_a_min_cm1",
        "rotational_constant_a_max_cm1",
        request.rotational_constant_a_min_cm1,
        request.rotational_constant_a_max_cm1,
    )
    _check_range(
        "rotational_constant_b_min_cm1",
        "rotational_constant_b_max_cm1",
        request.rotational_constant_b_min_cm1,
        request.rotational_constant_b_max_cm1,
    )
    _check_range(
        "rotational_constant_c_min_cm1",
        "rotational_constant_c_max_cm1",
        request.rotational_constant_c_min_cm1,
        request.rotational_constant_c_max_cm1,
    )
    _check_range(
        "electronic_level_count_min",
        "electronic_level_count_max",
        request.electronic_level_count_min,
        request.electronic_level_count_max,
    )

    stmt = select(Statmech.id)

    if request.scientific_origin is not None:
        stmt = stmt.where(Statmech.scientific_origin == request.scientific_origin)
    if request.external_symmetry is not None:
        stmt = stmt.where(Statmech.external_symmetry == request.external_symmetry)
    if request.is_linear is not None:
        stmt = stmt.where(Statmech.is_linear.is_(request.is_linear))
    if request.point_group is not None:
        stmt = stmt.where(Statmech.point_group == request.point_group)
    if request.statmech_treatment is not None:
        stmt = stmt.where(Statmech.statmech_treatment == request.statmech_treatment)
    if request.rigid_rotor_kind is not None:
        stmt = stmt.where(Statmech.rigid_rotor_kind == request.rigid_rotor_kind)
    if request.optical_isomers is not None:
        stmt = stmt.where(Statmech.optical_isomers == request.optical_isomers)

    stmt = _between(
        stmt,
        Statmech.rotational_constant_a_cm1,
        request.rotational_constant_a_min_cm1,
        request.rotational_constant_a_max_cm1,
    )
    stmt = _between(
        stmt,
        Statmech.rotational_constant_b_cm1,
        request.rotational_constant_b_min_cm1,
        request.rotational_constant_b_max_cm1,
    )
    stmt = _between(
        stmt,
        Statmech.rotational_constant_c_cm1,
        request.rotational_constant_c_min_cm1,
        request.rotational_constant_c_max_cm1,
    )

    if request.has_frequency_scale_factor is not None:
        stmt = stmt.where(
            Statmech.frequency_scale_factor_id.is_not(None)
            if request.has_frequency_scale_factor
            else Statmech.frequency_scale_factor_id.is_(None)
        )
    if request.has_torsions is not None:
        predicate = exists().where(StatmechTorsion.statmech_id == Statmech.id)
        stmt = stmt.where(predicate if request.has_torsions else ~predicate)
    if request.has_electronic_levels is not None:
        predicate = exists().where(
            StatmechElectronicLevel.statmech_id == Statmech.id
        )
        stmt = stmt.where(predicate if request.has_electronic_levels else ~predicate)
    if request.electronic_level_count_min is not None:
        stmt = stmt.where(_electronic_level_count() >= request.electronic_level_count_min)
    if request.electronic_level_count_max is not None:
        stmt = stmt.where(_electronic_level_count() <= request.electronic_level_count_max)

    filter_echo = _filter_echo(request, _STATMECH_FILTER_FIELDS)
    page = _run_page(
        session,
        endpoint=_STATMECH_ENDPOINT,
        record_type=SubmissionRecordType.statmech,
        id_column=Statmech.id,
        candidate_stmt=stmt,
        request=request,
        filter_echo=filter_echo,
        offset=offset,
        limit=limit,
    )

    records = _project_statmech(session, page) if page.ids else []
    return StatmechAnalyticsResponse(
        request=_echo(filter_echo, includes, page.mode),
        review_summary=page.summary,
        records=records,
        pagination=build_pagination(
            offset=page.offset,
            limit=page.limit,
            returned=len(records),
            total=page.total,
        ),
        next_cursor=page.next_cursor,
        watermark=page.watermark,
    )


def _project_statmech(session: Session, page: _Page) -> list[StatmechAnalyticsRecord]:
    rows = session.execute(
        select(
            Statmech.id,
            Statmech.public_ref,
            Statmech.species_entry_id,
            SpeciesEntry.public_ref.label("species_entry_ref"),
            Statmech.transition_state_entry_id,
            TransitionStateEntry.public_ref.label("transition_state_entry_ref"),
            Statmech.scientific_origin,
            Statmech.external_symmetry,
            Statmech.is_linear,
            Statmech.point_group,
            Statmech.statmech_treatment,
            Statmech.rigid_rotor_kind,
            Statmech.optical_isomers,
            Statmech.rotational_constant_a_cm1,
            Statmech.rotational_constant_b_cm1,
            Statmech.rotational_constant_c_cm1,
            Statmech.frequency_scale_factor_id.is_not(None).label("has_fsf"),
            _torsion_count().label("torsion_count"),
            _electronic_level_count().label("electronic_level_count"),
            Statmech.created_at,
        )
        .outerjoin(SpeciesEntry, Statmech.species_entry_id == SpeciesEntry.id)
        .outerjoin(
            TransitionStateEntry,
            Statmech.transition_state_entry_id == TransitionStateEntry.id,
        )
        .where(Statmech.id.in_(page.ids))
    ).all()
    return [
        StatmechAnalyticsRecord(
            statmech_id=row.id,
            statmech_ref=row.public_ref,
            species_entry_id=row.species_entry_id,
            species_entry_ref=row.species_entry_ref,
            transition_state_entry_id=row.transition_state_entry_id,
            transition_state_entry_ref=row.transition_state_entry_ref,
            scientific_origin=row.scientific_origin,
            external_symmetry=row.external_symmetry,
            is_linear=row.is_linear,
            point_group=row.point_group,
            statmech_treatment=row.statmech_treatment,
            rigid_rotor_kind=row.rigid_rotor_kind,
            optical_isomers=row.optical_isomers,
            rotational_constant_a_cm1=row.rotational_constant_a_cm1,
            rotational_constant_b_cm1=row.rotational_constant_b_cm1,
            rotational_constant_c_cm1=row.rotational_constant_c_cm1,
            has_frequency_scale_factor=row.has_fsf,
            torsion_count=row.torsion_count,
            electronic_level_count=row.electronic_level_count,
            review_status=page.statuses[row.id],
            created_at=row.created_at,
        )
        for row in _ordered(page.ids, rows, "id")
    ]


# ---------------------------------------------------------------------------
# Calculations
# ---------------------------------------------------------------------------

_CALCULATIONS_ENDPOINT = "/scientific/analytics/calculations"

_CALCULATION_FILTER_FIELDS: tuple[str, ...] = (
    "calculation_type",
    "electronic_energy_min_hartree",
    "electronic_energy_max_hartree",
    "zpe_min_hartree",
    "zpe_max_hartree",
    "n_imag",
    "converged",
    "t1_min",
    "t1_max",
    "d1_min",
    "d1_max",
    "s_squared_min",
    "s_squared_max",
    "method",
    "basis",
    "lot_ref",
    "software",
)


def search_calculation_analytics(
    session: Session, request: CalculationAnalyticsRequest
) -> CalculationAnalyticsResponse:
    """Quantitative calculation search over energy, convergence and diagnostics.

    :raises ValueError: 422 for sort, pagination, include, range or cursor
        violations.
    """
    offset, limit = _validate_paging(request)
    includes = _resolve_includes(request, _CALCULATIONS_ENDPOINT)

    _check_range(
        "electronic_energy_min_hartree",
        "electronic_energy_max_hartree",
        request.electronic_energy_min_hartree,
        request.electronic_energy_max_hartree,
    )
    _check_range(
        "zpe_min_hartree", "zpe_max_hartree",
        request.zpe_min_hartree, request.zpe_max_hartree,
    )
    _check_range("t1_min", "t1_max", request.t1_min, request.t1_max)
    _check_range("d1_min", "d1_max", request.d1_min, request.d1_max)
    _check_range(
        "s_squared_min", "s_squared_max",
        request.s_squared_min, request.s_squared_max,
    )

    stmt = select(Calculation.id)

    if request.calculation_type is not None:
        stmt = stmt.where(Calculation.type == request.calculation_type)

    # Each numeric axis lives on a per-type result or diagnostic table. The
    # join is inner and only added when the caller asked for that axis, so a
    # filtered query is also a presence filter and an unfiltered one costs
    # nothing.
    if (
        request.electronic_energy_min_hartree is not None
        or request.electronic_energy_max_hartree is not None
    ):
        stmt = stmt.join(
            CalculationSPResult,
            Calculation.id == CalculationSPResult.calculation_id,
        )
        stmt = _between(
            stmt,
            CalculationSPResult.electronic_energy_hartree,
            request.electronic_energy_min_hartree,
            request.electronic_energy_max_hartree,
        )

    if request.converged is not None:
        stmt = stmt.join(
            CalculationOptResult,
            Calculation.id == CalculationOptResult.calculation_id,
        ).where(CalculationOptResult.converged.is_(request.converged))

    if (
        request.zpe_min_hartree is not None
        or request.zpe_max_hartree is not None
        or request.n_imag is not None
    ):
        stmt = stmt.join(
            CalculationFreqResult,
            Calculation.id == CalculationFreqResult.calculation_id,
        )
        stmt = _between(
            stmt,
            CalculationFreqResult.zpe_hartree,
            request.zpe_min_hartree,
            request.zpe_max_hartree,
        )
        if request.n_imag is not None:
            stmt = stmt.where(CalculationFreqResult.n_imag == request.n_imag)

    if any(
        value is not None
        for value in (request.t1_min, request.t1_max, request.d1_min, request.d1_max)
    ):
        stmt = stmt.join(
            CalculationWavefunctionDiagnostic,
            Calculation.id == CalculationWavefunctionDiagnostic.calculation_id,
        )
        stmt = _between(
            stmt,
            CalculationWavefunctionDiagnostic.t1_diagnostic,
            request.t1_min,
            request.t1_max,
        )
        stmt = _between(
            stmt,
            CalculationWavefunctionDiagnostic.d1_diagnostic,
            request.d1_min,
            request.d1_max,
        )

    if request.s_squared_min is not None or request.s_squared_max is not None:
        stmt = stmt.join(
            CalculationSpinDiagnostic,
            Calculation.id == CalculationSpinDiagnostic.calculation_id,
        )
        stmt = _between(
            stmt,
            CalculationSpinDiagnostic.s_squared,
            request.s_squared_min,
            request.s_squared_max,
        )

    if (
        request.method is not None
        or request.basis is not None
        or request.lot_ref is not None
    ):
        stmt = stmt.join(LevelOfTheory, Calculation.lot_id == LevelOfTheory.id)
        if request.method is not None:
            stmt = stmt.where(LevelOfTheory.method == request.method)
        if request.basis is not None:
            stmt = stmt.where(LevelOfTheory.basis == request.basis)
        if request.lot_ref is not None:
            stmt = stmt.where(LevelOfTheory.public_ref == request.lot_ref)

    if request.software is not None:
        stmt = (
            stmt.join(
                SoftwareRelease,
                Calculation.software_release_id == SoftwareRelease.id,
            )
            .join(Software, SoftwareRelease.software_id == Software.id)
            .where(Software.name == request.software)
        )

    filter_echo = _filter_echo(request, _CALCULATION_FILTER_FIELDS)
    page = _run_page(
        session,
        endpoint=_CALCULATIONS_ENDPOINT,
        record_type=SubmissionRecordType.calculation,
        id_column=Calculation.id,
        candidate_stmt=stmt,
        request=request,
        filter_echo=filter_echo,
        offset=offset,
        limit=limit,
    )

    records = _project_calculations(session, page) if page.ids else []
    return CalculationAnalyticsResponse(
        request=_echo(filter_echo, includes, page.mode),
        review_summary=page.summary,
        records=records,
        pagination=build_pagination(
            offset=page.offset,
            limit=page.limit,
            returned=len(records),
            total=page.total,
        ),
        next_cursor=page.next_cursor,
        watermark=page.watermark,
    )


def _project_calculations(
    session: Session, page: _Page
) -> list[CalculationAnalyticsRecord]:
    rows = session.execute(
        select(
            Calculation.id,
            Calculation.public_ref,
            Calculation.type,
            Calculation.quality,
            CalculationSPResult.electronic_energy_hartree,
            CalculationOptResult.final_energy_hartree,
            CalculationOptResult.converged,
            CalculationFreqResult.zpe_hartree,
            CalculationFreqResult.n_imag,
            CalculationFreqResult.imag_freq_cm1,
            CalculationWavefunctionDiagnostic.t1_diagnostic,
            CalculationWavefunctionDiagnostic.d1_diagnostic,
            CalculationSpinDiagnostic.s_squared,
            CalculationSpinDiagnostic.s_squared_expected,
            LevelOfTheory.method,
            LevelOfTheory.basis,
            LevelOfTheory.public_ref.label("lot_ref"),
            Software.name.label("software"),
            SoftwareRelease.version.label("software_version"),
            Calculation.created_at,
        )
        .outerjoin(
            CalculationSPResult,
            Calculation.id == CalculationSPResult.calculation_id,
        )
        .outerjoin(
            CalculationOptResult,
            Calculation.id == CalculationOptResult.calculation_id,
        )
        .outerjoin(
            CalculationFreqResult,
            Calculation.id == CalculationFreqResult.calculation_id,
        )
        .outerjoin(
            CalculationWavefunctionDiagnostic,
            Calculation.id == CalculationWavefunctionDiagnostic.calculation_id,
        )
        .outerjoin(
            CalculationSpinDiagnostic,
            Calculation.id == CalculationSpinDiagnostic.calculation_id,
        )
        .outerjoin(LevelOfTheory, Calculation.lot_id == LevelOfTheory.id)
        .outerjoin(
            SoftwareRelease, Calculation.software_release_id == SoftwareRelease.id
        )
        .outerjoin(Software, SoftwareRelease.software_id == Software.id)
        .where(Calculation.id.in_(page.ids))
    ).all()
    return [
        CalculationAnalyticsRecord(
            calculation_id=row.id,
            calculation_ref=row.public_ref,
            calculation_type=row.type,
            quality=row.quality,
            electronic_energy_hartree=row.electronic_energy_hartree,
            final_energy_hartree=row.final_energy_hartree,
            converged=row.converged,
            zpe_hartree=row.zpe_hartree,
            n_imag=row.n_imag,
            imag_freq_cm1=row.imag_freq_cm1,
            t1_diagnostic=row.t1_diagnostic,
            d1_diagnostic=row.d1_diagnostic,
            s_squared=row.s_squared,
            s_squared_expected=row.s_squared_expected,
            method=row.method,
            basis=row.basis,
            level_of_theory_ref=row.lot_ref,
            software=row.software,
            software_version=row.software_version,
            review_status=page.statuses[row.id],
            created_at=row.created_at,
        )
        for row in _ordered(page.ids, rows, "id")
    ]


# ---------------------------------------------------------------------------
# Small shared SQL helper
# ---------------------------------------------------------------------------


def _between(stmt: Select, column: Any, low: Any, high: Any) -> Select:
    """Apply the supplied half of an inclusive range, or neither.

    A NULL column never matches either bound, which is the intended reading:
    "Ea between 10 and 40" must not return records that never stated an Ea.
    """
    if low is not None:
        stmt = stmt.where(column >= low)
    if high is not None:
        stmt = stmt.where(column <= high)
    return stmt


__all__ = [
    "DEFAULT_SORT",
    "InvalidCursorError",
    "search_calculation_analytics",
    "search_kinetics_analytics",
    "search_statmech_analytics",
    "search_thermo_analytics",
]
