"""Job queue endpoints — enqueue any upload type, poll for status.

POST /api/v1/jobs/computed-reaction
POST /api/v1/jobs/conformer
POST /api/v1/jobs/reaction
POST /api/v1/jobs/kinetics
POST /api/v1/jobs/network
POST /api/v1/jobs/network/pdep
POST /api/v1/jobs/thermo
POST /api/v1/jobs/transition-state
POST /api/v1/jobs/transport

GET  /api/v1/jobs/{job_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import get_current_user, get_db, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.common import UploadJobKind, UploadJobStatus
from app.db.models.upload_job import UploadJob
from app.schemas.jobs import JobEnqueueResponse, JobStatusResponse
from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.schemas.workflows.network_upload import NetworkUploadRequest
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transition_state_upload import TransitionStateUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadRequest

router = APIRouter()

# Per-route dependency wired onto every enqueue (POST) endpoint. The
# status GET is intentionally left open so a client running an old
# version can still poll a previously-enqueued job while the user
# upgrades.
_enqueue_deps = [Depends(require_supported_tckdb_client)]


def _enqueue(session: Session, kind: UploadJobKind, request, user_id: int) -> JobEnqueueResponse:
    """Insert a job row and return the enqueue response."""
    job = UploadJob(
        kind=kind,
        status=UploadJobStatus.queued,
        payload=request.model_dump(mode="json"),
        created_by=user_id,
    )
    session.add(job)
    session.flush()
    return JobEnqueueResponse(job_id=str(job.id), status=job.status, kind=job.kind)


# ---------------------------------------------------------------------------
# Enqueue endpoints
# ---------------------------------------------------------------------------

@router.post("/computed-reaction", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_computed_reaction(
    request: ComputedReactionUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.computed_reaction, request, current_user.id)


@router.post("/conformer", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_conformer(
    request: ConformerUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.conformer, request, current_user.id)


@router.post("/reaction", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_reaction(
    request: ReactionUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.reaction, request, current_user.id)


@router.post("/kinetics", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_kinetics(
    request: KineticsUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.kinetics, request, current_user.id)


@router.post("/network", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_network(
    request: NetworkUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.network, request, current_user.id)


@router.post("/network/pdep", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_network_pdep(
    request: NetworkPDepUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.network_pdep, request, current_user.id)


@router.post("/thermo", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_thermo(
    request: ThermoUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.thermo, request, current_user.id)


@router.post("/transition-state", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_transition_state(
    request: TransitionStateUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.transition_state, request, current_user.id)


@router.post("/transport", response_model=JobEnqueueResponse, status_code=202, dependencies=_enqueue_deps)
def enqueue_transport(
    request: TransportUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
):
    return _enqueue(session, UploadJobKind.transport, request, current_user.id)


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------

@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(
    job_id: str,
    session: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
):
    job = session.get(UploadJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return JobStatusResponse.from_orm_row(job)
