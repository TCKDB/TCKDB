"""Import-report warnings for wrong ``DUPLICATE`` flags (spec §6, DR-0036).

CHEMKIN readers disagree about what to do with a model whose ``DUPLICATE``
markers are wrong. Cantera refuses to load it. This module only *reports*: it
never adds or removes a marker and never drops a reaction, so the deposit is
the file as written.

Two cases:

* a reaction written more than once where at least one of the repeats lacks
  ``DUPLICATE`` (the pair is undeclared). Cantera's rule for "the same reaction":
  same participants with their coefficients, same direction, same rate type;
* a ``DUPLICATE`` marker on a reaction that has no partner. A partner is any other
  reaction with the same participants and direction, whatever its rate type: a
  marker beside an Arrhenius row and a Chebyshev row of one equation is accepted
  (a TCKDB export writes such markers and Cantera loads them), so it is not
  reported.

Every message names CHEMKIN line numbers: the line of each reaction and, for a
marker, the line of the ``DUPLICATE`` keyword.
"""

from __future__ import annotations

from collections import defaultdict

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


def _key(rxn: Reaction) -> tuple:
    return (*_equation_key(rxn), _rate_type(rxn))


def _equation_key(rxn: Reaction) -> tuple:
    def side(pairs: list[tuple[int, str]]) -> tuple:
        merged: dict[str, int] = defaultdict(int)
        for coeff, name in pairs:
            merged[name] += coeff
        return tuple(sorted(merged.items()))

    return (side(rxn.reactants), side(rxn.products), rxn.reversible)


def _eq(rxn: Reaction) -> str:
    def side(pairs: list[tuple[int, str]]) -> str:
        return " + ".join(f"{c if c != 1 else ''}{n}" for c, n in pairs)

    arrow = "<=>" if rxn.reversible else "=>"
    return f"{side(rxn.reactants)} {arrow} {side(rxn.products)}"


def _lines(rxns: list[Reaction]) -> str:
    return ", ".join(str(r.line_no) for r in rxns)


def duplicate_flag_warnings(reactions: list[Reaction]) -> list[str]:
    """Warnings for undeclared repeats and orphaned ``DUPLICATE`` markers."""
    groups: dict[tuple, list[Reaction]] = defaultdict(list)
    for rxn in reactions:
        groups[_key(rxn)].append(rxn)

    by_equation: dict[tuple, int] = defaultdict(int)
    for rxn in reactions:
        by_equation[_equation_key(rxn)] += 1

    warnings: list[str] = []
    for members in groups.values():
        if len(members) >= 2:
            unmarked = [m for m in members if not m.duplicate]
            if unmarked:
                warnings.append(
                    f"CHEMKIN reaction '{_eq(members[0])}' is written {len(members)} times "
                    f"(lines {_lines(members)}) but line(s) {_lines(unmarked)} carry no "
                    "DUPLICATE; the repeats are undeclared (Cantera refuses such a model). "
                    "Imported as written."
                )
        elif members[0].duplicate and by_equation[_equation_key(members[0])] == 1:
            rxn = members[0]
            marker = rxn.duplicate_line_no
            warnings.append(
                f"CHEMKIN DUPLICATE marker has no partner: reaction '{_eq(rxn)}' "
                f"(line {rxn.line_no}"
                + (f", DUPLICATE on line {marker}" if marker is not None else "")
                + ") is written once. Imported as written."
            )
    return warnings
