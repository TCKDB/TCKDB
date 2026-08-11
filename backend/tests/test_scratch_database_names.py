"""Every scratch database a test creates must be reclaimable.

Two reclaimers exist, and both only ever look at ``tckdb_test%``:

* ``_sweep_stale_test_databases`` in :mod:`conftest`, which runs at session
  start and drops databases this harness abandoned;
* ``backend/scripts/dev/reclaim_leaked_test_databases.py``, the deliberate
  human-driven cleanup for everything the sweep cannot reason about.

Several migration tests used to build their own scratch names —
``tckdb_et_scope_migration_*``, ``tckdb_stage2_legacy_*``,
``tckdb_exec_env_migration_*``, ``tckdb_rpa_migration_*``. They matched
neither reclaimer, so a run killed partway (a session limit, a Ctrl-C, an
OOM) leaked them permanently; one was found and dropped by hand on
2026-08-10.

Renaming them fixed the ones that existed. This file is what stops the next
one drifting back out silently: a new migration test that follows the
convention is covered automatically, and one that does not is named here
rather than discovered as a stray database months later.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import conftest
import pytest

TESTS_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = TESTS_ROOT.parent

#: ``conftest`` owns the session database and validates that name itself; this
#: file only quotes the statement it is scanning for.
_EXEMPT = {TESTS_ROOT / "conftest.py", Path(__file__).resolve()}


def _test_modules_that_create_databases() -> list[Path]:
    found = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        if path in _EXEMPT:
            continue
        if "CREATE DATABASE" in path.read_text(encoding="utf-8"):
            found.append(path)
    return found


def test_the_scan_finds_the_files_it_is_meant_to_guard() -> None:
    """A guard whose scan matches nothing would pass forever.

    Pinned to a floor rather than an exact list so adding a migration test
    does not fail this assertion for the wrong reason — the per-file check
    below is what actually judges a new one.
    """
    creators = _test_modules_that_create_databases()

    assert len(creators) >= 8, (
        "expected the migration tests that create scratch databases to be "
        f"found by this scan; got {[str(p) for p in creators]}"
    )
    names = {p.name for p in creators}
    assert "test_stage2_scientific_integrity_migration.py" in names
    assert "test_energy_transfer_scope_migration.py" in names


@pytest.mark.parametrize(
    "path", _test_modules_that_create_databases(), ids=lambda p: p.name
)
def test_scratch_databases_come_from_the_reclaimable_helper(path: Path) -> None:
    """A test that issues ``CREATE DATABASE`` must name it via the helper.

    Deliberately a structural rule, not a name pattern: matching the name
    pattern at the call site would still let a future test build a
    conforming-looking name by hand and get the length arithmetic wrong,
    which Postgres resolves by silently truncating. Routing every name
    through one function makes the guarantee one function's job.
    """
    source = path.read_text(encoding="utf-8")

    assert "scratch_database_name" in source, (
        f"{path.relative_to(BACKEND_ROOT)} issues CREATE DATABASE but does not "
        "use conftest.scratch_database_name. Scratch databases named any other "
        "way are invisible to _sweep_stale_test_databases and to "
        "scripts/dev/reclaim_leaked_test_databases.py, so a killed run leaks "
        "them permanently."
    )

    tree = ast.parse(source, filename=str(path))
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "conftest"
        and any(alias.name == "scratch_database_name" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported, (
        f"{path.relative_to(BACKEND_ROOT)} references scratch_database_name but "
        "never imports it from conftest"
    )


# ---------------------------------------------------------------------------
# The helper's own contract
# ---------------------------------------------------------------------------


def test_helper_output_is_reclaimable_by_both_reclaimers() -> None:
    name = conftest.scratch_database_name("stage2_legacy")

    assert conftest._TEST_DATABASE_NAME.fullmatch(name)
    assert _reclaim_script_pattern().fullmatch(name)
    assert name.startswith("tckdb_test_")
    assert "stage2_legacy" in name


def test_helper_output_fits_postgres_identifier_limit() -> None:
    """Postgres truncates over-long names silently rather than rejecting them.

    A truncated name is still reclaimable — the prefix survives — but two
    long labels must not collapse onto one database, so the uniqueness suffix
    is what has to survive trimming, not the label.
    """
    long_label = "a_very_long_migration_label_" + "x" * 80

    first = conftest.scratch_database_name(long_label)
    second = conftest.scratch_database_name(long_label)

    assert len(first) <= 63
    assert len(second) <= 63
    assert first != second
    assert conftest._TEST_DATABASE_NAME.fullmatch(first)


def test_helper_is_unique_per_call() -> None:
    names = {conftest.scratch_database_name("dupe") for _ in range(50)}

    assert len(names) == 50


def test_helper_sanitizes_labels_postgres_would_reject() -> None:
    name = conftest.scratch_database_name("et-scope.downgrade")

    assert conftest._TEST_DATABASE_NAME.fullmatch(name)
    assert "-" not in name
    assert "." not in name


def test_helper_rejects_an_empty_label() -> None:
    with pytest.raises(ValueError):
        conftest.scratch_database_name("---")


# ---------------------------------------------------------------------------
# The two reclaimers must agree on what "reclaimable" means
# ---------------------------------------------------------------------------


def _reclaim_script_pattern() -> re.Pattern[str]:
    """The name pattern the standalone reclaim script enforces.

    Read out of the source rather than imported: the script lives under
    ``scripts/dev`` and importing it drags in ``psycopg`` and argparse
    plumbing this file has no use for.
    """
    source = (
        BACKEND_ROOT / "scripts" / "dev" / "reclaim_leaked_test_databases.py"
    ).read_text(encoding="utf-8")
    match = re.search(r"^TEST_DB_NAME = re\.compile\(r\"(.+)\"\)$", source, re.MULTILINE)
    assert match is not None, "could not locate TEST_DB_NAME in the reclaim script"
    return re.compile(match.group(1))


def test_the_nightly_job_database_is_protected_from_the_reclaim_script() -> None:
    """``tckdb_test_ci`` is a CI ``DB_NAME``, not a harness database.

    The in-process sweep is safe from it by construction — no harness marker,
    no reclaim. This script reads no markers, and the name sits inside
    ``tckdb_test%``, so on a self-hosted runner ``plan`` would list the
    nightly job's own database as a candidate.
    """
    source = (
        BACKEND_ROOT / "scripts" / "dev" / "reclaim_leaked_test_databases.py"
    ).read_text(encoding="utf-8")
    protected = re.search(r"PROTECTED = frozenset\((.*?)\n\)", source, re.DOTALL)

    assert protected is not None
    assert '"tckdb_test_ci"' in protected.group(1)

    workflow = (
        BACKEND_ROOT.parent / ".github" / "workflows" / "backend-nightly.yml"
    ).read_text(encoding="utf-8")
    # Non-vacuous: the protection is only worth anything while the nightly
    # actually uses this name.
    assert "DB_NAME: tckdb_test_ci" in workflow


def test_both_reclaimers_use_the_same_name_pattern() -> None:
    """Widening one reclaimer without the other would be a silent half-fix.

    The sweep runs automatically and the script is run by a human; a name
    covered by one and not the other is reclaimed only sometimes, which is
    the worst of the three outcomes.
    """
    assert _reclaim_script_pattern().pattern == conftest._TEST_DATABASE_NAME.pattern
