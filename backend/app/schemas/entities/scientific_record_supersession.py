"""Read schema for accepted-science replacement history."""

from datetime import datetime

from app.db.models.common import SubmissionRecordType
from app.schemas.common import SchemaBase


class ScientificRecordSupersessionRead(SchemaBase):
    """One immutable scientific-record replacement edge."""

    id: int
    record_type: SubmissionRecordType
    superseded_record_id: int
    superseding_record_id: int
    reason: str
    created_by: int
    created_at: datetime
