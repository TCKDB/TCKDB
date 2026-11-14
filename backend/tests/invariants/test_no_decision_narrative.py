"""Source, test, script, client and frontend text describes present behaviour only.

Scans six trees for text that records how a decision was reached instead of what the
code does: design-record pointers, links into decision-record folders, references to the
manuscript folder, an owner's name, owner-decision wording, issue and pull-request ids,
review-finding numbers and phase labels used as history. Every hit is reported as
``path:line: matched text``.

The scan is a plain read of the working tree: no subprocess, no network.

Three properties keep it from passing on nothing:

* each tree must yield a minimum number of scanned files, so a wrong root fails;
* each allowlist entry (path + exact matched text + reason) must still match a line,
  so a stale entry fails;
* a self-test runs a planted sample of every pattern through the same matcher.

Exclusions are listed with a reason in ``_SKIP_DIRS``, ``_SKIP_SUFFIXES``,
``_SKIP_NAMES`` and ``_SKIP_PATHS``. This file is itself excluded by exact path,
because it has to spell the patterns out.
"""

from __future__ import annotations

import functools
import os
import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
THIS_FILE = "backend/tests/invariants/test_no_decision_narrative.py"

#: Scanned trees and the fewest files each must yield.
TREES: dict[str, int] = {
    "backend/app": 600,
    "backend/scripts": 80,
    "backend/tests": 1000,
    "schemas/python": 90,
    "clients/python": 240,
    "frontend/src": 450,
}

#: Pattern name -> regular expression. A line is a hit when any pattern matches.
PATTERNS: dict[str, re.Pattern[str]] = {
    "design-record pointer": re.compile(r"\bADR[ -]?\d|\bDR-\d{4}"),
    "decision-record folder link": re.compile(r"docs/(?:adr|decisions)/"),
    "manuscript folder": re.compile(r"\bpaper/"),
    "owner name": re.compile(r"\bCalvin\b"),
    "owner decision wording": re.compile(
        r"owner[ -]decisions?|owner rules?|decided (?:on|in) (?:\d{4}|Decision)|\bDecision \d"
        r"|\bcouncil\b|\bthe brief\b",
        re.IGNORECASE,
    ),
    "issue or pull-request id": re.compile(
        r"\(#\d+|\b(?:issue|PR|review|task) #\d+|\bfinding \d+\b|\bguard PR-\d", re.IGNORECASE
    ),
    "phase label": re.compile(r"\bPhase[- ]?[0-9A-Z]\b|\bphase[- ]\d\b|\bWP\d\b"),
}

#: Directory names never scanned. Reason: generated or third-party content, not authored text.
_SKIP_DIRS = frozenset(
    {
        "node_modules",  # third-party packages
        "dist",  # frontend build output
        "build",  # package build output
        "__pycache__",  # bytecode
        ".venv",  # virtual environment
        ".git",  # repository metadata
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)

#: Suffixes never scanned. Reason: binary content, or recorded program output and data.
_SKIP_SUFFIXES = frozenset(
    {
        ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".gz", ".zip", ".pdf", ".parquet",  # binary
        ".sdf",  # molecule files whose header lines hold depositor data, not prose
        ".out",  # captured ESS program output
        ".lock",  # lockfiles
        ".svg",  # vector artwork with numeric path data
    }
)

#: File names never scanned.
_SKIP_NAMES = frozenset(
    {
        "package-lock.json",  # lockfile
        "chem_annotated.yaml",  # recorded RMG model data: ids such as "#123" are data, not prose
    }
)

#: Files larger than this are machine-generated data (recorded payloads, models), not authored text.
_MAX_BYTES = 300_000

#: Exact repo-relative paths never scanned.
_SKIP_PATHS = frozenset(
    {
        THIS_FILE,  # spells the patterns out
        "backend/tests/api/golden/openapi.json",  # repeats docstrings that are already scanned at source
    }
)

#: Allowlist: (path, exact matched text, reason). No globs: each entry names one file and one matched text.
ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    ("schemas/python/tckdb-schemas/LICENSE", "Calvin", "copyright holder line of the licence"),
    ("clients/python/LICENSE", "Calvin", "copyright holder line of the licence"),
    ("backend/tests/scripts/test_aggregate_pr_gates.py", "paper/", "sample changed-file paths for the path-filter logic under test"),
    ("backend/tests/scripts/test_gate_coverage.py", "paper/", "names the manuscript folder as a path the workflow filters ignore"),
    ("backend/app/services/deposit/expected_outputs.py", "paper/", "names the real backend/scripts/paper/ directory"),
    ("backend/app/services/deposit/REPRODUCE.md.template", "paper/", "command line running the real backend/scripts/paper/ script"),
    ("backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/run_psi4_water_hessian.py", "paper/", "content-hashed fixture script: editing it changes its recorded digest"),
    ("backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/run_psi4_water_hessian.py", "Phase C", "content-hashed fixture script: editing it changes its recorded digest"),
    ("backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/README.md", "Phase C", "fixture notes recorded beside the content-hashed script"),
    ("backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/meta.json", "Phase C", "fixture metadata recorded with the content-hashed script"),
)


#: Lower-case literals every pattern needs in order to match. A line without any of them is skipped
#: before the regular expressions run, which keeps the scan fast over tens of megabytes of text.
_LITERALS = (
    "adr", "dr-", "docs/", "paper/", "calvin", "owner", "decided", "decision", "council", "the brief",
    "(#", "issue #", "pr #", "review #", "task #", "finding", "guard pr-", "phase", "wp",
)


def find_hits(text: str) -> list[tuple[int, str, str]]:
    """Return ``(line number, pattern name, matched text)`` for every hit in *text*."""
    hits: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if not any(literal in lowered for literal in _LITERALS):
            continue
        for name, pattern in PATTERNS.items():
            for match in pattern.finditer(line):
                hits.append((number, name, match.group(0)))
    return hits


def _iter_files(tree: str):
    for directory, dirnames, filenames in os.walk(REPO_ROOT / tree):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".egg-info"))
        for filename in sorted(filenames):
            path = Path(directory) / filename
            relative = path.relative_to(REPO_ROOT).as_posix()
            if filename in _SKIP_NAMES or path.suffix in _SKIP_SUFFIXES or relative in _SKIP_PATHS:
                continue
            if path.stat().st_size > _MAX_BYTES:
                continue
            yield relative, path


@functools.lru_cache(maxsize=1)
def scan() -> tuple[list[tuple[str, int, str, str]], Counter[str], Counter[tuple[str, str]]]:
    """Scan every tree. Returns (hits, files scanned per tree, allowlist entries that matched)."""
    allowed = {(path, text) for path, text, _reason in ALLOWLIST}
    hits: list[tuple[str, int, str, str]] = []
    scanned: Counter[str] = Counter()
    used: Counter[tuple[str, str]] = Counter()
    for tree in TREES:
        for relative, path in _iter_files(tree):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            scanned[tree] += 1
            for number, name, matched in find_hits(text):
                if (relative, matched) in allowed:
                    used[(relative, matched)] += 1
                else:
                    hits.append((relative, number, name, matched))
    return hits, scanned, used


def test_no_decision_narrative_in_scanned_trees() -> None:
    hits, _scanned, _used = scan()
    assert not hits, (
        "Decision narrative found. Describe present behaviour instead:\n"
        + "\n".join(f"{path}:{number}: [{name}] {matched}" for path, number, name, matched in hits)
    )


def test_every_tree_yields_the_minimum_number_of_files() -> None:
    _hits, scanned, _used = scan()
    short = {tree: (scanned[tree], minimum) for tree, minimum in TREES.items() if scanned[tree] < minimum}
    assert not short, f"Trees scanned fewer files than expected (scanned, minimum): {short}"


def test_every_allowlist_entry_still_matches() -> None:
    _hits, _scanned, used = scan()
    stale = [(path, text) for path, text, _reason in ALLOWLIST if used[(path, text)] == 0]
    assert not stale, f"Allowlist entries that no longer match any line: {stale}"


def test_every_allowlist_entry_names_a_reason_and_an_existing_file() -> None:
    for path, text, reason in ALLOWLIST:
        assert reason.strip(), f"allowlist entry {path!r} {text!r} has no reason"
        assert (REPO_ROOT / path).is_file(), f"allowlist entry names a missing file: {path}"


def test_every_excluded_path_exists() -> None:
    missing = [path for path in sorted(_SKIP_PATHS) if not (REPO_ROOT / path).is_file()]
    assert not missing, f"excluded paths that do not exist: {missing}"


#: One planted sample per pattern name; the matcher must flag each one.
_PLANTED = {
    "design-record pointer": ["see ADR 0012", "per ADR-0008", "DR-0038 says", "ADR0021"],
    "decision-record folder link": ["docs/adr/0003.md", "docs/decisions/0001-x.md"],
    "manuscript folder": ["see paper/figures", "paper/notes.md"],
    "owner name": ["Calvin chose this"],
    "owner decision wording": [
        "an owner decision",
        "the owner rules apply",
        "decided on 2026-10",
        "decided in Decision 3",
        "Decision 4 of the notes",
        "the council asked for it",
        "the brief expected green",
    ],
    "issue or pull-request id": ["fixed (#123)", "see issue #45", "PR #12", "review #7", "task #9", "finding 6", "guard PR-3"],
    "phase label": ["Phase 1 wiring", "Phase-7 demo", "phase 2 cell", "Phase D policy", "WP0 scan"],
}

#: Ordinary text the patterns must leave alone.
_CLEAN = [
    "a two-phase artifact upload",
    "the gas phase and the liquid phase",
    "the owner_kind column",
    "ownership rule",
    "Admin-only route (adr is not a word here)",
    "colour #1b1e23 and anchor #section",
    "the decision to refuse is made by the validator",
]


def test_every_pattern_catches_its_planted_samples() -> None:
    assert set(_PLANTED) == set(PATTERNS), "every pattern needs a planted sample"
    for name, samples in _PLANTED.items():
        for sample in samples:
            names = {found for _line, found, _text in find_hits(sample)}
            assert name in names, f"pattern {name!r} missed its planted sample {sample!r}"


def test_clean_text_is_not_flagged() -> None:
    for sample in _CLEAN:
        assert not find_hits(sample), f"clean sample was flagged: {sample!r} -> {find_hits(sample)}"
