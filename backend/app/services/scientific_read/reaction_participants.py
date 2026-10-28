"""One definition of a reaction entry's participant rows and equation string, shared by every read surface.

A deposit stores one structure-participant row per OCCURRENCE (``H + H2 + H -> 2 H2`` has three reactant rows).
Every surface that serves those rows (search, browse, ``/full``, the review queue) or an equation built from them
(``/full``'s ``reaction_entry.equation``, the transition-state read, search) must agree for the same entry, so the two
rules live here and nowhere else:

* :func:`collapse_occurrences` -- one row per distinct species entry per side, in order of the position it was first
  written at, carrying how many times it occurs and that first position;
* :func:`format_reaction_equation` -- the equation as written: every occurrence, in participant order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.db.models.common import ReactionRole


def collapse_occurrences(
    rows: Iterable[tuple[int, ReactionRole, int]],
    role: ReactionRole,
) -> list[tuple[int, int, int]]:
    """Collapse ``(species_entry_id, role, participant_index)`` rows of one side.

    :returns: ``(species_entry_id, occurrence_count, first_participant_index)`` per distinct entry, ordered by
        ``first_participant_index`` (the first position the entry was written at).
    """
    counts: dict[int, int] = {}
    first_index: dict[int, int] = {}
    for entry_id, row_role, index in sorted(rows, key=lambda r: (r[2], r[0])):
        if row_role != role:
            continue
        counts[entry_id] = counts.get(entry_id, 0) + 1
        first_index.setdefault(entry_id, index)
    return sorted(
        ((entry_id, count, first_index[entry_id]) for entry_id, count in counts.items()),
        key=lambda item: item[2],
    )


def format_reaction_equation(
    reactants: Sequence[tuple[int, str]],
    products: Sequence[tuple[int, str]],
    reversible: bool,
) -> str:
    """``"SMI + SMI <=> SMI"`` as written: ``(participant_index, smiles)`` per occurrence, in participant order."""
    arrow = "<=>" if reversible else "->"
    left = " + ".join(smiles for _, smiles in sorted(reactants, key=lambda r: r[0]))
    right = " + ".join(smiles for _, smiles in sorted(products, key=lambda r: r[0]))
    return f"{left} {arrow} {right}"
