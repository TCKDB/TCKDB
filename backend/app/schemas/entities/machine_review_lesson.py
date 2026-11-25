"""Pydantic schemas for the machine-review lesson routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.db.models.common import (
    MachineReviewLessonSignal,
    MachineReviewLessonStatus,
    SubmissionRecordType,
)
from app.services.machine_review.schemas import MachineReviewCategory

#: Longest lesson text, in characters.
LESSON_TEXT_MAX_CHARS = 500

_ScopeWord = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class LessonScope(BaseModel):
    """What a lesson is about, beyond category and record type; every key is optional."""

    model_config = ConfigDict(extra="forbid")

    software: _ScopeWord | None = None
    method_family: _ScopeWord | None = None
    check_code: _ScopeWord | None = None

    def as_json(self) -> dict[str, str] | None:
        data = {key: value for key, value in self.model_dump().items() if value is not None}
        return data or None


class MachineReviewLessonCreateRequest(BaseModel):
    """A curator's note on what the machine reviewer got wrong."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=LESSON_TEXT_MAX_CHARS)
    category: MachineReviewCategory
    signal: MachineReviewLessonSignal = MachineReviewLessonSignal.curator_flagged_missed_issue
    record_type: SubmissionRecordType | None = None
    scope: LessonScope | None = None
    source_job_id: int | None = Field(default=None, ge=1, le=2**63 - 1)


class MachineReviewLessonUpdate(BaseModel):
    """Fields to change on a lesson; an omitted field stays as it is."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str | None = Field(default=None, min_length=1, max_length=LESSON_TEXT_MAX_CHARS)
    category: MachineReviewCategory | None = None
    signal: MachineReviewLessonSignal | None = None
    record_type: SubmissionRecordType | None = None
    scope: LessonScope | None = None

    @model_validator(mode="after")
    def _needs_a_field(self) -> "MachineReviewLessonUpdate":
        if not self.model_fields_set:
            raise ValueError("Send at least one field to change.")
        if ("text" in self.model_fields_set and self.text is None) or (
            "signal" in self.model_fields_set and self.signal is None
        ):
            raise ValueError("text and signal cannot be null.")
        return self


class MachineReviewLessonRead(BaseModel):
    """One lesson as an admin or its author sees it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: MachineReviewLessonStatus
    signal: MachineReviewLessonSignal
    category: MachineReviewCategory | None
    record_type: SubmissionRecordType | None
    scope_json: dict[str, Any] | None
    text: str
    created_by: int
    created_by_username: str | None = None
    created_at: datetime
    updated_at: datetime
    verified_by: int | None
    verified_at: datetime | None
    retired_by: int | None
    retired_at: datetime | None
