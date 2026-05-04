"""Transition state read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.entities.transition_state import (
    TransitionStateEntryRead,
    TransitionStateRead,
)

router = APIRouter()


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
