"""Pydantic schemas for the record-review (per-record trust state) API."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.common import (
    SchemaBase,
    TimestampedCreatedByReadSchema,
)


class RecordReviewRead(TimestampedCreatedByReadSchema):
    """Read schema for one ``record_review`` row."""

    record_type: SubmissionRecordType
    record_id: int
    status: RecordReviewStatus
    submission_id: int | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    note: str | None = None


class RecordReviewSetStatusRequest(SchemaBase):
    """Payload for a curator manually setting a record's review status."""

    status: RecordReviewStatus
    submission_id: int | None = None
    note: str | None = Field(default=None)
