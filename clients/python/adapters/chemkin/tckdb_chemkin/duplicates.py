"""Import-report warnings for wrong ``DUPLICATE`` flags (spec §6, DR-0036).

CHEMKIN readers disagree about what to do with a model whose ``DUPLICATE``
markers are wrong. Cantera refuses to load it. This module only *reports*: it
never adds or removes a marker and never drops a reaction, so the deposit is
the file as written.

The rule follows Cantera 3.2's ``Kinetics::checkDuplicates``. Reactions are
grouped by their participant set, ignoring side and direction. Within a group:

* **Orphan marker.** A ``DUPLICATE``-marked reaction with no *other* marked
  reaction in its group. Rate type, direction and stoichiometry do not matter
  for this match: an Arrhenius row marked beside a marked Chebyshev row of the
  same participants is accepted, because both are marked; the same Arrhenius
  row marked beside an unmarked Chebyshev row is an orphan.
* **Undeclared repeat.** Two reactions of the same rate type that are the same
  reaction (identical sides, or swapped sides where at least one is reversible)
  and are not both marked.

Every message names CHEMKIN line numbers: the line of each reaction and, for a
marker, the line of the ``DUPLICATE`` keyword.
"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction

from .ast import Reaction


def _rate_type(rxn: Reaction) -> tuple:
    """Cantera's notion of the rate *type*: modified and plain Arrhenius are one type."""
    if rxn.chebyshev is not None:
        return ("chebyshev",)
    if rxn.plog:
        return ("plog",)
    if rxn.is_falloff:
        kind = "troe" if rxn.troe is not None else "sri" if rxn.sri is not None else "lindemann"
        return (kind, rxn.falloff_collider)
    if rxn.is_third_body:
        return ("three_body",)
    return ("arrhenius",)


def _side(pairs: list[tuple[int, str]]) -> tuple:
    merged: dict[str, int] = defaultdict(int)
    for coeff, name in pairs:
        merged[name] += coeff
    return tuple(sorted(merged.items()))


def _participants(rxn: Reaction) -> frozenset:
    return frozenset(n for _, n in rxn.reactants) | frozenset(n for _, n in rxn.products)


def _proportional(x: tuple, y: tuple) -> bool:
    """Whether two (reactants, products) coefficient maps agree up to a common positive factor."""
    dx = dict(x[0]) | {("p", k): v for k, v in x[1]}
    dy = dict(y[0]) | {("p", k): v for k, v in y[1]}
    if set(dx) != set(dy):
        return False
    ratios = {Fraction(dx[k], dy[k]) for k in dx}
    return len(ratios) == 1


def _same_reaction(a: Reaction, b: Reaction) -> bool:
    """Same reaction up to a common positive stoichiometric factor (2H+2O2=2HO2 is H+O2=HO2).

    Swapped sides count where at least one of the two is reversible.
    """
    ar, ap, br, bp = _side(a.reactants), _side(a.products), _side(b.reactants), _side(b.products)
    if _proportional((ar, ap), (br, bp)):
        return True
    return _proportional((ar, ap), (bp, br)) and (a.reversible or b.reversible)


def _eq(rxn: Reaction) -> str:
    """The equation as written, third body and falloff included."""

    def side(pairs: list[tuple[int, str]]) -> str:
        text = " + ".join(f"{c if c != 1 else ''}{n}" for c, n in pairs)
        if rxn.is_falloff:
            text += f" (+{rxn.falloff_collider or 'M'})"
        elif rxn.is_third_body:
            text += " + M"
        return text

    arrow = "<=>" if rxn.reversible else "=>"
    return f"{side(rxn.reactants)} {arrow} {side(rxn.products)}"


def _lines(rxns: list[Reaction]) -> str:
    return ", ".join(str(r.line_no) for r in rxns)


def _orphan_message(rxn: Reaction) -> str:
    at = f", DUPLICATE on line {rxn.duplicate_line_no}" if rxn.duplicate_line_no is not None else ""
    return (
        f"CHEMKIN DUPLICATE marker has no partner: reaction '{_eq(rxn)}' "
        f"(line {rxn.line_no}{at}) has no other DUPLICATE reaction with the same "
        "participants. Imported as written."
    )


def duplicate_flag_warnings(reactions: list[Reaction]) -> list[str]:
    """Warnings for undeclared repeats and orphaned ``DUPLICATE`` markers."""
    groups: dict[frozenset, list[Reaction]] = defaultdict(list)
    for rxn in reactions:
        groups[_participants(rxn)].append(rxn)

    warnings: list[str] = []
    for members in groups.values():
        marked = [m for m in members if m.duplicate]
        if len(members) < 2:
            if len(marked) == 1:
                warnings.append(_orphan_message(marked[0]))
            continue
        # Undeclared repeats: connected clusters of offending pairs.
        parent = list(range(len(members)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        offending: set[int] = set()
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if a.duplicate and b.duplicate:
                    continue
                if _rate_type(a) == _rate_type(b) and _same_reaction(a, b):
                    offending.update((i, j))
                    parent[find(i)] = find(j)
        # A lone marker whose own row is part of an undeclared repeat is one defect,
        # not two: adding the missing marker to the repeat fixes both.
        if len(marked) == 1 and members.index(marked[0]) not in offending:
            warnings.append(_orphan_message(marked[0]))
        clusters: dict[int, list[int]] = defaultdict(list)
        for i in sorted(offending):
            clusters[find(i)].append(i)
        for idxs in clusters.values():
            rs = [members[i] for i in idxs]
            unmarked = [r for r in rs if not r.duplicate]
            warnings.append(
                f"CHEMKIN reaction '{_eq(rs[0])}' is written {len(rs)} times "
                f"(lines {_lines(rs)}) but line(s) {_lines(unmarked)} carry no "
                "DUPLICATE; the repeats are undeclared (Cantera refuses such a model). "
                "Imported as written."
            )
    return warnings
