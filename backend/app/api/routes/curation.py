"""Curator operations for reproducibility and accepted-record replacement."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import PaginationParams, get_db, get_write_db, require_curator_or_admin
from app.db.models.app_user import AppUser
from app.db.models.common import StructureFindingScope, SubmissionRecordType
from app.schemas.entities.reproducibility_assessment import ReproducibilityAssessmentRead
from app.schemas.entities.scientific_record_supersession import (
    ScientificRecordSupersessionRead,
    ScientificRecordSupersessionRequest,
)
from app.schemas.entities.structure_evidence_finding import (
    StructureEvidenceFindingCreate,
    StructureEvidenceFindingPageRead,
    StructureEvidenceFindingRead,
)
from app.services.reproducibility_assessment import get_latest_reproducibility_assessment
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility
from app.services.scientific_record_supersession import supersede_scientific_record
from app.services.structure_evidence_finding import (
    append_structure_evidence_finding,
    list_findings_for_subject,
    present_findings,
)

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
    session: Session = Depends(get_write_db, scope="function"),
    _actor: AppUser = Depends(require_curator_or_admin),
) -> ReproducibilityAssessmentRead:
    """Derive and append the current system-owned assessment."""
    row = evaluate_and_append_reproducibility(
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
    session: Session = Depends(get_write_db, scope="function"),
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


@router.post(
    "/structure-evidence-findings",
    response_model=StructureEvidenceFindingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def create_structure_evidence_finding(
    body: StructureEvidenceFindingCreate,
    session: Session = Depends(get_write_db, scope="function"),
    actor: AppUser = Depends(require_curator_or_admin),
) -> StructureEvidenceFindingRead:
    """Append an authorized adjudication about a geometry, calculation or structure determination.

    Append-only: a finding is never edited or deleted, and a correction is a later adjudication naming the
    earlier finding. The authority is fixed by this route's role gate and the author is the caller.
    """
    finding = append_structure_evidence_finding(
        session,
        actor=actor,
        scope=body.scope,
        subject_ref=body.subject_ref,
        kind=body.kind,
        verdict=body.verdict,
        rationale=body.rationale,
        role=body.role,
        source_calculation_ref=body.source_calculation_ref,
        literature_ref=body.literature_ref,
        supersedes_finding_ref=body.supersedes_finding_ref,
    )
    (row,) = present_findings(session, [finding], body.subject_ref)
    return StructureEvidenceFindingRead.model_validate(row)


@router.get(
    "/structure-evidence-findings",
    response_model=StructureEvidenceFindingPageRead,
)
def list_structure_evidence_findings(
    subject_ref: str = Query(..., min_length=1, max_length=64),
    scope: StructureFindingScope | None = Query(default=None),
    pagination: PaginationParams = Depends(),
    session: Session = Depends(get_db),
    _actor: AppUser = Depends(require_curator_or_admin),
) -> StructureEvidenceFindingPageRead:
    """Curator/admin: every finding about one subject, oldest first, with whether each is still in force."""
    _scope, rows = list_findings_for_subject(
        session, subject_ref=subject_ref, scope=scope, offset=pagination.skip, limit=pagination.limit
    )
    return StructureEvidenceFindingPageRead(
        subject_ref=subject_ref,
        findings=[StructureEvidenceFindingRead.model_validate(r) for r in rows],
        offset=pagination.skip,
        limit=pagination.limit,
    )
