"""Tests for app/schemas/entities/workflow.py."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.entities.workflow import (
    WorkflowToolCreate,
    WorkflowToolRead,
    WorkflowToolReleaseCreate,
    WorkflowToolReleaseRead,
    WorkflowToolUpdate,
)


# ---------------------------------------------------------------------------
# WorkflowTool
# ---------------------------------------------------------------------------


class TestWorkflowToolCreate:
    def test_valid(self) -> None:
        wt = WorkflowToolCreate(name="ARC", description="Automated Rate Calculator")
        assert wt.name == "ARC"

    def test_normalizes_whitespace(self) -> None:
        wt = WorkflowToolCreate(name="  ARC  ")
        assert wt.name == "ARC"

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowToolCreate(name="")


class TestWorkflowToolUpdate:
    def test_normalizes_name(self) -> None:
        update = WorkflowToolUpdate(name="  RMG  ")
        assert update.name == "RMG"

    def test_allows_none_name(self) -> None:
        update = WorkflowToolUpdate()
        assert update.name is None


class TestWorkflowToolRead:
    def test_from_orm(self) -> None:
        wt = SimpleNamespace(
            id=1, name="ARC", description=None,
            created_at="2024-01-01T00:00:00",
        )
        read = WorkflowToolRead.model_validate(wt)
        assert read.id == 1
        assert read.name == "ARC"


# ---------------------------------------------------------------------------
# WorkflowToolRelease
# ---------------------------------------------------------------------------


class TestWorkflowToolReleaseCreate:
    def test_valid(self) -> None:
        wtr = WorkflowToolReleaseCreate(
            workflow_tool_id=1,
            version="1.2.0",
            git_commit="a" * 40,
        )
        assert wtr.version == "1.2.0"
        assert len(wtr.git_commit) == 40

    def test_normalizes_version_whitespace(self) -> None:
        wtr = WorkflowToolReleaseCreate(
            workflow_tool_id=1, version="  1.2.0  ",
        )
        assert wtr.version == "1.2.0"

    def test_rejects_short_git_commit(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowToolReleaseCreate(
                workflow_tool_id=1, git_commit="abc123",
            )

    def test_rejects_long_git_commit(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowToolReleaseCreate(
                workflow_tool_id=1, git_commit="a" * 41,
            )

    def test_allows_minimal(self) -> None:
        wtr = WorkflowToolReleaseCreate(workflow_tool_id=1)
        assert wtr.version is None
        assert wtr.git_commit is None


class TestWorkflowToolReleaseRead:
    def test_from_orm(self) -> None:
        wtr = SimpleNamespace(
            id=1, workflow_tool_id=1,
            version="1.2.0", git_commit="a" * 40,
            release_date=None, notes=None,
            created_at="2024-01-01T00:00:00",
        )
        read = WorkflowToolReleaseRead.model_validate(wtr)
        assert read.id == 1
        assert read.workflow_tool_id == 1
