"""Service helpers for creating curation-layer selection rows.

Covers the conformer-group and transition-state selection surfaces in the
schema. Each create helper validates existence, enforces parent-child
consistency, and applies the same-kind uniqueness rules already declared on the
ORM.
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
    selection_kind: TransitionStateSelectionKind,
    note: str | None = None,
    created_by: int | None = None,
) -> TransitionStateSelection:
    """Create a selection row for a transition state.

    Validates that the parent transition state exists and rejects same-kind
    duplicates for that transition state before relying on the database
    uniqueness constraint. Unlike conformer selection there is no
    assignment-scheme dimension.

    :param session: Active SQLAlchemy session.
    :param transition_state_id: Owning transition state id (from URL path).
    :param selection_kind: The selection role (e.g. ``lowest_barrier``).
    :param note: Optional free-text note.
    :param created_by: Optional application user id for provenance.
    :returns: The persisted ``TransitionStateSelection`` row.
    :raises NotFoundError: If the transition state does not exist.
    :raises DomainError: If a selection of the same kind already exists for
        the transition state.
    """
    transition_state = session.get(TransitionState, transition_state_id)
    if transition_state is None:
        raise NotFoundError(
            f"TransitionState {transition_state_id} not found"
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
        selection_kind=selection_kind,
        note=note,
        created_by=created_by,
    )
    session.add(selection)
    session.flush()
    return selection
