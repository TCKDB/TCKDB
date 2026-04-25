"""Parse an SDF file + kinetics CSV into a ComputedReactionUploadRequest payload.

Usage:
    python scripts/parse_sdf_to_bundle.py kfir_rxn_2 --json

Or import as a library:
    from scripts.parse_sdf_to_bundle import sdf_to_bundle
    payload = sdf_to_bundle("kfir_rxn_2", sdf_dir, csv_path)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

_CALMOLK_TO_JMOLK = 4.184
_KJMOL_TO_HARTREE = 1.0 / 2625.5

# Default paths — data is vendored into the repo at tests/fixtures/sdf/
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SDF_DIR = _REPO_ROOT / "tests" / "fixtures" / "sdf"
_CSV_PATH = _SDF_DIR / "kinetics_summary_dlnpo.csv"

# Role mapping: r1h, r2 → reactant; r2h, r1 → product
_REACTANT_TYPES = {"r1h", "r2"}
_PRODUCT_TYPES = {"r2h", "r1"}


def _parse_sdf_mol(mol_text: str) -> dict:
    """Parse one molecule block from an SDF file."""
    lines = mol_text.strip().split("\n")
    data = {}

    # Parse properties
    for i, line in enumerate(lines):
        m = re.match(r">\s+<(\w+)>", line)
        if m:
            key = m.group(1)
            if i + 1 < len(lines):
                data[key] = lines[i + 1].strip()

    # Extract XYZ from V2000 mol block
    xyz_lines = []
    natoms = 0
    for i, line in enumerate(lines):
        if "V2000" in line:
            parts = line.split()
            natoms = int(parts[0])
            for atom_line in lines[i + 1 : i + 1 + natoms]:
                ap = atom_line.split()
                if len(ap) >= 4:
                    x, y, z, elem = ap[0], ap[1], ap[2], ap[3]
                    xyz_lines.append(f"{elem}  {x}  {y}  {z}")
            break

    smiles = data.get("rmg_smiles", "")
    mult = int(float(data.get("multiplicity", "1")))
    mol_type = data.get("type", "unknown")

    xyz_text = f"{natoms}\n{smiles}\n" + "\n".join(xyz_lines) if xyz_lines else None

    return {
        "smiles": smiles,
        "multiplicity": mult,
        "type": mol_type,
        "xyz_text": xyz_text,
        "natoms": natoms,
        "lot_method": data.get("lot_method"),
        "lot_basis": data.get("lot_basis"),
        "E_elec_kJmol": _safe_float(data.get("E_elec_kJmol")),
        "E0_kJmol": _safe_float(data.get("E0_kJmol")),
        "H298_kJmol": _safe_float(data.get("H298_kJmol")),
        "S298_value": _safe_float(data.get("S298_value")),
        "S298_units": data.get("S298_units", ""),
        "Tmin_value": _safe_float(data.get("Tmin_value")),
        "Tmax_value": _safe_float(data.get("Tmax_value")),
        "thermo_class": data.get("thermo_class"),
        "polynomials": data.get("polynomials"),
        "frequencies_cm1": data.get("frequencies_cm1"),
        "ZPE_kJmol": _safe_float(data.get("ZPE_kJmol")),
        "frequency_value": data.get("frequency_value"),  # for TS imag freq
        "ts_imag_freq_cm1": _safe_float(data.get("ts_imag_freq_cm1")),
    }


def _safe_float(val: str | None) -> float | None:
    if val is None or val == "unknown" or val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _parse_nasa(polynomials_str: str) -> dict | None:
    """Parse NASA polynomial JSON string into a ThermoNASACreate dict."""
    if not polynomials_str or polynomials_str == "unknown":
        return None
    try:
        polys = json.loads(polynomials_str)
    except json.JSONDecodeError:
        return None

    if len(polys) != 2:
        return None

    p1, p2 = polys[0], polys[1]
    c1 = p1["coeffs"]
    c2 = p2["coeffs"]

    return {
        "t_low": p1["Tmin_value"],
        "t_mid": p1["Tmax_value"],
        "t_high": p2["Tmax_value"],
        "a1": c1[0], "a2": c1[1], "a3": c1[2], "a4": c1[3],
        "a5": c1[4], "a6": c1[5], "a7": c1[6],
        "b1": c2[0], "b2": c2[1], "b3": c2[2], "b4": c2[3],
        "b5": c2[4], "b6": c2[5], "b7": c2[6],
    }


def _build_species(mol: dict, key: str) -> dict:
    """Build a BundleSpeciesIn dict from a parsed SDF molecule."""
    lot_dft = {"method": mol["lot_method"] or "unknown", "basis": mol["lot_basis"]}
    software = {"name": "Gaussian", "version": "16"}

    species = {
        "key": key,
        "species_entry": {
            "smiles": mol["smiles"],
            "charge": 0,
            "multiplicity": mol["multiplicity"],
        },
    }

    # Conformer with opt calculation
    if mol["xyz_text"]:
        geom_key = f"{key}-geom"
        opt_key = f"{key}-opt"
        species["conformers"] = [{
            "key": f"{key}-conf",
            "geometry": {"key": geom_key, "xyz_text": mol["xyz_text"]},
            "calculation": {
                "key": opt_key,
                "type": "opt",
                "software_release": software,
                "level_of_theory": lot_dft,
                "opt_converged": True,
            },
            "label": f"{key}-opt",
        }]

        # DLPNO SP if we have electronic energy
        calcs = []
        if mol["E_elec_kJmol"] is not None:
            calcs.append({
                "key": f"{key}-dlpno-sp",
                "type": "sp",
                "geometry_key": geom_key,
                "software_release": {"name": "ORCA", "version": "5.0"},
                "level_of_theory": {"method": "DLPNO-CCSD(T)", "basis": "cc-pVTZ"},
                "sp_electronic_energy_hartree": mol["E_elec_kJmol"] * _KJMOL_TO_HARTREE,
            })
        if calcs:
            species["calculations"] = calcs

    # Thermo
    if mol["thermo_class"] == "NASA" and mol["H298_kJmol"] is not None:
        s298_j_mol_k = None
        if mol["S298_value"] is not None:
            if "cal" in mol["S298_units"]:
                s298_j_mol_k = mol["S298_value"] * _CALMOLK_TO_JMOLK
            else:
                s298_j_mol_k = mol["S298_value"]

        thermo = {
            "h298_kj_mol": mol["H298_kJmol"],
            "tmin_k": mol["Tmin_value"],
            "tmax_k": mol["Tmax_value"],
        }
        if s298_j_mol_k is not None:
            thermo["s298_j_mol_k"] = s298_j_mol_k

        nasa = _parse_nasa(mol["polynomials"])
        if nasa:
            thermo["nasa"] = nasa

        species["thermo"] = thermo

    return species


def _build_ts(mol: dict) -> dict | None:
    """Build a BundleTransitionStateIn dict from a parsed TS molecule."""
    if mol["xyz_text"] is None:
        return None

    lot_dft = {"method": mol["lot_method"] or "unknown", "basis": mol["lot_basis"]}
    software = {"name": "Gaussian", "version": "16"}

    ts = {
        "charge": 0,
        "multiplicity": mol["multiplicity"],
        "geometry": {"key": "ts-geom", "xyz_text": mol["xyz_text"]},
        "calculation": {
            "key": "ts-opt",
            "type": "opt",
            "software_release": software,
            "level_of_theory": lot_dft,
            "opt_converged": True,
        },
        "calculations": [],
        "label": "TS",
    }

    if mol.get("unmapped_smiles"):
        ts["unmapped_smiles"] = mol["unmapped_smiles"]

    # Freq calculation with imaginary frequency
    if mol["ts_imag_freq_cm1"] is not None:
        ts["calculations"].append({
            "key": "ts-freq",
            "type": "freq",
            "geometry_key": "ts-geom",
            "software_release": software,
            "level_of_theory": lot_dft,
            "freq_n_imag": 1,
            "freq_imag_freq_cm1": mol["ts_imag_freq_cm1"],
        })

    return ts


def _build_kinetics(csv_rows: list[dict], reactant_keys: list[str],
                     product_keys: list[str]) -> list[dict]:
    """Build BundleKineticsIn dicts from CSV kinetics rows."""
    kinetics = []
    for row in csv_rows:
        label = row["label"]

        # Determine direction from label
        if "rev" in label.lower():
            rkeys = product_keys
            pkeys = reactant_keys
        else:
            rkeys = reactant_keys
            pkeys = product_keys

        # Determine tunneling
        tunneling = None
        if "+T" in label or "+t" in label:
            tunneling = "Eckart"

        kin = {
            "reactant_keys": rkeys,
            "product_keys": pkeys,
            "a": float(row["A"]),
            "a_units": _normalize_a_units(row["A_units"]),
            "n": float(row["n"]),
            "reported_ea": float(row["Ea"]),
            "reported_ea_units": _normalize_ea_units(row["Ea_units"]),
            "tmin_k": float(row["Tmin"].replace(" K", "")),
            "tmax_k": float(row["Tmax"].replace(" K", "")),
            "note": label,
        }
        if tunneling:
            kin["tunneling_model"] = tunneling

        kinetics.append(kin)

    return kinetics


def _normalize_a_units(raw: str) -> str:
    """Convert CSV A_units to enum value."""
    mapping = {
        "s^-1": "per_s",
        "cm^3/(mol*s)": "cm3_mol_s",
        "cm^3/(molecule*s)": "cm3_molecule_s",
        "m^3/(mol*s)": "m3_mol_s",
    }
    return mapping.get(raw, "cm3_mol_s")


def _normalize_ea_units(raw: str) -> str:
    """Convert CSV Ea_units to enum value."""
    mapping = {
        "kJ/mol": "kj_mol",
        "kcal/mol": "kcal_mol",
        "J/mol": "j_mol",
        "cal/mol": "cal_mol",
    }
    return mapping.get(raw, "kj_mol")


def sdf_to_bundle(
    rxn_id: str,
    sdf_dir: str | Path = _SDF_DIR,
    csv_path: str | Path = _CSV_PATH,
) -> dict:
    """Parse an SDF file + kinetics CSV into a bundle payload dict.

    :param rxn_id: Reaction ID (e.g., "kfir_rxn_2").
    :param sdf_dir: Directory containing SDF files.
    :param csv_path: Path to kinetics CSV.
    :returns: Dict compatible with ComputedReactionUploadRequest.
    """
    sdf_path = Path(sdf_dir) / f"{rxn_id}.sdf"
    if not sdf_path.exists():
        raise FileNotFoundError(f"SDF file not found: {sdf_path}")

    with open(sdf_path) as f:
        content = f.read()

    mol_blocks = [m.strip() for m in content.split("$$$$") if m.strip()]
    parsed_mols = [_parse_sdf_mol(block) for block in mol_blocks]

    # Classify molecules
    reactants = []
    products = []
    ts_mol = None

    for mol in parsed_mols:
        if mol["type"] in _REACTANT_TYPES:
            reactants.append(mol)
        elif mol["type"] in _PRODUCT_TYPES:
            products.append(mol)
        elif mol["type"] == "ts":
            ts_mol = mol

    if not reactants or not products:
        raise ValueError(f"Could not identify reactants/products in {rxn_id}")

    # Build species (deduplicate by SMILES)
    species_by_smiles: dict[str, dict] = {}
    key_counter = 0

    def _get_key(mol: dict) -> str:
        nonlocal key_counter
        smiles = mol["smiles"]
        if smiles not in species_by_smiles:
            key_counter += 1
            key = f"sp{key_counter}"
            species_by_smiles[smiles] = _build_species(mol, key)
        return species_by_smiles[smiles]["key"]

    reactant_keys = [_get_key(mol) for mol in reactants]
    product_keys = [_get_key(mol) for mol in products]

    # Build TS
    ts_payload = None
    if ts_mol:
        ts_payload = _build_ts(ts_mol)

    # Load kinetics from CSV
    kinetics_rows = []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            if row["rxn"] == rxn_id:
                kinetics_rows.append(row)

    kinetics = _build_kinetics(kinetics_rows, reactant_keys, product_keys)

    # Assemble bundle
    bundle: dict = {
        "software_release": {"name": "Arkane", "version": "3.0"},
        "species": list(species_by_smiles.values()),
        "reversible": True,
        "reactant_keys": reactant_keys,
        "product_keys": product_keys,
        "kinetics": kinetics,
    }

    if ts_payload:
        bundle["transition_state"] = ts_payload

    return bundle


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parse_sdf_to_bundle.py <rxn_id> [--json]")
        sys.exit(1)

    rxn_id = sys.argv[1]
    bundle = sdf_to_bundle(rxn_id)

    if "--json" in sys.argv:
        print(json.dumps(bundle, indent=2))
    else:
        n_sp = len(bundle["species"])
        n_kin = len(bundle["kinetics"])
        has_ts = bundle.get("transition_state") is not None
        n_thermo = sum(1 for sp in bundle["species"] if "thermo" in sp)
        print(f"{rxn_id}: {n_sp} species, {n_kin} kinetics, TS={has_ts}, {n_thermo} thermo")
        for sp in bundle["species"]:
            print(f"  {sp['key']}: {sp['species_entry']['smiles']} (mult={sp['species_entry']['multiplicity']})")
