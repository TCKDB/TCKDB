"""Read schema for accepted-science replacement history."""

from datetime import datetime

from pydantic import Field

from app.db.models.common import SubmissionRecordType
from app.schemas.common import ORMBaseSchema, SchemaBase


class ScientificRecordSupersessionRequest(SchemaBase):
    """Curator request to replace one accepted record with another."""

    record_type: SubmissionRecordType
    superseded_record_id: int = Field(gt=0)
    superseding_record_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)


class ScientificRecordSupersessionRead(ORMBaseSchema):
    """One immutable scientific-record replacement edge."""

    id: int
    record_type: SubmissionRecordType
    superseded_record_id: int
    superseding_record_id: int
    reason: str
    created_by: int
    created_at: datetime
