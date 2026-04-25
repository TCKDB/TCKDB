"""Transition state read endpoints, plus selection writes."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    PaginationParams,
    get_db,
    get_write_db,
    require_curator_or_admin,
)
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.app_user import AppUser
from app.db.models.common import TransitionStateSelectionKind
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.entities.transition_state import (
    TransitionStateEntryRead,
    TransitionStateRead,
    TransitionStateSelectionRead,
)
from app.services.selection import create_transition_state_selection

router = APIRouter()


class TransitionStateSelectionCreateBody(BaseModel):
    """Request body for creating a TS selection (path carries the TS id)."""

    model_config = ConfigDict(extra="forbid")

    transition_state_entry_id: int
    selection_kind: TransitionStateSelectionKind
    note: str | None = None


@router.get("", response_model=PaginatedResponse[TransitionStateRead])
def list_transition_states(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    reaction_entry_id: int | None = Query(None),
    label: str | None = Query(None),
):
    base = select(TransitionState.id)
    if reaction_entry_id is not None:
        base = base.where(TransitionState.reaction_entry_id == reaction_entry_id)
    if label is not None:
        base = base.where(TransitionState.label == label)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(TransitionState)
        .where(TransitionState.id.in_(base))
        .order_by(TransitionState.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[TransitionStateRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{ts_id}", response_model=TransitionStateRead)
def get_transition_state(ts_id: int, session: Session = Depends(get_db)):
    ts = session.get(TransitionState, ts_id)
    if ts is None:
        raise NotFoundError(f"TransitionState {ts_id} not found")
    return TransitionStateRead.model_validate(ts)


@router.get(
    "/entries/{entry_id}",
    response_model=TransitionStateEntryRead,
)
def get_transition_state_entry(
    entry_id: int, session: Session = Depends(get_db)
):
    entry = session.get(TransitionStateEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"TransitionStateEntry {entry_id} not found")
    return TransitionStateEntryRead.model_validate(entry)


@router.post(
    "/{transition_state_id}/selections",
    response_model=TransitionStateSelectionRead,
    status_code=201,
)
def create_transition_state_selection_endpoint(
    transition_state_id: int,
    body: TransitionStateSelectionCreateBody,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(require_curator_or_admin),
):
    """Create a curation-layer selection row under a transition-state concept.

    Restricted to ``curator`` and ``admin`` app-user roles via
    :func:`require_curator_or_admin`. Authenticated ``user``-role callers
    receive 403; unauthenticated requests still get 401 upstream.
    """
    selection = create_transition_state_selection(
        session,
        transition_state_id=transition_state_id,
        transition_state_entry_id=body.transition_state_entry_id,
        selection_kind=body.selection_kind,
        note=body.note,
        created_by=current_user.id,
    )
    return TransitionStateSelectionRead.model_validate(selection)
