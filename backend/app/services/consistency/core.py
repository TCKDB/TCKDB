"""Input custody, findings and isolated persistence for advisory comparisons."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from math import isfinite

from sqlalchemy import inspect

from app.services.machine_review.context_hash import MachineReviewEvidenceContext, build_machine_review_context_hash
from app.services.machine_review.derivation import MachineReviewOutcome, derive_machine_review_status
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.query import (
    SCIENTIFIC_CHECK_PROVIDER,
    MachineReviewRecordFamily,
    get_latest_record_machine_review_row,
    get_record_machine_review_currency_for_record,
)
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, public_rubric_name
from app.services.machine_review.schemas import MachineReviewCategory, MachineReviewFinding, MachineReviewSeverity


def canonical(value):
    if isinstance(value, float) and not isfinite(value):
        return {"nonfinite_float": repr(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    return value


def encoded(value):
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def snapshot(row, relationships=()):
    """Capture stored inputs, excluding clocks and actor metadata; lists are sets of rows.

    Relationships are explicitly bounded by the caller, never recursively discovered.
    JSON array ordering is preserved because it may encode coefficients or raw evidence.
    """
    if row is None:
        return None
    result = {}
    for column in inspect(type(row)).columns:
        value = getattr(row, column.key)
        if column.key in {"created_at", "updated_at", "retrieved_at", "created_by", "created_by_id"}:
            continue
        if isinstance(value, (date, datetime)):
            continue
        result[column.key] = canonical(value)
    for name in relationships:
        value = getattr(row, name)
        result[name] = sorted((snapshot(v) for v in value), key=encoded) if isinstance(value, list) else snapshot(value)
    return result


def thermo_inputs(thermo):
    return snapshot(thermo, ("nasa", "nasa9_intervals", "points", "wilhoit", "source_calculations",
                            "literature", "software_release", "workflow_tool_release"))


def temperatures(values):
    grid = tuple(sorted({float(t) for t in values}))
    if any(not isfinite(t) or t <= 0 for t in grid):
        raise ValueError("temperatures must be finite and positive")
    return grid


def finding(target, payload, refs=()):
    return MachineReviewFinding(
        severity=MachineReviewSeverity.info, category=MachineReviewCategory.consistency,
        record_type=target.__tablename__, record_ref=target.public_ref,
        message=encoded(payload), evidence_keys=tuple(sorted(set(refs))),
    )


@dataclass(frozen=True)
class AdvisoryResult:
    target: object
    runner: str
    rubric: object
    findings: tuple
    inputs_json: str

    @property
    def digest(self):
        return build_machine_review_context_hash(MachineReviewEvidenceContext(
            record_type=self.target.__tablename__, record_ref=self.target.public_ref,
            rubric_name=self.rubric.name, rubric_version=self.rubric.version,
            notes=(self.inputs_json,),
        ))

    @property
    def recipe(self):
        key = public_rubric_name(self.rubric)
        return {key: ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS[key]}


def record_result(session, result):
    """Append exactly one review; transaction ownership stays with the caller."""
    target = result.target
    review = RecordMachineReview(
        record_type=target.__tablename__, record_ref=target.public_ref, record_id=target.id,
        findings=result.findings,
        status=derive_machine_review_status(result.findings, MachineReviewOutcome.completed),
        model=result.runner, provider=SCIENTIFIC_CHECK_PROVIDER,
        reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    return create_record_machine_review_row(
        session, record_type=target.__tablename__, record_id=target.id, review=review,
        context_digest=result.digest, prompt_version=result.runner, rubric_versions=result.recipe,
    )


def latest_recorded(session, result):
    """Latest recorded is not a claim of currency against live inputs."""
    return get_latest_record_machine_review_row(
        session, record_type=result.target.__tablename__, record_id=result.target.id,
        family=MachineReviewRecordFamily.scientific_check, model=result.runner,
    )


def currency(session, live_result):
    """Caller supplies a freshly resolved comparison, never a historical snapshot."""
    return get_record_machine_review_currency_for_record(
        session, record_type=live_result.target.__tablename__, record_id=live_result.target.id,
        current_context=live_result.digest, active_prompt_version=live_result.runner,
        active_rubric_versions=live_result.recipe,
        family=MachineReviewRecordFamily.scientific_check, model=live_result.runner,
    )
