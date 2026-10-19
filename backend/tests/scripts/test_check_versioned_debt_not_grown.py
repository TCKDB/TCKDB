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


def _debt_source(
    rows: list[tuple[str, str, str, int, str]],
    allowed: tuple[tuple[str, str, str, str], ...] = (),
    new: tuple[tuple[str, str, str], ...] = (),
    extra: str = "",
) -> str:
    body = "".join(f"    {row!r},\n" for row in rows)
    return (
        "COMPLETED_PRS = frozenset()\n"
        f"NEW_ALLOWANCES = {new!r}\n"
        f"ALLOWED = {allowed!r}\n"
        f"DEBT: tuple[tuple[str, str, str, int, str], ...] = (\n{body})\n{extra}"
    )


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _commit(
    repo: Path,
    rows: list[tuple[str, str, str, int, str]] | None,
    message: str,
    source: str | None = None,
) -> str:
    path = repo / DEBT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(source if source is not None else _debt_source(rows))
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


def _branch(
    repo: Path, rows: list[tuple[str, str, str, int, str]], source: str | None = None, base_source: str | None = None
) -> tuple[str, str]:
    base = _commit(repo, BASE_ROWS, "base", base_source)
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, rows, "feature", source)
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


def test_deleting_the_debt_file_while_the_base_still_lists_rows_is_refused(repo: Path) -> None:
    base, _ = _branch(repo, BASE_ROWS)
    (repo / DEBT_REL).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "delete the debt file")
    assert _run(repo, base, _git(repo, "rev-parse", "HEAD")) == 1


def test_deleting_the_debt_file_when_the_base_debt_is_already_empty_passes(repo: Path) -> None:
    base, _ = _branch(repo, [], base_source=_debt_source([]))
    (repo / DEBT_REL).unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "programme finished")
    assert _run(repo, base, _git(repo, "rev-parse", "HEAD")) == 0


def test_moving_debt_out_of_the_module_is_refused(repo: Path) -> None:
    moved = "from elsewhere import DEBT\nALLOWED = ()\nNEW_ALLOWANCES = ()\n"
    base, head = _branch(repo, [], source=moved)
    assert _run(repo, base, head) == 1


@pytest.mark.parametrize(
    "extra",
    [
        "DEBT = DEBT + (('backend/app/new.py', 'identifier', 'NEW_V1', 1, 'PR-3'),)\n",
        "DEBT += (('backend/app/new.py', 'identifier', 'NEW_V1', 1, 'PR-3'),)\n",
        "DEBT: tuple = ()\n",
        "for DEBT in ((),):\n    pass\n",
        "def DEBT():\n    return ()\n",
        "from x import DEBT\n",
    ],
)
def test_a_second_binding_of_debt_is_refused(repo: Path, extra: str, capsys: pytest.CaptureFixture[str]) -> None:
    base, head = _branch(repo, BASE_ROWS, source=_debt_source(BASE_ROWS, extra=extra))
    assert _run(repo, base, head) == 1
    assert "exactly once" in capsys.readouterr().err


@pytest.mark.parametrize(
    "assignment",
    ["DEBT = tuple(ROWS)", "DEBT = [('a', 'b', 'c', 1, 'PR-3')]", "DEBT = BASE + OTHER", "DEBT = ()  if True else ()"],
)
def test_debt_must_be_a_plain_literal_tuple(repo: Path, assignment: str, capsys: pytest.CaptureFixture[str]) -> None:
    source = f"ROWS = ()\nBASE = ()\nOTHER = ()\nALLOWED = ()\nNEW_ALLOWANCES = ()\n{assignment}\n"
    base, head = _branch(repo, [], source=source)
    assert _run(repo, base, head) == 1
    assert "literal tuple" in capsys.readouterr().err


def test_a_zero_or_empty_base_fails_closed(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, BASE_ROWS, "base")
    assert _run(repo, "0" * 40) == 2
    assert "Failing closed" in capsys.readouterr().err
    assert _run(repo, "") == 2


ALLOW = ("backend/app/x/*.py", "string", "tool/*", "A third party's own name, recorded as given by the tool.")


def test_a_new_allowance_must_be_declared(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base, head = _branch(repo, BASE_ROWS, source=_debt_source(BASE_ROWS, allowed=(ALLOW,)), base_source=_debt_source(BASE_ROWS))
    assert _run(repo, base, head) == 1
    assert "not declared in NEW_ALLOWANCES" in capsys.readouterr().err


def test_a_declared_new_allowance_passes_and_is_printed_for_the_reviewer(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _debt_source(BASE_ROWS, allowed=(ALLOW,), new=(ALLOW[:3],))
    base, head = _branch(repo, BASE_ROWS, source=source, base_source=_debt_source(BASE_ROWS))
    assert _run(repo, base, head) == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("::warning")]
    assert lines == [f"::warning title=New versioned-name allowance::{ALLOW[:3]!r} \u2014 {ALLOW[3]}"]


def test_a_widened_glob_counts_as_a_new_allowance(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    widened = (ALLOW[0], ALLOW[1], "*", ALLOW[3])
    base, head = _branch(
        repo, BASE_ROWS, source=_debt_source(BASE_ROWS, allowed=(widened,)), base_source=_debt_source(BASE_ROWS, allowed=(ALLOW,))
    )
    assert _run(repo, base, head) == 1
    assert "not declared" in capsys.readouterr().err


def test_an_unchanged_allowance_list_passes(repo: Path) -> None:
    source = _debt_source(BASE_ROWS, allowed=(ALLOW,))
    base, head = _branch(repo, BASE_ROWS, source=source, base_source=source)
    assert _run(repo, base, head) == 0


def test_declaring_an_allowance_that_is_not_in_allowed_is_refused(repo: Path) -> None:
    base, head = _branch(repo, BASE_ROWS, source=_debt_source(BASE_ROWS, new=(ALLOW[:3],)))
    assert _run(repo, base, head) == 1


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


def test_a_push_is_compared_with_the_commit_it_replaced() -> None:
    """On a push ``github.sha`` is the head, so using it as the base compares a commit with itself."""
    workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "repo-gate.yml").read_text()
    step = workflow[workflow.index("check_versioned_debt_not_grown.py\n") :]
    step = step[: step.index("--head-ref")]
    assert "github.event.pull_request.base.sha || github.event.before" in step
    assert "github.sha" not in step
