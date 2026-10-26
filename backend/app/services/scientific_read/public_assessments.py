"""Opt-in trust/reproducibility projection for machine-facing reads.

The public contract is intentionally compact.  It reports the current
deterministic evidence rubric and the latest immutable reproducibility claim,
including whether that claim still matches the evidence visible today.

**Two readouts, one source.** A record can carry the same verdict under two
include tokens: ``assessments.deterministic_trust`` (compact rubric name,
version and grade, alongside the reproducibility claim) and ``trust`` (the
fuller fragment with the evidence object and the curator review status).
They must never be two independently maintained opinions, so both are built
here from a single :func:`_evaluate` call — one grouped load of the evidence
graph per page, one ``evaluate_loaded_*`` invocation per record, and two
projections of its result. ``deterministic_trust.grade`` and
``trust.trust_status`` are therefore the same string by construction rather
than by agreement, and
``backend/tests/api/scientific/test_search_trust_include.py`` pins that.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.db.models.reproducibility_assessment import (
    RecordReproducibilityAssessment,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.schemas.reads.scientific_assessment import (
    DeterministicTrustSummary,
    MadeUnder,
    PublicAssessmentSummary,
    ReproducibilityAssessmentSummary,
)
from app.services.reproducibility_assessment import (
    is_reproducibility_assessment_context_current,
)
from app.services.reproducibility_rubric import (
    evaluate_reproducibility,
    reproducibility_rubric_for,
)
from app.services.scientific_read.kinetics import KINETICS_TRUST_EAGER_LOADS
from app.services.scientific_read.statmech import STATMECH_TRUST_EAGER_LOADS
from app.services.scientific_read.thermo import THERMO_TRUST_EAGER_LOADS
from app.services.scientific_read.transport import TRANSPORT_TRUST_EAGER_LOADS
from app.services.scientific_read.trust_floor import evaluate_shown_kinetics
from app.services.trust import (
    build_trust_fragment,
    evaluate_loaded_statmech,
    evaluate_loaded_thermo,
    evaluate_loaded_transport,
)
from app.services.trust.models import EvidenceEvaluation


def attach_kinetics_assessments(session: Session, payload: Any, *, check_inputs: bool = False) -> Any:
    """Attach summaries to detail or chemistry-search kinetics records."""
    records = list(_kinetics_records(payload))
    _attach(
        session,
        check_inputs=check_inputs,
        records=records,
        record_type=SubmissionRecordType.kinetics,
        model=Kinetics,
        eager_loads=KINETICS_TRUST_EAGER_LOADS,
        evaluator=evaluate_shown_kinetics,
    )
    return payload


def attach_thermo_assessments(session: Session, payload: Any, *, check_inputs: bool = False) -> Any:
    """Attach summaries to detail or chemistry-search thermo records."""
    records = list(_thermo_records(payload))
    _attach(
        session,
        check_inputs=check_inputs,
        records=records,
        record_type=SubmissionRecordType.thermo,
        model=Thermo,
        eager_loads=THERMO_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_thermo,
    )
    return payload


def attach_statmech_assessments(session: Session, payload: Any, *, check_inputs: bool = False) -> Any:
    """Attach summaries to statmech detail, search, and subresource records."""
    _attach(
        session,
        check_inputs=check_inputs,
        records=list(_statmech_records(payload)),
        record_type=SubmissionRecordType.statmech,
        model=Statmech,
        eager_loads=STATMECH_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_statmech,
    )
    return payload


def attach_transport_assessments(session: Session, payload: Any, *, check_inputs: bool = False) -> Any:
    """Attach summaries to transport detail, search, and subresource records."""
    _attach(
        session,
        check_inputs=check_inputs,
        records=list(_transport_records(payload)),
        record_type=SubmissionRecordType.transport,
        model=Transport,
        eager_loads=TRANSPORT_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_transport,
    )
    return payload


def attach_thermo_trust(
    session: Session, records: list[tuple[Any, int]]
) -> None:
    """Populate ``trust`` on a page of ``ThermoRecord``s.

    ``records`` pairs each record with its ``thermo.id``. Called by
    ``/scientific/thermo/search`` after pagination, so the evidence graph is
    loaded for the page the caller will actually receive rather than for
    every candidate the search walked.
    """
    attach_trust_fragments(
        session,
        records=records,
        model=Thermo,
        eager_loads=THERMO_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_thermo,
    )


def attach_kinetics_trust(
    session: Session, records: list[tuple[Any, int]]
) -> None:
    """Populate ``trust`` on a page of ``KineticsRecord``s.

    ``records`` pairs each record with its ``kinetics.id``. See
    :func:`attach_thermo_trust`.
    """
    attach_trust_fragments(
        session,
        records=records,
        model=Kinetics,
        eager_loads=KINETICS_TRUST_EAGER_LOADS,
        evaluator=evaluate_shown_kinetics,
    )


def _kinetics_records(payload: Any) -> Iterable[tuple[Any, int]]:
    for item in payload.records:
        record = item.kinetics if hasattr(item, "kinetics") else item
        yield record, record.kinetics_id


def _thermo_records(payload: Any) -> Iterable[tuple[Any, int]]:
    for item in payload.records:
        record = item.thermo if hasattr(item, "thermo") else item
        yield record, record.thermo_id


def _statmech_records(payload: Any) -> Iterable[tuple[Any, int]]:
    records = payload.records if hasattr(payload, "records") else [payload.record]
    for record in records:
        yield record, record.statmech.statmech_id


def _transport_records(payload: Any) -> Iterable[tuple[Any, int]]:
    records = payload.records if hasattr(payload, "records") else [payload.record]
    for record in records:
        yield record, record.transport.transport_id


def _evaluate(
    session: Session,
    *,
    ids: list[int],
    model: type[Any],
    eager_loads: tuple[Any, ...],
    evaluator: Callable[[Any], EvidenceEvaluation],
    targets_out: dict[int, Any] | None = None,
) -> dict[int, EvidenceEvaluation]:
    """Evaluate the deterministic rubric for a whole page in one load.

    The single source both public readouts are derived from. The eager-load
    tuple is the one the evaluator's check runners expect to find already
    loaded, applied as options on **one** statement over the page's ids, so
    the graph costs a fixed number of statements whatever the page size.
    """
    targets = session.scalars(
        select(model).where(model.id.in_(ids)).options(*eager_loads)
    ).all()
    target_by_id = {target.id: target for target in targets}
    if targets_out is not None:
        targets_out.update(target_by_id)
    return {
        record_id: evaluator(target_by_id[record_id])
        for record_id in ids
        if record_id in target_by_id
    }


def attach_trust_fragments(
    session: Session,
    *,
    records: list[tuple[Any, int]],
    model: type[Any],
    eager_loads: tuple[Any, ...],
    evaluator: Callable[[Any], EvidenceEvaluation],
) -> None:
    """Populate ``record.trust`` for a page, from the same evaluation.

    Used by the chemistry-search services, whose records are built by the
    detail-endpoint builders and so arrive with ``trust`` still ``None``.
    Resolving it here rather than inside the per-record builder is what
    keeps the evidence graph a per-page cost: the builders run once per
    record, and anything they load is multiplied by ``limit``.

    ``review_status`` comes off the record's own review badge, which the
    builder has already resolved — the read layer owns that half of the
    fragment, the evaluator owns the evidence half.
    """
    if not records:
        return
    ids = list(dict.fromkeys(record_id for _, record_id in records))
    evaluations = _evaluate(
        session,
        ids=ids,
        model=model,
        eager_loads=eager_loads,
        evaluator=evaluator,
    )
    for record, record_id in records:
        evaluation = evaluations.get(record_id)
        if evaluation is None:  # pragma: no cover — race with delete
            continue
        badge = getattr(record, "review", None)
        record.trust = build_trust_fragment(
            evaluation,
            review_status=getattr(badge, "status", None),
        )


def _attach(
    session: Session,
    *,
    check_inputs: bool,
    records: list[tuple[Any, int]],
    record_type: SubmissionRecordType,
    model: type[Any],
    eager_loads: tuple[Any, ...],
    evaluator: Callable[[Any], EvidenceEvaluation],
) -> None:
    if not records:
        return
    ids = list(dict.fromkeys(record_id for _, record_id in records))
    targets: dict[int, Any] = {}
    evaluations = _evaluate(
        session,
        ids=ids,
        model=model,
        eager_loads=eager_loads,
        evaluator=evaluator,
        targets_out=targets,
    )
    latest = _latest_assessments(session, record_type=record_type, record_ids=ids)

    summaries: dict[int, PublicAssessmentSummary] = {}
    for record_id in ids:
        trust = evaluations[record_id]
        assessment = latest.get(record_id)
        summaries[record_id] = PublicAssessmentSummary(
            deterministic_trust=DeterministicTrustSummary(
                rubric=trust.rubric,
                rubric_version=trust.rubric_version,
                grade=trust.label.value,
                hard_fail=(trust.hard_fail_reason.value if trust.hard_fail_reason is not None else None),
                # Evaluated just now, under the rubric version in force: current by construction.
                made_under=MadeUnder(
                    rubric=trust.rubric,
                    assessed_version=trust.rubric_version,
                    current_rubric=trust.rubric,
                    current_version=trust.rubric_version,
                    state="current",
                    inputs_checked=True,
                ),
            ),
            reproducibility=_reproducibility_summary(
                session,
                record_type=record_type,
                record_id=record_id,
                assessment=assessment,
                target=targets.get(record_id),
                check_inputs=check_inputs,
            ),
        )

    for record, record_id in records:
        record.assessments = summaries[record_id]


def _latest_assessments(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_ids: list[int],
) -> dict[int, RecordReproducibilityAssessment]:
    ranked = (
        select(
            RecordReproducibilityAssessment.id.label("assessment_id"),
            func.row_number()
            .over(
                partition_by=RecordReproducibilityAssessment.record_id,
                order_by=(
                    RecordReproducibilityAssessment.assessed_at.desc(),
                    RecordReproducibilityAssessment.id.desc(),
                ),
            )
            .label("recency_rank"),
        )
        .where(
            RecordReproducibilityAssessment.record_type == record_type,
            RecordReproducibilityAssessment.record_id.in_(record_ids),
        )
        .subquery()
    )
    rows = session.scalars(
        select(RecordReproducibilityAssessment)
        .join(ranked, ranked.c.assessment_id == RecordReproducibilityAssessment.id)
        .where(ranked.c.recency_rank == 1)
    ).all()
    return {row.record_id: row for row in rows}


def _reproducibility_summary(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
    assessment: RecordReproducibilityAssessment | None,
    target: Any | None = None,
    check_inputs: bool = False,
) -> ReproducibilityAssessmentSummary:
    if assessment is None:
        return ReproducibilityAssessmentSummary(state="unassessed", made_under=MadeUnder(state="unassessed", inputs_checked=None))

    if not check_inputs:
        # Whether the inputs of a stored assessment still match is a full re-evaluation (tens of statements) per record,
        # so only a route that is asked to (and bounds its page) does it. Here the rubric name and version are compared
        # and nothing is claimed about the inputs.
        rubric = reproducibility_rubric_for(target)
        rubric_is_current = (assessment.rubric_name, assessment.rubric_version) == (rubric.name, rubric.version)
        state = "inputs_unchecked" if rubric_is_current else "stale"
        return ReproducibilityAssessmentSummary(
            state=state,
            assessment_ref=assessment.public_ref,
            rubric=assessment.rubric_name,
            rubric_version=assessment.rubric_version,
            grade=assessment.grade,
            assessed_at=assessment.assessed_at,
            made_under=MadeUnder(
                rubric=assessment.rubric_name,
                assessed_version=assessment.rubric_version,
                current_rubric=rubric.name,
                current_version=rubric.version,
                state=state,
                stale_reason=None if rubric_is_current else "rubric_version",
                inputs_checked=False,
            ),
        )

    # A stored stamp was computed from the full set of inputs, with no read profile, so whether it is current is a property
    # of the record and its stamp, not of the reader: the recomputation below reads nothing from the request's profile
    # (``evaluate_reproducibility`` never consults it) and the answer is the same under ``curated`` and ``exploratory``
    # (ADR 0023, step 6). The answer is a state and a reason, never a ref.
    current = evaluate_reproducibility(session, record_type=record_type, record_id=record_id)
    rubric_is_current = (assessment.rubric_name, assessment.rubric_version) == (
        current.rubric_name,
        current.rubric_version,
    )
    is_current = rubric_is_current and is_reproducibility_assessment_context_current(
        assessment, current_context_json=current.context_json
    )
    return ReproducibilityAssessmentSummary(
        state="current" if is_current else "stale",
        made_under=MadeUnder(
            rubric=assessment.rubric_name,
            assessed_version=assessment.rubric_version,
            current_rubric=current.rubric_name,
            current_version=current.rubric_version,
            state="current" if is_current else "stale",
            stale_reason=None if is_current else ("inputs_changed" if rubric_is_current else "rubric_version"),
            inputs_checked=True,
        ),
        assessment_ref=assessment.public_ref,
        rubric=assessment.rubric_name,
        rubric_version=assessment.rubric_version,
        grade=assessment.grade,
        assessed_at=assessment.assessed_at,
    )


__all__ = [
    "attach_kinetics_assessments",
    "attach_statmech_assessments",
    "attach_thermo_assessments",
    "attach_transport_assessments",
]
