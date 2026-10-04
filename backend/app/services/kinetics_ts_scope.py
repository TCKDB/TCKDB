"""Does a transition state belong to a rate's reaction entry?

One rule, used wherever a record cites a transition state on behalf of a rate: the
interpretation and tunneling evidence of a kinetics upload (``app.workflows.kinetics``) and a
determination's channel target (``app.services.kinetics_declaration_resolution``). It lives
in a service so that the two cannot drift, and so that a service need not import a workflow.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.db.models.transition_state import TransitionState, TransitionStateEntry

#: How a rate's transition state must relate to the reaction entry the rate is
#: stored under. ``"entry"``: it is one of that entry's own transition states
#: (the standalone route, where the entry is derived from the TS). ``"reaction"``:
#: it belongs to *some* entry of the same graph reaction (the reaction bundle,
#: which always mints a new reaction entry, so it can never be the entry of a
#: transition state deposited earlier).
TransitionStateScope = Literal["entry", "reaction"]


def transition_state_belongs_to_rate(
    session: Session,
    ts_entry_id: int,
    reaction_entry: ReactionEntry,
    scope: TransitionStateScope,
) -> bool:
    ts_reaction_entry = session.scalar(
        select(ReactionEntry)
        .join(TransitionState, TransitionState.reaction_entry_id == ReactionEntry.id)
        .join(
            TransitionStateEntry,
            TransitionStateEntry.transition_state_id == TransitionState.id,
        )
        .where(TransitionStateEntry.id == ts_entry_id)
    )
    if ts_reaction_entry is None:
        return False
    if scope == "entry":
        return ts_reaction_entry.id == reaction_entry.id
    if ts_reaction_entry.reaction_id != reaction_entry.reaction_id:
        return False
    # Same graph reaction is not enough: an excited-state or isotopologue entry
    # of the same reaction has a different TS. Require the same structure
    # participants ((role, species_entry_id) multiset; species entries are
    # content-deduplicated, so ids compare soundly), allowing the two sides to
    # swap for a reverse-direction fit.
    def structures(entry_id: int) -> Counter:
        rows = session.execute(
            select(
                ReactionEntryStructureParticipant.role,
                ReactionEntryStructureParticipant.species_entry_id,
            ).where(ReactionEntryStructureParticipant.reaction_entry_id == entry_id)
        ).all()
        return Counter((role.value if hasattr(role, "value") else role, sid) for role, sid in rows)

    theirs = structures(ts_reaction_entry.id)
    ours = structures(reaction_entry.id)
    swapped = Counter(
        ({"reactant": "product", "product": "reactant"}[role], sid) for role, sid in ours.elements()
    )
    return theirs == ours or theirs == swapped
