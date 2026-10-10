"""Read-only coverage inventory for statmech selection: what the stored statmech corpus can support today.

Before any declaration schema is decided, this reads the linked content that already exists and reports, for each
supported claim, how many existing records are ``represented_and_usable``, ``represented_but_ambiguous`` or
``absent`` (and how many a claim cannot apply to by owner kind). It also counts ``unsupported_declaration_version``
(always 0 until declarations exist) and the metadata coverage behind the verdicts.

Absence on the statmech row is not proof of absence in the repository: a record whose own columns say nothing may be
linked to source calculations that do, and those links are read here (curvature results on any source calculation,
source geometries, structure determinations that name a source). Nothing is written, backfilled or inferred; every
verdict comes from :mod:`app.services.statmech_selection.legacy`, the same classifier the assessor uses, so the
inventory predicts the assessor's facts-level answer rather than a parallel one.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationFreqResult,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import CalculationGeometryRole
from app.db.models.energy_correction import AppliedEnergyCorrection, FrequencyScaleFactor
from app.db.models.statmech import (
    Statmech,
    StatmechElectronicLevel,
    StatmechSourceCalculation,
    StatmechTorsion,
)
from app.db.models.structure_determination import StructureDeterminationSource
from app.services.statmech_selection.legacy import PROFILES, LegacyFacts, Readiness, classify
from app.services.statmech_selection.vocabulary import OwnerKind

_GEOMETRY_SIDE = {"opt": "output", "composite": "output", "freq": "input"}


def load_legacy_facts(session: Session) -> dict[int, LegacyFacts]:
    """The stored facts of every statmech record, keyed by row id. Bulk reads; nothing is written."""
    rows = session.execute(
        select(
            Statmech.id,
            Statmech.species_entry_id,
            Statmech.scientific_origin,
            Statmech.rigid_rotor_kind,
            Statmech.is_linear,
            Statmech.rotational_constant_a_cm1,
            Statmech.rotational_constant_b_cm1,
            Statmech.rotational_constant_c_cm1,
            Statmech.external_symmetry,
            Statmech.optical_isomers,
            Statmech.statmech_treatment,
            Statmech.uses_projected_frequencies,
            Statmech.frequency_scale_factor_id,
        ).order_by(Statmech.id)
    ).all()
    scale_kind = {
        i: k.value
        for i, k in session.execute(select(FrequencyScaleFactor.id, FrequencyScaleFactor.scale_kind)).all()
    }
    levels: dict[int, int] = {
        sid: int(n)
        for sid, n in session.execute(
            select(StatmechElectronicLevel.statmech_id, func.count()).group_by(StatmechElectronicLevel.statmech_id)
        ).all()
    }
    torsions: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sid, kind, invalidated, scan in session.execute(
        select(
            StatmechTorsion.statmech_id,
            StatmechTorsion.treatment_kind,
            StatmechTorsion.invalidated_reason,
            StatmechTorsion.source_scan_calculation_id,
        )
    ):
        t = torsions[sid]
        t["n"] += 1
        if kind is None:
            t["no_kind"] += 1
        elif kind.value == "hindered_rotor" and scan is None and invalidated is None:
            t["hindered_no_scan"] += 1
        if invalidated is not None:
            t["invalidated"] += 1

    links = session.execute(
        select(StatmechSourceCalculation.statmech_id, StatmechSourceCalculation.calculation_id, Calculation.type)
        .join(Calculation, Calculation.id == StatmechSourceCalculation.calculation_id)
    ).all()
    calc_ids = {c for _, c, _ in links}
    curvature = set()
    designated: dict[int, bool] = {}
    if calc_ids:
        curvature |= set(session.scalars(select(CalculationFreqResult.calculation_id).where(
            CalculationFreqResult.calculation_id.in_(calc_ids))))
        curvature |= set(session.scalars(select(CalculationHessian.calculation_id).where(
            CalculationHessian.calculation_id.in_(calc_ids))))
        designated = {
            c: rc is not None
            for c, rc in session.execute(
                select(CalculationFreqResult.calculation_id, CalculationFreqResult.reaction_coordinate_mode_index).where(
                    CalculationFreqResult.calculation_id.in_(calc_ids)
                )
            )
        }
    geometry: dict[int, dict[str, set[int]]] = defaultdict(lambda: {"input": set(), "output": set(), "final": set()})
    applied_scale: dict[int, set[str]] = defaultdict(set)
    if calc_ids:
        # The one recorded evidence that a scale factor was applied: an applied energy correction that carries a
        # frequency scale factor on one of the record's own source calculations.
        for calc_id, kind in session.execute(
            select(AppliedEnergyCorrection.source_calculation_id, FrequencyScaleFactor.scale_kind)
            .join(FrequencyScaleFactor, FrequencyScaleFactor.id == AppliedEnergyCorrection.frequency_scale_factor_id)
            .where(AppliedEnergyCorrection.source_calculation_id.in_(calc_ids))
        ):
            applied_scale[calc_id].add(kind.value)
        for c, g in session.execute(select(CalculationInputGeometry.calculation_id, CalculationInputGeometry.geometry_id).where(
                CalculationInputGeometry.calculation_id.in_(calc_ids))):
            geometry[c]["input"].add(g)
        for c, g, geometry_role in session.execute(
            select(
                CalculationOutputGeometry.calculation_id,
                CalculationOutputGeometry.geometry_id,
                CalculationOutputGeometry.role,
            ).where(CalculationOutputGeometry.calculation_id.in_(calc_ids))
        ):
            geometry[c]["output"].add(g)
            if geometry_role is CalculationGeometryRole.final:
                geometry[c]["final"].add(g)
    determinations: dict[int, set[int]] = defaultdict(set)
    if calc_ids:
        by_calc: dict[int, set[int]] = defaultdict(set)
        for d, c in session.execute(
            select(StructureDeterminationSource.determination_id, StructureDeterminationSource.calculation_id).where(
                StructureDeterminationSource.calculation_id.in_(calc_ids)
            )
        ):
            by_calc[c].add(d)
    else:
        by_calc = {}
    per_statmech_curv: dict[int, int] = defaultdict(int)
    per_statmech_geoms: dict[int, set[int]] = defaultdict(set)
    per_statmech_rc: dict[int, bool] = {}
    per_statmech_scale: dict[int, set[str]] = defaultdict(set)
    for sid, cid, ctype in links:
        if cid in curvature:
            per_statmech_curv[sid] += 1
        side = _GEOMETRY_SIDE.get(ctype.value)
        if side is not None:
            chosen = geometry[cid][side]
            if side == "output" and geometry[cid]["final"]:
                # Where the calculation declares which output is its final geometry, an initial or intermediate one
                # is not an alternative to it.
                chosen = geometry[cid]["final"]
            per_statmech_geoms[sid] |= chosen
        per_statmech_scale[sid] |= applied_scale.get(cid, set())
        if cid in designated:
            per_statmech_rc[sid] = per_statmech_rc.get(sid, False) or designated[cid]
        determinations[sid] |= by_calc.get(cid, set())

    out: dict[int, LegacyFacts] = {}
    for r in rows:
        t = torsions.get(r.id, {})
        n_det = len(determinations.get(r.id, ()))
        out[r.id] = LegacyFacts(
            owner_kind=OwnerKind.species_entry if r.species_entry_id is not None else OwnerKind.transition_state_entry,
            scientific_origin=r.scientific_origin.value,
            rigid_rotor_kind=r.rigid_rotor_kind.value if r.rigid_rotor_kind else None,
            is_linear=r.is_linear,
            n_rotational_constants=sum(
                v is not None
                for v in (r.rotational_constant_a_cm1, r.rotational_constant_b_cm1, r.rotational_constant_c_cm1)
            ),
            external_symmetry=r.external_symmetry,
            optical_isomers=r.optical_isomers,
            statmech_treatment=r.statmech_treatment.value if r.statmech_treatment else None,
            uses_projected_frequencies=r.uses_projected_frequencies,
            scale_factor_kind=scale_kind.get(r.frequency_scale_factor_id),
            has_scale_factor=r.frequency_scale_factor_id is not None,
            applied_scale_kinds=frozenset(per_statmech_scale.get(r.id, ())),
            n_electronic_levels=int(levels.get(r.id, 0)),
            n_torsions=t.get("n", 0),
            n_torsions_without_kind=t.get("no_kind", 0),
            n_torsions_hindered_without_scan=t.get("hindered_no_scan", 0),
            n_torsions_invalidated=t.get("invalidated", 0),
            n_curvature_sources=per_statmech_curv.get(r.id, 0),
            n_source_geometries=len(per_statmech_geoms.get(r.id, ())),
            determination_tie="none" if n_det == 0 else "single" if n_det == 1 else "multiple",
            reaction_coordinate_designated=per_statmech_rc.get(r.id),
        )
    return out


def statmech_coverage_inventory(session: Session) -> dict[str, Any]:
    """Counts of what the stored statmech corpus supports, per claim. Pure reads; see the module docstring."""
    facts = load_legacy_facts(session)
    by_profile: dict[str, dict[str, int]] = {}
    gaps: dict[str, dict[str, int]] = {}
    for profile in PROFILES:
        counts = {r.value: 0 for r in Readiness}
        missing: dict[str, int] = defaultdict(int)
        for f in facts.values():
            readiness, states = classify(profile, f)
            counts[readiness.value] += 1
            if readiness is Readiness.ambiguous:
                for name, state in states.items():
                    if state != "present":
                        missing[f"{name}:{state}"] += 1
        counts["unsupported_declaration_version"] = 0
        by_profile[profile] = counts
        gaps[profile] = dict(sorted(missing.items()))
    owners: dict[str, int] = defaultdict(int)
    for f in facts.values():
        owners[f.owner_kind.value] += 1
    return {
        "records": len(facts),
        "by_owner_kind": dict(sorted(owners.items())),
        "by_profile": by_profile,
        "ambiguity_breakdown": gaps,
        "metadata": {
            "with_scale_factor_linked": sum(f.has_scale_factor for f in facts.values()),
            "with_recorded_scale_application": sum(bool(f.applied_scale_kinds) for f in facts.values()),
            "with_electronic_levels": sum(f.n_electronic_levels > 0 for f in facts.values()),
            "with_optical_isomers": sum(f.optical_isomers is not None for f in facts.values()),
            "with_external_symmetry": sum(f.external_symmetry is not None for f in facts.values()),
            "with_curvature_source": sum(f.n_curvature_sources > 0 for f in facts.values()),
            "with_several_source_geometries": sum(f.n_source_geometries > 1 for f in facts.values()),
            "with_torsions": sum(f.n_torsions > 0 for f in facts.values()),
            "torsion_bearing_with_unknown_projection": sum(
                f.torsion_bearing and f.uses_projected_frequencies is None for f in facts.values()
            ),
            "ts_with_designated_reaction_coordinate": sum(
                f.owner_kind is OwnerKind.transition_state_entry and f.reaction_coordinate_designated is True
                for f in facts.values()
            ),
        },
        "declarations": {
            "unsupported_declaration_version": 0,
            "note": "No declaration schema exists in this release; every claim is read from the legacy record.",
        },
        "note": (
            "Counts of stored rows only. A verdict is the facts-level readiness of a legacy record for a claim, not an "
            "assessment: review, source status, findings and geometry coherence are checked live. Nothing was written, "
            "backfilled or inferred."
        ),
    }


__all__ = ["load_legacy_facts", "statmech_coverage_inventory"]
