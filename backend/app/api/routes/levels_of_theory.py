"""Level of theory read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.level_of_theory import LevelOfTheory, LevelOfTheoryMerge
from app.schemas.entities.level_of_theory import LevelOfTheoryRead
from app.services.scientific_read.handles import canonical_level_of_theory_id
from app.services.scientific_read.lot_identity_filters import (
    basis_matches,
    dispersion_matches,
    method_matches,
    solvent_matches,
)

router = APIRouter()


@router.get("", response_model=PaginatedResponse[LevelOfTheoryRead])
def list_levels_of_theory(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    dispersion: str | None = Query(None),
    solvent: str | None = Query(None),
    lot_hash: str | None = Query(None),
):
    # A merged row (#574) is another spelling of a listed row; it is not a
    # level of theory of its own.
    base = select(LevelOfTheory.id).where(
        LevelOfTheory.id.not_in(select(LevelOfTheoryMerge.merged_lot_id))
    )
    if method is not None:
        base = base.where(method_matches(method))
    if basis is not None:
        base = base.where(basis_matches(basis))
    if dispersion is not None:
        base = base.where(dispersion_matches(dispersion))
    if solvent is not None:
        base = base.where(solvent_matches(solvent))
    if lot_hash is not None:
        base = base.where(LevelOfTheory.lot_hash == lot_hash)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(LevelOfTheory)
        .where(LevelOfTheory.id.in_(base))
        .order_by(LevelOfTheory.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[LevelOfTheoryRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{lot_id}", response_model=LevelOfTheoryRead)
def get_level_of_theory(lot_id: int, session: Session = Depends(get_db)):
    # A merged row's id names the row it was merged into (#591), as its
    # ``lot_...`` ref does on the scientific routes.
    row = session.get(LevelOfTheory, canonical_level_of_theory_id(session, lot_id))
    if row is None:
        raise NotFoundError("LevelOfTheory not found")
    return LevelOfTheoryRead.model_validate(row)
