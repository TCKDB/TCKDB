"""Entity schemas for workflow tool provenance models.

Covers: WorkflowTool (stable identity) and WorkflowToolRelease
(exact code state provenance).
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import SchemaBase, TimestampedReadSchema
from app.schemas.utils import normalize_optional_text, normalize_required_text


# ---------------------------------------------------------------------------
# Workflow tool
# ---------------------------------------------------------------------------


class WorkflowToolBase(BaseModel):
    """Shared fields for a workflow tool.

    :param name: Workflow tool name.
    :param description: Optional description.
    """

    name: str = Field(min_length=1)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return normalize_required_text(value)


class WorkflowToolCreate(WorkflowToolBase, SchemaBase):
    """Create schema for a workflow tool."""


class WorkflowToolUpdate(SchemaBase):
    """Patch schema for a workflow tool."""

    name: str | None = Field(default=None, min_length=1)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_required_text(value)


class WorkflowToolRead(WorkflowToolBase, TimestampedReadSchema):
    """Read schema for a workflow tool."""


# ---------------------------------------------------------------------------
# Workflow tool release
# ---------------------------------------------------------------------------


class WorkflowToolReleaseBase(BaseModel):
    """Shared fields for a workflow tool release.

    :param workflow_tool_id: Owning workflow-tool id.
    :param version: Optional version string.
    :param git_commit: Optional 40-character git commit SHA.
    :param release_date: Optional release date.
    :param notes: Optional release notes.
    """

    workflow_tool_id: int
    version: str | None = None
    git_commit: str | None = Field(
        default=None, min_length=40, max_length=40
    )
    release_date: date | None = None
    notes: str | None = None

    @field_validator("version")
    @classmethod
    def normalize_version(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


class WorkflowToolReleaseCreate(WorkflowToolReleaseBase, SchemaBase):
    """Create schema for a workflow tool release."""


class WorkflowToolReleaseUpdate(SchemaBase):
    """Patch schema for a workflow tool release."""

    version: str | None = None
    git_commit: str | None = Field(
        default=None, min_length=40, max_length=40
    )
    release_date: date | None = None
    notes: str | None = None

    @field_validator("version")
    @classmethod
    def normalize_version(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


class WorkflowToolReleaseRead(WorkflowToolReleaseBase, TimestampedReadSchema):
    """Read schema for a workflow tool release."""


# ---------------------------------------------------------------------------
# Nested read shapes (tool detail + release with tool summary)
# ---------------------------------------------------------------------------


class WorkflowToolSummary(BaseModel):
    """Compact workflow tool view used when nested under a release."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None


class WorkflowToolReleaseSummary(WorkflowToolReleaseBase, TimestampedReadSchema):
    """Compact release view used when nested under a tool detail.

    Field-wise identical to :class:`WorkflowToolReleaseRead`; named separately
    so the nested shape can evolve independently of the top-level read.
    """


class WorkflowToolDetailRead(WorkflowToolBase, TimestampedReadSchema):
    """Detail view for a workflow tool, including its releases."""

    releases: list[WorkflowToolReleaseSummary] = Field(default_factory=list)


class WorkflowToolReleaseDetailRead(WorkflowToolReleaseBase, TimestampedReadSchema):
    """Release view that embeds a parent workflow tool summary."""

    workflow_tool: WorkflowToolSummary
