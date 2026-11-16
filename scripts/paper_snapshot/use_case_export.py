"""CHEMKIN export of the use case, requested as the build's curator.

After curation, ``POST /scientific/export/chemkin`` is called for the reaction of one ARC run with the records that run
deposited named in the request: its rate record and one thermo record for each species of the reaction. The exporter
never picks, so a refusal (``export_selection_ambiguous`` or any other) stops the build with the server's answer.

The returned files are written to ``<out-dir>/chemkin_export/`` with ``export_request.json``, the request body. The
generator ``use_case_chemkin_export`` finds the same records from the database, makes the export in-process and loads
the files with Cantera; the build then checks the saved files against that in-process export.

The ``chemkin_export`` block of the sources file::

    chemkin_export:
      run: <an ARC run in sources.arc_runs.runs>
      species: [C, "[OH]", O, "[CH3]"]     # SMILES; one thermo record of the run each
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import curation
from common import REPO_ROOT, Report

DIRECTORY = "chemkin_export"
REQUEST_FILE = "export_request.json"
ALLOWED_FILES = frozenset({"chem.inp", "therm.dat", "tran.dat", "manifest.json"})
OUTPUT_NAME = "use_case_chemkin_export"


def problems(config: dict[str, Any]) -> list[str]:
    """What is wrong with the ``chemkin_export`` block, or nothing."""
    plan = config.get("chemkin_export")
    if not plan:
        return []
    arc = (config.get("sources") or {}).get("arc_runs") or {}
    runs = set(arc.get("runs") or []) if arc.get("enabled", True) else set()
    out = [f"chemkin_export: unknown key {k!r}" for k in sorted(set(plan) - {"run", "species"})]
    if plan.get("run") not in runs:
        out.append(f"chemkin_export: run {plan.get('run')!r} is not in sources.arc_runs.runs")
    species = plan.get("species")
    if not species or not all(isinstance(s, str) and s for s in species) or len(set(species)) != len(species):
        out.append("chemkin_export: species must be a non-empty list of distinct SMILES")
    out += _release_problems(config, plan)
    return out


def _release_problems(config: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """The generator finds its records from release ``RELEASE_TAG`` and the species ``NAMED_SPECIES``; the plan must agree."""
    from scripts.paper.chemkin_export import NAMED_SPECIES, RELEASE_TAG, SELECTED_SPECIES

    out = []
    if sorted(plan.get("species") or []) != sorted(NAMED_SPECIES):
        out.append(f"chemkin_export: species must be {sorted(NAMED_SPECIES)}, the ones the generator names")
    release = (config.get("curation") or {}).get("release") or {}
    if release.get("tag") != RELEASE_TAG:
        out.append(f"chemkin_export: curation.release.tag must be {RELEASE_TAG!r}, the release the generator reads")
    chosen = {(s.get("run"), s.get("record"), s.get("smiles")) for s in release.get("selections") or []}
    for wanted in ((plan.get("run"), "kinetics", None), (plan.get("run"), "thermo", SELECTED_SPECIES)):
        if wanted not in chosen:
            out.append(f"chemkin_export: curation.release.selections lacks {wanted[1]} {wanted[2] or ''} of run {wanted[0]}".replace("  ", " "))
    return out


def named_thermo_ref(curator: curation.Curator, response: dict[str, Any], smiles: str) -> str:
    """The one thermo record of the run's deposit for ``smiles``; raise when there are none or several."""
    mine = {curation.record_ref(curator, "thermo", i) for i in response["thermo_ids"]}
    found = curator.ok("GET", "/scientific/thermo/search", params={"smiles": smiles, "limit": 200})
    refs = sorted(mine & {r["thermo"]["thermo_ref"] for r in found["records"]})
    if len(refs) != 1:
        raise RuntimeError(f"chemkin_export: the run deposited {len(refs)} thermo records for {smiles}, not one")
    return refs[0]


def build_request(curator: curation.Curator, report: Report, plan: dict[str, Any]) -> dict[str, Any]:
    """The request body: the run's reaction as the seed, its rate record and one thermo record per species named."""
    response = curation.arc_response(report, plan["run"])
    if len(response["kinetics_ids"]) != 1:
        raise RuntimeError(f"chemkin_export: {plan['run']} deposited {len(response['kinetics_ids'])} rate records, not one")
    return {
        "seed": {"reaction_refs": [curation.record_ref(curator, "reaction_entry", response["reaction_entry_id"])]},
        "kinetics_refs": [curation.record_ref(curator, "kinetics", response["kinetics_ids"][0])],
        "thermo_refs": [named_thermo_ref(curator, response, smiles) for smiles in plan["species"]],
        "energy_units": "cal/mol",
        "include_transport": True,
        "naming_policy": "formula",
    }


def check_against_release(request: dict[str, Any], plan: dict[str, Any], release: dict[str, Any] | None) -> list[str]:
    """Records the release selected must be the ones the request names; returns what was checked."""
    if not release:
        return []
    checked = []
    named = set(request["kinetics_refs"]) | set(request["thermo_refs"])
    for selection in release.get("selections") or []:
        if selection["record"] in ("kinetics", "thermo"):
            if selection["record_ref"] not in named:
                raise RuntimeError(
                    f"chemkin_export: the release selected {selection['record']} {selection['record_ref']}, "
                    "which the export request does not name"
                )
            checked.append(f"{selection['record']} {selection['record_ref']}")
    return checked


def write_files(archive: bytes, out: Path) -> dict[str, dict[str, Any]]:
    """Write the zip's members under ``out`` (no other names, no paths); return each file's size and digest."""
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for name in sorted(zf.namelist()):
            if name not in ALLOWED_FILES:
                raise RuntimeError(f"chemkin_export: the export holds an unexpected member {name!r}")
            data = zf.read(name)
            (out / name).write_bytes(data)
            written[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    missing = sorted({"chem.inp", "therm.dat", "manifest.json"} - set(written))
    if missing:
        raise RuntimeError(f"chemkin_export: the export lacks {missing}")
    return written


def export_use_case(
    curator: curation.Curator, report: Report, plan: dict[str, Any], out_dir: Path, release: dict[str, Any] | None
) -> dict[str, Any]:
    """Request the export, save it under ``out_dir/chemkin_export``, and return the request and the files' digests."""
    request = build_request(curator, report, plan)
    checked = check_against_release(request, plan, release)
    response = curator.call("POST", "/scientific/export/chemkin", json=request)
    if response.status_code != 200:
        raise RuntimeError(f"chemkin_export: the exporter answered {response.status_code}: {response.text[:1500]}")
    target = out_dir / DIRECTORY
    files = write_files(response.content, target)
    (target / REQUEST_FILE).write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"run": plan["run"], "request": request, "named_records_also_selected_by_the_release": checked, "files": files}


def check_saved(directory: Path, env: dict[str, str]) -> dict[str, Any]:
    """Run ``scripts.paper.chemkin_export --check`` against the database; raise unless the saved files match the
    in-process export (byte for byte, refs included; the manifest without ``generated_at``)."""
    done = subprocess.run(
        [sys.executable, "-m", "scripts.paper.chemkin_export", "--check", str(directory)],
        cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True,
    )
    try:
        result = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"chemkin_export: the check did not run:\n{done.stdout}\n{done.stderr}") from None
    if done.returncode != 0 or not result.get("match"):
        raise RuntimeError(f"chemkin_export: the saved files differ from the in-process export: {result}")
    return result


#: The least precision the exporter writes today; a build that finds fewer digits is refused, so the bounds computed from
#: the written digits cannot widen to let a coarser file through. More digits pass.
MIN_N_DECIMALS = 3
MIN_A_MANTISSA_DECIMALS = 4
MIN_EA_DECIMALS = 4
MIN_NASA_MANTISSA_DECIMALS = 8
#: A written number that is exactly half a unit from the stored one (a tie, such as 985.655 written as 985.66) can come
#: out a few parts in 1e16 above 1 in floating point; the gate allows that and nothing larger. The value reported in the
#: output is rounded for reading; the gate uses the unrounded one.
HALF_UNIT_FLOAT_EPSILON = 1.0e-9
#: Cantera's k against the hand evaluation of the written A, n and Ea (same formula, same digits).
CANTERA_VS_HAND_RELATIVE = 1.0e-6


def _decimals(text: str) -> int:
    mantissa = str(text).lower().partition("e")[0]
    return len(mantissa.split(".")[1]) if "." in mantissa else 0


def _rate_problems(rate: dict[str, Any]) -> list[str]:
    """The forward rate must be evaluated, written to at least today's precision, parameter by parameter the stored
    fit's, Cantera's own evaluation of the written digits, and within what the written digits explain at 300 K."""
    if rate.get("cantera_k_forward") is None or rate.get("stored_fit_k") is None:
        return ["no forward rate coefficient was compared with the stored fit"]
    out = []
    if rate.get("stored_fit_units") != "cm3_mol_s":
        out.append(f"the stored fit is in {rate.get('stored_fit_units')!r}, not cm3_mol_s; Cantera's value is not comparable")
    written = rate.get("written_in_chem_inp") or {}
    for key, floor, label in (
        ("n_text", MIN_N_DECIMALS, "n"),
        ("a_text", MIN_A_MANTISSA_DECIMALS, "A"),
        ("ea_text", MIN_EA_DECIMALS, "Ea"),
    ):
        if key not in written or _decimals(written[key]) < floor:
            out.append(f"{label} is written as {written.get(key)!r}, fewer than the {floor} decimals the export writes")
    parameters = rate.get("written_vs_stored_fit")
    if not parameters:
        out.append("the written parameters were not compared with the stored fit")
    else:
        for key in ("n", "a", "ea"):
            over = (parameters.get(key) or {}).get("over_half_unit_unrounded")
            if over is None or over > 1 + HALF_UNIT_FLOAT_EPSILON:
                out.append(f"written {key} differs from the stored fit by {over} half-units of its last written place")
    hand = rate.get("cantera_over_hand_evaluation_of_written_digits")
    if hand is None or abs(hand - 1.0) > CANTERA_VS_HAND_RELATIVE:
        out.append(f"Cantera's k is {hand} times the hand evaluation of the written A, n and Ea")
    bound = (rate.get("ratio_explained_by_written_digits") or {}).get("total")
    ratio = rate.get("cantera_over_stored")
    if bound is None or ratio is None or ratio <= 0:
        out.append("the rate ratio or the bound from the written digits is missing")
    elif abs(math.log(ratio)) > bound:
        out.append(f"k(300 K) from Cantera is {ratio} times the stored fit's; the written digits explain at most {bound}")
    return out


def _h298_problems(h298: dict[str, Any]) -> list[str]:
    """Cantera's H298 of [CH3] must match the exported polynomial's. The difference between the record's scalar H298
    and its own polynomial is reported, not gated."""
    if h298.get("exported_nasa_h298_kj_mol") is None:
        return ["no H298 of [CH3] was evaluated from the exported polynomial"]
    agreement = h298.get("cantera_agreement_bound_kj_mol")
    difference = h298.get("cantera_minus_exported_kj_mol")
    if agreement is None or difference is None or abs(difference) > agreement:
        return [f"Cantera's H298 of [CH3] differs from the exported polynomial's by {difference} kJ/mol (bound {agreement})"]
    return []


def _nasa_problems(row: dict[str, Any]) -> list[str]:
    """Every number written in a species' NASA card is the stored record's to within half a unit of its last written
    place, and the coefficients are written to at least today's precision."""
    check = row.get("nasa_vs_record")
    if not check:
        return [f"species {row.get('smiles')}: the NASA card was not compared with the stored record"]
    out = []
    worst = check.get("max_difference_over_half_unit_unrounded")
    if worst is None or worst > 1 + HALF_UNIT_FLOAT_EPSILON:
        out.append(
            f"species {row['smiles']}: a written NASA number differs from the stored record by "
            f"{worst} half-units of its last written place"
        )
    if check["min_coefficient_mantissa_decimals"] < MIN_NASA_MANTISSA_DECIMALS:
        out.append(
            f"species {row['smiles']}: coefficients are written with {check['min_coefficient_mantissa_decimals']} "
            f"mantissa decimals, fewer than the {MIN_NASA_MANTISSA_DECIMALS} the export writes"
        )
    return out


def verify_output(document: dict[str, Any]) -> list[str]:
    """Reasons the generator's output does not show a loaded, checked export; empty when it does."""
    if document.get("export") != "found":
        return [f"the generator's export is {document.get('export')!r}: {document.get('reason') or document.get('error')}"]
    out = []
    cantera = document.get("cantera") or {}
    if cantera.get("status") != "loaded":
        out.append(f"Cantera did not load the files: {cantera.get('status')}: {cantera.get('error') or cantera.get('detail')}")
    out += _rate_problems(document.get("forward_rate") or {})
    out += _h298_problems(document.get("ch3_h298") or {})
    if (document.get("manifest") or {}).get("gaps"):
        out.append(f"the manifest lists gaps: {document['manifest']['gaps']}")
    for row in document.get("species") or []:
        if len(row.get("thermo_refs_named") or []) != 1 or not row.get("has_nasa_in_therm_dat"):
            out.append(f"species {row.get('smiles')}: needs one named thermo record and a NASA entry in therm.dat")
        else:
            out += _nasa_problems(row)
    if document.get("named_thermo_refs_without_a_species_in_chem_inp"):
        out.append("a named thermo record has no species in chem.inp")
    return out
