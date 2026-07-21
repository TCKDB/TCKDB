"""Schemas for append-only scientific reproducibility assessments."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from app.db.models.common import (
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    SubmissionRecordType,
)
from app.schemas.common import ORMBaseSchema, SchemaBase
from app.schemas.utils import normalize_required_text


class ReproducibilityAssessmentAppend(SchemaBase):
    """Validated internal input for appending one assessor claim."""

    record_type: SubmissionRecordType
    record_id: int = Field(gt=0)
    grade: ReproducibilityGrade
    rubric_name: str = Field(min_length=1, max_length=128)
    rubric_version: str = Field(min_length=1, max_length=64)
    context_json: dict[str, Any]
    expected_context_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    passed: list[Any] = Field(default_factory=list)
    missing: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    assessor_kind: ReproducibilityAssessorKind
    assessor_user_id: int | None = Field(default=None, gt=0)
    source_submission_id: int | None = Field(default=None, gt=0)
    assessed_at: datetime | None = None

    @field_validator("rubric_name", "rubric_version")
    @classmethod
    def normalize_rubric_text(cls, value: str) -> str:
        """Strip rubric identifiers and reject whitespace-only values."""
        return normalize_required_text(value)

    @model_validator(mode="after")
    def validate_assessor_identity(self) -> Self:
        """Require a user for curator assessments and forbid one for system runs."""
        if self.assessor_kind is ReproducibilityAssessorKind.curator and self.assessor_user_id is None:
            raise ValueError("curator assessments require assessor_user_id")
        if self.assessor_kind is ReproducibilityAssessorKind.system and self.assessor_user_id is not None:
            raise ValueError("system assessments must not set assessor_user_id")
        return self


class ReproducibilityAssessmentRead(ORMBaseSchema):
    """Read projection for one immutable assessment row."""

    id: int
    record_type: SubmissionRecordType
    record_id: int
    grade: ReproducibilityGrade
    rubric_name: str
    rubric_version: str
    context_hash: str
    context_json: dict[str, Any]
    passed: list[Any] = Field(validation_alias="passed_json")
    missing: list[Any] = Field(validation_alias="missing_json")
    warnings: list[Any] = Field(validation_alias="warnings_json")
    assessor_kind: ReproducibilityAssessorKind
    assessor_user_id: int | None
    source_submission_id: int | None
    assessed_at: datetime
    created_at: datetime
