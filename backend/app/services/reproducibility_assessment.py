"""Append and read reproducibility-assessment history.

This service owns the only application write interface for
``record_reproducibility_assessment``.  It appends a new immutable snapshot and
never changes scientific data, human review state, or trust badges.  The
database trigger remains the authoritative append-only guard for direct SQL
and ORM use outside this service.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import (
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    SubmissionRecordType,
)
from app.db.models.energy_correction import AppliedEnergyCorrection
from app.db.models.kinetics import Kinetics
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.reproducibility_assessment import (
    RecordReproducibilityAssessment,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport
from app.schemas.entities.reproducibility_assessment import (
    ReproducibilityAssessmentAppend,
)

_RECORD_MODELS: dict[SubmissionRecordType, type[Any]] = {
    SubmissionRecordType.species: Species,
    SubmissionRecordType.species_entry: SpeciesEntry,
    SubmissionRecordType.conformer_group: ConformerGroup,
    SubmissionRecordType.conformer_observation: ConformerObservation,
    SubmissionRecordType.reaction: ChemReaction,
    SubmissionRecordType.reaction_entry: ReactionEntry,
    SubmissionRecordType.transition_state: TransitionState,
    SubmissionRecordType.transition_state_entry: TransitionStateEntry,
    SubmissionRecordType.calculation: Calculation,
    SubmissionRecordType.statmech: Statmech,
    SubmissionRecordType.thermo: Thermo,
    SubmissionRecordType.kinetics: Kinetics,
    SubmissionRecordType.transport: Transport,
    SubmissionRecordType.network: Network,
    SubmissionRecordType.network_solve: NetworkSolve,
    SubmissionRecordType.applied_energy_correction: AppliedEnergyCorrection,
    SubmissionRecordType.artifact: CalculationArtifact,
}

_MAX_FUTURE_ASSESSMENT_SKEW = timedelta(minutes=5)


def _naive_utc(value: datetime | None) -> datetime:
    """Return the supplied instant in the repository's naive-UTC DB convention."""
    if value is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _validated_assessed_at(value: datetime | None) -> datetime:
    """Normalize an assessment timestamp and reject material future dating."""
    assessed_at = _naive_utc(value)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if assessed_at > now + _MAX_FUTURE_ASSESSMENT_SKEW:
        raise ValueError("assessed_at must not be materially in the future")
    return assessed_at


def _canonical_context_and_hash(
    context_json: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Round-trip a JSON object to canonical form and compute its SHA-256."""
    try:
        encoded = json.dumps(
            context_json,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("context_json must contain only finite, JSON-serializable values") from exc
    canonical_context = json.loads(encoded)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return canonical_context, digest


def _require_record_exists(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
) -> None:
    """Reject an assessment whose polymorphic target row does not exist."""
    model = _RECORD_MODELS[record_type]
    if session.get(model, record_id) is None:
        raise ValueError(f"{record_type.value} record {record_id} does not exist")


def append_reproducibility_assessment(
    session: Session,
    *,
    record_type: str | SubmissionRecordType,
    record_id: int,
    grade: str | ReproducibilityGrade,
    rubric_name: str,
    rubric_version: str,
    context_json: dict[str, Any],
    expected_context_hash: str | None = None,
    passed: Sequence[Any] | None = None,
    missing: Sequence[Any] | None = None,
    warnings: Sequence[Any] | None = None,
    assessor_kind: str | ReproducibilityAssessorKind,
    assessor_user_id: int | None = None,
    source_submission_id: int | None = None,
    assessed_at: datetime | None = None,
) -> RecordReproducibilityAssessment:
    """Validate and append one assessor's reproducibility claim.

    The helper flushes so the returned row has an id, but never commits; the
    caller owns the transaction.  Reassessment always creates another row.
    The supplied grade records the named assessor's rubric-based claim; this
    service does not independently derive or verify that grade.
    The service canonicalizes and stores ``context_json`` and computes its
    SHA-256; callers cannot supply an unrelated digest.  When
    ``expected_context_hash`` is supplied it must match the computed value.
    """
    payload = ReproducibilityAssessmentAppend(
        record_type=record_type,
        record_id=record_id,
        grade=grade,
        rubric_name=rubric_name,
        rubric_version=rubric_version,
        context_json=context_json,
        expected_context_hash=expected_context_hash,
        passed=list(passed or ()),
        missing=list(missing or ()),
        warnings=list(warnings or ()),
        assessor_kind=assessor_kind,
        assessor_user_id=assessor_user_id,
        source_submission_id=source_submission_id,
        assessed_at=assessed_at,
    )
    _require_record_exists(
        session,
        record_type=payload.record_type,
        record_id=payload.record_id,
    )
    canonical_context, context_hash = _canonical_context_and_hash(payload.context_json)
    if payload.expected_context_hash is not None and payload.expected_context_hash != context_hash:
        raise ValueError("expected_context_hash does not match canonical context_json")

    row = RecordReproducibilityAssessment(
        record_type=payload.record_type,
        record_id=payload.record_id,
        grade=payload.grade,
        rubric_name=payload.rubric_name,
        rubric_version=payload.rubric_version,
        context_hash=context_hash,
        context_json=canonical_context,
        passed_json=payload.passed,
        missing_json=payload.missing,
        warnings_json=payload.warnings,
        assessor_kind=payload.assessor_kind,
        assessor_user_id=payload.assessor_user_id,
        source_submission_id=payload.source_submission_id,
        assessed_at=_validated_assessed_at(payload.assessed_at),
    )
    session.add(row)
    session.flush()
    return row


def is_reproducibility_assessment_context_current(
    assessment: RecordReproducibilityAssessment,
    *,
    current_context_json: dict[str, Any],
) -> bool:
    """Return whether current canonical evidence matches an assessment snapshot."""
    _, current_context_hash = _canonical_context_and_hash(current_context_json)
    return hmac.compare_digest(assessment.context_hash, current_context_hash)


def get_latest_reproducibility_assessment(
    session: Session,
    *,
    record_type: str | SubmissionRecordType,
    record_id: int,
) -> RecordReproducibilityAssessment | None:
    """Return the newest assessment for one record, or ``None``.

    ``assessed_at`` is the primary ordering key and the immutable row id is the
    deterministic tie-break.  The query is read-only.
    """
    resolved_record_type = (
        record_type if isinstance(record_type, SubmissionRecordType) else SubmissionRecordType(record_type)
    )
    if record_id <= 0:
        raise ValueError("record_id must be positive")

    stmt = (
        select(RecordReproducibilityAssessment)
        .where(
            RecordReproducibilityAssessment.record_type == resolved_record_type,
            RecordReproducibilityAssessment.record_id == record_id,
        )
        .order_by(
            RecordReproducibilityAssessment.assessed_at.desc(),
            RecordReproducibilityAssessment.id.desc(),
        )
        .limit(1)
    )
    return session.scalar(stmt)
