#!/usr/bin/env python
"""Arkane statmech/thermo round-trip validation for a single TCKDB species.

Paper validation exhibit paralleling the Cantera/CHEMKIN round-trip. It proves
(or disproves) TCKDB's "statmech-completeness" claim: that TCKDB stores enough
structural/vibrational data (geometry, harmonic frequencies, external symmetry,
spin multiplicity, optical isomers, energy) to REGENERATE a species' thermo with
Arkane, without the original ESS output files.

What it does
------------
1. Reads one species entirely from the LIVE TCKDB read API (public
   ``/scientific/*`` endpoints, no auth) plus two DB-only fields (see below).
2. Assembles an Arkane ``thermo('NASA')`` input purely from TCKDB-stored data,
   using explicit statmech ``modes`` (IdealGasTranslation / NonlinearRotor /
   HarmonicOscillator, plus ``HinderedRotor`` for floppy species) — the moments
   of inertia are computed here from the TCKDB geometry + atomic masses, which
   is exactly the completeness claim.
3. Runs Arkane in the ``rmg_env`` conda environment (via subprocess).
4. Numerically compares Arkane's recomputed S298 and Cp(T) against the thermo
   TCKDB already stores for that species.

Hindered rotors (floppy species)
---------------------------------
For species with torsions, each rotor is reconstructed from TCKDB:
  * SYMMETRY number and PIVOT atoms          -> API statmech ``torsions``
  * scan POTENTIAL (energy vs dihedral)      -> API ``/calculations/{ref}/scan``
  * rotating-TOP atom set                    -> DERIVED from stored geometry
    connectivity (``torsion.top_description`` is NULL; the top is a
    deterministic graph property of an acyclic single-bond rotor)
The reduced moment of inertia (rmgpy option=3) and the Fourier potential fit
are computed with rmgpy (in ``rmg_env``) so they match Arkane exactly, then
embedded as literals. The R torsional modes are dropped from the harmonic list
(the R lowest stored frequencies) so they are not double-counted. Example:

    conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py \
        --species-entry-ref spe_sgidibgknrjbvcetgc6xsej74q   # ethylperoxy CCO[O]

Primary comparison targets are S298 and Cp(300/500/1000/1500 K): they depend
only on geometry + frequencies + symmetry number + multiplicity + optical
isomers, and NOT on the absolute-energy reference. H298 of formation is a
secondary/stretch target that additionally needs the same atom-energy /
bond-correction scheme ARC used originally; a mismatch there is a
correction-reference difference, NOT a statmech-completeness failure.

Data accessibility split (a finding in its own right)
-----------------------------------------------------
Most data is reachable over the public read API, including the full rotor scan
potential (``/calculations/{ref}/scan``) and torsion topology. Pieces NOT on the
API (read directly from the DB, read-only) or not stored at all:
  * per-mode harmonic frequencies   -> table ``calc_freq_mode.frequency_cm1``
                                       (DB only; not on the API)
  * ``statmech.optical_isomers``     -> column exists but is NULL for the ARC
                                       corpus and omitted from the payload; the
                                       harness instead derives it from the
                                       API-served ``point_group`` (C1 -> 2)
  * rotor top atom set               -> ``torsion.top_description`` is NULL;
                                       derived here from geometry connectivity

Usage
-----
    conda run -n tckdb_env python backend/scripts/validation/arkane_statmech_roundtrip.py \
        --species-entry-ref spe_oxmflzmwl4xzkeujaj3oj3efl4

Run this in ``tckdb_env`` (needs numpy + requests-free stdlib). Arkane itself is
invoked as a subprocess in ``rmg_env``. Read-only against the live Pi.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile

import numpy as np

# Deployment-specific endpoints are read from the environment so this harness
# is reproducible against any TCKDB instance and no infra topology is committed.
# Set before running:
#   TCKDB_API_BASE      scientific read API base, e.g. https://<host>/api/v1/scientific
#   TCKDB_DB_SSH        ssh target for the DB host (ONLY needed for the two
#                       DB-only statmech fields; see docs/validation findings)
#   TCKDB_DB_CONTAINER  postgres container name (default: tckdbv2-db-1)
#   ARKANE_ENTRY        path to RMG-Py Arkane.py
#   RMG_ENV             conda env that has Arkane (default: rmg_env)
API_BASE = os.environ.get(
    "TCKDB_API_BASE", "http://localhost:8000/api/v1/scientific"
)
PI_SSH = os.environ.get("TCKDB_DB_SSH")  # required only for the DB-only fields
DB_CONTAINER = os.environ.get("TCKDB_DB_CONTAINER", "tckdbv2-db-1")
ARKANE_ENTRY = os.environ.get("ARKANE_ENTRY", "/home/calvin/code/RMG-Py/Arkane.py")
RMG_ENV = os.environ.get("RMG_ENV", "rmg_env")

HARTREE_TO_KJMOL = 2625.499638
R_GAS = 8.31446  # J/mol/K
CAL_TO_J = 4.184

# Standard atomic weights (g/mol); matches rmgpy element masses closely enough
# that principal moments of inertia agree to well within our tolerance.
ATOMIC_MASS = {
    "H": 1.00794, "D": 2.01410, "C": 12.0107, "N": 14.0067,
    "O": 15.9994, "F": 18.9984, "S": 32.065, "Cl": 35.453,
    "P": 30.9738, "Br": 79.904,
}

CP_TEMPERATURES = (300.0, 500.0, 1000.0, 1500.0)

AMU_ANGSTROM2_TO_KG_M2 = 1.66053906660e-47  # 1 amu*angstrom^2 in kg*m^2

# Covalent radii (angstrom) — only used to reconstruct the molecular bond graph
# from the stored geometry so the rotating-top atom set can be derived (TCKDB
# does not store the top; see docs/validation findings). Distance-based bonds
# are unambiguous for the small, well-optimized species this harness targets.
COVALENT_RADII = {
    "H": 0.31, "D": 0.31, "C": 0.76, "N": 0.71, "O": 0.66,
    "F": 0.57, "S": 1.05, "Cl": 1.02, "P": 1.07, "Br": 1.20,
}


# --------------------------------------------------------------------------- #
# Live data access
# --------------------------------------------------------------------------- #
def optical_isomers_from_point_group(point_group: str | None) -> int | None:
    """Infer optical-isomer count from a Schoenflies point group.

    A point group is *chiral* (2 optical isomers) iff it contains only proper
    rotations — i.e. the pure-rotation groups C1, Cn, Dn, T, O, I. Any group
    with an improper element (a mirror plane, inversion centre, or Sn axis:
    Cs, Ci, Cnv, Cnh, Dnh, Dnd, Sn, Td, Oh, Ih ...) is achiral (1). This mirrors
    Arkane's own optical-isomer determination and lets the round-trip recover
    the value from the *stored* ``point_group`` when the dedicated
    ``optical_isomers`` column is NULL.
    """
    if not point_group:
        return None
    pg = point_group.strip()
    if pg in {"T", "O", "I"}:
        return 2
    import re as _re
    if _re.fullmatch(r"C\d+", pg) or _re.fullmatch(r"D\d+", pg):
        return 2  # pure-rotation groups Cn / Dn (includes C1) -> chiral
    return 1


def api_get(path: str) -> dict:
    """GET a public scientific read endpoint and return parsed JSON."""
    url = f"{API_BASE}{path}"
    sep = "&" if "?" in url else "?"
    # under_review is required: the ARC corpus is all under_review and the API
    # defaults to min_review_status=approved (which returns zero rows).
    if "min_review_status" not in url:
        url = f"{url}{sep}min_review_status=under_review"
    out = subprocess.run(
        ["curl", "-sf", url], capture_output=True, text=True
    )
    if out.returncode != 0:
        raise RuntimeError(f"API GET failed: {url}\n{out.stderr}")
    return json.loads(out.stdout)


def db_query(sql: str) -> list[list[str]]:
    """Run a read-only psql query on the live Pi and return rows of columns."""
    if not PI_SSH:
        raise RuntimeError(
            "TCKDB_DB_SSH is not set. Per-mode frequencies and optical_isomers "
            "are DB-only (not on the public read API), so this harness needs an "
            "ssh target for the DB host to fetch them. Set TCKDB_DB_SSH."
        )
    remote = (
        f"docker exec {DB_CONTAINER} psql -U tckdb -d tckdb -t -A -F'|' "
        f"-c {shlex.quote(sql)}"
    )
    out = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", PI_SSH, remote],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"DB query failed:\n{out.stderr}")
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line:
            rows.append(line.split("|"))
    return rows


# --------------------------------------------------------------------------- #
# Assemble the species record from TCKDB
# --------------------------------------------------------------------------- #
def gather_species(species_entry_ref: str) -> dict:
    """Collect every datum Arkane needs, recording its source (API vs DB)."""
    data: dict = {"species_entry_ref": species_entry_ref, "sources": {}}

    # --- statmech record (API): symmetry, scale factor, multiplicity, refs ---
    sm = api_get(
        f"/species-entries/{species_entry_ref}/statmech?include=all"
    )["records"][0]
    stat = sm["statmech"]
    data["external_symmetry"] = stat["external_symmetry"]
    data["is_linear"] = stat["is_linear"]
    data["point_group"] = stat["point_group"]
    data["freq_scale_factor"] = stat["frequency_scale_factor_value"]
    data["multiplicity"] = sm["species"]["multiplicity"]
    data["smiles"] = sm["species"]["canonical_smiles"]
    data["statmech_ref"] = stat["statmech_ref"]
    data["sources"]["external_symmetry"] = "API statmech"
    data["sources"]["freq_scale_factor"] = "API statmech"
    data["sources"]["multiplicity"] = "API statmech"

    src = {c["role"]: c["calculation_ref"] for c in sm["source_calculations"]}
    data["opt_ref"], data["freq_ref"], data["sp_ref"] = (
        src.get("opt"), src.get("freq"), src.get("sp"),
    )

    # --- geometry (API) ---
    geom_ref = api_get(
        f"/calculations/{data['freq_ref']}?include=input_geometries"
    )["record"]["input_geometries"][0]["geometry_ref"]
    geom = api_get(f"/geometries/{geom_ref}")
    data["symbols"] = geom["symbols"]
    data["coords"] = np.array(geom["coords"], dtype=float)
    data["geometry_ref"] = geom_ref
    data["sources"]["geometry"] = "API geometry"

    # --- energies (API): SP electronic energy + freq ZPE ---
    sp_res = api_get(
        f"/calculations/{data['sp_ref']}?include=results"
    )["record"]["results"]["sp"]
    data["electronic_energy_hartree"] = sp_res["electronic_energy_hartree"]
    data["sources"]["electronic_energy"] = "API calc sp result"

    freq_res = api_get(
        f"/calculations/{data['freq_ref']}?include=results"
    )["record"]["results"]["freq"]
    data["zpe_hartree"] = freq_res["zpe_hartree"]
    data["sources"]["zpe"] = "API calc freq result"

    # --- per-mode harmonic frequencies (DB ONLY: not exposed by API) ---
    rows = db_query(
        "SELECT m.frequency_cm1 FROM calc_freq_mode m "
        "JOIN calculation c ON c.id = m.calculation_id "
        f"WHERE c.public_ref = '{data['freq_ref']}' ORDER BY m.mode_index;"
    )
    data["frequencies_cm1"] = [float(r[0]) for r in rows]
    data["sources"]["frequencies"] = "DB calc_freq_mode (API GAP)"

    # --- optical_isomers (DB ONLY: column exists, omitted from API payload) ---
    rows = db_query(
        "SELECT optical_isomers FROM statmech "
        f"WHERE public_ref = '{data['statmech_ref']}';"
    )
    raw_oi = rows[0][0] if rows and rows[0] else ""
    data["optical_isomers_stored"] = int(raw_oi) if raw_oi else None
    # When the dedicated column is NULL, fall back to the stored point group
    # (served on the API). C1/Cn/Dn are chiral -> 2. This is load-bearing for
    # floppy/chiral species: a wrong value shifts S298 by R*ln(2) ~ 5.76 J/mol/K.
    data["optical_isomers_from_pg"] = optical_isomers_from_point_group(
        data["point_group"]
    )
    if data["optical_isomers_stored"] is not None:
        data["optical_isomers"] = data["optical_isomers_stored"]
        data["sources"]["optical_isomers"] = "DB statmech (API GAP)"
    elif data["optical_isomers_from_pg"] is not None:
        data["optical_isomers"] = data["optical_isomers_from_pg"]
        data["sources"]["optical_isomers"] = (
            f"DERIVED from API point_group={data['point_group']} "
            "(statmech.optical_isomers NULL)"
        )
    else:
        data["optical_isomers"] = 1  # Arkane default
        data["sources"]["optical_isomers"] = "default=1 (no stored value)"

    # --- stored thermo (API): S298, H298, NASA ---
    thermo = api_get(
        f"/species-entries/{species_entry_ref}/thermo?include=all"
    )["records"][0]
    data["stored_s298"] = thermo["s298_j_mol_k"]
    data["stored_h298_kj_mol"] = thermo["h298_kj_mol"]
    data["stored_nasa"] = thermo["nasa"]
    data["thermo_ref"] = thermo["thermo_ref"]
    data["sources"]["stored_thermo"] = "API thermo"
    return data


# --------------------------------------------------------------------------- #
# Hindered-rotor data (torsions + scan potentials)
# --------------------------------------------------------------------------- #
def build_bond_graph(symbols, coords) -> dict[int, set[int]]:
    """Reconstruct a 1-indexed bond graph from geometry via covalent radii.

    Needed only because TCKDB does not store the rotor top atom set; the top is
    a deterministic graph property once bonds are known. A pair is bonded when
    their separation is < 1.3 x (r_cov(a) + r_cov(b)).
    """
    n = len(symbols)
    adj: dict[int, set[int]] = {i: set() for i in range(1, n + 1)}
    for i in range(n):
        for j in range(i + 1, n):
            d = float(np.linalg.norm(coords[i] - coords[j]))
            cutoff = 1.3 * (COVALENT_RADII[symbols[i]] + COVALENT_RADII[symbols[j]])
            if d < cutoff:
                adj[i + 1].add(j + 1)
                adj[j + 1].add(i + 1)
    return adj


def derive_top(adj: dict[int, set[int]], pivots: tuple[int, int]) -> list[int]:
    """1-indexed rotating-top atom set: the side of the pivot bond reachable
    from ``pivots[0]`` after the pivot bond is cut. Deterministic for an
    acyclic single-bond rotor (exactly two disconnected sides). Includes the
    pivot atom ``pivots[0]``, as rmgpy's reduced-MOI routine requires.
    """
    p0, p1 = pivots
    seen = {p0}
    stack = [p0]
    while stack:
        node = stack.pop()
        for nb in adj[node]:
            if node == p0 and nb == p1:
                continue  # cut the pivot bond
            if nb == p0 and node != p0:
                continue
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    if p1 in seen:
        raise RuntimeError(
            f"Pivot bond {pivots} is in a ring (both sides connected); this "
            "rotor is not a simple single-bond internal rotation."
        )
    return sorted(seen)


def gather_rotors(data: dict) -> None:
    """Collect hindered-rotor inputs for each torsion, recording provenance.

    Per rotor Arkane needs: the scan POTENTIAL (energy vs dihedral), the PIVOT
    atoms, the rotating-TOP atom set, and the rotor SYMMETRY number. Where each
    comes from in TCKDB is recorded in ``data['sources']``.
    """
    sm = api_get(
        f"/species-entries/{data['species_entry_ref']}/statmech?include=torsions"
    )["records"][0]
    torsions = sm.get("torsions") or []
    adj = build_bond_graph(data["symbols"], data["coords"])
    rotors = []
    for t in torsions:
        if t.get("treatment_kind") != "hindered_rotor":
            continue
        coord = t["coordinates"][0]
        pivots = (coord["atom2_index"], coord["atom3_index"])  # API dihedral -> pivots
        top = derive_top(adj, pivots)
        scan_ref = t["source_scan_calculation_ref"]

        # scan potential (energy vs dihedral) — full trajectory from the API
        # specialized scan endpoint.
        # /scan paginates (server max limit=200); rotor scans are well under.
        scan = api_get(f"/calculations/{scan_ref}/scan?limit=200")
        total = scan.get("pagination", {}).get("total")
        if total is not None and total > 200:
            raise RuntimeError(
                f"scan {scan_ref} has {total} points (>200); paginated fetch "
                "not implemented (no rotor scan in this corpus needs it)."
            )
        pts = sorted(scan["points"], key=lambda p: p["point_index"])
        angles_deg, v_kjmol = [], []
        for p in pts:
            ang = p["coordinate_values"][0]["coordinate_value"]
            angles_deg.append(float(ang))
            v_kjmol.append(float(p["relative_energy_kj_mol"]))
        # drop a duplicate 360 deg endpoint if present (scan wraps 0..360)
        if len(angles_deg) >= 2 and abs((angles_deg[-1] - angles_deg[0]) - 360.0) < 1e-3:
            angles_deg = angles_deg[:-1]
            v_kjmol = v_kjmol[:-1]

        rotors.append({
            "torsion_index": t["torsion_index"],
            "pivots": list(pivots),
            "top": top,
            "symmetry": t["symmetry_number"],
            "scan_ref": scan_ref,
            "top_description_stored": t.get("top_description"),
            "n_scan_points": len(angles_deg),
            "barrier_kj_mol": max(v_kjmol) - min(v_kjmol),
            "angles_rad": [math.radians(a) for a in angles_deg],
            "v_j_mol": [v * 1000.0 for v in v_kjmol],
        })
    data["rotors"] = rotors
    if rotors:
        data["sources"]["rotor_symmetry"] = "API statmech torsions"
        data["sources"]["rotor_pivots"] = "API statmech torsions (dihedral atoms)"
        data["sources"]["rotor_potential"] = "API calc scan endpoint (/scan)"
        data["sources"]["rotor_top"] = "DERIVED from API geometry (top_description NULL)"


def compute_rotor_terms(data: dict) -> None:
    """Compute each rotor's reduced moment of inertia + Fourier potential fit.

    Runs a small rmgpy snippet in ``rmg_env`` so the reduced MOI (option=3, the
    ARC/Arkane default) and Fourier fit exactly match Arkane's own routines. The
    resulting inertia (amu*angstrom^2) and Fourier coefficients (kJ/mol) are then
    embedded as literals in the Arkane input, keeping it fully auditable.
    """
    if not data.get("rotors"):
        return
    payload = {
        "symbols": data["symbols"],
        "coords": data["coords"].tolist(),
        "masses": [ATOMIC_MASS[s] for s in data["symbols"]],
        "rotors": [
            {
                "pivots": r["pivots"],
                "top": r["top"],
                "angles_rad": r["angles_rad"],
                "v_j_mol": r["v_j_mol"],
            }
            for r in data["rotors"]
        ],
    }
    snippet = r'''
import json, sys
import numpy as np
from rmgpy.statmech import Conformer, HinderedRotor
with open(sys.argv[1]) as _fh:  # conda run does not forward stdin reliably
    inp = json.load(_fh)
coords = np.array(inp["coords"], float)
masses = np.array(inp["masses"], float)
conf = Conformer(
    mass=(masses.tolist(), "amu"),
    coordinates=(coords.tolist(), "angstrom"),
)
out = []
KG_M2_PER_AMU_A2 = 1.66053906660e-47
for r in inp["rotors"]:
    i_si = conf.get_internal_reduced_moment_of_inertia(r["pivots"], r["top"], option=3)
    inertia_amu_a2 = i_si / KG_M2_PER_AMU_A2
    hr = HinderedRotor(
        inertia=(inertia_amu_a2, "amu*angstrom^2"), symmetry=1,
    )
    angle = np.array(r["angles_rad"], float)
    V = np.array(r["v_j_mol"], float)
    hr.fit_fourier_potential_to_data(angle, V)
    coeffs = (hr.fourier.value_si / 1000.0).tolist()  # J/mol -> kJ/mol
    out.append({"inertia_amu_a2": inertia_amu_a2, "fourier_kj_mol": coeffs})
json.dump(out, sys.stdout)
'''
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="rotor_terms_"
    ) as tf:
        json.dump(payload, tf)
        payload_path = tf.name
    try:
        proc = subprocess.run(
            ["conda", "run", "-n", RMG_ENV, "python", "-c", snippet, payload_path],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(payload_path)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(
            f"rotor-term computation failed in {RMG_ENV}:\n{proc.stderr[-2000:]}"
        )
    terms = json.loads(proc.stdout)
    for rotor, term in zip(data["rotors"], terms):
        rotor["inertia_amu_a2"] = term["inertia_amu_a2"]
        rotor["fourier_kj_mol"] = term["fourier_kj_mol"]


# --------------------------------------------------------------------------- #
# Statmech helpers
# --------------------------------------------------------------------------- #
def principal_moments(symbols, coords) -> np.ndarray:
    """Principal moments of inertia (amu*angstrom^2) from geometry + masses."""
    m = np.array([ATOMIC_MASS[s] for s in symbols])
    com = (m[:, None] * coords).sum(0) / m.sum()
    r = coords - com
    tensor = np.zeros((3, 3))
    for mi, ri in zip(m, r):
        tensor += mi * (np.dot(ri, ri) * np.eye(3) - np.outer(ri, ri))
    return np.linalg.eigvalsh(tensor), m.sum()


def eval_nasa_cp(nasa: dict, temperature: float) -> float:
    """Cp (J/mol/K) from a stored TCKDB NASA polynomial at a temperature."""
    if temperature <= nasa["t_mid"]:
        c = nasa["low_temperature_coefficients"]
    else:
        c = nasa["high_temperature_coefficients"]
    cp_over_r = (
        c[0] + c[1] * temperature + c[2] * temperature ** 2
        + c[3] * temperature ** 3 + c[4] * temperature ** 4
    )
    return cp_over_r * R_GAS


# --------------------------------------------------------------------------- #
# Arkane input / run / parse
# --------------------------------------------------------------------------- #
def build_arkane_input(data: dict, work_dir: str) -> str:
    """Write an Arkane input.py built purely from TCKDB-stored data."""
    moments, mol_weight = principal_moments(data["symbols"], data["coords"])
    scale = data["freq_scale_factor"]
    rotors = data.get("rotors") or []
    n_rotors = len(rotors)

    # When hindered rotors replace R torsional modes, those R modes must be
    # removed from the harmonic-oscillator list to avoid double-counting. TCKDB
    # stores the full unprojected 3N-6 spectrum; the torsional modes are the R
    # lowest frequencies for these species, so we drop the R lowest. (This is an
    # approximation to Arkane's Hessian projection; see the findings doc.)
    all_freqs = sorted(float(f) for f in data["frequencies_cm1"])
    dropped = all_freqs[:n_rotors]
    kept = all_freqs[n_rotors:]
    scaled_freqs = [round(f * scale, 4) for f in kept]
    e0 = (data["electronic_energy_hartree"] + data["zpe_hartree"]) * HARTREE_TO_KJMOL

    rotor_blocks = ""
    for r in rotors:
        rotor_blocks += (
            f"        HinderedRotor(inertia=({r['inertia_amu_a2']:.10f}, "
            f"'amu*angstrom^2'), symmetry={r['symmetry']}, "
            f"fourier=({r['fourier_kj_mol']}, 'kJ/mol')),\n"
        )

    if data["is_linear"]:
        # linear molecule: one non-zero moment of inertia
        rotor = (
            f"LinearRotor(inertia=({max(moments):.10f}, 'amu*angstrom^2'), "
            f"symmetry={data['external_symmetry']})"
        )
    else:
        rotor = (
            f"NonlinearRotor(inertia=([{moments[0]:.10f}, {moments[1]:.10f}, "
            f"{moments[2]:.10f}], 'amu*angstrom^2'), "
            f"symmetry={data['external_symmetry']})"
        )

    label = "SPC"
    use_rotors = "True" if n_rotors else "False"
    content = f"""#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Arkane input assembled purely from TCKDB-stored data for {data['smiles']}
# species_entry_ref={data['species_entry_ref']}  statmech_ref={data['statmech_ref']}
# hindered rotors: {n_rotors} (torsional freqs dropped from HO list: {[round(x,2) for x in dropped]})
useHinderedRotors = {use_rotors}
useAtomCorrections = False
useBondCorrections = False

species('{label}',
    E0 = ({e0:.6f}, 'kJ/mol'),
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
    input_path = os.path.join(work_dir, "input.py")
    with open(input_path, "w") as fh:
        fh.write(content)
    data["_label"] = label
    data["_moments"] = moments.tolist()
    data["_mol_weight"] = mol_weight
    data["_scaled_freqs"] = scaled_freqs
    data["_dropped_freqs"] = dropped
    return input_path


def run_arkane(input_path: str) -> str:
    """Run Arkane in rmg_env; return the output.py content."""
    work_dir = os.path.dirname(input_path)
    proc = subprocess.run(
        ["conda", "run", "-n", RMG_ENV, "python", ARKANE_ENTRY, input_path],
        capture_output=True, text=True, cwd=work_dir,
    )
    output_path = os.path.join(work_dir, "output.py")
    if not os.path.isfile(output_path):
        raise RuntimeError(
            f"Arkane did not produce output.py.\nSTDOUT:\n{proc.stdout[-2000:]}"
            f"\nSTDERR:\n{proc.stderr[-2000:]}"
        )
    with open(output_path) as fh:
        return fh.read()


def parse_arkane_output(output: str) -> dict:
    """Parse S298, Cp(T) and NASA from Arkane's output.py comment table."""
    result: dict = {"cp_cal": {}}
    for line in output.splitlines():
        s = line.strip().lstrip("#").strip()
        if s.startswith("Entropy of formation (298 K)"):
            # "... = 44.466 cal/(mol*K)"
            result["s298_cal"] = float(s.split("=")[1].split()[0])
        elif s.startswith("Enthalpy of formation (298 K)"):
            result["h298_kcal"] = float(s.split("=")[1].split()[0])
        else:
            parts = s.split()
            # data rows: T  Cp  H  S  G  (all numeric)
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


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def build_report(data: dict, arkane: dict) -> str:
    lines = []
    p = lines.append
    p("=" * 72)
    p("ARKANE STATMECH ROUND-TRIP  —  TCKDB statmech-completeness check")
    p("=" * 72)
    p(f"Species            : {data['smiles']}  (mult={data['multiplicity']}, "
      f"charge from entry)  point_group={data['point_group']}")
    p(f"species_entry_ref  : {data['species_entry_ref']}")
    p(f"statmech_ref       : {data['statmech_ref']}")
    p(f"thermo_ref         : {data['thermo_ref']}")
    p("")
    p("Assembled statmech inputs (all from TCKDB):")
    p(f"  external symmetry : {data['external_symmetry']}")
    p(f"  optical isomers   : stored={data['optical_isomers_stored']} "
      f"pg_derived={data.get('optical_isomers_from_pg')} "
      f"(from point_group={data['point_group']}) used={data['optical_isomers']}")
    p(f"  freq scale factor : {data['freq_scale_factor']}")
    p(f"  n frequencies     : {len(data['frequencies_cm1'])} stored, "
      f"{len(data['_scaled_freqs'])} used as HO (dropped "
      f"{[round(x,2) for x in data.get('_dropped_freqs', [])]} cm^-1 as torsions)")
    p(f"  principal moments : {[round(x,4) for x in data['_moments']]} amu*A^2")
    p(f"  molecular weight  : {data['_mol_weight']:.5f} amu")
    if data.get("rotors"):
        p(f"  hindered rotors   : {len(data['rotors'])}")
        for r in data["rotors"]:
            p(f"    rotor {r['torsion_index']}: pivots={r['pivots']} "
              f"top={r['top']} sym={r['symmetry']} "
              f"I_red={r['inertia_amu_a2']:.4f} amu*A^2 "
              f"V_barrier={r['barrier_kj_mol']:.2f} kJ/mol "
              f"({r['n_scan_points']} scan pts)")
    p("")
    p("Data provenance (API vs direct-DB):")
    for key, source in data["sources"].items():
        p(f"  {key:20s} <- {source}")
    p("")

    # S298
    p("-" * 72)
    p("PRIMARY TARGET  S298 (J/mol/K)   [independent of energy reference]")
    p("-" * 72)
    s_stored = data["stored_s298"]
    s_ark = arkane["s298_j"]
    p(f"  TCKDB stored      : {s_stored:10.3f}")
    p(f"  Arkane recomputed : {s_ark:10.3f}")
    p(f"  abs delta         : {abs(s_ark - s_stored):10.3f} J/mol/K")
    p(f"  %  delta          : {100*abs(s_ark - s_stored)/s_stored:10.4f} %")
    p("")

    # Cp
    p("-" * 72)
    p("PRIMARY TARGET  Cp(T) (J/mol/K)   [independent of energy reference]")
    p("-" * 72)
    p(f"  {'T (K)':>7} | {'TCKDB NASA':>12} | {'Arkane':>12} | "
      f"{'abs d':>8} | {'% d':>7}")
    for temp in CP_TEMPERATURES:
        cp_stored = eval_nasa_cp(data["stored_nasa"], temp)
        # nearest Arkane tabulated temperature (300/500/1000/1500 are present)
        cp_ark = arkane["cp_cal"].get(temp)
        cp_ark_j = cp_ark * CAL_TO_J if cp_ark is not None else float("nan")
        d = abs(cp_ark_j - cp_stored)
        pct = 100 * d / cp_stored
        p(f"  {temp:7.0f} | {cp_stored:12.3f} | {cp_ark_j:12.3f} | "
          f"{d:8.3f} | {pct:6.3f}%")
    p("")

    # H298
    p("-" * 72)
    p("SECONDARY / STRETCH  H298 (kJ/mol)  [needs atom-energy corrections]")
    p("-" * 72)
    p(f"  TCKDB stored H298f          : {data['stored_h298_kj_mol']:14.3f}")
    p(f"  Arkane recomputed (no corr) : {arkane['h298_kj']:14.3f}")
    p("  NOTE: Arkane here uses NO atom-energy/bond corrections, so its H298 is")
    p("  an absolute enthalpy (E_elec+ZPE+thermal), not a formation enthalpy.")
    p("  The large difference is a correction-reference difference, NOT a")
    p("  statmech-completeness failure.")
    p("=" * 72)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--species-entry-ref", default="spe_oxmflzmwl4xzkeujaj3oj3efl4",
        help="TCKDB species-entry public ref (default: methane / CH4).",
    )
    ap.add_argument(
        "--keep", action="store_true",
        help="Keep the Arkane scratch directory instead of a temp dir.",
    )
    args = ap.parse_args()

    print(f"[1/4] Reading species {args.species_entry_ref} from live TCKDB ...")
    data = gather_species(args.species_entry_ref)
    gather_rotors(data)
    if data.get("rotors"):
        print(f"      {len(data['rotors'])} hindered rotor(s) found; "
              "computing reduced MOI + Fourier fits in rmg_env ...")
        compute_rotor_terms(data)

    work_dir = (
        os.path.join(os.environ.get("CLAUDE_JOB_DIR", "/tmp"), "arkane_roundtrip")
        if args.keep else tempfile.mkdtemp(prefix="arkane_rt_")
    )
    os.makedirs(work_dir, exist_ok=True)

    print(f"[2/4] Assembling Arkane input in {work_dir} ...")
    input_path = build_arkane_input(data, work_dir)

    print("[3/4] Running Arkane in rmg_env ...")
    output = run_arkane(input_path)
    arkane = parse_arkane_output(output)

    print("[4/4] Comparison:\n")
    print(build_report(data, arkane))
    return 0


if __name__ == "__main__":
    sys.exit(main())
