"""Confirmed structure findings, applied to the sources a stored product rests on.

A thermo record or a supplied kinetics record names the calculations it was computed from, each in a role (an optimisation,
a frequency job, a single point). A scoped ``StructureEvidenceFinding`` can say that one of those calculations, or the
geometry it ran on or produced, is the wrong identity, state or path, or that a role it fills is invalid. The legacy trust
badge deliberately does not judge that (it does not know the claim, only that a record exists), and the structure assessor
only reads findings for the units it assesses, so a product's selection would otherwise ignore a confirmed invalidation of
its own source. This module is the one place the H298 and kinetics assessors ask: *does a live, supported finding invalidate
a source in the role this product uses it for?*

The rules (they are the structure assessor's, reused, not a second set):

* **Subject.** A calculation-scope finding applies to that calculation; a geometry-scope finding to a geometry the
  calculation ran on or produced *on the side its role reads* (a single point's energy describes its input geometry, an
  optimisation's its output, a frequency job's curvature its input); a determination-scope finding only when the
  determination names this calculation as a source in a role the product uses it for and evaluates one of those geometries.
  A finding about anything else is a finding about another target and changes nothing here.
* **Role.** A ``role_invalidation`` bites only the role it names. Identity, state and path incompatibilities invalidate the
  geometry for every role. A ``contradictory_characterization`` is about curvature, so it bites the roles that read
  curvature and never bans a separately valid recorded electronic energy that happens to sit on the same calculation.
* **Settling.** Only an authorized adjudication about the same subject settles an earlier finding
  (``live_findings``); a producer's own "does not invalidate" does not erase an authorized disproof. A settled finding is
  history and does not affect a fresh decision.
* **Unreadable is disclosed, never a failure.** An unknown kind or semantic version is noted and excludes nothing.
* **Unresolved is not confirmed.** An ``unresolved`` verdict makes the product unresolved for the question, not blocked.

Nothing here reads a heuristic geometry-validation row: a heuristic mismatch is advisory evidence (trust contract v2), and
this module is what restores the force of a *confirmed* invalidation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationInputGeometry, CalculationOutputGeometry
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from app.services.structure_selection.assessment import (
    CURVATURE_SIDE,
    ENERGY_SIDE,
    _apply_finding,
    _Findings,
    live_findings,
)
from app.services.structure_selection.loader import findings_by_subject, to_finding_facts
from app.services.structure_selection.models import FindingFacts

#: The structure roles a thermo source role stands for. An optimisation supplies the geometry and, where the product takes
#: its energy from it, the energy; a frequency job supplies curvature; a single point, composite or imported value an energy.
THERMO_ROLES: dict[str, frozenset[str]] = {
    "opt": frozenset({"geometry_optimization", "energy"}),
    "freq": frozenset({"curvature", "alternative_characterization"}),
    "sp": frozenset({"energy"}),
    "composite": frozenset({"energy"}),
    "imported": frozenset({"energy"}),
}
#: The same for a supplied kinetics record's source roles. IRC, master-equation and fit-source links supply no structural
#: role the selection reads, so they are not consulted.
KINETICS_ROLES: dict[str, frozenset[str]] = {
    "reactant_energy": frozenset({"energy"}),
    "product_energy": frozenset({"energy"}),
    "ts_energy": frozenset({"energy"}),
    "freq": frozenset({"curvature", "alternative_characterization"}),
}
_CURVATURE_ROLES = frozenset({"curvature", "alternative_characterization"})


@dataclass(frozen=True)
class SourceUse:
    """One calculation a product uses, and the structure roles it uses it for."""

    calculation_id: int
    calculation_type: str
    legacy_role: str
    structure_roles: frozenset[str]


@dataclass(frozen=True)
class SourceFindings:
    """What live findings say about one product's sources: confirmed blockers, unresolved ones, and disclosures."""

    blocking: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()


@dataclass
class _Context:
    geometries: dict[int, dict[str, set[int]]] = field(default_factory=lambda: defaultdict(lambda: {"input": set(), "output": set()}))
    #: calculation id -> [(determination id, source role value, evaluated geometry id)]
    determinations: dict[int, list[tuple[int, str, int]]] = field(default_factory=lambda: defaultdict(list))


def thermo_uses(thermo) -> list[SourceUse]:
    """The uses a loaded thermo record's source links make of their calculations."""
    out: list[SourceUse] = []
    for link in thermo.source_calculations:
        roles = THERMO_ROLES.get(link.role.value)
        if roles is not None and link.calculation is not None:
            out.append(SourceUse(link.calculation_id, link.calculation.type.value, link.role.value, roles))
    return out


def kinetics_uses(kinetics) -> list[SourceUse]:
    """The uses a loaded kinetics record's source links make of their calculations."""
    out: list[SourceUse] = []
    for link in kinetics.source_calculations:
        roles = KINETICS_ROLES.get(link.role.value)
        if roles is not None and link.calculation is not None:
            out.append(SourceUse(link.calculation_id, link.calculation.type.value, link.role.value, roles))
    return out


def _relevant_geometries(use: SourceUse, ctx: _Context) -> set[int]:
    sides: set[str] = set()
    for role in use.structure_roles:
        if role == "energy":
            side = ENERGY_SIDE.get(use.calculation_type)
        elif role in _CURVATURE_ROLES:
            side = CURVATURE_SIDE.get(use.calculation_type)
        else:  # geometry_optimization
            side = "output"
        if side is None:
            sides.update({"input", "output"})  # the side is not established: the conservative reading, every geometry
        else:
            sides.add(side)
    geometry = ctx.geometries[use.calculation_id]
    return set().union(*(geometry[s] for s in sides)) if sides else set()


def _context(session: Session, calc_ids: set[int]) -> _Context:
    ctx = _Context()
    if not calc_ids:
        return ctx
    for calc_id, geometry_id in session.execute(
        select(CalculationInputGeometry.calculation_id, CalculationInputGeometry.geometry_id).where(
            CalculationInputGeometry.calculation_id.in_(calc_ids)
        )
    ):
        ctx.geometries[calc_id]["input"].add(geometry_id)
    for calc_id, geometry_id in session.execute(
        select(CalculationOutputGeometry.calculation_id, CalculationOutputGeometry.geometry_id).where(
            CalculationOutputGeometry.calculation_id.in_(calc_ids)
        )
    ):
        ctx.geometries[calc_id]["output"].add(geometry_id)
    for calc_id, determination_id, role, evaluated in session.execute(
        select(
            StructureDeterminationSource.calculation_id,
            StructureDeterminationSource.determination_id,
            StructureDeterminationSource.role,
            StructureDetermination.evaluated_geometry_id,
        )
        .join(StructureDetermination, StructureDetermination.id == StructureDeterminationSource.determination_id)
        .where(StructureDeterminationSource.calculation_id.in_(calc_ids))
    ):
        ctx.determinations[calc_id].append((determination_id, role.value, evaluated))
    return ctx


def assess_source_findings(
    session: Session, uses_by_key: Mapping[Hashable, Sequence[SourceUse]]
) -> dict[Hashable, SourceFindings]:
    """Apply live findings to every product's sources. One batch of queries for the whole population."""
    calc_ids = {u.calculation_id for uses in uses_by_key.values() for u in uses}
    ctx = _context(session, calc_ids)
    geometry_ids = {g for c in calc_ids for sides in (ctx.geometries[c],) for g in sides["input"] | sides["output"]}
    determination_ids = {d for c in calc_ids for d, _, _ in ctx.determinations[c]}
    by_subject = findings_by_subject(
        session, geometry_ids=geometry_ids, calc_ids=calc_ids, determination_ids=determination_ids
    )
    # Every loaded finding row once, so a superseding finding about the same subject is seen whichever subject reached it.
    all_rows = {f.id: f for rows in by_subject.values() for f in rows}
    refs = {
        **{("geometry", g): f"geometry:{g}" for g in geometry_ids},
        **{("calculation", c): f"calculation:{c}" for c in calc_ids},
        **{("determination", d): f"determination:{d}" for d in determination_ids},
    }
    facts_by_id = {
        f.id: fact
        for f, fact in zip(
            sorted(all_rows.values(), key=lambda x: x.id),
            to_finding_facts(session, sorted(all_rows.values(), key=lambda x: x.id), refs),
            strict=True,
        )
    }

    live = live_findings(tuple(facts_by_id[i] for i in sorted(facts_by_id)))
    live_by_subject: dict[tuple[str, str], list[FindingFacts]] = defaultdict(list)
    for fact in live:
        live_by_subject[(fact.scope, fact.subject_ref)].append(fact)

    out: dict[Hashable, SourceFindings] = {}
    for key, uses in uses_by_key.items():
        f = _Findings()
        for use in uses:
            relevant_geometries = _relevant_geometries(use, ctx)
            candidates: list[FindingFacts] = []
            for fact in live_by_subject.get(("calculation", f"calculation:{use.calculation_id}"), []):
                candidates.append(fact)
            for g in relevant_geometries:
                for fact in live_by_subject.get(("geometry", f"geometry:{g}"), []):
                    candidates.append(fact)
            for determination_id, role, evaluated in ctx.determinations.get(use.calculation_id, []):
                if role in use.structure_roles and evaluated in relevant_geometries:
                    for fact in live_by_subject.get(("determination", f"determination:{determination_id}"), []):
                        candidates.append(fact)
            for fact in candidates:
                scoped = _Findings()
                _apply_finding(scoped, fact, blocks=_bites(fact, use), role_needed=_role_needed(fact, use))
                for code in scoped.blocking:
                    f.block(f"source_finding:{use.legacy_role}:{code}")
                for reason in scoped.reasons:
                    f.unresolved(f"source_finding:{use.legacy_role}:{reason.code}")
                for note in scoped.advisory:
                    f.note(f"source_finding:{use.legacy_role}:{note}")
        out[key] = SourceFindings(
            blocking=tuple(sorted(set(f.blocking))),
            unresolved=tuple(sorted({r.code for r in f.reasons})),
            advisory=tuple(sorted(set(f.advisory))),
        )
    return out


def _bites(fact: FindingFacts, use: SourceUse) -> bool:
    """Whether a non-role finding invalidates this use: a curvature contradiction bites only curvature roles."""
    if fact.kind == "contradictory_characterization":
        return bool(use.structure_roles & _CURVATURE_ROLES)
    return True


def _role_needed(fact: FindingFacts, use: SourceUse) -> bool:
    return fact.role in use.structure_roles if fact.role is not None else True
