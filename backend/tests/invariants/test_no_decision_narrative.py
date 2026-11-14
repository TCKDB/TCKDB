"""Source, test, script, client and frontend text describes present behaviour only.

Scans six trees for text that records how a decision was reached instead of what the
code does: design-record pointers, links into decision-record folders, references to the
manuscript folder, an owner's name, owner-decision wording, issue and pull-request ids,
review-finding numbers, work-package and phase labels used as history. Every hit is
reported as ``path:line: [pattern] matched text``.

Lowercase usernames and email addresses are not part of this guard; ``\bCalvin\b`` is
matched case-sensitively.

The file list is ``git ls-files`` over the six trees, so untracked and ignored files
never reach the scan (a new file is scanned once it is ``git add``-ed, and CI always
sees it); the test fails with a clear message when git or the repository
is unavailable. Nothing else is read from outside the working tree. The module uses
only the standard library, so it also runs as
``python -m pytest --noconftest backend/tests/invariants/test_no_decision_narrative.py``
in the path-unfiltered repo gate.

Properties that keep it from passing on nothing:

* each tree must yield a minimum number of scanned files, so a wrong root fails;
* each allowlist entry (path + exact matched text + expected count + reason) must match
  exactly that many times, so a stale entry fails and a new hit in an allowlisted file
  fails;
* every global exclusion (suffix, name, path, rule) must still match a tracked file;
* a tracked file that is neither a known binary suffix nor valid UTF-8 fails the scan;
* a tracked file over ``_LARGE_FILE_BYTES`` that no exclusion names fails the scan;
* a self-test runs a planted sample of every pattern through the same matcher,
  including the literal pre-filter.

Adding a pattern alternative has three parts: the regular expression, a lower-case
literal in ``_LITERALS`` that any match must contain (lines without one are skipped
before the regular expressions run), and a planted sample in ``_PLANTED``.
The self-test fails when a planted sample is not flagged or does not pass the pre-filter.

Exclusions are listed with a reason in ``_BINARY_SUFFIXES`` and ``_SKIP_RULES``. This file
is itself excluded by exact path, because it has to spell the patterns out.
"""

from __future__ import annotations

import fnmatch
import functools
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
THIS_FILE = "backend/tests/invariants/test_no_decision_narrative.py"

#: Scanned trees and the fewest files each must yield.
TREES: dict[str, int] = {
    "backend/app": 600,
    "backend/scripts": 80,
    "backend/tests": 1000,
    "schemas/python": 90,
    "clients/python": 190,
    "frontend/src": 450,
}

#: Pattern name -> regular expression. A line is a hit when any pattern matches.
PATTERNS: dict[str, re.Pattern[str]] = {
    "design-record pointer": re.compile(r"\bADR[ -]?\d+|\bDR-\d{4}"),
    "decision-record folder link": re.compile(r"docs/(?:adr|decisions)/"),
    "manuscript folder": re.compile(r"\bpaper/"),
    "owner name": re.compile(r"\bCalvin\b"),
    "owner decision wording": re.compile(
        r"owner[ -]decisions?|owner rules?|decided (?:on|in) (?:\d{4}|Decision)|\bDecision \d"
        r"|\bcouncil\b|\bthe brief\b"
        r"|\bdecided\b[^.\n]{0,20}\b20\d\d|\b20\d\d-\d\d-\d\d decision",
        re.IGNORECASE,
    ),
    "issue or pull-request id": re.compile(
        r"\(#\d+(?![\da-fA-F])|\b(?:issue|PR|review|task) #\d+|\bfinding \d+\b|\bguard PR-\d"
        r"|(?:pre-|since |after |before |until )#\d+"
        r"|\bfinding F\d+\b",
        re.IGNORECASE,
    ),
    "phase or work-package label": re.compile(
        r"\bPhase[- ]?(?:\d+[A-Za-z]?|[A-Z]\d*[a-z]?)\b|\bphase[- ]\d+[A-Za-z]?\b|\bWP\d\b"
        r"|\bC-[EQ]\d+\b|\bwork package\b|\bChunks? \d{1,2}\b|\bpre-Stage\b"
    ),
}

#: Patterns applied only to files with these suffixes: they match short tokens that are
#: ordinary in data files.
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".md")
SOURCE_ONLY_PATTERNS: dict[str, re.Pattern[str]] = {
    "decision id": re.compile(r"\((?:B|D|M)\d\)"),
}

#: Suffixes that are binary and never read.
_BINARY_SUFFIXES = frozenset({".png"})

#: A scanned file larger than this must be covered by an explicit exclusion below; see
#: ``test_no_large_file_is_scanned_by_accident``.
_LARGE_FILE_BYTES = 300_000

_GENERATED = "generated from the scanned sources by generate_producer_contract.py; its --check keeps it in sync"
_CONTRACT = "schemas/python/tckdb-schemas/tckdb_schemas/contract/"

#: Directory-scoped exclusions: (directory prefix, file-name glob, reason).
_SKIP_RULES: tuple[tuple[str, str, str], ...] = (
    (_CONTRACT + "schemas/", "*", _GENERATED),
    (_CONTRACT, "PRODUCER_CONTRACT.md", _GENERATED),
    (_CONTRACT, "REFUSAL_CODES.md", _GENERATED),
    (_CONTRACT, "WARNING_CODES.md", _GENERATED),
    (
        _CONTRACT, "CHANGELOG.md",
        "byte-identical copy of the scanned schemas/python/tckdb-schemas/CHANGELOG.md, written by generate_producer_contract.py",
    ),
    ("backend/tests/fixtures/arc_runs/", "output.yml", "recorded producer program output, data not prose"),
    ("backend/tests/fixtures/arc_runs/", "*.payload.json", "recorded producer payloads, data not prose"),
    ("backend/tests/fixtures/qcschema_scan/", "*.json", "recorded producer payloads, data not prose"),
    ("clients/python/adapters/qcschema/tests/fixtures/", "*.json", "recorded QCSchema documents, data not prose"),
    (
        "backend/tests/integration/fixtures/rmg_formic_acid_wu2026/", "chem_annotated.*",
        "recorded RMG model data, ids such as \"#123\" are data, not prose",
    ),
    ("backend/tests/fixtures/sdf/", "*.csv", "recorded kinetics table, data not prose"),
    ("backend/tests/fixtures/sdf/", "*.sdf", "molecule files whose header lines hold depositor data, not prose"),
    ("backend/tests/fixtures/", "*.out", "captured ESS program output, data not prose"),
    ("frontend/src/assets/", "*.svg", "vector artwork with numeric path data"),
    (
        "backend/tests/api/golden/", "openapi.json",
        "repeats docstrings that are already scanned at source",
    ),
)

#: Exact repo-relative paths never scanned.
_SKIP_PATHS = frozenset(
    {
        THIS_FILE,  # spells the patterns out
    }
)

_PSI4 = "backend/tests/fixtures/qcschema/water_b3lyp_def2tzvp_psi4/run_psi4_water_hessian.py"
_PSI4_REASON = "content-hashed fixture script: editing it changes its recorded digest"
_LICENCE_REASON = "copyright holder line of the licence"

#: Allowlist: (path, exact matched text, expected number of matches, reason).
#: No globs: each entry names one file and one matched text, and pins how often it occurs.
ALLOWLIST: tuple[tuple[str, str, int, str], ...] = (
    ("schemas/python/tckdb-schemas/LICENSE", "Calvin", 1, _LICENCE_REASON),
    ("clients/python/LICENSE", "Calvin", 1, _LICENCE_REASON),
    (
        "backend/tests/scripts/test_aggregate_pr_gates.py", "paper/", 6,
        "sample changed-file paths for the path-filter logic under test",
    ),
    (
        "backend/tests/scripts/test_gate_coverage.py", "paper/", 1,
        "names the manuscript folder as a path the workflow filters ignore",
    ),
    ("backend/app/services/deposit/expected_outputs.py", "paper/", 1, "names the real backend/scripts/paper/ directory"),
    (
        "backend/app/services/deposit/REPRODUCE.md.template", "paper/", 1,
        "command line running the real backend/scripts/paper/ script",
    ),
    (_PSI4, "paper/", 1, _PSI4_REASON),
    (_PSI4, "Phase C", 1, _PSI4_REASON),
    (_PSI4, "C-Q4", 1, _PSI4_REASON),
)

#: Lower-case literals every pattern needs in order to match. A line without any of them is skipped
#: before the regular expressions run, which keeps the scan fast over tens of megabytes of text.
_LITERALS = (
    "adr", "dr-", "docs/", "paper/", "calvin", "owner", "decided", "decision", "council", "the brief",
    "(#", "issue #", "pr #", "review #", "task #", "finding", "guard pr-",
    "pre-#", "since #", "after #", "before #", "until #",
    "phase", "wp", "c-e", "c-q", "work package", "chunk", "pre-stage",
    "(b", "(d", "(m",
)


def _passes_prefilter(line: str) -> bool:
    lowered = line.lower()
    return any(literal in lowered for literal in _LITERALS)


def find_hits(text: str, suffix: str = ".py") -> list[tuple[int, str, str]]:
    """Return ``(line number, pattern name, matched text)`` for every hit in *text*."""
    patterns = dict(PATTERNS)
    if suffix in SOURCE_SUFFIXES:
        patterns.update(SOURCE_ONLY_PATTERNS)
    hits: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not _passes_prefilter(line):
            continue
        for name, pattern in patterns.items():
            for match in pattern.finditer(line):
                hits.append((number, name, match.group(0)))
    return hits


def _tracked_files() -> list[str]:
    """Repo-relative paths of the files git tracks under the scanned trees."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", *TREES],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        pytest.fail(
            "This guard lists files with `git ls-files`; git or the repository is unavailable "
            f"at {REPO_ROOT}: {error}"
        )
    return sorted(name for name in result.stdout.decode("utf-8").split("\0") if name)


def _excluded_by_rule(relative: str) -> bool:
    directory, _, name = relative.rpartition("/")
    return any(
        f"{directory}/".startswith(prefix) and fnmatch.fnmatchcase(name, pattern)
        for prefix, pattern, _reason in _SKIP_RULES
    )


def _tree_of(relative: str) -> str:
    return next(tree for tree in TREES if relative.startswith(tree + "/"))


def _scannable_files() -> list[str]:
    return [
        relative
        for relative in _tracked_files()
        if relative not in _SKIP_PATHS
        and Path(relative).suffix not in _BINARY_SUFFIXES
        and not _excluded_by_rule(relative)
    ]


@functools.lru_cache(maxsize=1)
def scan() -> tuple[list[tuple[str, int, str, str]], Counter[str], Counter[tuple[str, str]], list[str], list[str]]:
    """Scan every tracked file.

    Returns (hits, files scanned per tree, allowlist matches per (path, text), undecodable files,
    large files).
    """
    allowed = {(path, text) for path, text, _count, _reason in ALLOWLIST}
    hits: list[tuple[str, int, str, str]] = []
    scanned: Counter[str] = Counter()
    used: Counter[tuple[str, str]] = Counter()
    undecodable: list[str] = []
    large: list[str] = []
    for relative in _scannable_files():
        path = REPO_ROOT / relative
        if path.stat().st_size > _LARGE_FILE_BYTES:
            large.append(relative)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            undecodable.append(relative)
            continue
        scanned[_tree_of(relative)] += 1
        for number, name, matched in find_hits(text, path.suffix):
            if (relative, matched) in allowed:
                used[(relative, matched)] += 1
            else:
                hits.append((relative, number, name, matched))
    return hits, scanned, used, undecodable, large


def test_no_decision_narrative_in_scanned_trees() -> None:
    hits, _scanned, _used, _undecodable, _large = scan()
    assert not hits, (
        "Decision narrative found. Describe present behaviour instead:\n"
        + "\n".join(f"{path}:{number}: [{name}] {matched}" for path, number, name, matched in hits)
    )


def test_every_tree_yields_the_minimum_number_of_files() -> None:
    _hits, scanned, _used, _undecodable, _large = scan()
    short = {tree: (scanned[tree], minimum) for tree, minimum in TREES.items() if scanned[tree] < minimum}
    assert not short, f"Trees scanned fewer files than expected (scanned, minimum): {short}"


def test_every_allowlist_entry_matches_exactly_its_expected_count() -> None:
    _hits, _scanned, used, _undecodable, _large = scan()
    wrong = [
        (path, text, f"expected {count}, found {used[(path, text)]}")
        for path, text, count, _reason in ALLOWLIST
        if used[(path, text)] != count
    ]
    assert not wrong, f"Allowlist entries whose match count changed: {wrong}"


def test_every_allowlist_entry_names_a_reason_and_a_tracked_file() -> None:
    tracked = set(_tracked_files())
    for path, text, count, reason in ALLOWLIST:
        assert reason.strip(), f"allowlist entry {path!r} {text!r} has no reason"
        assert count >= 1, f"allowlist entry {path!r} {text!r} has no expected count"
        assert path in tracked, f"allowlist entry names an untracked or missing file: {path}"


def test_every_tracked_file_is_text_or_a_known_binary() -> None:
    _hits, _scanned, _used, undecodable, _large = scan()
    assert not undecodable, (
        f"Files that are not UTF-8 text and not a known binary suffix {sorted(_BINARY_SUFFIXES)}: {undecodable}"
    )


def test_no_large_file_is_scanned_by_accident() -> None:
    """A big file that no exclusion names fails, so adding one is a deliberate decision."""
    _hits, _scanned, _used, _undecodable, large = scan()
    assert not large, (
        f"Files over {_LARGE_FILE_BYTES} bytes are scanned as prose. Split them, or add an explicit "
        f"exclusion with a reason: {large}"
    )


def test_every_exclusion_still_matches_a_tracked_file() -> None:
    tracked = _tracked_files()
    stale_rules = [
        (prefix, pattern)
        for prefix, pattern, _reason in _SKIP_RULES
        if not any(
            relative.startswith(prefix) and fnmatch.fnmatchcase(relative.rpartition("/")[2], pattern)
            for relative in tracked
        )
    ]
    assert not stale_rules, f"exclusion rules that match no tracked file: {stale_rules}"
    stale_paths = [path for path in sorted(_SKIP_PATHS) if path not in set(tracked)]
    assert not stale_paths, f"excluded paths that are not tracked: {stale_paths}"
    stale_suffixes = [
        suffix for suffix in sorted(_BINARY_SUFFIXES) if not any(name.endswith(suffix) for name in tracked)
    ]
    assert not stale_suffixes, f"binary suffixes that match no tracked file: {stale_suffixes}"


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
        "this was decided in October 2026",
        "2026-10-03 decision",
    ],
    "issue or pull-request id": [
        "fixed (#123)", "see issue #45", "PR #12", "review #7", "task #9", "finding 6", "guard PR-3",
        "the pre-#234 behaviour", "since #12", "after #5", "before #6", "until #7",
        "review finding 3", "review finding F2", "finding F12",
    ],
    "phase or work-package label": [
        "Phase 1 wiring", "Phase-7 demo", "phase 2 cell", "Phase D policy", "Phase 3C", "Phase 5a", "phase 10",
        "WP0 scan", "(C-Q1)", "C-E6 brief", "a work package", "Chunk 3", "Chunks 4", "pre-Stage tests",
    ],
}
_PLANTED_SOURCE_ONLY = {"decision id": ["a rule (B1)", "the call (D5)", "see (M1)"]}

#: Ordinary text the patterns must leave alone.
_CLEAN = [
    "a two-phase artifact upload",
    "the gas phase and the liquid phase",
    "the owner_kind column",
    "ownership rule",
    "Admin-only route (adr is not a word here)",
    "colour #1b1e23 and anchor #section",
    "background (#1b1e23)",
    "the decision to refuse is made by the validator",
    "Phase Transition data",
    "chunked upload of one chunk",
    "the value is decided by the digest",
]


def test_every_pattern_catches_its_planted_samples() -> None:
    assert set(_PLANTED) == set(PATTERNS), "every pattern needs a planted sample"
    for name, samples in _PLANTED.items():
        for sample in samples:
            names = {found for _line, found, _text in find_hits(sample)}
            assert name in names, f"pattern {name!r} missed its planted sample {sample!r}"


def test_source_only_patterns_catch_their_samples_in_source_files_only() -> None:
    assert set(_PLANTED_SOURCE_ONLY) == set(SOURCE_ONLY_PATTERNS)
    for name, samples in _PLANTED_SOURCE_ONLY.items():
        for sample in samples:
            assert name in {found for _l, found, _t in find_hits(sample, ".py")}, sample
            assert not find_hits(sample, ".json"), f"{name!r} matched in a data file: {sample!r}"


def test_every_planted_sample_passes_the_literal_prefilter() -> None:
    samples = [s for group in (*_PLANTED.values(), *_PLANTED_SOURCE_ONLY.values()) for s in group]
    missing = [sample for sample in samples if not _passes_prefilter(sample)]
    assert not missing, f"planted samples skipped by the literal pre-filter: {missing}"


def test_clean_text_is_not_flagged() -> None:
    for sample in _CLEAN:
        assert not find_hits(sample), f"clean sample was flagged: {sample!r} -> {find_hits(sample)}"
