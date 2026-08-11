"""Every test file must be run by at least one required CI job.

This is a repo-check, not a backend test: it reads the gate scripts and
``.github/workflows/backend-ci.yml`` and asserts that the union of what
they select covers ``backend/tests/`` exactly.

It exists because that property silently stopped holding and nothing
noticed. The two required jobs ran ``tests/api/`` and
``tests/api/scientific/`` + ``tests/services/scientific_read/``. Everything
else -- tests/db/, tests/workflows/, tests/invariants/, tests/services/
outside scientific_read, tests/schemas/, tests/importers/, tests/parsers/,
tests/cli/, tests/workers/, tests/integration/ -- 3,806 tests, more than
half the suite by count, gated no pull request at all. It ran nightly, so a
defect merged green and surfaced the next morning attached to no PR:
``tests/db/test_identifier_lengths.py`` sat red on main for days that way.

Nothing about that was visible. ``test-api.sh`` read as "the API gate"
while being "some of the API", and a directory added to ``tests/`` joined
no gate unless somebody remembered to add it. The gate scripts now state
what they exclude, and this test is what makes the statement checkable --
a comment claiming coverage is the failure mode, not the fix.

The check is deliberately mechanical about the *files*, not the
directories: it enumerates the real ``test_*.py`` on disk and asks which
job would run each one, so a new directory is caught the moment it exists
rather than the next time somebody audits a list.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
TESTS_ROOT = BACKEND_ROOT / "tests"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "backend-ci.yml"

#: The gate scripts the workflow is required to invoke. Their union is
#: supposed to be a partition of ``tests/``; deleting one of these entries
#: is exactly the regression this file is here to refuse.
REQUIRED_GATE_SCRIPTS = (
    "backend/scripts/test-api.sh",
    "backend/scripts/test-scientific.sh",
    "backend/scripts/test-rest.sh",
)


class Selection:
    """A pytest invocation reduced to "which files would this run".

    ``paths`` are the positional test paths; ``ignored`` are the
    ``--ignore`` arguments. Both are stored relative to ``backend/`` with
    any trailing slash removed, because the scripts and the workflow are
    inconsistent about it (``tests/api/`` in one, ``tests/api`` in the
    other) and that difference must not change the answer.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.paths: set[str] = set()
        self.ignored: set[str] = set()

    def covers(self, rel_path: str) -> bool:
        if any(_is_under(rel_path, ignored) for ignored in self.ignored):
            return False
        return any(_is_under(rel_path, path) for path in self.paths)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"Selection({self.label!r}, paths={sorted(self.paths)}, ignored={sorted(self.ignored)})"


def _normalize(token: str) -> str:
    return token.rstrip("/")


def _is_under(rel_path: str, prefix: str) -> bool:
    """True when ``rel_path`` is ``prefix`` or lives inside it.

    Compared segment-wise rather than with ``str.startswith`` so that
    ``tests/api`` does not appear to contain ``tests/api_extra/x.py``.
    """
    path_parts = Path(rel_path).parts
    prefix_parts = Path(prefix).parts
    return path_parts[: len(prefix_parts)] == prefix_parts


def _absorb_args(selection: Selection, tokens: list[str]) -> None:
    """Fold a pytest argument list into ``selection``."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--ignore="):
            selection.ignored.add(_normalize(token.split("=", 1)[1]))
        elif token == "--ignore" and index + 1 < len(tokens):
            selection.ignored.add(_normalize(tokens[index + 1]))
            index += 1
        elif token.startswith("tests/") or token == "tests":
            selection.paths.add(_normalize(token))
        index += 1


def _is_syntax_check(tokens: list[str]) -> bool:
    """``bash -n <script>`` parses a script; it does not run its tests.

    Adjacency matters. The real gate steps read
    ``conda run -n tckdb_env bash backend/scripts/test-api.sh ...``, which
    contains both ``bash`` and ``-n`` while being an invocation -- a
    membership test would classify every gate step as a syntax check and
    report the whole suite uncovered.
    """
    return any(
        token == "bash" and index + 1 < len(tokens) and tokens[index + 1] == "-n"
        for index, token in enumerate(tokens)
    )


def _join_continuations(text: str) -> list[str]:
    """Collapse backslash-continued shell lines into single logical lines."""
    lines: list[str] = []
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append(pending + stripped)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def _script_selection(script_rel: str) -> Selection:
    """The selection a gate script performs when run with no extra args."""
    script = REPO_ROOT / script_rel
    selection = Selection(script_rel)
    for line in _join_continuations(script.read_text(encoding="utf-8")):
        if line.startswith("#") or "pytest" not in line:
            continue
        _absorb_args(selection, shlex.split(line))
    return selection


def _workflow_selections() -> list[Selection]:
    """Every pytest selection reachable from a required backend-ci job.

    All matrix legs are unioned because all of them are required: the
    ``backend-ci`` job needs ``backend-gate`` and asserts its result is
    ``success``, so a test run by any leg is a test that gates the PR.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    selections: list[Selection] = []
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            run = step.get("run")
            if not run:
                continue
            label = f"{job_name}:{step.get('name', '<unnamed>')}"
            for line in _join_continuations(run):
                if line.startswith("#"):
                    continue
                try:
                    tokens = shlex.split(line)
                except ValueError:
                    # Heredoc bodies and Python fragments are not shell
                    # commands; nothing in them invokes pytest.
                    continue
                if _is_syntax_check(tokens):
                    # ``bash -n backend/scripts/test-rest.sh`` parses the
                    # script and runs nothing. Counting it would let the
                    # complement gate be deleted from the matrix while the
                    # hygiene step kept this file green -- the exact shape
                    # of the defect being guarded against.
                    continue
                gate = next(
                    (token for token in tokens if token in REQUIRED_GATE_SCRIPTS),
                    None,
                )
                if gate is not None:
                    selection = _script_selection(gate)
                    selection.label = f"{label} -> {gate}"
                    _absorb_args(selection, tokens)
                    selections.append(selection)
                elif "pytest" in tokens:
                    selection = Selection(label)
                    _absorb_args(selection, tokens)
                    if selection.paths:
                        selections.append(selection)
    return selections


def _test_files() -> list[str]:
    return sorted(
        str(path.relative_to(BACKEND_ROOT).as_posix())
        for path in TESTS_ROOT.rglob("test_*.py")
        if "__pycache__" not in path.parts
    )


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


def test_every_test_file_is_run_by_a_required_ci_job() -> None:
    selections = _workflow_selections()
    files = _test_files()
    assert files, "no test files discovered; the walk is wrong, not the repo"

    uncovered = [path for path in files if not any(sel.covers(path) for sel in selections)]
    assert not uncovered, (
        "these test files are run by no required CI job, so a defect in them "
        "merges green and surfaces in the nightly attached to no pull "
        "request:\n  " + "\n  ".join(uncovered)
    )


def test_complement_gate_excludes_only_what_another_gate_runs() -> None:
    """test-rest.sh may subtract only subtrees another gate owns.

    Adding an ``--ignore`` here is the cheapest possible way to reopen the
    gap, because it looks like a narrowing rather than a deletion.
    """
    rest = _script_selection("backend/scripts/test-rest.sh")
    others = [
        _script_selection("backend/scripts/test-api.sh"),
        _script_selection("backend/scripts/test-scientific.sh"),
    ]
    assert rest.paths == {"tests"}, (
        "test-rest.sh must select the whole tree and subtract from it. An "
        "include-list stops covering a directory the day somebody adds one."
    )
    for ignored in sorted(rest.ignored):
        owned_by = [
            other.label
            for other in others
            # Either direction: the complement may subtract a subtree another
            # gate selects wholesale (tests/services/scientific_read), or one
            # that contains what another gate selects (tests/api, of which the
            # scientific gate takes tests/api/scientific).
            if any(_is_under(path, ignored) or _is_under(ignored, path) for path in other.paths)
        ]
        assert owned_by, (
            f"test-rest.sh ignores {ignored!r}, which no other gate script "
            "selects, so nothing on a pull request runs it."
        )


@pytest.mark.parametrize("script_rel", REQUIRED_GATE_SCRIPTS)
def test_workflow_invokes_every_gate_script(script_rel: str) -> None:
    """Named, so a deleted matrix leg fails here and not only in aggregate.

    Asserted against the parsed invocations rather than against the file
    text: the hygiene step mentions all three scripts under ``bash -n``,
    and a substring search would call that an invocation.
    """
    invoked = {
        label.split(" -> ", 1)[1]
        for label in (selection.label for selection in _workflow_selections())
        if " -> " in label
    }
    assert script_rel in invoked, (
        f"{script_rel} is not invoked by backend-ci.yml. A gate script that "
        "no workflow runs is documentation, not a gate."
    )


# ---------------------------------------------------------------------------
# Non-vacuity: the checks above pass trivially if the parser reports
# everything as covered, so pin that ignores are actually honoured.
# ---------------------------------------------------------------------------


def test_ignores_are_honoured_by_the_parser() -> None:
    rest = _script_selection("backend/scripts/test-rest.sh")
    assert rest.covers("tests/db/test_identifier_lengths.py")
    assert not rest.covers("tests/api/test_api_health.py")
    assert not rest.covers("tests/services/scientific_read/test_species.py")


def test_prefix_matching_is_segment_wise() -> None:
    assert _is_under("tests/api/test_x.py", "tests/api")
    assert not _is_under("tests/api_extra/test_x.py", "tests/api")
    assert _is_under("tests/api", "tests/api")
