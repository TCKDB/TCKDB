"""Pydantic schemas for the machine-review job routes.

Reads carry codes and timings only: never a transcript, a failure detail, a
budget, a context hash, token counts or lease state.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models.common import (
    MachineReviewAvailability,
    MachineReviewExecutor,
    MachineReviewJobEventKind,
    MachineReviewJobFailure,
    MachineReviewJobStatus,
    MachineReviewProviderKey,
)


class MachineReviewJobCreateRequest(BaseModel):
    """Ask for a machine review of one submission."""

    model_config = ConfigDict(extra="forbid")

    submission_id: int = Field(ge=1, le=2**63 - 1)
    provider_key: MachineReviewProviderKey


class MachineReviewJobEventRead(BaseModel):
    """One numbered progress entry."""

    model_config = ConfigDict(from_attributes=True)

    seq: int
    kind: MachineReviewJobEventKind
    tool_name: str | None
    status_code: str | None
    started_at: datetime | None
    duration_ms: int | None


class MachineReviewJobRead(BaseModel):
    """The lifecycle of one machine-review attempt."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    submission_id: int
    requested_by: int
    requested_by_username: str | None = None
    provider_key: MachineReviewProviderKey
    executor: MachineReviewExecutor
    status: MachineReviewJobStatus
    cancel_requested: bool
    failure_code: MachineReviewJobFailure | None
    step_count: int
    model: str | None
    prompt_name: str | None
    prompt_version: int | None
    audit_event_id: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MachineReviewJobDetailRead(MachineReviewJobRead):
    """A job with its progress entries after the requested ``seq``."""

    events: list[MachineReviewJobEventRead]


class MachineReviewProviderRead(BaseModel):
    """One reviewer backend and whether the caller may use it now."""

    provider_key: MachineReviewProviderKey
    availability: MachineReviewAvailability


class MachineReviewProvidersRead(BaseModel):
    """Every reviewer backend with the caller's availability."""

    providers: list[MachineReviewProviderRead]


class MachineReviewSettingsRead(BaseModel):
    """The two stored switches and what they allow with the deployment mode."""

    machine_review_enabled: bool
    curator_requests_enabled: bool
    assistant_mode: str
    review_allowed: bool
    curator_allowed: bool


class MachineReviewSettingsUpdate(BaseModel):
    """Switches to change; an omitted field stays as it is."""

    model_config = ConfigDict(extra="forbid")

    machine_review_enabled: bool | None = None
    curator_requests_enabled: bool | None = None

    @model_validator(mode="after")
    def _needs_a_field(self) -> "MachineReviewSettingsUpdate":
        if self.machine_review_enabled is None and self.curator_requests_enabled is None:
            raise ValueError("Send at least one of machine_review_enabled and curator_requests_enabled.")
        return self
