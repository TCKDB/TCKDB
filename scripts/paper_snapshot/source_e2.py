"""Source 1: the E2 conformer study, deposited through ``tckdb_client.builders``.

One ``ComputedSpeciesUpload`` per optimised minimum (molecule x start x level): an ``opt`` and a ``freq`` calculation,
the final geometry, and the job's ``job.log`` and ``job.gjf`` attached in the builders' second phase. Read from the
files: final geometry, last SCF energy, zero-point correction, frequency list, convergence and the start coordinates
in the input deck. The server reads the route line and SCF settings from the log.

Not deposited:

* ``fort.7``: no artifact kind admits the name.
* The GFN2-xTB level: listing ``gfn2`` in ``levels`` is refused at validation.
* A task with a repair directory deposits the continuation's output (restart geometry); the first attempt's log is not
  deposited.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
)

from common import Report, Session, TCKDBError, collect_response_warnings, digest_files, slug

SOURCE = "e2_conformers"

DEFAULT_LEVELS: dict[str, dict[str, Any]] = {
    "b3lyp_d3bj": {
        "software": {"software": "Gaussian", "version": "16", "revision": "C.02"},
        "level": {"method": "B3LYP", "basis": "def2-TZVP", "dispersion": "D3BJ"},
    },
    "wb97xd": {
        "software": {"software": "Gaussian", "version": "16", "revision": "C.02"},
        "level": {"method": "wB97X-D", "basis": "def2-TZVP"},
    },
    "gfn2": {
        "software": {"software": "xtb", "version": "6.7.1"},
        "level": {"method": "GFN2-xTB"},
    },
}


@dataclass
class Task:
    task: str  # "<level>/<molecule>/<start>"
    level: str
    molecule: str
    start: str
    smiles: str
    charge: int
    multiplicity: int
    elements: list[str]
    directory: Path  # the directory whose output is the final one
    base_directory: Path
    repaired: str | None = None


@dataclass
class Parsed:
    xyz: list[list[Any]] | None = None
    input_xyz: list[list[Any]] | None = None
    e_elec: float | None = None
    zpe: float | None = None
    freqs: list[float] = field(default_factory=list)
    converged: bool = False
    normal_terminations: int = 0
    error: bool = False

    @property
    def n_imag(self) -> int:
        return sum(1 for f in self.freqs if f < 0)

    def is_minimum(self, level: str) -> bool:
        needed = 1 if level == "gfn2" else 2
        return bool(
            self.xyz and self.converged and self.freqs and self.n_imag == 0 and self.normal_terminations >= needed
        )


# --------------------------------------------------------------------------
# reading the campaign
# --------------------------------------------------------------------------


def load_tasks(root: Path, levels: list[str]) -> tuple[list[Task], dict[str, Any]]:
    """The campaign table (``raw_outputs/tasks.txt``) joined with molecule metadata."""
    raw = root / "raw_outputs"
    manifest = {m["label"]: m for m in json.loads((raw / "manifest_union.json").read_text())}
    listed = [ln.strip() for ln in (raw / "tasks.txt").read_text().splitlines() if ln.strip()]
    tasks: list[Task] = []
    for line in listed:
        level, molecule, start = line.split("/")
        if level not in levels:
            continue
        meta = manifest[molecule]
        base = raw / "tasks" / level / molecule / start
        final = base
        repaired = None
        for name in ("repair1", "repair2"):
            if (base / name).is_dir():
                final, repaired = base / name, name
        tasks.append(
            Task(
                task=line,
                level=level,
                molecule=molecule,
                start=start,
                smiles=meta["smiles"],
                charge=int(meta["charge"]),
                multiplicity=int(meta["multiplicity"]),
                elements=list(meta["elements"]),
                directory=final,
                base_directory=base,
                repaired=repaired,
            )
        )
    tasks.sort(key=lambda t: (t.molecule, t.start, levels.index(t.level)))
    starts = sum(len(m["conformers"]) for m in manifest.values())
    per_level = {lv: sum(1 for ln in listed if ln.startswith(lv + "/")) for lv in levels}
    table = {"tasks_listed": len(listed), "tasks_listed_per_level": per_level, "tasks_selected": len(tasks),
             "starts_in_manifest_including_unused": starts}
    return tasks, table


def provenance_check(root: Path, tasks_all: list[Task]) -> dict[str, Any]:
    """Check that every task in the campaign table has its output."""
    missing: list[str] = []
    empty: list[str] = []
    no_status: list[str] = []
    for task in tasks_all:
        main = task.base_directory / ("xtb.out" if task.level == "gfn2" else "job.log")
        if not main.is_file():
            missing.append(task.task)
        elif main.stat().st_size == 0:
            empty.append(task.task)
        if not (task.base_directory / "status.txt").is_file():
            no_status.append(task.task)
    return {
        "tasks_in_campaign_table": len(tasks_all),
        "without_main_output": missing,
        "with_empty_main_output": empty,
        "without_status_file": no_status,
        "repaired_tasks": sorted(f"{t.task} ({t.repaired})" for t in tasks_all if t.repaired),
    }


# --------------------------------------------------------------------------
# parsing the outputs
# --------------------------------------------------------------------------


def _last_input_orientation(text: str, elements: list[str]) -> list[list[Any]] | None:
    index = text.rfind("Input orientation:")
    if index < 0:
        return None
    lines = text[index:].splitlines()[5 : 5 + len(elements)]
    coords = []
    for element, line in zip(elements, lines):
        parts = line.split()
        coords.append([element, float(parts[3]), float(parts[4]), float(parts[5])])
    return coords


def parse_gjf(path: Path, elements: list[str]) -> list[list[Any]] | None:
    """Start coordinates from the input deck (blank-line separated Gaussian layout)."""
    if not path.is_file():
        return None
    blocks = re.split(r"\n\s*\n", path.read_text())
    if len(blocks) < 3:
        return None
    lines = [ln for ln in blocks[2].splitlines() if ln.strip()][1 : 1 + len(elements)]
    atoms = []
    for element, line in zip(elements, lines):
        parts = line.split()
        if parts[0].capitalize() != element:
            return None
        atoms.append([element, float(parts[1]), float(parts[2]), float(parts[3])])
    return atoms if len(atoms) == len(elements) else None


def parse_gaussian(directory: Path, elements: list[str]) -> Parsed:
    log = directory / "job.log"
    text = log.read_text(errors="replace")
    out = Parsed()
    out.normal_terminations = text.count("Normal termination of Gaussian")
    out.error = "Error termination" in text
    out.converged = "Optimization completed." in text
    scf = re.findall(r"SCF Done:\s+E\([^)]+\)\s*=\s*(-?\d+\.\d+)", text)
    out.e_elec = float(scf[-1]) if scf else None
    match = re.search(r"Zero-point correction=\s+(-?\d+\.\d+)", text)
    out.zpe = float(match.group(1)) if match else None
    out.freqs = [float(x) for line in re.findall(r"Frequencies --\s+(.*)", text) for x in line.split()]
    out.xyz = _last_input_orientation(text, elements)
    out.input_xyz = parse_gjf(directory / "job.gjf", elements)
    return out


def parse_xtb(directory: Path, elements: list[str]) -> Parsed:
    out = Parsed()
    text = (directory / "xtb.out").read_text(errors="replace")
    out.normal_terminations = int("normal termination of xtb" in text)
    out.error = "abnormal termination" in text.lower()
    out.converged = "GEOMETRY OPTIMIZATION CONVERGED" in text
    energies = re.findall(r"TOTAL ENERGY\s+(-?\d+\.\d+)\s+Eh", text)
    out.e_elec = float(energies[-1]) if energies else None
    match = re.search(r":: zero point energy\s+(-?\d+\.\d+)\s+Eh", text)
    out.zpe = float(match.group(1)) if match else None
    spectrum = directory / "vibspectrum"
    if spectrum.is_file():
        for line in spectrum.read_text().splitlines():
            parts = line.split()
            if parts and parts[0].isdigit() and abs(float(parts[-3])) > 1e-2:
                out.freqs.append(float(parts[-3]))
    final = directory / "xtbopt.xyz"
    if final.is_file():
        rows = final.read_text().splitlines()[2 : 2 + len(elements)]
        atoms = [row.split()[:4] for row in rows]
        if [a[0].capitalize() for a in atoms] == elements:
            out.xyz = [[e, float(x), float(y), float(z)] for e, (_, x, y, z) in zip(elements, atoms)]
    return out


def parse(task: Task) -> Parsed:
    if task.level == "gfn2":
        return parse_xtb(task.directory, task.elements)
    return parse_gaussian(task.directory, task.elements)


def xyz_text(atoms: list[list[Any]], comment: str) -> str:
    body = "\n".join("%s %.10f %.10f %.10f" % tuple(a) for a in atoms)
    return f"{len(atoms)}\n{comment}\n{body}\n"


# --------------------------------------------------------------------------
# building and sending
# --------------------------------------------------------------------------


def build_upload(task: Task, parsed: Parsed, levels: dict[str, Any]):
    spec = levels[task.level]
    software = SoftwareRelease(**spec["software"])
    level = LevelOfTheory(**spec["level"])
    final = Geometry.from_xyz(xyz_text(parsed.xyz, task.task))
    start = (
        Geometry.from_xyz(xyz_text(parsed.input_xyz, task.task + " (input deck)"))
        if parsed.input_xyz
        else None
    )
    opt = Calculation.opt(
        software,
        level,
        input_geometry=start,
        output_geometry=final,
        final_energy_hartree=parsed.e_elec,
        converged=True,
    )
    freq = Calculation.freq(
        software,
        level,
        input_geometry=final,
        frequencies_cm1=parsed.freqs,
        n_imag=parsed.n_imag,
        zpe_hartree=parsed.zpe,
        depends_on=opt,
    )
    for calc in (opt, freq):
        calc.add_artifact(task.directory / "job.log", "output_log")
        calc.add_artifact(task.directory / "job.gjf", "input")
    return ComputedSpeciesUpload(
        species=Species(smiles=task.smiles, charge=task.charge, multiplicity=task.multiplicity),
        calculations=[opt, freq],
    )


LEVELS_AVAILABLE = ("b3lyp_d3bj", "wb97xd")
GFN2_REFUSAL = "the gfn2 level is not deposited: xtb job files are not part of this build"


def validate(cfg: dict[str, Any]) -> list[str]:
    """Everything that can be checked before a single deposit is made."""
    problems: list[str] = []
    root = Path(str(cfg.get("root", ""))).expanduser()
    if not cfg.get("root"):
        problems.append(f"{SOURCE}: 'root' (the E2 directory) is required")
    else:
        for needed in ("raw_outputs/manifest_union.json", "raw_outputs/tasks.txt", "raw_outputs/tasks"):
            if not (root / needed).exists():
                problems.append(f"{SOURCE}: {needed} not found under the configured root")
    levels = cfg.get("levels") or list(LEVELS_AVAILABLE)
    for level in levels:
        if level == "gfn2":
            problems.append(f"{SOURCE}: {GFN2_REFUSAL}")
        elif level not in LEVELS_AVAILABLE:
            problems.append(f"{SOURCE}: unknown level {level!r}; available: {list(LEVELS_AVAILABLE)}")
    if "gfn2_log_as" in cfg:
        problems.append(f"{SOURCE}: 'gfn2_log_as' is not an option")
    return problems


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    """Count and digest of the source files this deposit reads, for the snapshot record (no absolute paths)."""
    root = Path(cfg["root"]).expanduser()
    levels = list(cfg.get("levels") or LEVELS_AVAILABLE)
    tasks, _ = load_tasks(root, levels)
    files = [root / "raw_outputs" / "manifest_union.json", root / "raw_outputs" / "tasks.txt"]
    for task in tasks:
        files += [task.directory / "job.log", task.directory / "job.gjf"]
    return digest_files(root, files)


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    root = Path(cfg["root"]).expanduser()
    levels_cfg = {**DEFAULT_LEVELS, **(cfg.get("level_definitions") or {})}
    wanted = list(cfg.get("levels") or LEVELS_AVAILABLE)
    src = report.source(SOURCE)

    all_levels = ["b3lyp_d3bj", "wb97xd", "gfn2"]
    tasks_all, table = load_tasks(root, all_levels)
    src["campaign_table"] = table
    src["provenance"] = provenance_check(root, tasks_all)
    skipped_levels = [lv for lv in all_levels if lv not in wanted]
    if skipped_levels:
        report.note(f"{SOURCE}: levels not deposited: {skipped_levels}. For gfn2: {GFN2_REFUSAL}.")
    tasks = [t for t in tasks_all if t.level in wanted]
    if cfg.get("limit_tasks"):  # a smoke-test aid; leave unset for a real build
        tasks = tasks[: int(cfg["limit_tasks"])]

    excluded: list[dict[str, Any]] = []
    unreadable: list[str] = []
    client = session.client()
    sent = 0
    try:
        for index, task in enumerate(tasks, 1):
            where = task.task
            try:
                parsed = parse(task)
            except Exception as exc:  # an unreadable log is a recorded outcome, never fatal and never silent
                report.refuse(SOURCE, where, exc)
                report.count(SOURCE, "outputs_unreadable")
                unreadable.append(task.task)
                continue
            if not parsed.is_minimum(task.level):
                excluded.append(
                    {
                        "task": task.task,
                        "converged": parsed.converged,
                        "n_imag": parsed.n_imag,
                        "lowest_cm1": min(parsed.freqs) if parsed.freqs else None,
                        "normal_terminations": parsed.normal_terminations,
                        "reason": "not a verified minimum (the study excludes it the same way)",
                    }
                )
                continue
            key = f"paper-snapshot:e2:{slug(task.task)}"
            try:
                upload = build_upload(task, parsed, levels_cfg)
                result = client.upload(upload, idempotency_key=key)
                report.count(SOURCE, "uploads_accepted")
                collect_response_warnings(report, SOURCE, where, result)
                plan = upload.artifact_plan(result)
                if plan:
                    outcome = client.upload_artifacts(plan, idempotency_key_prefix=key, batch_by_calculation=True)
                    for item in outcome:
                        report.count(SOURCE, "artifact_files_accepted", item.artifact_count)
                        for warning in item.warnings:
                            report.warn(SOURCE, where, warning if isinstance(warning, dict) else {"code": str(warning)})
                sent += 1
            except TCKDBError as exc:
                report.refuse(SOURCE, where, exc)
                report.count(SOURCE, "uploads_refused")
            except Exception as exc:  # builder-side validation: still a recorded outcome, never a silent skip
                report.refuse(SOURCE, where, exc)
                report.count(SOURCE, "uploads_failed_locally")
            if index % 20 == 0:
                print(f"  [{SOURCE}] {index}/{len(tasks)}", file=sys.stderr, flush=True)
    finally:
        client.close()
    src["excluded_not_minima"] = excluded
    src["outputs_unreadable"] = unreadable
    src["status"] = "deposited" if sent else "nothing deposited"
    src["levels_deposited"] = wanted
    src["tasks_attempted"] = len(tasks)
    src["not_deposited_files"] = {
        "gaussian": "fort.7 (punch file): no artifact kind admits the name",
        "repairs": "tasks with a repair directory deposit the continuation's output only (listed under provenance)",
    }
