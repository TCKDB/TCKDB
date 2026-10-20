"""The compact submission context a machine-review provider reviews over."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMRecordRef(BaseModel):
    """Compact reference to a submission-linked record."""

    model_config = ConfigDict(frozen=True)

    record_type: str
    record_id: int
    role: str | None = None


class LLMPrecheckContext(BaseModel):
    """Compact structured context sent to an optional LLM precheck provider."""

    model_config = ConfigDict(frozen=True)

    submission_id: int
    submission_status: str | None = None
    submission_kind: str | None = None
    source_kind: str | None = None
    title: str | None = None
    summary: str | None = None
    record_refs: tuple[LLMRecordRef, ...] = Field(default_factory=tuple)
    trust_summaries: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    included_artifact_text: bool = False
    included_coordinates: bool = False
    included_private_notes: bool = False
