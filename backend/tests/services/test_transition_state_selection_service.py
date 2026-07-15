"""Tests for ``create_transition_state_selection``.

These pin the contract of the transition-state curation-overlay selection
service (schema-audit ref R6), the transition-state analog of
``create_conformer_selection``. Each test uses the per-test rollback
transaction in ``conftest.py`` and direct ORM inserts; no upload pipeline is
exercised. Unlike conformer selection there is no assignment-scheme dimension.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import DomainError, NotFoundError
from app.db.models.common import TransitionStateSelectionKind
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateSelection,
)
from app.services.selection import create_transition_state_selection


def _make_transition_state(db_session: Session) -> TransitionState:
    reaction = ChemReaction(reversible=False)
    db_session.add(reaction)
    db_session.flush()
    entry = ReactionEntry(reaction_id=reaction.id)
    db_session.add(entry)
    db_session.flush()
    ts = TransitionState(reaction_entry_id=entry.id)
    db_session.add(ts)
    db_session.flush()
    return ts


def test_create_selection_persists_row(db_session: Session) -> None:
    ts = _make_transition_state(db_session)

    selection = create_transition_state_selection(
        db_session,
        transition_state_id=ts.id,
        selection_kind=TransitionStateSelectionKind.lowest_barrier,
        note="lowest barrier candidate",
        created_by=None,
    )

    assert selection.id is not None
    persisted = db_session.get(TransitionStateSelection, selection.id)
    assert persisted is not None
    assert persisted.transition_state_id == ts.id
    assert persisted.selection_kind == TransitionStateSelectionKind.lowest_barrier
    assert persisted.note == "lowest barrier candidate"
    assert persisted.created_by is None


def test_different_kinds_coexist(db_session: Session) -> None:
    ts = _make_transition_state(db_session)

    create_transition_state_selection(
        db_session,
        transition_state_id=ts.id,
        selection_kind=TransitionStateSelectionKind.lowest_barrier,
    )
    create_transition_state_selection(
        db_session,
        transition_state_id=ts.id,
        selection_kind=TransitionStateSelectionKind.curator_pick,
    )

    rows = db_session.scalars(
        select(TransitionStateSelection).where(
            TransitionStateSelection.transition_state_id == ts.id
        )
    ).all()
    assert len(rows) == 2


def test_duplicate_same_kind_raises_domain_error(db_session: Session) -> None:
    ts = _make_transition_state(db_session)

    create_transition_state_selection(
        db_session,
        transition_state_id=ts.id,
        selection_kind=TransitionStateSelectionKind.display_default,
    )

    with pytest.raises(DomainError):
        create_transition_state_selection(
            db_session,
            transition_state_id=ts.id,
            selection_kind=TransitionStateSelectionKind.display_default,
        )


def test_unknown_transition_state_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        create_transition_state_selection(
            db_session,
            transition_state_id=999999,
            selection_kind=TransitionStateSelectionKind.display_default,
        )


def test_unique_constraint_rejects_duplicate_kind(db_session: Session) -> None:
    """The DB unique constraint rejects a same-kind duplicate even if the
    service-layer guard were bypassed."""
    ts = _make_transition_state(db_session)

    db_session.add(
        TransitionStateSelection(
            transition_state_id=ts.id,
            selection_kind=TransitionStateSelectionKind.curator_pick,
        )
    )
    db_session.add(
        TransitionStateSelection(
            transition_state_id=ts.id,
            selection_kind=TransitionStateSelectionKind.curator_pick,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
