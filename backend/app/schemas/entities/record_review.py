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
    #: The handle the archive is addressed by, resolved at read time.
    #:
    #: ``record_id`` alone names a row and nothing a reader can use: it
    #: cannot be pasted into a URL, quoted in an issue, or looked up
    #: through any public route. A review queue whose whole job is "go and
    #: look at this record" is unusable without this field, which is why
    #: it is resolved here rather than left to the client.
    #:
    #: ``None`` for either of the two reasons a record cannot be named:
    #: its table has no ``public_ref`` column
    #: (``applied_energy_correction``), or the row is gone. The same
    #: contract ``AdminCuratorTaskResponse.record_public_ref`` carries.
    #:
    #: The ``description`` is not a duplicate of the above: these ``#:``
    #: comments reach a reader of this file, and the description is what
    #: reaches a reader of the published OpenAPI, who has neither this
    #: source nor any other statement of when the field is null.
    record_public_ref: str | None = Field(
        default=None,
        description=(
            "The public handle naming this record, resolved at read time. "
            "Null when the record cannot be named: its table has no "
            "public_ref column (applied_energy_correction), or the row no "
            "longer exists. Prefer this over record_id, which is an "
            "internal row id and resolves through no public route."
        ),
    )
    status: RecordReviewStatus
    submission_id: int | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    first_approved_at: datetime | None = None
    note: str | None = None


class RecordReviewSetStatusRequest(SchemaBase):
    """Payload for a curator manually setting a record's review status."""

    status: RecordReviewStatus
    submission_id: int | None = None
    note: str | None = Field(default=None)
