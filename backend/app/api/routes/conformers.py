"""Conformer group and observation read endpoints, plus selection writes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.client_version import require_supported_tckdb_client
from app.api.deps import (
    PaginationParams,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.api.errors import NotFoundError
from app.api.routes._legacy_reads import (
    EntityListResponse,
    Gate,
    LegacyVisibility,
    entity_page,
    legacy_route_class,
)
from app.db.models.app_user import AppUser
from app.db.models.common import ConformerSelectionKind, ScientificOriginKind, SubmissionRecordType
from app.db.models.species import ConformerGroup, ConformerObservation
from app.schemas.entities.conformer import (
    ConformerGroupDetailRead,
    ConformerGroupRead,
    ConformerObservationRead,
    ConformerSelectionRead,
)
from app.services.selection import create_conformer_selection

groups_router = APIRouter(
    route_class=legacy_route_class(
        {
            "group_id": Gate(SubmissionRecordType.conformer_group, "ConformerGroup"),
        }
    )
)
#: The write route of the conformer groups path: no read profile applies to it.
groups_write_router = APIRouter()
observations_router = APIRouter(
    route_class=legacy_route_class(
        {
            "observation_id": Gate(SubmissionRecordType.conformer_observation, "ConformerObservation"),
        }
    )
)


class ConformerSelectionCreateBody(BaseModel):
    """Request body for creating a conformer selection (path carries group id)."""

    model_config = ConfigDict(extra="forbid")

    selection_kind: ConformerSelectionKind
    assignment_scheme_id: int | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Conformer groups
# ---------------------------------------------------------------------------


@groups_router.get("", response_model=EntityListResponse[ConformerGroupRead])
def list_conformer_groups(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    visibility: LegacyVisibility = Depends(),
    species_entry_id: int | None = Query(None),
    label: str | None = Query(None),
):
    base = select(ConformerGroup.id)
    if species_entry_id is not None:
        base = base.where(ConformerGroup.species_entry_id == species_entry_id)
    if label is not None:
        base = base.where(ConformerGroup.label == label)

    hidden = visibility.hidden(session, SubmissionRecordType.conformer_group, base)
    base = visibility.restrict(base, ConformerGroup.id, SubmissionRecordType.conformer_group)

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
    return entity_page(
        [ConformerGroupRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
        hidden=hidden,
    )


@groups_router.get("/{group_id}", response_model=ConformerGroupDetailRead)
def get_conformer_group(
    group_id: int, session: Session = Depends(get_db), visibility: LegacyVisibility = Depends()
):
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
        raise NotFoundError("ConformerGroup not found")
    # The observations the request shows (the default exclusions and the profile's floor); the count equals the list.
    shown = set(
        session.scalars(
            visibility.restrict(
                select(ConformerObservation.id).where(ConformerObservation.conformer_group_id == group_id),
                ConformerObservation.id,
                SubmissionRecordType.conformer_observation,
            )
        )
    )
    observations = [obs for obs in row.observations if obs.id in shown]
    return ConformerGroupDetailRead(
        **ConformerGroupRead.model_validate(row).model_dump(),
        observation_count=len(observations),
        observations=[ConformerObservationRead.model_validate(obs) for obs in observations],
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
        raise NotFoundError("ConformerGroup not found")
    return [ConformerSelectionRead.model_validate(s) for s in group.selections]


@groups_write_router.post(
    "/{conformer_group_id}/selections",
    response_model=ConformerSelectionRead,
    status_code=201,
    dependencies=[Depends(require_supported_tckdb_client)],
)
def create_conformer_group_selection(
    conformer_group_id: int,
    body: ConformerSelectionCreateBody,
    session: Session = Depends(get_write_db, scope="function"),
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
    "", response_model=EntityListResponse[ConformerObservationRead]
)
def list_conformer_observations(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    visibility: LegacyVisibility = Depends(),
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

    hidden = visibility.hidden_child(session, SubmissionRecordType.conformer_observation, base)
    base = visibility.restrict(base, ConformerObservation.id, SubmissionRecordType.conformer_observation)
    # An observation is shown only with its group, under the same visibility (PARENT_OF).
    base = visibility.restrict(base, ConformerObservation.conformer_group_id, SubmissionRecordType.conformer_group)

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
    return entity_page(
        [ConformerObservationRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
        hidden=hidden,
    )


@observations_router.get(
    "/{observation_id}", response_model=ConformerObservationRead
)
def get_conformer_observation(
    observation_id: int, session: Session = Depends(get_db)
):
    row = session.get(ConformerObservation, observation_id)
    if row is None:
        raise NotFoundError("ConformerObservation not found")
    return ConformerObservationRead.model_validate(row)
