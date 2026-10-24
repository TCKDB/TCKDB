"""Kinetics read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._legacy_reads import (
    EntityListResponse,
    Gate,
    LegacyVisibility,
    entity_page,
    legacy_route_class,
)
from app.db.models.common import KineticsModelKind, ScientificOriginKind, SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.schemas.entities.kinetics import KineticsRead

router = APIRouter(
    route_class=legacy_route_class(
        {
        "kinetics_id": Gate(SubmissionRecordType.kinetics, "Kinetics"),
        }
    )
)


@router.get("", response_model=EntityListResponse[KineticsRead])
def list_kinetics(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    visibility: LegacyVisibility = Depends(),
    reaction_entry_id: int | None = Query(None),
    scientific_origin: ScientificOriginKind | None = Query(None),
    model_kind: KineticsModelKind | None = Query(None),
    software_release_id: int | None = Query(None),
    literature_id: int | None = Query(None),
):
    base = select(Kinetics.id)
    if reaction_entry_id is not None:
        base = base.where(Kinetics.reaction_entry_id == reaction_entry_id)
    if scientific_origin is not None:
        base = base.where(Kinetics.scientific_origin == scientific_origin)
    if model_kind is not None:
        base = base.where(Kinetics.model_kind == model_kind)
    if software_release_id is not None:
        base = base.where(Kinetics.software_release_id == software_release_id)
    if literature_id is not None:
        base = base.where(Kinetics.literature_id == literature_id)

    hidden = visibility.hidden(session, SubmissionRecordType.kinetics, base)
    base = visibility.restrict(base, Kinetics.id, SubmissionRecordType.kinetics)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(Kinetics)
        .where(Kinetics.id.in_(base))
        .order_by(Kinetics.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return entity_page(
        [KineticsRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
        hidden=hidden,
    )


@router.get("/{kinetics_id}", response_model=KineticsRead)
def get_kinetics(kinetics_id: int, session: Session = Depends(get_db)):
    kinetics = session.get(Kinetics, kinetics_id)
    if kinetics is None:
        raise NotFoundError("Kinetics not found")
    return KineticsRead.model_validate(kinetics)
