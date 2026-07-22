"""Opt-in trust/reproducibility projection for machine-facing reads.

The public contract is intentionally compact.  It reports the current
deterministic evidence rubric and the latest immutable reproducibility claim,
including whether that claim still matches the evidence visible today.
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
    PublicAssessmentSummary,
    ReproducibilityAssessmentSummary,
)
from app.services.reproducibility_assessment import (
    is_reproducibility_assessment_context_current,
)
from app.services.reproducibility_rubric import (
    RUBRIC_NAME,
    RUBRIC_VERSION,
    evaluate_reproducibility_v1,
)
from app.services.scientific_read.kinetics import KINETICS_TRUST_EAGER_LOADS
from app.services.scientific_read.statmech import STATMECH_TRUST_EAGER_LOADS
from app.services.scientific_read.thermo import THERMO_TRUST_EAGER_LOADS
from app.services.scientific_read.transport import TRANSPORT_TRUST_EAGER_LOADS
from app.services.trust import (
    evaluate_loaded_kinetics,
    evaluate_loaded_statmech,
    evaluate_loaded_thermo,
    evaluate_loaded_transport,
)
from app.services.trust.models import EvidenceEvaluation


def attach_kinetics_assessments(session: Session, payload: Any) -> Any:
    """Attach summaries to detail or chemistry-search kinetics records."""
    records = list(_kinetics_records(payload))
    _attach(
        session,
        records=records,
        record_type=SubmissionRecordType.kinetics,
        model=Kinetics,
        eager_loads=KINETICS_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_kinetics,
    )
    return payload


def attach_thermo_assessments(session: Session, payload: Any) -> Any:
    """Attach summaries to detail or chemistry-search thermo records."""
    records = list(_thermo_records(payload))
    _attach(
        session,
        records=records,
        record_type=SubmissionRecordType.thermo,
        model=Thermo,
        eager_loads=THERMO_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_thermo,
    )
    return payload


def attach_statmech_assessments(session: Session, payload: Any) -> Any:
    """Attach summaries to statmech detail, search, and subresource records."""
    _attach(
        session,
        records=list(_statmech_records(payload)),
        record_type=SubmissionRecordType.statmech,
        model=Statmech,
        eager_loads=STATMECH_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_statmech,
    )
    return payload


def attach_transport_assessments(session: Session, payload: Any) -> Any:
    """Attach summaries to transport detail, search, and subresource records."""
    _attach(
        session,
        records=list(_transport_records(payload)),
        record_type=SubmissionRecordType.transport,
        model=Transport,
        eager_loads=TRANSPORT_TRUST_EAGER_LOADS,
        evaluator=evaluate_loaded_transport,
    )
    return payload


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


def _attach(
    session: Session,
    *,
    records: list[tuple[Any, int]],
    record_type: SubmissionRecordType,
    model: type[Any],
    eager_loads: tuple[Any, ...],
    evaluator: Callable[[Any], EvidenceEvaluation],
) -> None:
    if not records:
        return
    ids = list(dict.fromkeys(record_id for _, record_id in records))
    targets = session.scalars(select(model).where(model.id.in_(ids)).options(*eager_loads)).all()
    target_by_id = {target.id: target for target in targets}
    latest = _latest_assessments(session, record_type=record_type, record_ids=ids)

    summaries: dict[int, PublicAssessmentSummary] = {}
    for record_id in ids:
        target = target_by_id[record_id]
        trust = evaluator(target)
        assessment = latest.get(record_id)
        summaries[record_id] = PublicAssessmentSummary(
            deterministic_trust=DeterministicTrustSummary(
                rubric=trust.rubric,
                rubric_version=str(trust.rubric_version),
                grade=trust.label.value,
                hard_fail=(trust.hard_fail_reason.value if trust.hard_fail_reason is not None else None),
            ),
            reproducibility=_reproducibility_summary(
                session,
                record_type=record_type,
                record_id=record_id,
                assessment=assessment,
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
) -> ReproducibilityAssessmentSummary:
    if assessment is None:
        return ReproducibilityAssessmentSummary(state="unassessed")

    current = evaluate_reproducibility_v1(session, record_type=record_type, record_id=record_id)
    is_current = (
        assessment.rubric_name == RUBRIC_NAME
        and assessment.rubric_version == RUBRIC_VERSION
        and is_reproducibility_assessment_context_current(assessment, current_context_json=current.context_json)
    )
    return ReproducibilityAssessmentSummary(
        state="current" if is_current else "stale",
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
