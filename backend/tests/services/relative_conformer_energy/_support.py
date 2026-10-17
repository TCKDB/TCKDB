"""Builders for the relative conformer energy tests: pure basins (no database) over the structure-selection builders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import count
from typing import Any

from app.services.relative_conformer_energy.compute import compute_relative_energies
from app.services.relative_conformer_energy.models import (
    Certificate,
    EnergyKind,
    LevelSelector,
    MemberResult,
    RelativeEnergyRequest,
    RelativeEnergyResult,
)
from app.services.relative_conformer_energy.service import to_structure_request
from app.services.structure_selection.assessment import assess_unit
from app.services.structure_selection.models import (
    CurvatureFacts,
    EnergyFacts,
    NormalizedCalculation,
    NormalizedDetermination,
)
from tests.services.structure_selection._support import (
    G1,
    T0,
    calc,
    declaration,
    det,
    freq,
    level,
    source,
    subject,
)

LOT_A = "lot_a"
LOT_B = "lot_b"
_ids = count(1)


@dataclass
class Basin:
    """One conformer-basin determination with the calculations it pins."""

    det: NormalizedDetermination
    calcs: tuple[NormalizedCalculation, ...]


def basin(
    group: str,
    *,
    energy: float | None = -76.4,
    kind: str = "electronic",
    lot: str = LOT_A,
    freq_lot: str | None = None,
    decl: dict[str, Any] | None = None,
    age_days: float = 0,
    observation: str | None = None,
    curvature: CurvatureFacts | None = None,
    uncertainty: float | None = None,
    quantity: str | None = None,
    sp_type: str | None = None,
) -> Basin:
    """A basin of ``group``: an optimisation, an energy and a frequency, all on geometry ``geom_g1``.

    ``kind='e0'`` pins a composite calculation as the energy source (which is what supplies a zero-kelvin energy).
    """
    n = next(_ids)
    declared = decl or declaration()
    energy_level = level(lot)
    opt = calc(f"calc_o{n}", type="opt", energy=(energy or 0) + 0.01, declared=declared, level=energy_level)
    if kind == "e0":
        e = calc(f"calc_e{n}", type="composite", energy=energy, declared=declared, level=energy_level)
    else:
        e = calc(
            f"calc_e{n}",
            type=sp_type or "sp",
            energy=EnergyFacts(sp_electronic_hartree=energy, sp_uncertainty_hartree=uncertainty),
            declared=declared,
            level=energy_level,
        )
    f = freq(f"calc_f{n}", declared=declared, level=level(freq_lot or lot), **({"curvature": curvature} if curvature else {}))
    d = det(
        f"sdet_{n}",
        target_kind="conformer_basin",
        quantity=quantity or ("zero_kelvin_energy" if kind == "e0" else "electronic_energy"),
        energy_convention={"zero_point_treatment": "composite_recipe"} if kind == "e0" else None,
        conformer_group_ref=group,
        conformer_observation_ref=observation or f"co_{n}",
        key=f"k{n}",
        rank=n,
        created_at=T0 - timedelta(days=age_days),
        sources=(source("geometry_optimization", opt.calculation_ref), source("energy", e.calculation_ref), source("curvature", f.calculation_ref)),
    )
    return Basin(d, (opt, e, f))


STIFF_IMAGINARY = CurvatureFacts(
    has_freq_result=True, n_imag=1, imaginary_modes=((1, -300.0, None),), imag_freq_cm1=-300.0
)


def request(**changes: Any) -> RelativeEnergyRequest:
    base: dict[str, Any] = {
        "reference_conformer_ref": "cg_a",
        "energy_kind": EnergyKind.electronic_energy,
        "level": LevelSelector(level_of_theory_ref=LOT_A),
        "certificate": Certificate.local_minimum_any_surface,
    }
    base.update(changes)
    return RelativeEnergyRequest(**base)


def compute(
    req: RelativeEnergyRequest,
    groups: list[str],
    *basins: Basin,
    below: frozenset[str] = frozenset(),
) -> RelativeEnergyResult:
    """Assess every basin and compute, the way the service does."""
    calcs = {c.calculation_ref: c for b in basins for c in b.calcs}
    sreq = to_structure_request(req, ("sdet_x",))
    dets = [b.det for b in basins]
    assessments = [assess_unit(d, request=sreq, subject=subject(), calculations=calcs) for d in dets]
    return compute_relative_energies(
        req,
        sreq,
        group_refs=groups,
        below_floor_refs=below,
        determinations=dets,
        assessments=assessments,
        calculations=calcs,
    )


def member(result: RelativeEnergyResult, ref: str) -> MemberResult:
    return next(m for m in result.members if m.conformer_ref == ref)


__all__ = ["G1", "LOT_A", "LOT_B", "STIFF_IMAGINARY", "Basin", "basin", "compute", "member", "request"]
