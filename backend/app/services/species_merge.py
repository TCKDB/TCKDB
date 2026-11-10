"""Merged-away species, species entries and reactions: who is live, and where a merged row forwards (DR-0038).

When two rows turn out to be one thing (``C/C=C/C`` and ``CC=CC`` are one species; two entries that name one
configuration are one entry) the row that loses is **kept**: its public ref still resolves, and the reviews, machine
reviews, release selections and supersession edges recorded about it stay on it. Its ``merged_into_*`` column names the
survivor, which holds the results. A survivor is never itself merged, so one hop is always enough (a trigger refuses a
chain), the same rule ``level_of_theory_merge`` keeps.

Three things follow, and this module is where each lives:

* every lookup that resolves an *identity* (an upload, a search for one species) considers **live rows only**
  (:func:`live`), because the unique keys apply to live rows only;
* a handle that names a merged row resolves to its survivor (:func:`canonical_species`, ...), and a read says so;
* nothing new is written onto a merged row (the database refuses it; see revision ``c7e2a9f4d138``).
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.db.models.reaction import ChemReaction
from app.db.models.species import Species, SpeciesEntry

#: Model class -> the column that names a merged row's survivor.
MERGE_COLUMNS = {
    Species: "merged_into_species_id",
    SpeciesEntry: "merged_into_species_entry_id",
    ChemReaction: "merged_into_chem_reaction_id",
}


def live(model: type) -> ColumnElement[bool]:
    """``WHERE`` clause keeping only rows that were not merged into another."""

    return getattr(model, MERGE_COLUMNS[model]).is_(None)


def canonical_id(session: Session, model: type, row_id: int) -> int:
    """The row a row stands for: itself, or the row it was merged into (one hop)."""

    column = getattr(model, MERGE_COLUMNS[model])
    into = session.scalar(select(column).where(model.id == row_id))
    return into if into is not None else row_id


def canonical_species(session: Session, species: Species) -> Species:
    into = species.merged_into_species_id
    return species if into is None else session.get(Species, into)


def canonical_species_entry(session: Session, entry: SpeciesEntry) -> SpeciesEntry:
    into = entry.merged_into_species_entry_id
    return entry if into is None else session.get(SpeciesEntry, into)


def canonical_chem_reaction(session: Session, reaction: ChemReaction) -> ChemReaction:
    into = reaction.merged_into_chem_reaction_id
    return reaction if into is None else session.get(ChemReaction, into)
