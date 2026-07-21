"""Curator operations for reproducibility and accepted-record replacement."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import get_db, get_write_db, require_curator_or_admin
from app.db.models.app_user import AppUser
from app.db.models.common import SubmissionRecordType
from app.schemas.entities.reproducibility_assessment import ReproducibilityAssessmentRead
from app.schemas.entities.scientific_record_supersession import (
    ScientificRecordSupersessionRead,
    ScientificRecordSupersessionRequest,
)
from app.services.reproducibility_assessment import get_latest_reproducibility_assessment
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility_v1
from app.services.scientific_record_supersession import supersede_scientific_record

router = APIRouter()


@router.post(
    "/reproducibility-assessments/{record_type}/{record_id}/evaluate",
    response_model=ReproducibilityAssessmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def evaluate_reproducibility(
    record_type: SubmissionRecordType,
    record_id: int,
    session: Session = Depends(get_write_db),
    _actor: AppUser = Depends(require_curator_or_admin),
) -> ReproducibilityAssessmentRead:
    """Derive and append a system-owned v1 assessment for one target."""
    row = evaluate_and_append_reproducibility_v1(
        session,
        record_type=record_type,
        record_id=record_id,
    )
    return ReproducibilityAssessmentRead.model_validate(row)


@router.get(
    "/reproducibility-assessments/{record_type}/{record_id}/latest",
    response_model=ReproducibilityAssessmentRead,
)
def read_latest_reproducibility_assessment(
    record_type: SubmissionRecordType,
    record_id: int,
    session: Session = Depends(get_db),
    _actor: AppUser = Depends(require_curator_or_admin),
) -> ReproducibilityAssessmentRead:
    """Return the latest immutable assessment for one record."""
    row = get_latest_reproducibility_assessment(
        session,
        record_type=record_type,
        record_id=record_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No reproducibility assessment found for this record.")
    return ReproducibilityAssessmentRead.model_validate(row)


@router.post(
    "/scientific-record-supersessions",
    response_model=ScientificRecordSupersessionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def create_scientific_record_supersession(
    body: ScientificRecordSupersessionRequest,
    session: Session = Depends(get_write_db),
    actor: AppUser = Depends(require_curator_or_admin),
) -> ScientificRecordSupersessionRead:
    """Append a same-subject replacement edge and deprecate the older record."""
    edge = supersede_scientific_record(
        session,
        record_type=body.record_type,
        superseded_record_id=body.superseded_record_id,
        superseding_record_id=body.superseding_record_id,
        actor=actor,
        reason=body.reason,
    )
    return ScientificRecordSupersessionRead.model_validate(edge)
