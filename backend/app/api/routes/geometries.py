"""Geometry read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.geometry import Geometry
from app.schemas.entities.geometry import GeometryRead

router = APIRouter()


@router.get("", response_model=PaginatedResponse[GeometryRead])
def list_geometries(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    geom_hash: str | None = Query(None),
    natoms: int | None = Query(None, ge=1),
):
    base = select(Geometry.id)
    if geom_hash is not None:
        base = base.where(Geometry.geom_hash == geom_hash)
    if natoms is not None:
        base = base.where(Geometry.natoms == natoms)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(Geometry)
        .where(Geometry.id.in_(base))
        .options(selectinload(Geometry.atoms))
        .order_by(Geometry.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[GeometryRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{geometry_id}", response_model=GeometryRead)
def get_geometry(geometry_id: int, session: Session = Depends(get_db)):
    row = session.scalar(
        select(Geometry)
        .where(Geometry.id == geometry_id)
        .options(selectinload(Geometry.atoms))
    )
    if row is None:
        raise NotFoundError(f"Geometry {geometry_id} not found")
    return GeometryRead.model_validate(row)
