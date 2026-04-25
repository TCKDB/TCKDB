"""Thermo read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.db.models.common import ScientificOriginKind
from app.db.models.thermo import Thermo
from app.schemas.entities.thermo import ThermoRead
from app.api.routes._pagination import PaginatedResponse

router = APIRouter()


@router.get("", response_model=PaginatedResponse[ThermoRead])
def list_thermo(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    species_entry_id: int | None = Query(None),
    scientific_origin: ScientificOriginKind | None = Query(None),
    software_release_id: int | None = Query(None),
    literature_id: int | None = Query(None),
):
    base = select(Thermo.id)
    if species_entry_id is not None:
        base = base.where(Thermo.species_entry_id == species_entry_id)
    if scientific_origin is not None:
        base = base.where(Thermo.scientific_origin == scientific_origin)
    if software_release_id is not None:
        base = base.where(Thermo.software_release_id == software_release_id)
    if literature_id is not None:
        base = base.where(Thermo.literature_id == literature_id)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(Thermo)
        .where(Thermo.id.in_(base))
        .order_by(Thermo.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[ThermoRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{thermo_id}", response_model=ThermoRead)
def get_thermo(thermo_id: int, session: Session = Depends(get_db)):
    thermo = session.get(Thermo, thermo_id)
    if thermo is None:
        raise NotFoundError(f"Thermo {thermo_id} not found")
    return ThermoRead.model_validate(thermo)
