#!/usr/bin/env python
"""Arkane statmech replay: regenerate S298 and Cp(T) from a statmech record,
using only what a restored TCKDB database holds.

The check behind TCKDB's "statmech-completeness" claim: geometry, harmonic
frequencies, external symmetry, spin multiplicity, optical isomers, rotor
scans and energies stored in TCKDB are enough to rebuild an Arkane
``thermo('NASA')`` deck and recompute the entropy and heat capacity the
database already stores for that species -- without the original ESS
output files. Paper validation exhibit paralleling the Cantera/CHEMKIN
round-trip; findings are written up in
``docs/validation/arkane_statmech_roundtrip.md``.

How it reads
------------
Everything comes from a SQLAlchemy session over the configured database
(the ``DB_*`` environment variables ``Settings.database_url`` reads, via
``app.api.deps.SessionLocal``). No HTTP, no SSH. That is what makes it
runnable against a restored deposit database, and it is why the review
status of a record no longer matters to the replay: the database is read
directly, so nothing is hidden behind ``min_review_status``.

How it runs Arkane
------------------
Arkane stays **out of process**, in the ``rmg_env`` conda environment
(``RMG_ENV`` overrides the name). Before anything is replayed the script
asks that environment for ``rmgpy.__version__`` and for the ``Arkane.py``
beside the installed ``rmgpy`` package (``ARKANE_ENTRY`` overrides), and
records both in the output together with ``git describe`` of the RMG-Py
checkout when it is one. When the environment is absent, the decks are
still assembled from the database and every record is reported with
status ``arkane_skipped`` and the reason; nothing is fabricated.
``--skip-arkane`` forces that path, which is how the deck assembly is
tested without Arkane.

Two approximations, stated
--------------------------
1. **Torsions are removed from the harmonic list by dropping the R lowest
   stored frequencies**, where R is the number of hindered rotors. Arkane
   itself projects the rotor out of the Hessian; TCKDB stores the full
   unprojected 3N-6 spectrum, and for the corpus species the torsional
   modes are the R lowest. This is an approximation that a species with a
   low-frequency non-torsional mode (a ring pucker below a methyl torsion)
   would get wrong, and the dropped frequencies are listed in the deck
   and in the JSON so a reader can check.
2. **No atom-energy or bond-additivity corrections are applied**, so the
   Arkane enthalpy is an absolute ``E_elec + ZPE + thermal`` quantity and
   is not comparable with the stored enthalpy of formation. S298 and
   Cp(T) do not depend on the energy reference and are the targets; H298
   is reported for completeness with that caveat and is never a
   pass/fail quantity.

Two data-access findings, as of this refactor (measured 2026-09-19)
-------------------------------------------------------------------
The original harness recorded two public-API gaps: per-mode frequencies
and ``statmech.optical_isomers`` were not served. Both are now on the read
surface (``GET /scientific/calculations/{ref}?include=freq_modes``,
``app/services/scientific_read/calculations.py``; ``optical_isomers`` on
``app/schemas/reads/scientific_statmech.py``). What remains true and is
not fixed here:

* ``statmech.optical_isomers`` is NULL for the ARC corpus, so the replay
  derives it from the stored ``point_group`` (C1/Cn/Dn/T/O/I are chiral,
  2; anything with an improper element, 1) and records which it used.
  For a chiral species the naive default of 1 costs exactly ``R ln 2``
  in S298.
* ``statmech_torsion.top_description`` is NULL, so the rotating top is
  derived from the stored geometry's connectivity (cut the pivot bond,
  take the side reachable from the first pivot atom). Deterministic for
  an acyclic single-bond rotor; a ring rotor is refused.

Conventions carried over from the original run
----------------------------------------------
Principal and reduced moments of inertia use **standard atomic weights**
(the table below), because that is what rmgpy uses and what the stored
thermo was originally computed with. This is deliberately *not* the
isotopic convention the Hessian reanalysis applies; the two answer
different questions. An element outside the table is a skip reason,
never a guessed mass.

Usage
-----
    conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py \\
        --species-entry-ref spe_...          # one species entry (its statmech records)
    conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py \\
        --statmech-ref sm_...                # one statmech record
    conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py \\
        --all-statmech --json-out replay.json   # every statmech record, JSON summary

Exit status: ``0`` when every compared record is within the declared
tolerance (``--s298-tolerance-j-mol-k``, ``--cp-tolerance-percent``),
``1`` when any compared record exceeds it or Arkane failed on a record,
``2`` when nothing was compared (empty scope, everything skipped, or
Arkane unavailable). The JSON carries every record either way.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.chemistry.normal_modes import perceive_bonds  # noqa: E402
from app.db.models.calculation import (  # noqa: E402
    Calculation,
    CalculationFreqMode,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
)
from app.db.models.common import (  # noqa: E402
    CoordinateUnit,
    StatmechCalculationRole,
    TorsionTreatmentKind,
)
from app.db.models.geometry import GeometryAtom  # noqa: E402
from app.db.models.species import SpeciesEntry  # noqa: E402
from app.db.models.statmech import Statmech  # noqa: E402
from app.db.models.thermo import Thermo  # noqa: E402

RMG_ENV = os.environ.get("RMG_ENV", "rmg_env")

HARTREE_TO_KJMOL = 2625.499638
R_GAS = 8.31446  # J/mol/K
CAL_TO_J = 4.184

#: Standard atomic weights (g/mol), rmgpy's convention for moments of
#: inertia. See the module docstring: not the isotopic table on purpose.
ATOMIC_MASS = {
    "H": 1.00794,
    "D": 2.01410,
    "C": 12.0107,
    "N": 14.0067,
    "O": 15.9994,
    "F": 18.9984,
    "S": 32.065,
    "Cl": 35.453,
    "P": 30.9738,
    "Br": 79.904,
}

CP_TEMPERATURES = (300.0, 500.0, 1000.0, 1500.0)

#: Declared before any record is compared. The original exhibit called
#: S298 within 0.5 J/mol/K and Cp within 1% a match; both were then met by
#: two orders of magnitude, and the figures are kept as the declared
#: tolerance rather than tightened to the measurement.
DEFAULT_S298_TOLERANCE_J_MOL_K = 0.5
DEFAULT_CP_TOLERANCE_PERCENT = 1.0

APPROXIMATIONS = (
    "torsional modes removed from the harmonic-oscillator list by dropping the R lowest stored "
    "frequencies (R = number of hindered rotors) instead of projecting the rotor out of the Hessian",
    "no atom-energy or bond-additivity corrections, so the Arkane enthalpy is absolute and H298 is "
    "not comparable with the stored enthalpy of formation; S298 and Cp(T) are the targets",
)

EXIT_OK = 0
EXIT_EXCEEDED = 1
EXIT_NOTHING_COMPARED = 2


# --------------------------------------------------------------------------- #
# RMG environment
# --------------------------------------------------------------------------- #
def probe_rmg_environment(env: str = RMG_ENV) -> dict:
    """Ask the conda environment what it has. Never raises.

    Returns ``{"available": bool, "env": env, "rmgpy_version": ..,
    "arkane_entry": .., "rmg_py_git": .., "reason": ..}``. ``ARKANE_ENTRY``
    overrides the located ``Arkane.py``.
    """

    info: dict = {"env": env, "available": False, "rmgpy_version": None, "arkane_entry": None, "rmg_py_git": None}
    if shutil.which("conda") is None:
        info["reason"] = "conda is not on PATH"
        return info
    snippet = "import rmgpy, pathlib; print(rmgpy.__version__); print(pathlib.Path(rmgpy.__file__).resolve().parents[1])"
    try:
        proc = subprocess.run(
            ["conda", "run", "-n", env, "python", "-c", snippet],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        info["reason"] = f"could not run python in conda env {env!r}: {exc}"
        return info
    if proc.returncode != 0:
        info["reason"] = f"conda env {env!r} has no importable rmgpy: {proc.stderr.strip()[-400:]}"
        return info
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        info["reason"] = f"unexpected probe output from conda env {env!r}"
        return info
    info["rmgpy_version"] = lines[0].strip()
    rmg_root = Path(lines[1].strip())
    entry = os.environ.get("ARKANE_ENTRY") or str(rmg_root / "Arkane.py")
    if not Path(entry).is_file():
        info["reason"] = f"Arkane.py not found at {entry} (set ARKANE_ENTRY)"
        return info
    info["arkane_entry"] = entry
    try:
        described = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            cwd=str(Path(entry).parent),
            timeout=30,
        )
        if described.returncode == 0:
            info["rmg_py_git"] = described.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    info["available"] = True
    return info


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #
def statmech_scope(
    session: Session,
    *,
    statmech_ref: str | None = None,
    species_entry_ref: str | None = None,
    all_statmech: bool = False,
) -> list[Statmech]:
    """The statmech records to replay, ordered by public ref."""

    statement = select(Statmech)
    if statmech_ref is not None:
        statement = statement.where(Statmech.public_ref == statmech_ref)
    elif species_entry_ref is not None:
        statement = statement.join(SpeciesEntry, SpeciesEntry.id == Statmech.species_entry_id).where(
            SpeciesEntry.public_ref == species_entry_ref
        )
    elif not all_statmech:
        raise ValueError("one of statmech_ref, species_entry_ref or all_statmech is required")
    rows = list(session.scalars(statement).all())
    rows.sort(key=lambda sm: (sm.public_ref or "", sm.id))
    return rows


# --------------------------------------------------------------------------- #
# Chemistry helpers
# --------------------------------------------------------------------------- #
def optical_isomers_from_point_group(point_group: str | None) -> int | None:
    """Infer optical-isomer count from a Schoenflies point group.

    A point group is chiral (2 optical isomers) iff it contains only proper
    rotations: C1, Cn, Dn, T, O, I. Any group with an improper element
    (Cs, Ci, Cnv, Cnh, Dnh, Dnd, Sn, Td, Oh, Ih, ...) is achiral (1). This
    mirrors Arkane's own determination and recovers the value from the
    stored ``point_group`` when ``optical_isomers`` is NULL.
    """

    if not point_group:
        return None
    pg = point_group.strip()
    if pg in {"T", "O", "I"}:
        return 2
    if re.fullmatch(r"C\d+", pg) or re.fullmatch(r"D\d+", pg):
        return 2
    return 1


def build_bond_graph(symbols, coords) -> dict[int, set[int]]:
    """1-indexed adjacency from distance-perceived bonds
    (:func:`app.chemistry.normal_modes.perceive_bonds`)."""

    adj: dict[int, set[int]] = {i: set() for i in range(1, len(symbols) + 1)}
    for a, b in perceive_bonds(list(symbols), np.asarray(coords, dtype=float)):
        adj[a].add(b)
        adj[b].add(a)
    return adj


def derive_top(adj: dict[int, set[int]], pivots: tuple[int, int]) -> list[int]:
    """1-indexed rotating-top atom set: the side of the pivot bond reachable
    from ``pivots[0]`` once the bond is cut, ``pivots[0]`` included (as
    rmgpy's reduced-moment routine expects). Raises on a ring rotor."""

    p0, p1 = pivots
    seen = {p0}
    stack = [p0]
    while stack:
        node = stack.pop()
        for nb in adj[node]:
            if node == p0 and nb == p1:
                continue
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    if p1 in seen:
        raise ValueError(f"pivot bond {pivots} is in a ring; not a simple single-bond rotor")
    return sorted(seen)


def principal_moments(symbols, coords) -> tuple[np.ndarray, float]:
    """Principal moments of inertia (amu*angstrom^2) and total mass."""

    m = np.array([ATOMIC_MASS[s] for s in symbols])
    coords = np.asarray(coords, dtype=float)
    com = (m[:, None] * coords).sum(0) / m.sum()
    r = coords - com
    tensor = np.zeros((3, 3))
    for mi, ri in zip(m, r, strict=True):
        tensor += mi * (np.dot(ri, ri) * np.eye(3) - np.outer(ri, ri))
    return np.linalg.eigvalsh(tensor), float(m.sum())


def eval_nasa_cp(nasa: dict, temperature: float) -> float:
    """Cp (J/mol/K) from a NASA-7 polynomial dictionary at a temperature."""

    c = nasa["low"] if temperature <= nasa["t_mid"] else nasa["high"]
    cp_over_r = c[0] + c[1] * temperature + c[2] * temperature**2 + c[3] * temperature**3 + c[4] * temperature**4
    return cp_over_r * R_GAS


# --------------------------------------------------------------------------- #
# Gather one statmech record from the database
# --------------------------------------------------------------------------- #
class Skip(Exception):
    """A record that cannot be replayed, with the reason."""


def _source_calculations(statmech: Statmech) -> dict[str, Calculation]:
    """One calculation per role; the first (lowest id) when several share a role."""

    by_role: dict[str, Calculation] = {}
    for row in sorted(statmech.source_calculations, key=lambda r: (r.role.value, r.calculation_id)):
        by_role.setdefault(row.role.value, row.calculation)
    return by_role


def _geometry_of(session: Session, calc: Calculation):
    inputs = sorted(calc.input_geometries, key=lambda g: g.input_order)
    if not inputs:
        return None, []
    geometry = inputs[0].geometry
    atoms = list(
        session.scalars(
            select(GeometryAtom).where(GeometryAtom.geometry_id == geometry.id).order_by(GeometryAtom.atom_index)
        ).all()
    )
    return geometry, atoms


def gather_species(session: Session, statmech: Statmech) -> dict:
    """Collect every datum Arkane needs for one statmech record, recording
    where each came from. Raises :class:`Skip` with the reason when a
    load-bearing piece is absent."""

    data: dict = {
        "statmech_ref": statmech.public_ref,
        "species_entry_ref": None,
        "smiles": None,
        "sources": {},
    }
    entry = statmech.species_entry
    if entry is None:
        raise Skip("statmech record is not attached to a species entry (transition-state statmech is out of scope)")
    data["species_entry_ref"] = entry.public_ref
    data["smiles"] = entry.species.smiles
    data["multiplicity"] = int(entry.species.multiplicity)
    data["sources"]["multiplicity"] = "species.multiplicity"

    if statmech.external_symmetry is None:
        raise Skip("no_external_symmetry")
    data["external_symmetry"] = int(statmech.external_symmetry)
    data["point_group"] = statmech.point_group
    data["sources"]["external_symmetry"] = "statmech.external_symmetry"

    fsf = statmech.frequency_scale_factor
    data["freq_scale_factor"] = float(fsf.value) if fsf is not None else 1.0
    data["sources"]["freq_scale_factor"] = (
        "frequency_scale_factor.value" if fsf is not None else "none stored; 1.0 assumed"
    )

    roles = _source_calculations(statmech)
    freq_calc = roles.get(StatmechCalculationRole.freq.value)
    if freq_calc is None:
        raise Skip("no_frequency_calculation")
    data["freq_ref"] = freq_calc.public_ref
    sp_calc = roles.get(StatmechCalculationRole.sp.value)
    data["sp_ref"] = sp_calc.public_ref if sp_calc is not None else None
    data["opt_ref"] = roles[StatmechCalculationRole.opt.value].public_ref if StatmechCalculationRole.opt.value in roles else None

    modes = list(
        session.scalars(
            select(CalculationFreqMode)
            .where(CalculationFreqMode.calculation_id == freq_calc.id)
            .order_by(CalculationFreqMode.mode_index)
        ).all()
    )
    if not modes:
        raise Skip("no_frequencies")
    data["frequencies_cm1"] = [float(m.frequency_cm1) for m in modes]
    data["sources"]["frequencies"] = "calc_freq_mode.frequency_cm1 (freq calculation)"
    if any(f < 0.0 for f in data["frequencies_cm1"]):
        raise Skip("imaginary_frequency_on_a_minimum")

    geometry, atoms = _geometry_of(session, freq_calc)
    if geometry is None or not atoms:
        raise Skip("no_geometry")
    symbols = [atom.element.strip() for atom in atoms]
    missing = sorted({s for s in symbols if s not in ATOMIC_MASS})
    if missing:
        raise Skip(f"element_mass_unavailable:{','.join(missing)}")
    data["symbols"] = symbols
    data["coords"] = np.array([[atom.x, atom.y, atom.z] for atom in atoms], dtype=float)
    data["geometry_ref"] = geometry.public_ref
    data["sources"]["geometry"] = "freq calculation input geometry"

    if statmech.is_linear is not None:
        data["is_linear"] = bool(statmech.is_linear)
        data["sources"]["is_linear"] = "statmech.is_linear"
    else:
        moments, _ = principal_moments(symbols, data["coords"])
        data["is_linear"] = bool(moments[0] <= 1e-6 * max(moments[-1], 1e-12))
        data["sources"]["is_linear"] = "derived from principal moments (statmech.is_linear NULL)"

    freq_result = freq_calc.freq_result
    data["zpe_hartree"] = float(freq_result.zpe_hartree) if freq_result is not None and freq_result.zpe_hartree is not None else None
    sp_result = sp_calc.sp_result if sp_calc is not None else None
    data["electronic_energy_hartree"] = (
        float(sp_result.electronic_energy_hartree)
        if sp_result is not None and sp_result.electronic_energy_hartree is not None
        else None
    )
    if data["electronic_energy_hartree"] is not None and data["zpe_hartree"] is not None:
        data["e0_kj_mol"] = (data["electronic_energy_hartree"] + data["zpe_hartree"]) * HARTREE_TO_KJMOL
        data["sources"]["e0"] = "sp electronic energy + freq ZPE (no corrections)"
    else:
        data["e0_kj_mol"] = 0.0
        data["sources"]["e0"] = "0.0 assumed (sp energy or ZPE missing); S298 and Cp(T) unaffected"

    data["optical_isomers_stored"] = statmech.optical_isomers
    data["optical_isomers_from_pg"] = optical_isomers_from_point_group(statmech.point_group)
    if statmech.optical_isomers is not None:
        data["optical_isomers"] = int(statmech.optical_isomers)
        data["sources"]["optical_isomers"] = "statmech.optical_isomers"
    elif data["optical_isomers_from_pg"] is not None:
        data["optical_isomers"] = data["optical_isomers_from_pg"]
        data["sources"]["optical_isomers"] = (
            f"derived from statmech.point_group={statmech.point_group} (optical_isomers NULL)"
        )
    else:
        data["optical_isomers"] = 1
        data["sources"]["optical_isomers"] = "default 1 (no stored value, no point group)"

    thermo = _stored_thermo(statmech)
    data["thermo_ref"] = thermo.public_ref if thermo is not None else None
    data["stored_s298"] = float(thermo.s298_j_mol_k) if thermo is not None and thermo.s298_j_mol_k is not None else None
    data["stored_h298_kj_mol"] = float(thermo.h298_kj_mol) if thermo is not None and thermo.h298_kj_mol is not None else None
    data["stored_nasa"] = None
    if thermo is not None and thermo.nasa is not None and thermo.nasa.t_mid is not None:
        n = thermo.nasa
        low = [n.a1, n.a2, n.a3, n.a4, n.a5, n.a6, n.a7]
        high = [n.b1, n.b2, n.b3, n.b4, n.b5, n.b6, n.b7]
        if all(c is not None for c in low + high):
            data["stored_nasa"] = {"t_mid": float(n.t_mid), "low": [float(c) for c in low], "high": [float(c) for c in high]}
    data["sources"]["stored_thermo"] = "thermo rows with statmech_id = this record" if thermo is not None else "none"

    gather_rotors(session, statmech, data)
    return data


def _stored_thermo(statmech: Statmech) -> Thermo | None:
    """The thermo record derived from this statmech: prefer one with S298
    and a NASA polynomial, lowest id first."""

    candidates = sorted(statmech.thermo_records, key=lambda t: t.id)
    for thermo in candidates:
        if thermo.s298_j_mol_k is not None and thermo.nasa is not None:
            return thermo
    for thermo in candidates:
        if thermo.s298_j_mol_k is not None:
            return thermo
    return candidates[0] if candidates else None


def gather_rotors(session: Session, statmech: Statmech, data: dict) -> None:
    """Hindered-rotor inputs per torsion: symmetry, pivots (stored), top
    (derived), potential (scan points, stored)."""

    adj = build_bond_graph(data["symbols"], data["coords"])
    rotors = []
    for torsion in sorted(statmech.torsions, key=lambda t: t.torsion_index):
        if torsion.treatment_kind is not TorsionTreatmentKind.hindered_rotor:
            continue
        if torsion.dimension != 1:
            raise Skip(f"torsion_{torsion.torsion_index}_is_{torsion.dimension}d")
        coordinates = sorted(torsion.coordinates, key=lambda c: c.coordinate_index)
        if not coordinates:
            raise Skip(f"torsion_{torsion.torsion_index}_has_no_definition")
        pivots = (int(coordinates[0].atom2_index), int(coordinates[0].atom3_index))
        try:
            top = derive_top(adj, pivots)
        except ValueError as exc:
            raise Skip(f"torsion_{torsion.torsion_index}_top_underivable:{exc}") from exc
        scan = torsion.source_scan_calculation
        if scan is None:
            raise Skip(f"torsion_{torsion.torsion_index}_has_no_scan")
        if torsion.symmetry_number is None:
            raise Skip(f"torsion_{torsion.torsion_index}_has_no_symmetry_number")

        points = list(
            session.scalars(
                select(CalculationScanPoint)
                .where(CalculationScanPoint.calculation_id == scan.id)
                .order_by(CalculationScanPoint.point_index)
            ).all()
        )
        angles_deg: list[float] = []
        v_kjmol: list[float] = []
        for point in points:
            values = session.scalars(
                select(CalculationScanPointCoordinateValue)
                .where(
                    CalculationScanPointCoordinateValue.calculation_id == scan.id,
                    CalculationScanPointCoordinateValue.point_index == point.point_index,
                )
                .order_by(CalculationScanPointCoordinateValue.coordinate_index)
            ).all()
            if not values or point.relative_energy_kj_mol is None:
                continue
            value = values[0]
            if value.value_unit is not None and value.value_unit is not CoordinateUnit.degree:
                raise Skip(f"torsion_{torsion.torsion_index}_scan_not_in_degrees")
            angles_deg.append(float(value.coordinate_value))
            v_kjmol.append(float(point.relative_energy_kj_mol))
        if len(angles_deg) < 3:
            raise Skip(f"torsion_{torsion.torsion_index}_scan_has_{len(angles_deg)}_usable_points")
        # Drop a duplicate 360-degree endpoint (scan wraps 0..360).
        if len(angles_deg) >= 2 and abs((angles_deg[-1] - angles_deg[0]) - 360.0) < 1e-3:
            angles_deg = angles_deg[:-1]
            v_kjmol = v_kjmol[:-1]

        rotors.append(
            {
                "torsion_index": torsion.torsion_index,
                "pivots": list(pivots),
                "top": top,
                "symmetry": int(torsion.symmetry_number),
                "scan_ref": scan.public_ref,
                "top_description_stored": torsion.top_description,
                "n_scan_points": len(angles_deg),
                "barrier_kj_mol": max(v_kjmol) - min(v_kjmol),
                "angles_rad": [math.radians(a) for a in angles_deg],
                "v_j_mol": [v * 1000.0 for v in v_kjmol],
            }
        )
    data["rotors"] = rotors
    if rotors:
        data["sources"]["rotor_symmetry"] = "statmech_torsion.symmetry_number"
        data["sources"]["rotor_pivots"] = "statmech_torsion_definition atom2/atom3"
        data["sources"]["rotor_potential"] = "calc_scan_point.relative_energy_kj_mol vs coordinate value"
        data["sources"]["rotor_top"] = "derived from geometry connectivity (top_description NULL)"


# --------------------------------------------------------------------------- #
# Rotor terms (rmg_env) and the deck
# --------------------------------------------------------------------------- #
_ROTOR_SNIPPET = r'''
import json, sys
import numpy as np
from rmgpy.statmech import Conformer, HinderedRotor
with open(sys.argv[1]) as _fh:  # conda run does not forward stdin reliably
    inp = json.load(_fh)
coords = np.array(inp["coords"], float)
masses = np.array(inp["masses"], float)
conf = Conformer(mass=(masses.tolist(), "amu"), coordinates=(coords.tolist(), "angstrom"))
out = []
KG_M2_PER_AMU_A2 = 1.66053906660e-47
for r in inp["rotors"]:
    i_si = conf.get_internal_reduced_moment_of_inertia(r["pivots"], r["top"], option=3)
    inertia_amu_a2 = i_si / KG_M2_PER_AMU_A2
    hr = HinderedRotor(inertia=(inertia_amu_a2, "amu*angstrom^2"), symmetry=1)
    hr.fit_fourier_potential_to_data(np.array(r["angles_rad"], float), np.array(r["v_j_mol"], float))
    out.append({"inertia_amu_a2": inertia_amu_a2, "fourier_kj_mol": (hr.fourier.value_si / 1000.0).tolist()})
json.dump(out, sys.stdout)
'''


def compute_rotor_terms(data: dict, *, env: str = RMG_ENV) -> None:
    """Reduced moment of inertia (rmgpy option=3) and Fourier fit per rotor,
    computed by rmgpy itself in ``env`` so they match Arkane exactly, then
    embedded as literals in the deck."""

    if not data.get("rotors"):
        return
    payload = {
        "symbols": data["symbols"],
        "coords": np.asarray(data["coords"]).tolist(),
        "masses": [ATOMIC_MASS[s] for s in data["symbols"]],
        "rotors": [
            {"pivots": r["pivots"], "top": r["top"], "angles_rad": r["angles_rad"], "v_j_mol": r["v_j_mol"]}
            for r in data["rotors"]
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, prefix="rotor_terms_") as tf:
        json.dump(payload, tf)
        payload_path = tf.name
    try:
        proc = subprocess.run(
            ["conda", "run", "-n", env, "python", "-c", _ROTOR_SNIPPET, payload_path],
            capture_output=True,
            text=True,
        )
    finally:
        os.unlink(payload_path)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"rotor-term computation failed in {env}:\n{proc.stderr[-2000:]}")
    terms = json.loads(proc.stdout)
    for rotor, term in zip(data["rotors"], terms, strict=True):
        rotor["inertia_amu_a2"] = term["inertia_amu_a2"]
        rotor["fourier_kj_mol"] = term["fourier_kj_mol"]


def build_arkane_input(data: dict) -> str:
    """Render the Arkane ``input.py`` deck from gathered data, as text.

    Pure: no filesystem, no subprocess. Rotor entries must already carry
    ``inertia_amu_a2`` and ``fourier_kj_mol`` (from
    :func:`compute_rotor_terms`, or injected by a test).
    """

    moments, mol_weight = principal_moments(data["symbols"], data["coords"])
    scale = data["freq_scale_factor"]
    rotors = data.get("rotors") or []
    n_rotors = len(rotors)

    # Approximation 1 (module docstring): the R lowest stored frequencies
    # are taken to be the torsions and removed from the harmonic list.
    all_freqs = sorted(float(f) for f in data["frequencies_cm1"])
    dropped = all_freqs[:n_rotors]
    kept = all_freqs[n_rotors:]
    scaled_freqs = [round(f * scale, 4) for f in kept]

    rotor_blocks = ""
    for r in rotors:
        rotor_blocks += (
            f"        HinderedRotor(inertia=({r['inertia_amu_a2']:.10f}, 'amu*angstrom^2'), "
            f"symmetry={r['symmetry']}, fourier=({r['fourier_kj_mol']}, 'kJ/mol')),"
            f"  # torsion {r['torsion_index']}: pivots={r['pivots']} top={r['top']}\n"
        )

    if data["is_linear"]:
        rotor = f"LinearRotor(inertia=({max(moments):.10f}, 'amu*angstrom^2'), symmetry={data['external_symmetry']})"
    else:
        rotor = (
            f"NonlinearRotor(inertia=([{moments[0]:.10f}, {moments[1]:.10f}, {moments[2]:.10f}], "
            f"'amu*angstrom^2'), symmetry={data['external_symmetry']})"
        )

    label = "SPC"
    use_rotors = "True" if n_rotors else "False"
    deck = f"""#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Arkane input assembled purely from TCKDB-stored data for {data['smiles']}
# species_entry_ref={data['species_entry_ref']}  statmech_ref={data['statmech_ref']}
# hindered rotors: {n_rotors} (torsional freqs dropped from HO list: {[round(x, 2) for x in dropped]})
# approximations: R lowest frequencies dropped in place of Hessian projection; no atom/bond corrections
useHinderedRotors = {use_rotors}
useAtomCorrections = False
useBondCorrections = False

species('{label}',
    E0 = ({data['e0_kj_mol']:.6f}, 'kJ/mol'),
    modes = [
        IdealGasTranslation(mass=({mol_weight:.6f}, 'amu')),
        {rotor},
        HarmonicOscillator(frequencies=({scaled_freqs}, 'cm^-1')),
{rotor_blocks}    ],
    spinMultiplicity = {data['multiplicity']},
    opticalIsomers = {data['optical_isomers']},
)
thermo('{label}', 'NASA')
"""
    data["_label"] = label
    data["_moments"] = [float(x) for x in moments]
    data["_mol_weight"] = mol_weight
    data["_scaled_freqs"] = scaled_freqs
    data["_dropped_freqs"] = dropped
    return deck


def deck_summary(data: dict) -> dict:
    """What went into the deck, JSON-ready."""

    return {
        "external_symmetry": data["external_symmetry"],
        "point_group": data.get("point_group"),
        "is_linear": data["is_linear"],
        "multiplicity": data["multiplicity"],
        "optical_isomers": data["optical_isomers"],
        "optical_isomers_stored": data.get("optical_isomers_stored"),
        "optical_isomers_from_point_group": data.get("optical_isomers_from_pg"),
        "frequency_scale_factor": data["freq_scale_factor"],
        "n_frequencies_stored": len(data["frequencies_cm1"]),
        "n_harmonic_oscillators": len(data.get("_scaled_freqs", [])),
        "dropped_frequencies_cm1": [round(x, 4) for x in data.get("_dropped_freqs", [])],
        "principal_moments_amu_a2": [round(x, 6) for x in data.get("_moments", [])],
        "molecular_weight_amu": round(data["_mol_weight"], 6) if "_mol_weight" in data else None,
        "e0_kj_mol": round(data["e0_kj_mol"], 6),
        "rotors": [
            {
                "torsion_index": r["torsion_index"],
                "pivots": r["pivots"],
                "top": r["top"],
                "symmetry": r["symmetry"],
                "scan_ref": r["scan_ref"],
                "n_scan_points": r["n_scan_points"],
                "barrier_kj_mol": round(r["barrier_kj_mol"], 6),
                "inertia_amu_a2": (round(r["inertia_amu_a2"], 6) if "inertia_amu_a2" in r else None),
            }
            for r in data.get("rotors", [])
        ],
        "sources": data["sources"],
    }


# --------------------------------------------------------------------------- #
# Arkane run and comparison
# --------------------------------------------------------------------------- #
def run_arkane(deck_path: str, *, entry: str, env: str = RMG_ENV) -> str:
    """Run Arkane in ``env``; return ``output.py``."""

    work_dir = os.path.dirname(deck_path)
    proc = subprocess.run(
        ["conda", "run", "-n", env, "python", entry, deck_path],
        capture_output=True,
        text=True,
        cwd=work_dir,
    )
    output_path = os.path.join(work_dir, "output.py")
    if not os.path.isfile(output_path):
        raise RuntimeError(
            f"Arkane did not produce output.py.\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}"
        )
    with open(output_path) as fh:
        return fh.read()


def parse_arkane_output(output: str) -> dict:
    """S298, H298 and Cp(T) from Arkane's ``output.py`` comment table."""

    result: dict = {"cp_cal": {}}
    for line in output.splitlines():
        s = line.strip().lstrip("#").strip()
        if s.startswith("Entropy of formation (298 K)"):
            result["s298_cal"] = float(s.split("=")[1].split()[0])
        elif s.startswith("Enthalpy of formation (298 K)"):
            result["h298_kcal"] = float(s.split("=")[1].split()[0])
        else:
            parts = s.split()
            if len(parts) == 5:
                try:
                    temp = float(parts[0])
                    cp = float(parts[1])
                except ValueError:
                    continue
                result["cp_cal"][temp] = cp
    result["s298_j"] = result.get("s298_cal", float("nan")) * CAL_TO_J
    result["h298_kj"] = result.get("h298_kcal", float("nan")) * CAL_TO_J
    return result


def compare(data: dict, arkane: dict, *, s298_tolerance: float, cp_tolerance_percent: float) -> dict:
    """Deviations against the stored thermo, and whether they are inside
    the declared tolerance."""

    out: dict = {"s298": None, "cp": {}, "h298": None, "within_tolerance": None}
    if data["stored_s298"] is not None and not math.isnan(arkane["s298_j"]):
        delta = arkane["s298_j"] - data["stored_s298"]
        out["s298"] = {
            "stored_j_mol_k": round(data["stored_s298"], 6),
            "arkane_j_mol_k": round(arkane["s298_j"], 6),
            "abs_delta_j_mol_k": round(abs(delta), 6),
            "pct_delta": round(100.0 * abs(delta) / data["stored_s298"], 6),
        }
    if data["stored_nasa"] is not None:
        for temp in CP_TEMPERATURES:
            cp_ark = arkane["cp_cal"].get(temp)
            if cp_ark is None:
                continue
            cp_stored = eval_nasa_cp(data["stored_nasa"], temp)
            cp_ark_j = cp_ark * CAL_TO_J
            out["cp"][str(int(temp))] = {
                "stored_j_mol_k": round(cp_stored, 6),
                "arkane_j_mol_k": round(cp_ark_j, 6),
                "abs_delta_j_mol_k": round(abs(cp_ark_j - cp_stored), 6),
                "pct_delta": round(100.0 * abs(cp_ark_j - cp_stored) / cp_stored, 6),
            }
    if data["stored_h298_kj_mol"] is not None and not math.isnan(arkane["h298_kj"]):
        out["h298"] = {
            "stored_formation_kj_mol": round(data["stored_h298_kj_mol"], 6),
            "arkane_absolute_no_corrections_kj_mol": round(arkane["h298_kj"], 6),
            "comparable": False,
            "note": "absolute enthalpy without atom/bond corrections; not a completeness measure",
        }
    checks = []
    if out["s298"] is not None:
        checks.append(out["s298"]["abs_delta_j_mol_k"] <= s298_tolerance)
    checks.extend(row["pct_delta"] <= cp_tolerance_percent for row in out["cp"].values())
    out["within_tolerance"] = all(checks) if checks else None
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def replay_record(
    session: Session,
    statmech: Statmech,
    *,
    rmg: dict,
    skip_arkane: bool,
    keep_dir: str | None,
    s298_tolerance: float,
    cp_tolerance_percent: float,
) -> dict:
    """One statmech record end to end. Never raises; the status says what happened."""

    record: dict = {
        "statmech_ref": statmech.public_ref,
        "species_entry_ref": None,
        "smiles": None,
        "status": None,
        "skip_reason": None,
        "deck": None,
        "comparison": None,
    }
    try:
        data = gather_species(session, statmech)
    except Skip as exc:
        record["status"] = "skipped"
        record["skip_reason"] = str(exc)
        return record
    record["species_entry_ref"] = data["species_entry_ref"]
    record["smiles"] = data["smiles"]
    record["thermo_ref"] = data["thermo_ref"]

    arkane_possible = rmg["available"] and not skip_arkane
    if data.get("rotors") and arkane_possible:
        try:
            compute_rotor_terms(data, env=rmg["env"])
        except RuntimeError as exc:
            record["status"] = "arkane_failed"
            record["skip_reason"] = f"rotor terms: {exc}"[:600]
            return record
    elif data.get("rotors"):
        for rotor in data["rotors"]:
            rotor.setdefault("inertia_amu_a2", float("nan"))
            rotor.setdefault("fourier_kj_mol", [])

    deck = build_arkane_input(data)
    record["deck"] = deck_summary(data)

    if not arkane_possible:
        record["status"] = "arkane_skipped"
        record["skip_reason"] = "--skip-arkane" if skip_arkane else rmg.get("reason", "rmg environment unavailable")
        return record
    if data["stored_s298"] is None and data["stored_nasa"] is None:
        record["status"] = "skipped"
        record["skip_reason"] = "no_stored_thermo_to_compare"
        return record

    work_dir = (
        os.path.join(keep_dir, statmech.public_ref or f"statmech_{statmech.id}")
        if keep_dir
        else tempfile.mkdtemp(prefix="arkane_rt_")
    )
    os.makedirs(work_dir, exist_ok=True)
    deck_path = os.path.join(work_dir, "input.py")
    with open(deck_path, "w") as fh:
        fh.write(deck)
    try:
        output = run_arkane(deck_path, entry=rmg["arkane_entry"], env=rmg["env"])
    except RuntimeError as exc:
        record["status"] = "arkane_failed"
        record["skip_reason"] = str(exc)[:1200]
        return record
    finally:
        if not keep_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
    arkane = parse_arkane_output(output)
    record["comparison"] = compare(data, arkane, s298_tolerance=s298_tolerance, cp_tolerance_percent=cp_tolerance_percent)
    record["status"] = "compared"
    return record


def build_report(record: dict, rmg: dict) -> str:
    """Human-readable block for one record."""

    lines = []
    p = lines.append
    p("=" * 72)
    p(f"ARKANE STATMECH REPLAY  {record['statmech_ref']}  ({record.get('smiles')})  status={record['status']}")
    p("=" * 72)
    if record["skip_reason"]:
        p(f"  reason: {record['skip_reason']}")
    deck = record.get("deck")
    if deck:
        p(f"  external symmetry {deck['external_symmetry']}  point group {deck['point_group']}  "
          f"linear {deck['is_linear']}  multiplicity {deck['multiplicity']}  optical isomers {deck['optical_isomers']} "
          f"(stored {deck['optical_isomers_stored']}, from point group {deck['optical_isomers_from_point_group']})")
        p(f"  frequencies: {deck['n_frequencies_stored']} stored, {deck['n_harmonic_oscillators']} as HO, "
          f"dropped {deck['dropped_frequencies_cm1']} cm^-1 as torsions; scale {deck['frequency_scale_factor']}")
        for r in deck["rotors"]:
            p(f"  rotor {r['torsion_index']}: pivots={r['pivots']} top={r['top']} sym={r['symmetry']} "
              f"I_red={r['inertia_amu_a2']} amu*A^2 barrier={r['barrier_kj_mol']:.2f} kJ/mol ({r['n_scan_points']} pts)")
    comparison = record.get("comparison")
    if comparison:
        if comparison["s298"]:
            s = comparison["s298"]
            p(f"  S298  stored {s['stored_j_mol_k']:.3f}  arkane {s['arkane_j_mol_k']:.3f}  "
              f"|d| {s['abs_delta_j_mol_k']:.3f} J/mol/K ({s['pct_delta']:.4f} %)")
        for temp, row in comparison["cp"].items():
            p(f"  Cp({temp} K)  stored {row['stored_j_mol_k']:.3f}  arkane {row['arkane_j_mol_k']:.3f}  "
              f"|d| {row['abs_delta_j_mol_k']:.3f} ({row['pct_delta']:.4f} %)")
        if comparison["h298"]:
            h = comparison["h298"]
            p(f"  H298  stored formation {h['stored_formation_kj_mol']:.3f}  arkane absolute (no corrections) "
              f"{h['arkane_absolute_no_corrections_kj_mol']:.3f}  -- not comparable")
        p(f"  within declared tolerance: {comparison['within_tolerance']}")
    p(f"  rmgpy {rmg.get('rmgpy_version')}  RMG-Py {rmg.get('rmg_py_git')}  env {rmg.get('env')}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = ap.add_mutually_exclusive_group(required=True)
    scope.add_argument("--species-entry-ref", help="replay every statmech record of one species entry")
    scope.add_argument("--statmech-ref", help="replay one statmech record")
    scope.add_argument("--all-statmech", action="store_true", help="replay every statmech record")
    ap.add_argument("--json-out", type=Path, default=None, help="write the JSON summary here")
    ap.add_argument("--skip-arkane", action="store_true", help="assemble decks only; do not run Arkane")
    ap.add_argument("--keep", type=Path, default=None, help="keep each record's Arkane scratch directory under this path")
    ap.add_argument("--s298-tolerance-j-mol-k", type=float, default=DEFAULT_S298_TOLERANCE_J_MOL_K)
    ap.add_argument("--cp-tolerance-percent", type=float, default=DEFAULT_CP_TOLERANCE_PERCENT)
    ap.add_argument("--quiet", action="store_true", help="print only the summary line")
    args = ap.parse_args()

    rmg = {"env": RMG_ENV, "available": False, "reason": "--skip-arkane"} if args.skip_arkane else probe_rmg_environment()
    if not args.skip_arkane and not rmg["available"]:
        print(f"Arkane step SKIPPED: {rmg.get('reason')}. Decks are assembled; nothing is compared.")

    from app.api.deps import SessionLocal

    records: list[dict] = []
    with SessionLocal() as session:
        targets = statmech_scope(
            session,
            statmech_ref=args.statmech_ref,
            species_entry_ref=args.species_entry_ref,
            all_statmech=args.all_statmech,
        )
        for statmech in targets:
            record = replay_record(
                session,
                statmech,
                rmg=rmg,
                skip_arkane=args.skip_arkane,
                keep_dir=str(args.keep) if args.keep else None,
                s298_tolerance=args.s298_tolerance_j_mol_k,
                cp_tolerance_percent=args.cp_tolerance_percent,
            )
            records.append(record)
            if not args.quiet:
                print(build_report(record, rmg))

    by_status: dict[str, int] = {}
    for record in records:
        by_status[record["status"]] = by_status.get(record["status"], 0) + 1
    compared = [r for r in records if r["status"] == "compared"]
    exceeding = [r for r in compared if r["comparison"]["within_tolerance"] is False]
    failed = [r for r in records if r["status"] == "arkane_failed"]
    summary = {
        "generator": "arkane_statmech_replay",
        "rmg": {k: rmg.get(k) for k in ("env", "available", "rmgpy_version", "rmg_py_git", "arkane_entry", "reason")},
        "approximations": list(APPROXIMATIONS),
        "tolerance": {
            "s298_j_mol_k": args.s298_tolerance_j_mol_k,
            "cp_percent": args.cp_tolerance_percent,
            "cp_temperatures_k": list(CP_TEMPERATURES),
        },
        "scope": {
            "statmech_count": len(records),
            "by_status": dict(sorted(by_status.items())),
            "compared_count": len(compared),
            "within_tolerance_count": len(compared) - len(exceeding),
            "exceeding_count": len(exceeding),
            "arkane_failed_count": len(failed),
        },
        "records": records,
    }
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")

    print(
        f"\n{len(records)} statmech record(s) in scope; {len(compared)} compared; "
        f"{len(exceeding)} exceeding; {len(failed)} Arkane failure(s); "
        f"statuses {summary['scope']['by_status']}"
    )
    if not compared:
        print("RESULT: NOTHING COMPARED.")
        return EXIT_NOTHING_COMPARED
    if exceeding or failed:
        for record in exceeding:
            print(f"  exceeds tolerance: {record['statmech_ref']} ({record.get('smiles')})")
        for record in failed:
            print(f"  Arkane failed: {record['statmech_ref']}: {record['skip_reason'][:200]}")
        print("RESULT: EXCEEDED.")
        return EXIT_EXCEEDED
    print("RESULT: every compared record is within the declared tolerance.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
