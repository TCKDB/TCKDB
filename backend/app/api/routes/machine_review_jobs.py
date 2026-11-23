"""Request, poll, list and cancel machine reviews, and the admin switches that gate them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.client_version import require_supported_tckdb_client
from app.api.config import settings
from app.api.deps import get_db, get_write_db, require_admin, require_curator_or_admin
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.api.server_faults import ServerFaultRoute
from app.db.models.app_user import AppUser
from app.db.models.machine_review_job import MachineReviewJob
from app.db.models.submission import Submission
from app.schemas.entities.machine_review_job import (
    MachineReviewJobCreateRequest,
    MachineReviewJobDetailRead,
    MachineReviewJobEventRead,
    MachineReviewJobRead,
    MachineReviewProviderRead,
    MachineReviewProvidersRead,
    MachineReviewSettingsRead,
    MachineReviewSettingsUpdate,
)
from app.services.machine_review import jobs
from app.services.machine_review.switches import read_machine_review_switches, write_machine_review_switches

router = APIRouter(route_class=ServerFaultRoute)
admin_router = APIRouter(route_class=ServerFaultRoute)

_JOB_ID = Path(ge=1, le=2**63 - 1)


def _usernames(session: Session, rows: list[MachineReviewJob]) -> dict[int, str]:
    ids = {row.requested_by for row in rows}
    if not ids:
        return {}
    return dict(session.execute(select(AppUser.id, AppUser.username).where(AppUser.id.in_(ids))).all())


def _read(row: MachineReviewJob, usernames: dict[int, str]) -> MachineReviewJobRead:
    out = MachineReviewJobRead.model_validate(row)
    out.requested_by_username = usernames.get(row.requested_by)
    return out


@router.post(
    "/jobs",
    response_model=MachineReviewJobRead,
    status_code=202,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def request_job(
    body: MachineReviewJobCreateRequest,
    session: Session = Depends(get_write_db, scope="function"),
    user: AppUser = Depends(require_curator_or_admin),
) -> MachineReviewJobRead:
    """Queue a machine review of a submission (curator/admin).

    Refused with a coded 403/409 when the caller, the switches, the deployment
    mode or the provider key do not allow it, and with 409
    ``machine_review_job_conflict`` when the submission already has an open job.
    """
    job = jobs.create_job(session, submission_id=body.submission_id, requester=user, provider_key=body.provider_key)
    return _read(job, {user.id: user.username})


@router.get("/jobs", response_model=PaginatedResponse[MachineReviewJobRead])
def list_submission_jobs(
    submission_id: int = Query(ge=1, le=2**63 - 1),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=50),
    session: Session = Depends(get_db),
    _user: AppUser = Depends(require_curator_or_admin),
) -> PaginatedResponse[MachineReviewJobRead]:
    """A submission's machine-review jobs, newest first, so an open job is the first item."""
    if session.get(Submission, submission_id) is None:
        raise NotFoundError("Submission not found.")
    rows, total = jobs.list_jobs(session, submission_id, offset=offset, limit=limit)
    names = _usernames(session, rows)
    return PaginatedResponse(items=[_read(r, names) for r in rows], total=total, skip=offset, limit=limit)


@router.get("/jobs/{job_id}", response_model=MachineReviewJobDetailRead)
def get_job_progress(
    job_id: int = _JOB_ID,
    after_seq: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
    _user: AppUser = Depends(require_curator_or_admin),
) -> MachineReviewJobDetailRead:
    """One job with its progress entries whose ``seq`` is greater than ``after_seq``."""
    job = jobs.get_job(session, job_id)
    base = _read(job, _usernames(session, [job]))
    events = [MachineReviewJobEventRead.model_validate(e) for e in jobs.list_events(session, job.id, after_seq=after_seq)]
    return MachineReviewJobDetailRead(**base.model_dump(), events=events)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=MachineReviewJobRead,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def cancel_job(
    job_id: int = _JOB_ID,
    session: Session = Depends(get_write_db, scope="function"),
    _user: AppUser = Depends(require_curator_or_admin),
) -> MachineReviewJobRead:
    """Cancel a queued job now, or ask a running one to stop at its next step; 409 once it has ended."""
    job = jobs.request_cancel(session, job_id)
    return _read(job, _usernames(session, [job]))


@router.get("/providers", response_model=MachineReviewProvidersRead)
def list_providers(
    session: Session = Depends(get_db),
    user: AppUser = Depends(require_curator_or_admin),
) -> MachineReviewProvidersRead:
    """Every reviewer backend with whether the caller may request it now."""
    return MachineReviewProvidersRead(
        providers=[
            MachineReviewProviderRead(provider_key=key, availability=availability)
            for key, availability in jobs.list_provider_availability(session, user).items()
        ]
    )


def _settings_read(session: Session) -> MachineReviewSettingsRead:
    switches = read_machine_review_switches(session)
    return MachineReviewSettingsRead(
        machine_review_enabled=switches.machine_review_enabled,
        curator_requests_enabled=switches.curator_requests_enabled,
        assistant_mode=settings.ai_review_assistant_mode,
        review_allowed=switches.review_allowed,
        curator_allowed=switches.curator_allowed,
    )


@admin_router.get("/settings", response_model=MachineReviewSettingsRead)
def get_settings(
    session: Session = Depends(get_db),
    _admin: AppUser = Depends(require_admin),
) -> MachineReviewSettingsRead:
    """The machine-review switches and what they allow under the deployment mode (admin only)."""
    return _settings_read(session)


@admin_router.patch("/settings", response_model=MachineReviewSettingsRead)
def update_settings(
    body: MachineReviewSettingsUpdate,
    session: Session = Depends(get_write_db, scope="function"),
    admin: AppUser = Depends(require_admin),
) -> MachineReviewSettingsRead:
    """Change the switches that are sent and leave the others as they are (admin only)."""
    write_machine_review_switches(
        session,
        actor=admin,
        machine_review_enabled=body.machine_review_enabled,
        curator_requests_enabled=body.curator_requests_enabled,
    )
    return _settings_read(session)
