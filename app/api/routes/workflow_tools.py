"""Workflow-tool and workflow-tool-release read endpoints.

Read-only provenance views.  Workflow tools and releases are created
implicitly during upload flows via resolution services; these endpoints
only expose the persisted rows for inspection.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.entities.workflow import (
    WorkflowToolDetailRead,
    WorkflowToolRead,
    WorkflowToolReleaseDetailRead,
)

router = APIRouter()
releases_router = APIRouter()


def _release_ordering():
    """Deterministic newest-first ordering for releases.

    release_date is nullable, so nulls sort last; id DESC is the stable
    tie-break (and also the fallback when no release_date is populated).
    """
    return (
        WorkflowToolRelease.release_date.desc().nulls_last(),
        WorkflowToolRelease.id.desc(),
    )


# ---------------------------------------------------------------------------
# Workflow tools
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[WorkflowToolRead])
def list_workflow_tools(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    name: str | None = Query(None, description="Case-insensitive partial match"),
):
    base = select(WorkflowTool.id)
    if name is not None:
        base = base.where(WorkflowTool.name.ilike(f"%{name}%"))

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(WorkflowTool)
        .where(WorkflowTool.id.in_(base))
        .order_by(WorkflowTool.name.asc(), WorkflowTool.id.asc())
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[WorkflowToolRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{workflow_tool_id}", response_model=WorkflowToolDetailRead)
def get_workflow_tool(
    workflow_tool_id: int,
    session: Session = Depends(get_db),
):
    tool = session.get(WorkflowTool, workflow_tool_id)
    if tool is None:
        raise NotFoundError(f"WorkflowTool {workflow_tool_id} not found")

    releases = session.scalars(
        select(WorkflowToolRelease)
        .where(WorkflowToolRelease.workflow_tool_id == workflow_tool_id)
        .order_by(*_release_ordering())
    ).all()

    return WorkflowToolDetailRead.model_validate(
        {
            "id": tool.id,
            "name": tool.name,
            "description": tool.description,
            "created_at": tool.created_at,
            "releases": list(releases),
        }
    )


# ---------------------------------------------------------------------------
# Workflow tool releases (mounted under /workflow-tool-releases in router.py)
# ---------------------------------------------------------------------------


@releases_router.get(
    "", response_model=PaginatedResponse[WorkflowToolReleaseDetailRead]
)
def list_workflow_tool_releases(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    workflow_tool_id: int | None = Query(None),
    tool_name: str | None = Query(
        None, description="Case-insensitive partial match on parent tool name"
    ),
    version: str | None = Query(None, description="Case-insensitive partial match"),
    git_commit: str | None = Query(None, description="Exact match"),
):
    base = select(WorkflowToolRelease.id)
    if workflow_tool_id is not None:
        base = base.where(WorkflowToolRelease.workflow_tool_id == workflow_tool_id)
    if tool_name is not None:
        base = base.join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        ).where(WorkflowTool.name.ilike(f"%{tool_name}%"))
    if version is not None:
        base = base.where(WorkflowToolRelease.version.ilike(f"%{version}%"))
    if git_commit is not None:
        base = base.where(WorkflowToolRelease.git_commit == git_commit)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(WorkflowToolRelease)
        .where(WorkflowToolRelease.id.in_(base))
        .options(selectinload(WorkflowToolRelease.workflow_tool))
        .order_by(*_release_ordering())
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[WorkflowToolReleaseDetailRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@releases_router.get(
    "/{workflow_tool_release_id}",
    response_model=WorkflowToolReleaseDetailRead,
)
def get_workflow_tool_release(
    workflow_tool_release_id: int,
    session: Session = Depends(get_db),
):
    row = session.scalar(
        select(WorkflowToolRelease)
        .where(WorkflowToolRelease.id == workflow_tool_release_id)
        .options(selectinload(WorkflowToolRelease.workflow_tool))
    )
    if row is None:
        raise NotFoundError(
            f"WorkflowToolRelease {workflow_tool_release_id} not found"
        )
    return WorkflowToolReleaseDetailRead.model_validate(row)
