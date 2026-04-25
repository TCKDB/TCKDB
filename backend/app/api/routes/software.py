"""Software and software-release read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.software import Software, SoftwareRelease
from app.schemas.entities.software import SoftwareRead, SoftwareReleaseRead

router = APIRouter()
releases_router = APIRouter()


# ---------------------------------------------------------------------------
# Software
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[SoftwareRead])
def list_software(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    name: str | None = Query(None),
):
    base = select(Software.id)
    if name is not None:
        base = base.where(Software.name == name)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(Software)
        .where(Software.id.in_(base))
        .order_by(Software.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[SoftwareRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{software_id}", response_model=SoftwareRead)
def get_software(software_id: int, session: Session = Depends(get_db)):
    row = session.get(Software, software_id)
    if row is None:
        raise NotFoundError(f"Software {software_id} not found")
    return SoftwareRead.model_validate(row)


# ---------------------------------------------------------------------------
# Software releases (mounted under /software-releases in router.py)
# ---------------------------------------------------------------------------


@releases_router.get("", response_model=PaginatedResponse[SoftwareReleaseRead])
def list_software_releases(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    software_id: int | None = Query(None),
    version: str | None = Query(None),
    revision: str | None = Query(None),
    build: str | None = Query(None),
):
    base = select(SoftwareRelease.id)
    if software_id is not None:
        base = base.where(SoftwareRelease.software_id == software_id)
    if version is not None:
        base = base.where(SoftwareRelease.version == version)
    if revision is not None:
        base = base.where(SoftwareRelease.revision == revision)
    if build is not None:
        base = base.where(SoftwareRelease.build == build)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(SoftwareRelease)
        .where(SoftwareRelease.id.in_(base))
        .order_by(SoftwareRelease.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[SoftwareReleaseRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@releases_router.get("/{release_id}", response_model=SoftwareReleaseRead)
def get_software_release(release_id: int, session: Session = Depends(get_db)):
    row = session.get(SoftwareRelease, release_id)
    if row is None:
        raise NotFoundError(f"SoftwareRelease {release_id} not found")
    return SoftwareReleaseRead.model_validate(row)
