"""Conformer group and observation read endpoints, plus selection writes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sqlalchemy.orm import selectinload

from app.api.deps import (
    PaginationParams,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.app_user import AppUser
from app.db.models.common import ConformerSelectionKind, ScientificOriginKind
from app.db.models.species import ConformerGroup, ConformerObservation
from app.schemas.entities.conformer import (
    ConformerGroupDetailRead,
    ConformerGroupRead,
    ConformerObservationRead,
    ConformerSelectionRead,
)
from app.services.selection import create_conformer_selection

groups_router = APIRouter()
observations_router = APIRouter()


class ConformerSelectionCreateBody(BaseModel):
    """Request body for creating a conformer selection (path carries group id)."""

    model_config = ConfigDict(extra="forbid")

    selection_kind: ConformerSelectionKind
    assignment_scheme_id: int | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Conformer groups
# ---------------------------------------------------------------------------


@groups_router.get("", response_model=PaginatedResponse[ConformerGroupRead])
def list_conformer_groups(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    species_entry_id: int | None = Query(None),
    label: str | None = Query(None),
):
    base = select(ConformerGroup.id)
    if species_entry_id is not None:
        base = base.where(ConformerGroup.species_entry_id == species_entry_id)
    if label is not None:
        base = base.where(ConformerGroup.label == label)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(ConformerGroup)
        .where(ConformerGroup.id.in_(base))
        .order_by(ConformerGroup.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[ConformerGroupRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@groups_router.get("/{group_id}", response_model=ConformerGroupDetailRead)
def get_conformer_group(group_id: int, session: Session = Depends(get_db)):
    """Return one conformer group (basin) with its nested observations and selections.

    This is the basin-first drill-down read: clients list groups for a species
    entry via `/species-entries/{id}/conformer-groups`, then fetch the detail
    of one basin here to see the supporting observations.
    """
    row = session.scalar(
        select(ConformerGroup)
        .where(ConformerGroup.id == group_id)
        .options(
            selectinload(ConformerGroup.observations),
            selectinload(ConformerGroup.selections),
        )
    )
    if row is None:
        raise NotFoundError(f"ConformerGroup {group_id} not found")
    return ConformerGroupDetailRead(
        **ConformerGroupRead.model_validate(row).model_dump(),
        observation_count=len(row.observations),
        observations=[
            ConformerObservationRead.model_validate(obs) for obs in row.observations
        ],
    )


@groups_router.get(
    "/{group_id}/selections",
    response_model=list[ConformerSelectionRead],
)
def list_conformer_group_selections(
    group_id: int,
    session: Session = Depends(get_db),
):
    """Return all curation-layer selections attached to a conformer group."""
    group = session.get(ConformerGroup, group_id)
    if group is None:
        raise NotFoundError(f"ConformerGroup {group_id} not found")
    return [ConformerSelectionRead.model_validate(s) for s in group.selections]


@groups_router.post(
    "/{conformer_group_id}/selections",
    response_model=ConformerSelectionRead,
    status_code=201,
)
def create_conformer_group_selection(
    conformer_group_id: int,
    body: ConformerSelectionCreateBody,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(require_curator_or_admin),
):
    """Create a curation-layer selection row under a conformer group.

    Restricted to ``curator`` and ``admin`` app-user roles via
    :func:`require_curator_or_admin`. Authenticated ``user``-role callers
    receive 403; unauthenticated requests still get 401 upstream.
    """
    selection = create_conformer_selection(
        session,
        conformer_group_id=conformer_group_id,
        selection_kind=body.selection_kind,
        assignment_scheme_id=body.assignment_scheme_id,
        note=body.note,
        created_by=current_user.id,
    )
    return ConformerSelectionRead.model_validate(selection)


# ---------------------------------------------------------------------------
# Conformer observations (mounted under /conformer-observations in router.py)
# ---------------------------------------------------------------------------


@observations_router.get(
    "", response_model=PaginatedResponse[ConformerObservationRead]
)
def list_conformer_observations(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    conformer_group_id: int | None = Query(None),
    assignment_scheme_id: int | None = Query(None),
    scientific_origin: ScientificOriginKind | None = Query(None),
):
    base = select(ConformerObservation.id)
    if conformer_group_id is not None:
        base = base.where(
            ConformerObservation.conformer_group_id == conformer_group_id
        )
    if assignment_scheme_id is not None:
        base = base.where(
            ConformerObservation.assignment_scheme_id == assignment_scheme_id
        )
    if scientific_origin is not None:
        base = base.where(
            ConformerObservation.scientific_origin == scientific_origin
        )

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(ConformerObservation)
        .where(ConformerObservation.id.in_(base))
        .order_by(ConformerObservation.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[ConformerObservationRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@observations_router.get(
    "/{observation_id}", response_model=ConformerObservationRead
)
def get_conformer_observation(
    observation_id: int, session: Session = Depends(get_db)
):
    row = session.get(ConformerObservation, observation_id)
    if row is None:
        raise NotFoundError(f"ConformerObservation {observation_id} not found")
    return ConformerObservationRead.model_validate(row)
