"""Transport read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.common import ScientificOriginKind
from app.db.models.transport import Transport
from app.schemas.entities.transport import TransportRead

router = APIRouter()


@router.get("", response_model=PaginatedResponse[TransportRead])
def list_transport(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    species_entry_id: int | None = Query(None),
    scientific_origin: ScientificOriginKind | None = Query(None),
    software_release_id: int | None = Query(None),
    workflow_tool_release_id: int | None = Query(None),
    literature_id: int | None = Query(None),
):
    base = select(Transport.id)
    if species_entry_id is not None:
        base = base.where(Transport.species_entry_id == species_entry_id)
    if scientific_origin is not None:
        base = base.where(Transport.scientific_origin == scientific_origin)
    if software_release_id is not None:
        base = base.where(Transport.software_release_id == software_release_id)
    if workflow_tool_release_id is not None:
        base = base.where(
            Transport.workflow_tool_release_id == workflow_tool_release_id
        )
    if literature_id is not None:
        base = base.where(Transport.literature_id == literature_id)

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        select(Transport)
        .where(Transport.id.in_(base))
        .options(selectinload(Transport.source_calculations))
        .order_by(Transport.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()

    return PaginatedResponse(
        items=[TransportRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{transport_id}", response_model=TransportRead)
def get_transport(transport_id: int, session: Session = Depends(get_db)):
    row = session.scalar(
        select(Transport)
        .where(Transport.id == transport_id)
        .options(selectinload(Transport.source_calculations))
    )
    if row is None:
        raise NotFoundError(f"Transport {transport_id} not found")
    return TransportRead.model_validate(row)
