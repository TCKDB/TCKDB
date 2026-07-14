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
   HarmonicOscillator) — the moments of inertia are computed here from the
   TCKDB geometry + atomic masses, which is exactly the completeness claim.
3. Runs Arkane in the ``rmg_env`` conda environment (via subprocess).
4. Numerically compares Arkane's recomputed S298 and Cp(T) against the thermo
   TCKDB already stores for that species.

Primary comparison targets are S298 and Cp(300/500/1000/1500 K): they depend
only on geometry + frequencies + symmetry number + multiplicity + optical
isomers, and NOT on the absolute-energy reference. H298 of formation is a
secondary/stretch target that additionally needs the same atom-energy /
bond-correction scheme ARC used originally; a mismatch there is a
correction-reference difference, NOT a statmech-completeness failure.

Data accessibility split (a finding in its own right)
-----------------------------------------------------
Most data is reachable over the public read API. Two pieces are NOT exposed by
the API and must be read directly from the DB (read-only):
  * per-mode harmonic frequencies   -> table ``calc_freq_mode.frequency_cm1``
  * ``statmech.optical_isomers``     -> column exists but omitted from payload

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


# --------------------------------------------------------------------------- #
# Live data access
# --------------------------------------------------------------------------- #
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
    data["optical_isomers"] = data["optical_isomers_stored"] or 1  # Arkane default
    data["sources"]["optical_isomers"] = "DB statmech (API GAP)"

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
    scaled_freqs = [round(f * scale, 4) for f in data["frequencies_cm1"]]
    e0 = (data["electronic_energy_hartree"] + data["zpe_hartree"]) * HARTREE_TO_KJMOL

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
    content = f"""#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Arkane input assembled purely from TCKDB-stored data for {data['smiles']}
# species_entry_ref={data['species_entry_ref']}  statmech_ref={data['statmech_ref']}
useHinderedRotors = False
useAtomCorrections = False
useBondCorrections = False

species('{label}',
    E0 = ({e0:.6f}, 'kJ/mol'),
    modes = [
        IdealGasTranslation(mass=({mol_weight:.6f}, 'amu')),
        {rotor},
        HarmonicOscillator(frequencies=({scaled_freqs}, 'cm^-1')),
    ],
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
      f"used={data['optical_isomers']}")
    p(f"  freq scale factor : {data['freq_scale_factor']}")
    p(f"  n frequencies     : {len(data['frequencies_cm1'])}")
    p(f"  principal moments : {[round(x,4) for x in data['_moments']]} amu*A^2")
    p(f"  molecular weight  : {data['_mol_weight']:.5f} amu")
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
