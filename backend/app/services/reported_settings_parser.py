"""Read the grid and SCF settings an output log prints as the ones the program used.

A request (what a deck or an echoed input asks for) says what the depositor wanted; a program prints the settings
it applied, defaults included. This module reads only the second kind, and only the DFT integration grid and the SCF
convergence thresholds. Every pattern below was confirmed on a fixture log (excerpts are in the tests).

Rules, all deliberate:

* **Observations only.** A row is made only for a line the log prints. Absence is never a report.
* **No keyword is invented.** A program prints numbers (ORCA ``IntAcc`` and a Lebedev grid, Gaussian's internal
  ``IRadAn`` code), not ``DefGrid2`` or ``UltraFine``. The numbers are stored under their own canonical keys, and
  ``grid.quality`` / ``scf.convergence`` are never set from a report. Gaussian's ``IRadAn`` is stored as a raw row
  with no canonical key, because its correspondence to named grids is not documented.
* **Each report belongs to its own job.** A file is split into jobs at the program's banner (``Entering Gaussian
  System`` for Gaussian, the ORCA banner for ORCA). A setting is read from a job only when that job prints one value
  for it; when the jobs of a file print different values the setting is not reported at all, never taken from one
  job for another.
* **The COSX grids are not the DFT grid.** Only the block under ``DFT GRID GENERATION`` is read.
* **The optimiser's thresholds are not the SCF's.** ORCA prints ``Convergence Tolerances:`` (plural, geometry
  optimiser) and ``Convergence Tolerance:`` (SCF); only the SCF block under ``SCF SETTINGS`` is read.
"""

from __future__ import annotations

import re

#: Statement kind carried on every row this module returns.
REPORTED = "reported"

_ORCA_BANNER = re.compile(r"^\s*\*{3,}\s*\n\s*\*\s+O\s+R\s+C\s+A", re.M)
_GAUSSIAN_JOB = re.compile(r"Entering Gaussian System")


def _split(text: str, marker: re.Pattern[str]) -> list[str]:
    starts = [m.start() for m in marker.finditer(text)]
    if not starts:
        return [text]
    return [text[a:b] for a, b in zip(starts, [*starts[1:], len(text)], strict=True)]


def _number(token: str) -> float:
    return float(token.replace("D", "E").replace("d", "e"))


def _fmt(value: float) -> str:
    return repr(value)


def _row(raw_key, raw_value, canonical_key, canonical_value, section, value_type=None, unit=None) -> dict:
    return {
        "raw_key": raw_key,
        "raw_value": raw_value,
        "canonical_key": canonical_key,
        "canonical_value": canonical_value,
        "section": section,
        "value_type": value_type,
        "unit": unit,
        "statement_kind": REPORTED,
    }


def _agree(per_job: list[dict[str, dict]]) -> list[dict]:
    """Rows for the keys every job prints, identically. A key a job omits or prints differently is dropped."""
    if not per_job:
        return []
    out = []
    for key in per_job[0]:
        first = per_job[0][key]
        if all(job.get(key) == first for job in per_job):
            out.append(first)
    return out


# ---------------------------------------------------------------------------
# ORCA
# ---------------------------------------------------------------------------

_ORCA_GRID_BLOCK = re.compile(r"DFT GRID GENERATION\n-+\n\n(.*?)(?=\n\n[A-Z]|\Z)", re.S)
_ORCA_INTACC = re.compile(r"IntAcc\s+\.\.\.\s+([0-9.]+)")
_ORCA_ANGULAR = re.compile(r"AngularGrid\s+\.\.\.\s+(\d+)\s+\(([^)]*)\)")
_ORCA_PRUNING = re.compile(r"GridPruning\s+\.\.\.\s+(\d+)\s+\(([^)]*)\)")
_ORCA_LEBEDEV = re.compile(r"Lebedev-(\d+)")
_ORCA_SCF_TOL = re.compile(r"\nConvergence Tolerance:\n((?: .*\n)+)")
_ORCA_TOLS = {
    "TolE": ("scf.tol_energy_eh", "Eh"),
    "TolG": ("scf.tol_orbital_gradient", None),
    "TolX": ("scf.tol_orbital_rotation", None),
    "TolErr": ("scf.tol_diis_error", None),
}


def _orca_job(job: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    blocks = {m.group(1) for m in _ORCA_GRID_BLOCK.finditer(job)}
    parsed = []
    for block in blocks:
        acc, ang, prune = _ORCA_INTACC.search(block), _ORCA_ANGULAR.search(block), _ORCA_PRUNING.search(block)
        if acc and ang:
            parsed.append((acc.group(1), ang.group(1), ang.group(2), prune.group(0) if prune else None, prune))
    if len(parsed) == 1:
        acc, ang_n, ang_text, _, prune = parsed[0]
        rows["IntAcc"] = _row("IntAcc", acc, "grid.integration_accuracy", _fmt(float(acc)), "grid", "float")
        leb = _ORCA_LEBEDEV.search(ang_text)
        rows["AngularGrid"] = _row(
            "AngularGrid",
            f"{ang_n} ({ang_text})",
            "grid.angular_max" if leb else None,
            leb.group(1) if leb else None,
            "grid",
            "int" if leb else None,
        )
        if prune:
            rows["GridPruning"] = _row("GridPruning", f"{prune.group(1)} ({prune.group(2)})", None, None, "grid")
    scf_at = job.find("SCF SETTINGS")
    if scf_at >= 0:
        blocks = {m.group(1) for m in _ORCA_SCF_TOL.finditer(job, scf_at)}
        if len(blocks) == 1:
            block = next(iter(blocks))
            for name, (key, unit) in _ORCA_TOLS.items():
                m = re.search(rf"\b{name}\s+\.\.\.\.\s+([0-9.]+[eE][+-]?\d+)", block)
                if m:
                    rows[name] = _row(name, m.group(1), key, _fmt(_number(m.group(1))), "scf", "float", unit)
    return rows


_ORCA_MULTI_JOB = re.compile(r"^\|\s*\d+>.*(\$new_job|%compound)", re.I | re.M)
_GAUSSIAN_LINK1 = re.compile(r"^\s*--Link1--\s*$", re.I | re.M)


def parse_orca_reported(text: str) -> list[dict]:
    """Reported grid and SCF rows of an ORCA log; ``[]`` when the log prints none (or its jobs disagree).

    An ORCA file with ``$new_job`` or ``%compound`` in its echoed input prints its banner once for several jobs, so a
    block cannot be bound to one calculation: nothing is reported from such a file.
    """
    text = text.replace("\r\n", "\n")
    if _ORCA_MULTI_JOB.search(text):
        return []
    return _agree([_orca_job(job) for job in _split(text, _ORCA_BANNER)])


# ---------------------------------------------------------------------------
# Gaussian
# ---------------------------------------------------------------------------

_G_SCF = re.compile(
    r"Requested convergence on RMS density matrix=(\S+) within \d+ cycles\.\n"
    r" Requested convergence on MAX density matrix=(\S+)\.\n"
    r" Requested convergence on\s+energy=(\S+)\."
)
_G_GRID = re.compile(r"IXCGrd=\s*\d+ IRadAn=\s*(\d+) IRanWt")


def _gaussian_job(job: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    grids = set(_G_GRID.findall(job))
    if len(grids) == 1:
        code = next(iter(grids))
        rows["IRadAn"] = _row("IRadAn", code, None, None, "grid")
    triples = set(_G_SCF.findall(job))
    if len(triples) == 1:
        rms, mx, en = next(iter(triples))
        rows["RMS density"] = _row("rms_density", rms, "scf.tol_rms_density", _fmt(_number(rms)), "scf", "float")
        rows["MAX density"] = _row("max_density", mx, "scf.tol_max_density", _fmt(_number(mx)), "scf", "float")
        rows["energy"] = _row("energy", en, "scf.tol_energy_eh", _fmt(_number(en)), "scf", "float", "Eh")
    return rows


def parse_gaussian_reported(text: str) -> list[dict]:
    """Reported grid and SCF rows of a Gaussian log; ``[]`` when the log prints none (or its jobs disagree).

    A log whose echo shows ``--Link1--`` more often than the log splits into jobs cannot be bound to one calculation:
    nothing is reported from it.
    """
    text = text.replace("\r\n", "\n")
    jobs = _split(text, _GAUSSIAN_JOB)
    if len(_GAUSSIAN_LINK1.findall(text)) >= len(jobs):
        return []
    return _agree([_gaussian_job(job) for job in jobs])
