"""Shared builders for the machine-review job tests."""

from __future__ import annotations

from app.api.config import Settings
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    MachineReviewExecutor,
    MachineReviewJobStatus,
    MachineReviewProviderKey,
    SubmissionKind,
)
from app.db.models.machine_review_job import MachineReviewJob
from app.services.submission import create_submission

FAKE_SETTINGS = Settings(
    ai_review_assistant_mode="test",
    deployment_mode="local",
    llm_precheck_fake_step_delay_seconds=0.0,
)
OFF_SETTINGS = Settings(ai_review_assistant_mode="off")
CLOUD_SETTINGS = Settings(ai_review_assistant_mode="cloud", deployment_mode="local")


def make_user(session, username: str, role: AppUserRole) -> AppUser:
    user = AppUser(username=username, role=role)
    session.add(user)
    session.flush()
    return user


def make_submission(session, user_id: int):
    submission = create_submission(
        session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title="Machine-review job test",
        summary="Compact submission summary",
    )
    session.flush()
    return submission


def insert_job(
    session,
    submission_id: int,
    user_id: int,
    *,
    status: MachineReviewJobStatus = MachineReviewJobStatus.queued,
    **fields,
) -> MachineReviewJob:
    job = MachineReviewJob(
        submission_id=submission_id,
        requested_by=user_id,
        provider_key=fields.pop("provider_key", MachineReviewProviderKey.fake),
        executor=fields.pop("executor", MachineReviewExecutor.in_process),
        status=status,
        **fields,
    )
    session.add(job)
    session.flush()
    return job
