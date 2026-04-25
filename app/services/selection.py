"""Service helpers for creating curation-layer selection rows.

Covers the two selection surfaces in the schema:
- ``ConformerSelection`` — group/scheme-scoped selection for a conformer group.
- ``TransitionStateSelection`` — points at a specific candidate entry under
  a transition-state concept.

Each create helper validates existence, enforces parent-child consistency,
and applies the same-kind uniqueness rules already declared on the ORM.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError, NotFoundError
from app.db.models.common import (
    ConformerSelectionKind,
    TransitionStateSelectionKind,
)
from app.db.models.species import (
    ConformerAssignmentScheme,
    ConformerGroup,
    ConformerSelection,
)
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
    TransitionStateSelection,
)


def create_conformer_selection(
    session: Session,
    *,
    conformer_group_id: int,
    selection_kind: ConformerSelectionKind,
    assignment_scheme_id: int | None = None,
    note: str | None = None,
    created_by: int | None = None,
) -> ConformerSelection:
    """Create a selection row for a conformer group.

    Validates that the parent group exists (and the assignment scheme, when
    supplied) and rejects same-kind duplicates under the same scheme before
    relying on the database uniqueness constraint.

    :param session: Active SQLAlchemy session.
    :param conformer_group_id: Owning conformer group id (from URL path).
    :param selection_kind: The selection role (e.g. ``display_default``).
    :param assignment_scheme_id: Optional scheme that produced the selection.
    :param note: Optional free-text note.
    :param created_by: Optional application user id for provenance.
    :returns: The persisted ``ConformerSelection`` row.
    :raises NotFoundError: If the group or named scheme does not exist.
    :raises DomainError: If a selection of the same kind already exists for
        the ``(group, scheme)`` pair.
    """
    group = session.get(ConformerGroup, conformer_group_id)
    if group is None:
        raise NotFoundError(
            f"ConformerGroup {conformer_group_id} not found"
        )

    if assignment_scheme_id is not None:
        scheme = session.get(ConformerAssignmentScheme, assignment_scheme_id)
        if scheme is None:
            raise NotFoundError(
                f"ConformerAssignmentScheme {assignment_scheme_id} not found"
            )

    scheme_clause = (
        ConformerSelection.assignment_scheme_id.is_(None)
        if assignment_scheme_id is None
        else ConformerSelection.assignment_scheme_id == assignment_scheme_id
    )
    existing = session.scalar(
        select(ConformerSelection).where(
            ConformerSelection.conformer_group_id == conformer_group_id,
            scheme_clause,
            ConformerSelection.selection_kind == selection_kind,
        )
    )
    if existing is not None:
        raise DomainError(
            f"A '{selection_kind.value}' selection already exists for "
            f"conformer group {conformer_group_id} under the specified "
            "assignment scheme"
        )

    selection = ConformerSelection(
        conformer_group_id=conformer_group_id,
        assignment_scheme_id=assignment_scheme_id,
        selection_kind=selection_kind,
        note=note,
        created_by=created_by,
    )
    session.add(selection)
    session.flush()
    return selection


def create_transition_state_selection(
    session: Session,
    *,
    transition_state_id: int,
    transition_state_entry_id: int,
    selection_kind: TransitionStateSelectionKind,
    note: str | None = None,
    created_by: int | None = None,
) -> TransitionStateSelection:
    """Create a selection row for a transition-state concept.

    Validates that both the parent TS and the target entry exist, that the
    entry belongs to the parent TS, and that no same-kind selection already
    exists for the parent.

    :param session: Active SQLAlchemy session.
    :param transition_state_id: Owning TS concept id (from URL path).
    :param transition_state_entry_id: The entry being selected.
    :param selection_kind: The selection role (e.g. ``validated_reference``).
    :param note: Optional free-text note.
    :param created_by: Optional application user id for provenance.
    :returns: The persisted ``TransitionStateSelection`` row.
    :raises NotFoundError: If the TS or entry does not exist.
    :raises DomainError: If the entry belongs to a different TS, or if a
        selection of the same kind already exists for the parent.
    """
    ts = session.get(TransitionState, transition_state_id)
    if ts is None:
        raise NotFoundError(
            f"TransitionState {transition_state_id} not found"
        )

    entry = session.get(TransitionStateEntry, transition_state_entry_id)
    if entry is None:
        raise NotFoundError(
            f"TransitionStateEntry {transition_state_entry_id} not found"
        )

    if entry.transition_state_id != transition_state_id:
        raise DomainError(
            f"TransitionStateEntry {transition_state_entry_id} does not "
            f"belong to TransitionState {transition_state_id}"
        )

    existing = session.scalar(
        select(TransitionStateSelection).where(
            TransitionStateSelection.transition_state_id == transition_state_id,
            TransitionStateSelection.selection_kind == selection_kind,
        )
    )
    if existing is not None:
        raise DomainError(
            f"A '{selection_kind.value}' selection already exists for "
            f"transition state {transition_state_id}"
        )

    selection = TransitionStateSelection(
        transition_state_id=transition_state_id,
        transition_state_entry_id=transition_state_entry_id,
        selection_kind=selection_kind,
        note=note,
        created_by=created_by,
    )
    session.add(selection)
    session.flush()
    return selection
