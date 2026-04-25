"""Statmech read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.common import (
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
)
from app.db.models.statmech import Statmech, StatmechTorsion
from app.schemas.entities.statmech import StatmechRead

router = APIRouter()


@router.get("", response_model=PaginatedResponse[StatmechRead])
def list_statmech(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    species_entry_id: int | None = Query(None),
    scientific_origin: ScientificOriginKind | None = Query(None),
    software_release_id: int | None = Query(None),
    workflow_tool_release_id: int | None = Query(None),
    literature_id: int | None = Query(None),
    statmech_treatment: StatmechTreatmentKind | None = Query(None),
    rigid_rotor_kind: RigidRotorKind | None = Query(None),
):
    base = select(Statmech.id)
    if species_entry_id is not None:
        base = base.where(Statmech.species_entry_id == species_entry_id)
    if scientific_origin is not None:
        base = base.where(Statmech.scientific_origin == scientific_origin)
    if software_release_id is not None:
        base = base.where(Statmech.software_release_id == software_release_id)
    if workflow_tool_release_id is not None:
        base = base.where(
            Statmech.workflow_tool_release_id == workflow_tool_release_id
        )
    if literature_id is not None:
        base = base.where(Statmech.literature_id == literature_id)
    if statmech_treatment is not None:
        base = base.where(Statmech.statmech_treatment == statmech_treatment)
    if rigid_rotor_kind is not None:
        base = base.where(Statmech.rigid_rotor_kind == rigid_rotor_kind)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(Statmech)
        .where(Statmech.id.in_(base))
        .options(
            selectinload(Statmech.source_calculations),
            selectinload(Statmech.torsions).selectinload(
                StatmechTorsion.coordinates
            ),
        )
        .order_by(Statmech.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[StatmechRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{statmech_id}", response_model=StatmechRead)
def get_statmech(statmech_id: int, session: Session = Depends(get_db)):
    row = session.scalar(
        select(Statmech)
        .where(Statmech.id == statmech_id)
        .options(
            selectinload(Statmech.source_calculations),
            selectinload(Statmech.torsions).selectinload(
                StatmechTorsion.coordinates
            ),
        )
    )
    if row is None:
        raise NotFoundError(f"Statmech {statmech_id} not found")
    return StatmechRead.model_validate(row)
