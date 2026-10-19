"""The debt-growth check fires on a count-for-count swap, which the in-PR tests cannot see.

``tests/test_no_versioned_names.py`` measures the debt list against numbers in
the same change. Fix one violation, plant another, swap the debt row, and every
total is unchanged. The one reference the author cannot edit is the merge base,
so ``backend/scripts/check_versioned_debt_not_grown.py`` reads the base's copy of
the list through git. These tests build real repositories because git's merge
base is the subject.

The module is loaded by path and needs nothing from the backend, because the
repo gate runs it with the standard library only (``--noconftest``).
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_versioned_debt_not_grown.py"
_spec = importlib.util.spec_from_file_location("check_versioned_debt_not_grown", SCRIPT)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

DEBT_REL = checker.DEBT_PATH


def _debt_source(rows: list[tuple[str, str, str, int, str]]) -> str:
    body = "".join(f"    {row!r},\n" for row in rows)
    return f"COMPLETED_PRS = frozenset()\nDEBT: tuple[tuple[str, str, str, int, str], ...] = (\n{body})\n"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _commit(repo: Path, rows: list[tuple[str, str, str, int, str]] | None, message: str) -> str:
    path = repo / DEBT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(_debt_source(rows))
    (repo / "marker.txt").write_text(message)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


BASE_ROWS = [
    ("backend/app/a.py", "identifier", "OLD_V1", 2, "PR-3"),
    ("backend/app/b.py", "string", "tckdb.thing.v1", 1, "PR-4"),
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def _run(repo: Path, base: str, head: str | None = None) -> int:
    argv = ["--repo", str(repo), "--base-ref", base]
    if head:
        argv += ["--head-ref", head]
    return int(checker.main(argv))


def _branch(repo: Path, rows: list[tuple[str, str, str, int, str]]) -> tuple[str, str]:
    base = _commit(repo, BASE_ROWS, "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, rows, "feature")
    return base, head


def test_a_shrinking_debt_passes(repo: Path) -> None:
    base, head = _branch(repo, [("backend/app/a.py", "identifier", "OLD_V1", 1, "PR-3")])
    assert _run(repo, base, head) == 0


def test_an_unchanged_debt_passes(repo: Path) -> None:
    base, head = _branch(repo, BASE_ROWS)
    assert _run(repo, base, head) == 0


def test_the_count_for_count_swap_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Fix OLD_V1 (2 uses), plant NEW_V1 (2 uses), swap the row: totals are identical."""
    swapped = [
        ("backend/app/a.py", "identifier", "NEW_V1", 2, "PR-3"),
        ("backend/app/b.py", "string", "tckdb.thing.v1", 1, "PR-4"),
    ]
    assert sum(r[3] for r in swapped) == sum(r[3] for r in BASE_ROWS) and len(swapped) == len(BASE_ROWS)
    base, head = _branch(repo, swapped)
    assert _run(repo, base, head) == 1
    assert "new debt row" in capsys.readouterr().err


def test_a_higher_count_is_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base, head = _branch(repo, [("backend/app/a.py", "identifier", "OLD_V1", 3, "PR-3"), BASE_ROWS[1]])
    assert _run(repo, base, head) == 1
    assert "grew from x2 to x3" in capsys.readouterr().err


def test_the_working_tree_is_used_when_no_head_ref_is_given(repo: Path) -> None:
    base, _ = _branch(repo, BASE_ROWS)
    (repo / DEBT_REL).write_text(_debt_source([("backend/app/z.py", "identifier", "SNEAKY_V2", 1, "PR-3")]))
    assert _run(repo, base) == 1


def test_a_branch_behind_main_is_measured_against_its_merge_base(repo: Path) -> None:
    """Main moved on and shrank the list; the feature branch must not be failed for that."""
    base = _commit(repo, BASE_ROWS, "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, BASE_ROWS, "feature")
    _git(repo, "checkout", "-q", "main")
    tip = _commit(repo, [BASE_ROWS[1]], "main moved: shrank")
    assert _run(repo, tip, head) == 0
    assert base != tip


def test_a_deleted_debt_file_at_head_passes(repo: Path) -> None:
    base, head = _branch(repo, [])
    path = repo / DEBT_REL
    path.unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "programme finished")
    assert _run(repo, base, _git(repo, "rev-parse", "HEAD")) == 0
    assert head != base


def test_a_base_without_the_debt_file_means_the_guard_is_being_introduced(repo: Path) -> None:
    (repo / "x.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, BASE_ROWS, "introduce")
    assert _run(repo, base, head) == 0


def test_no_merge_base_is_a_refusal_not_a_pass(repo: Path) -> None:
    base = _commit(repo, BASE_ROWS, "base")
    assert _run(repo, base, "no-such-ref") == 2


def test_grown_reports_each_problem_by_key() -> None:
    base = {("p", "identifier", "A"): 2}
    head = {("p", "identifier", "A"): 3, ("p", "identifier", "B"): 1}
    problems = checker.grown(base, head)
    assert len(problems) == 2
    assert any("'B'" in p and "new debt row" in p for p in problems)
    assert any("'A'" in p and "grew" in p for p in problems)


def test_parse_debt_reads_the_real_debt_file_without_importing_it() -> None:
    real = (Path(__file__).resolve().parents[1] / "versioned_names_debt.py").read_text(encoding="utf-8")
    parsed = checker.parse_debt(real)
    assert len(parsed) > 100
    assert all(isinstance(count, int) and count >= 1 for count in parsed.values())


def test_the_repo_gate_runs_the_check_with_the_base_and_head_shas() -> None:
    workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "repo-gate.yml").read_text()
    assert "backend/scripts/check_versioned_debt_not_grown.py" in workflow
    assert "fetch-depth: 0" in workflow
