from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.db.models.common import UploadJobKind, UploadJobStatus


class JobEnqueueResponse(BaseModel):
    """Returned immediately when a job is queued."""

    job_id: str
    status: UploadJobStatus
    kind: UploadJobKind


class JobStatusResponse(BaseModel):
    """Full status snapshot for a job — returned by the GET endpoint."""

    model_config = {"from_attributes": True}

    job_id: str
    status: UploadJobStatus
    kind: UploadJobKind
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempts: int
    result: dict | None = None
    error: str | None = None

    @classmethod
    def from_orm_row(cls, row) -> "JobStatusResponse":
        return cls(
            job_id=str(row.id),
            status=row.status,
            kind=row.kind,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
            attempts=row.attempts,
            result=row.result,
            error=row.error,
        )
