"""Normalising a unit's actual recipe: which facts are established, which contradict, which cohort it can be in.

A level-of-theory label names a method and a basis; it does not establish the full recipe, and **equal nulls do
not prove equivalence**. This module combines what the level of theory states with what the depositor declared
(:class:`tckdb_schemas.structure_declarations.ActualProtocolDeclaration`, and for a determination its own
composite recipe) into one :class:`~app.services.structure_selection.models.NormalizedRecipe`:

* a fact stated by one side is established by that side; stated by both and equal, by both; stated by both and
  different (or ``not_applicable`` against a level that states a value) is a **conflict**, a finding and not a
  precedence won by upload order;
* a fact stated by neither (or declared ``unknown``) is **unestablished**, never defaulted: a missing solvent is
  not gas phase, a missing core treatment is not frozen core;
* the **cohort key** exists only when every fact a numerical comparison needs is established and nothing
  conflicts. Two units with the same key may be ordered by value; a unit without one can still be reported on its
  own, but it can be compared with nothing.

Operational settings (threads, memory, scratch) never enter. Software identity does not divide a cohort in
normaliser version 1: it is disclosed with every unit, and a difference that matters scientifically (a
different integration grid, a density-fitting approximation) divides cohorts through the *declared* material
approximations. That is a documented decision of ``NORMALIZER_VERSION`` 1, not an omission.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.structure_selection.models import (
    NORMALIZER_VERSION,
    LevelFacts,
    NormalizedRecipe,
    RecipeFact,
)

#: Facts the level of theory cannot carry and a numerical comparison therefore needs declared (or the level's own
#: column stating them). Auxiliary basis and dispersion are not here: the level of theory's identity already
#: carries them, and a declaration can only add a contradiction.
REQUIRED_FACTS: tuple[str, ...] = (
    "electronic_state",
    "spin_treatment",
    "relativistic_treatment",
    "effective_core_potential",
    "core_treatment",
    "solvation",
    "numerical_approximations",
    "included_corrections",
)
#: Facts that enter the cohort key when established, whether or not they are required.
OPTIONAL_KEYED_FACTS: tuple[str, ...] = ("auxiliary_basis", "dispersion")
#: Required additionally for an optimisation endpoint, whose value depends on whether the search was constrained.
ENDPOINT_FACTS: tuple[str, ...] = ("constraints",)

_SPIN_VALUES = frozenset({"restricted", "unrestricted", "restricted_open"})


def _list_value(items: list[Any] | None, field: str) -> tuple[str, str | None]:
    if items is None:
        return "unknown", None
    if field == "numerical_approximations":
        parts = sorted(f"{i['kind']}={i.get('setting') or ''}" for i in items)
    else:
        parts = sorted(str(i) for i in items)
    return "known", ";".join(parts) if parts else "none"


def declared_fact(declaration: dict[str, Any] | None, name: str) -> tuple[str, str | None] | None:
    """``(state, value)`` a stored declaration states for one fact, or ``None`` when it does not state it."""
    if declaration is None:
        return None
    if name in ("numerical_approximations", "included_corrections"):
        if name not in declaration or declaration[name] is None:
            return None
        return _list_value(declaration[name], name)
    block = declaration.get(name)
    if block is None:
        return None
    state = block["state"]
    if state != "known":
        return state, None
    if name == "electronic_state":
        return "known", str(block["root"])
    if name == "solvation":
        detail = block.get("detail")
        return "known", f"{block['kind']}:{detail}" if detail else str(block["kind"])
    return "known", str(block["value"])


def _lot_fact(level: LevelFacts, name: str) -> tuple[str, str | None] | None:
    """What the level of theory itself states for a fact, or ``None`` when it states nothing."""
    if name == "spin_treatment":
        return ("known", level.spin_treatment) if level.spin_treatment in _SPIN_VALUES else None
    if name == "core_treatment":
        return ("known", level.core_treatment) if level.core_treatment else None
    if name == "auxiliary_basis":
        return ("known", level.aux_basis.strip().lower()) if level.aux_basis else None
    if name == "dispersion":
        return ("known", level.dispersion.strip().lower()) if level.dispersion else None
    if name == "solvation":
        if level.solvent:
            model = f":{level.solvent_model.strip().lower()}" if level.solvent_model else ""
            return "known", f"implicit_solvent:{level.solvent.strip().lower()}{model}"
        return None
    return None


def _solvation_conflict(declared: tuple[str, str | None], lot: tuple[str, str | None]) -> bool:
    """A declared gas phase against a level that names a solvent is a contradiction; an implicit-solvent claim that
    disagrees only in its free-text detail is not compared (the level's own solvent is what enters the key)."""
    return declared[0] == "known" and declared[1] == "gas_phase" and lot[0] == "known"


def _combine(
    name: str,
    lot: tuple[str, str | None] | None,
    declared: tuple[str, str | None] | None,
) -> tuple[RecipeFact | None, bool]:
    """One fact from the two sources. Returns ``(fact, conflict)``; the fact is ``None`` on a conflict."""
    if lot is not None and declared is not None:
        if declared[0] == "known":
            if name == "solvation":
                if _solvation_conflict(declared, lot):
                    return None, True
                return RecipeFact(name, "known", lot[1], "both"), False
            if declared[1] == lot[1]:
                return RecipeFact(name, "known", lot[1], "both"), False
            return None, True
        if declared[0] == "not_applicable":
            return None, True
        return RecipeFact(name, "known", lot[1], "lot"), False
    if lot is not None:
        return RecipeFact(name, "known", lot[1], "lot"), False
    if declared is not None:
        return RecipeFact(name, declared[0], declared[1], "declaration"), False
    return RecipeFact(name, "unknown", None, "none"), False


def _merge_declared(
    calc: tuple[str, str | None] | None, determination: tuple[str, str | None] | None
) -> tuple[tuple[str, str | None] | None, bool]:
    """A determination's own composite recipe and the calculation's declaration: stated by both and different is
    a conflict; stated by one stands; an explicit ``unknown`` yields to a stated value."""
    if calc is None:
        return determination, False
    if determination is None:
        return calc, False
    if calc[0] == "unknown":
        return determination, False
    if determination[0] == "unknown":
        return calc, False
    if calc == determination:
        return calc, False
    return None, True


def normalize_recipe(
    level: LevelFacts,
    declaration: dict[str, Any] | None,
    *,
    constraint_rows: int,
    needs_constraints: bool,
    determination_recipe: dict[str, Any] | None = None,
) -> NormalizedRecipe:
    """The unit's recipe, with conflicts and the cohort key. Pure.

    :param needs_constraints: The value depends on whether a geometry search was constrained (an optimisation
        endpoint); the ``constraints`` fact is then required to establish a cohort.
    :param determination_recipe: A determination's own composite recipe, stored form.
    """
    facts: list[RecipeFact] = []
    conflicts: list[str] = []
    names = (*REQUIRED_FACTS, *OPTIONAL_KEYED_FACTS, *ENDPOINT_FACTS)
    for name in names:
        calc_declared = declared_fact(declaration, name)
        det_declared = declared_fact(determination_recipe, name)
        declared, conflict = _merge_declared(calc_declared, det_declared)
        if conflict:
            conflicts.append(name)
            continue
        if name == "constraints":
            fact, conflict = _constraints_fact(declared, constraint_rows)
        else:
            fact, conflict = _combine(name, _lot_fact(level, name), declared)
        if conflict or fact is None:
            conflicts.append(name)
            continue
        facts.append(fact)

    required = (*REQUIRED_FACTS, *(ENDPOINT_FACTS if needs_constraints else ()))
    by_name = {f.name: f for f in facts}
    unestablished = tuple(n for n in required if n not in conflicts and by_name[n].state == "unknown")
    key = None
    identity = level.composite_scheme_ref or level.level_ref
    if not conflicts and not unestablished and identity is not None:
        keyed = [
            (f.name, f.state, f.value)
            for f in facts
            if f.name in required or (f.name in OPTIONAL_KEYED_FACTS and f.state != "unknown")
        ]
        canonical = json.dumps(
            {"normalizer": NORMALIZER_VERSION, "level": identity, "facts": sorted(keyed)},
            sort_keys=True,
            separators=(",", ":"),
        )
        key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return NormalizedRecipe(
        facts=tuple(facts), conflicts=tuple(sorted(conflicts)), unestablished=unestablished, cohort_key=key
    )


def _constraints_fact(declared: tuple[str, str | None] | None, rows: int) -> tuple[RecipeFact | None, bool]:
    """Constraint rows establish "constrained"; their absence is not "unconstrained" unless declared."""
    if rows > 0:
        if declared is not None and declared[0] == "known" and declared[1] == "unconstrained":
            return None, True
        return RecipeFact("constraints", "known", "constrained", "rows" if declared is None else "both"), False
    if declared is None:
        return RecipeFact("constraints", "unknown", None, "none"), False
    if declared[0] == "known" and declared[1] == "constrained":
        # Declared constrained with no constraint rows: the claim stands (rows are optional), but it is the
        # depositor's alone.
        return RecipeFact("constraints", "known", "constrained", "declaration"), False
    return RecipeFact("constraints", declared[0], declared[1], "declaration"), False


def recipe_request_mismatches(recipe: NormalizedRecipe, requested: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``(differs, unestablished)`` fact names where a unit's recipe does not meet a requested one.

    A requested fact the unit states differently *differs* (incompatible); one the unit does not establish is
    *unestablished* (unresolved). A request never fills in a missing fact.
    """
    by_name = {f.name: f for f in recipe.facts}
    differs: list[str] = []
    unestablished: list[str] = []
    for name in (*REQUIRED_FACTS, *OPTIONAL_KEYED_FACTS, *ENDPOINT_FACTS):
        wanted = declared_fact(requested, name)
        if wanted is None or wanted[0] == "unknown":
            continue
        if name in recipe.conflicts:
            unestablished.append(name)
            continue
        fact = by_name.get(name)
        if fact is None or fact.state == "unknown":
            unestablished.append(name)
        elif (fact.state, fact.value) != wanted:
            differs.append(name)
    return differs, unestablished
