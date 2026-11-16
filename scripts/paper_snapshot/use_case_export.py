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


def verify_output(document: dict[str, Any]) -> list[str]:
    """Reasons the generator's output does not show a loaded, checked export; empty when it does."""
    if document.get("export") != "found":
        return [f"the generator's export is {document.get('export')!r}: {document.get('reason') or document.get('error')}"]
    out = []
    cantera = document.get("cantera") or {}
    if cantera.get("status") != "loaded":
        out.append(f"Cantera did not load the files: {cantera.get('status')}: {cantera.get('error') or cantera.get('detail')}")
    if (document.get("forward_rate") or {}).get("cantera_k_forward") is None:
        out.append("no forward rate coefficient was evaluated")
    if (document.get("ch3_h298") or {}).get("exported_nasa_h298_kj_mol") is None:
        out.append("no H298 of [CH3] was evaluated from the exported polynomial")
    for row in document.get("species") or []:
        if len(row.get("thermo_refs_named") or []) != 1 or not row.get("has_nasa_in_therm_dat"):
            out.append(f"species {row.get('smiles')}: needs one named thermo record and a NASA entry in therm.dat")
    if document.get("named_thermo_refs_without_a_species_in_chem_inp"):
        out.append("a named thermo record has no species in chem.inp")
    return out
