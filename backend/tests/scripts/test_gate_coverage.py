"""Every test file in the repository must be run by a required CI job.

This is a repo-check, not a backend test: it reads the gate scripts and
the workflows under ``.github/workflows/`` and asserts that the union of
what the required jobs select covers every ``test_*.py`` in the tree --
``backend/tests/``, ``clients/python/``, ``integrations/mcp/`` and
``schemas/python/`` alike.

It exists because that property silently stopped holding and nothing
noticed. Twice.

The first time was inside ``backend/tests/``. The two required jobs ran
``tests/api/`` and ``tests/api/scientific/`` + ``tests/services/
scientific_read/``. Everything else -- tests/db/, tests/workflows/,
tests/invariants/, tests/services/ outside scientific_read, tests/
schemas/, tests/importers/, tests/parsers/, tests/cli/, tests/workers/,
tests/integration/ -- 3,806 tests, more than half the suite by count,
gated no pull request at all. It ran nightly, so a defect merged green
and surfaced the next morning attached to no pull request:
``tests/db/test_identifier_lengths.py`` sat red on main for days that
way. The complement gate and the first version of this file closed that.

The second time was everything this file could not see, because its walk
stopped at ``backend/tests/``:

  * ``clients/python/tests/test_openapi_parity.py`` sat red on main from
    the moment #121 added two routes it did not classify until #133
    noticed by hand. The "Python client tests" job exists -- it simply
    was not consulted, because nothing made a red client suite block a
    merge, and a job that exists is not a job that is read.
  * ``clients/python/adapters/chemkin/tests/`` -- 54 tests -- ran in no
    job at all. ``testpaths = ["tests"]`` in ``clients/python/
    pyproject.toml`` means bare ``pytest`` there never sees the adapter,
    and the workflow ran bare ``pytest``.
  * ``schemas/python/tckdb-schemas/tests/`` -- 38 tests over the wire
    contract every client and the backend share -- ran in no job at all.
    Two workflows install the package; neither tests it.
  * ``integrations/mcp/tests/`` ran in backend-ci, whose ``paths:``
    filter does not mention ``integrations/**``. A pull request touching
    only the MCP server therefore triggered no workflow whatsoever: the
    job covered the files and could not be reached by a change to them.

That last one is why coverage alone is not the property. A required job
that runs a file but does not *trigger* on it is a gate the file can
walk around, so ``test_every_test_file_is_run_by_a_job_that_triggers_on_it``
matches each file against the ``paths:`` filter of the workflow that
runs it.

The check is deliberately mechanical about the *files*, not the
directories: it enumerates the real ``test_*.py`` on disk and asks which
job would run each one, so a new directory is caught the moment it
exists rather than the next time somebody audits a list.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import tomllib
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


def _load_module(name: str, path: Path):
    """Import by path, without putting ``backend/scripts`` on ``sys.path``.

    This file is run by the repo gate with ``--noconftest`` and no backend
    rootdir, so the usual ``from scripts import ...`` is unavailable; and
    ``backend/scripts`` contains ``lib``, ``dev``, ``ops`` and
    ``validation``, which have no business shadowing anything.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The path-filter reader lives with the aggregate gate that depends on it
#: at runtime (``backend/scripts/aggregate_pr_gates.py``) and is imported
#: here rather than reimplemented. Two answers to "would this workflow
#: trigger on this file" is one answer too many: the aggregate excuses an
#: absent run when the filter says it could not have started, and this
#: file asserts a covered file can reach the job that covers it. If those
#: two disagreed, a file could be declared reachable here and excused
#: there.
gates = _load_module("aggregate_pr_gates", BACKEND_ROOT / "scripts" / "aggregate_pr_gates.py")

#: The workflows whose jobs gate a pull request. Everything else under
#: ``.github/workflows/`` is a schedule, a deploy, or a docs build, and a
#: test that only those run is a test that gates nothing.
#:
#: This is an include-list, and it is the only one here on purpose: there
#: is no way to derive "required" from the repository -- GitHub keeps it
#: in branch settings, not in the tree. Everything downstream of it (which
#: files exist, which jobs run them, which paths reach those jobs) is
#: derived rather than listed.
#:
#: Three of these four are required *indirectly*. Those three are path-filtered,
#: and a path-filtered check cannot be listed in branch protection: it
#: never reports on a pull request outside its paths and GitHub waits for
#: it forever, so listing ``Backend CI`` would make every ``paper/`` typo
#: permanently unmergeable. They gate a merge through the "Required gates"
#: job in ``repo-gate.yml``, which always runs and reports their
#: conclusions. ``test_every_required_workflow_is_actually_required``
#: below is what keeps this list and that job's list the same list.
REQUIRED_WORKFLOWS = (
    ".github/workflows/repo-gate.yml",
    ".github/workflows/backend-ci.yml",
    ".github/workflows/frontend-ci.yml",
    ".github/workflows/python-client-ci.yml",
)

#: The workflow holding the checks branch protection lists by name. Every
#: other required workflow reaches branch protection through it.
DIRECTLY_REQUIRED_WORKFLOW = ".github/workflows/repo-gate.yml"

#: The job names branch protection lists as required contexts. Renaming a
#: job silently un-requires whatever it gates, because a context that
#: never reports is a context GitHub stops waiting for only if an admin
#: removes it -- and an admin who removes it removes the gate.
REQUIRED_CONTEXTS = ("CI gate coverage", "Required gates")

#: The gate scripts the backend workflow is required to invoke. Their union
#: is supposed to cover ``backend/tests/`` between them; deleting one of
#: these entries is exactly the regression this file is here to refuse.
REQUIRED_GATE_SCRIPTS = (
    "backend/scripts/test-api.sh",
    "backend/scripts/test-scientific.sh",
    "backend/scripts/test-rest.sh",
)

#: Directory names the walk never descends into. ``.``-prefixed components
#: are skipped wholesale, which matters more than it looks: this repository
#: is developed in git worktrees under ``.claude/worktrees/``, each a full
#: copy of the tree. Walking one would let a stale checkout decide whether
#: this repository's CI is complete.
SKIPPED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        "node_modules",
        "site-packages",
        "build",
        "dist",
        "venv",
        "site",
    }
)

#: pytest options that consume the following token, so that the value is
#: not mistaken for a positional test path.
OPTIONS_TAKING_A_VALUE = frozenset(
    {
        "-c",
        "-k",
        "-m",
        "-n",
        "-o",
        "-p",
        "-W",
        "--deselect",
        "--ignore",
        "--ignore-glob",
        "--import-mode",
        "--junitxml",
        "--maxfail",
        "--rootdir",
        "--tb",
    }
)


class Selection:
    """A pytest invocation reduced to "which files would this run".

    ``paths`` are the positional test paths and ``ignored`` the
    ``--ignore`` arguments, both resolved to repository-relative POSIX
    paths with any trailing slash removed -- the scripts and the
    workflows are inconsistent about it (``tests/api/`` in one,
    ``tests/api`` in the other) and that difference must not change the
    answer.

    ``workflow`` is the workflow file the invocation came from, which is
    what lets the trigger check ask whether a change to a covered file
    would actually start the job that covers it.
    """

    def __init__(self, label: str, workflow: str | None = None) -> None:
        self.label = label
        self.workflow = workflow
        self.paths: set[str] = set()
        self.ignored: set[str] = set()

    def covers(self, rel_path: str) -> bool:
        if any(_is_under(rel_path, ignored) for ignored in self.ignored):
            return False
        return any(_is_under(rel_path, path) for path in self.paths)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"Selection({self.label!r}, paths={sorted(self.paths)}, ignored={sorted(self.ignored)})"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _resolve(base: str, token: str) -> str:
    """Repository-relative POSIX form of ``token`` interpreted under ``base``."""
    joined = Path(base) / token if base not in ("", ".") else Path(token)
    normalized = Path(joined.as_posix().replace("./", "")).as_posix().rstrip("/")
    return normalized


def _is_under(rel_path: str, prefix: str) -> bool:
    """True when ``rel_path`` is ``prefix`` or lives inside it.

    Compared segment-wise rather than with ``str.startswith`` so that
    ``backend/tests/api`` does not appear to contain
    ``backend/tests/api_extra/x.py``.
    """
    path_parts = Path(rel_path).parts
    prefix_parts = Path(prefix).parts
    return path_parts[: len(prefix_parts)] == prefix_parts


# ---------------------------------------------------------------------------
# Shell / pytest argument parsing
# ---------------------------------------------------------------------------


def _absorb_args(selection: Selection, tokens: list[str], base: str, *, after_pytest: bool = False) -> None:
    """Fold one pytest argument list, interpreted under ``base``, into ``selection``.

    Nothing is read as a pytest argument until the ``pytest`` token has
    been seen, and this is load-bearing in both directions. Everything to
    the left belongs to a launcher with its own flag vocabulary, in which
    the same spellings mean other things: ``conda run -n tckdb_env
    pytest`` would contribute the environment name as a test path if
    positionals were taken from the start, and ``python -m pytest ...``
    is worse -- ``-m`` is pytest's marker option, so consuming its value
    swallows the word ``pytest`` itself, the invocation parses as having
    no arguments at all, and the selection silently falls back to
    ``testpaths``. Both of those were live defects in the first draft of
    this parser, and the second made the whole check vacuous while every
    assertion in this file passed.

    ``after_pytest=True`` is for gate-script invocations, whose extra
    arguments (``--ignore=...``, ``--maxfail=5``) are forwarded to pytest
    by the script and so are pytest arguments despite no ``pytest`` token
    appearing on the line.

    Positional paths are taken only when they exist on disk. The
    existence test rejects the unexpanded shell fragments the gate
    scripts are written with (``"${TCKDB_PYTEST_ARGS[@]}"``, ``"$@"``);
    the failure direction is safe, because a path this drops shows up as
    an uncovered file rather than as silent coverage.
    """
    seen_pytest = after_pytest
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not seen_pytest:
            seen_pytest = token == "pytest" or token.endswith("/pytest")
        elif token.startswith("--ignore="):
            selection.ignored.add(_resolve(base, token.split("=", 1)[1]))
        elif token in ("--ignore", "--ignore-glob") and index + 1 < len(tokens):
            selection.ignored.add(_resolve(base, tokens[index + 1]))
            index += 1
        elif token in OPTIONS_TAKING_A_VALUE and index + 1 < len(tokens):
            index += 1
        elif token.startswith("-"):
            pass
        elif "$" not in token and (REPO_ROOT / _resolve(base, token)).exists():
            selection.paths.add(_resolve(base, token))
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


def _testpaths(base: str) -> list[str]:
    """``testpaths`` from the pyproject governing a bare ``pytest`` in ``base``.

    A bare ``pytest`` is not "run everything under the working
    directory": ``clients/python/pyproject.toml`` sets
    ``testpaths = ["tests"]``, which is precisely why 54 CHEMKIN adapter
    tests sitting one directory over ran in no job while the workflow
    step looked like it ran the package.
    """
    pyproject = REPO_ROOT / base / "pyproject.toml" if base not in ("", ".") else REPO_ROOT / "pyproject.toml"
    if not pyproject.is_file():
        return [base]
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    paths = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths")
    if not paths:
        return [base]
    return [_resolve(base, path) for path in paths]


def _script_selection(script_rel: str) -> Selection:
    """The selection a gate script performs when run with no extra args.

    The gate scripts ``cd`` to the directory above ``scripts/`` before
    invoking pytest, so their ``tests/`` arguments are relative to
    ``backend/`` rather than to wherever CI called them from.
    """
    script = REPO_ROOT / script_rel
    base = Path(script_rel).parent.parent.as_posix()
    selection = Selection(script_rel)
    for line in _join_continuations(script.read_text(encoding="utf-8")):
        if line.startswith("#") or "pytest" not in line:
            continue
        _absorb_args(selection, shlex.split(line), base)
    return selection


# ---------------------------------------------------------------------------
# Workflow parsing
# ---------------------------------------------------------------------------


def _workflow(workflow_rel: str) -> dict:
    return gates.load_workflow(workflow_rel, REPO_ROOT)


#: The ``on:`` block. PyYAML is a YAML 1.1 parser, in which the bare key
#: ``on`` is the boolean ``True``; reading ``workflow["on"]`` returns
#: nothing and every path filter silently reads as absent, which would
#: make the trigger check below pass unconditionally.
_triggers = gates.workflow_triggers


def _workflow_triggers_on(workflow_rel: str, rel_path: str) -> bool:
    """Would editing ``rel_path`` start this workflow's pull_request run?"""
    return gates.workflow_triggers_on(_workflow(workflow_rel), rel_path)


def _step_base(workflow: dict, job: dict, step: dict) -> str:
    for source in (step, job.get("defaults", {}).get("run", {}), workflow.get("defaults", {}).get("run", {})):
        directory = source.get("working-directory")
        if directory:
            return str(directory).rstrip("/")
    return "."


def _workflow_selections() -> list[Selection]:
    """Every pytest selection reachable from a required job.

    All matrix legs are unioned because all of them are required: the
    ``backend-ci`` job needs ``backend-gate`` and asserts its result is
    ``success``, so a test run by any leg is a test that gates the pull
    request.
    """
    selections: list[Selection] = []
    for workflow_rel in REQUIRED_WORKFLOWS:
        workflow = _workflow(workflow_rel)
        for job_name, job in workflow["jobs"].items():
            for step in job.get("steps", []):
                run = step.get("run")
                if not run:
                    continue
                base = _step_base(workflow, job, step)
                label = f"{workflow_rel}::{job_name}:{step.get('name', '<unnamed>')}"
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
                    gate = next((token for token in tokens if token in REQUIRED_GATE_SCRIPTS), None)
                    if gate is not None:
                        selection = _script_selection(gate)
                        selection.label = f"{label} -> {gate}"
                        selection.workflow = workflow_rel
                        _absorb_args(
                            selection,
                            tokens,
                            Path(gate).parent.parent.as_posix(),
                            after_pytest=True,
                        )
                        selections.append(selection)
                    elif "pytest" in tokens:
                        selection = Selection(label, workflow_rel)
                        _absorb_args(selection, tokens, base)
                        if not selection.paths:
                            selection.paths.update(_testpaths(base))
                        selections.append(selection)
    return selections


def _is_test_file(name: str) -> bool:
    """pytest's default ``python_files``, both spellings.

    ``*_test.py`` is not used in this repository today. It is matched
    anyway, because the first file written that way would otherwise be
    collected by pytest and invisible to this check -- an uncovered test
    that the coverage guard itself agrees is fine.
    """
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _test_files() -> list[str]:
    """Every file pytest would collect as a test module, repository-relative."""
    found: list[str] = []
    stack = [REPO_ROOT]
    while stack:
        directory = stack.pop()
        for entry in directory.iterdir():
            if entry.is_dir():
                if entry.name.startswith(".") or entry.name in SKIPPED_DIR_NAMES or entry.name.endswith(".egg-info"):
                    continue
                stack.append(entry)
            elif _is_test_file(entry.name):
                found.append(entry.relative_to(REPO_ROOT).as_posix())
    return sorted(found)


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
        "merges green and surfaces -- if at all -- attached to no pull "
        "request:\n  " + "\n  ".join(uncovered)
    )


def test_every_test_file_is_run_by_a_job_that_triggers_on_it() -> None:
    """Coverage is not enough: the covering job has to start.

    ``integrations/mcp/tests/`` was run by backend-ci, whose ``paths:``
    filter listed ``backend/**`` and ``schemas/**`` and nothing else, so
    a pull request that changed only the MCP server started no workflow
    at all. The job covered the files; a change to them could not reach
    the job.
    """
    selections = _workflow_selections()
    unreachable = [
        path
        for path in _test_files()
        if not any(
            sel.covers(path) and sel.workflow is not None and _workflow_triggers_on(sel.workflow, path)
            for sel in selections
        )
    ]
    assert not unreachable, (
        "these test files are run only by workflows whose paths: filter a "
        "change to them does not match, so the pull request that breaks them "
        "starts no job that would notice:\n  " + "\n  ".join(unreachable)
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
    assert rest.paths == {"backend/tests"}, (
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
        f"{script_rel} is not invoked by a required workflow. A gate script "
        "that no workflow runs is documentation, not a gate."
    )


@pytest.mark.parametrize("workflow_rel", REQUIRED_WORKFLOWS)
def test_required_workflows_run_on_pull_requests(workflow_rel: str) -> None:
    assert "pull_request" in _triggers(_workflow(workflow_rel)), (
        f"{workflow_rel} is listed as a required gate but has no pull_request "
        "trigger, so nothing it checks gates a merge."
    )


def test_repo_gate_is_not_path_filtered() -> None:
    """The guard itself must run on every pull request.

    If ``repo-gate.yml`` grew a ``paths:`` filter, a pull request that
    added an ungated test directory outside that filter would not run
    this file, and the check would go quiet in exactly the case it exists
    for. It is cheap enough (no database, no conda, seconds) that there is
    no reason to filter it.
    """
    pull_request = _triggers(_workflow(".github/workflows/repo-gate.yml")).get("pull_request")
    assert not (isinstance(pull_request, dict) and pull_request.get("paths")), (
        "repo-gate.yml must run on every pull request. A path filter here is "
        "a hole shaped exactly like the ones this file exists to close."
    )


# ---------------------------------------------------------------------------
# The bridge from "required" (a GitHub setting) to "runs" (this tree)
#
# REQUIRED_WORKFLOWS above is an include-list because GitHub keeps
# "required" in branch settings. Three of its four entries are path-filtered
# and therefore cannot be listed there at all; they gate a merge only
# because the "Required gates" job in repo-gate.yml waits for them. These
# checks are what stop that sentence from quietly becoming false.
# ---------------------------------------------------------------------------


def _repo_gate_jobs() -> dict:
    return _workflow(DIRECTLY_REQUIRED_WORKFLOW)["jobs"]


def _job_named(name: str) -> dict:
    jobs = [job for job in _repo_gate_jobs().values() if job.get("name") == name]
    assert len(jobs) == 1, f"expected exactly one job named {name!r} in {DIRECTLY_REQUIRED_WORKFLOW}, found {len(jobs)}"
    return jobs[0]


@pytest.mark.parametrize("context", REQUIRED_CONTEXTS)
def test_the_required_contexts_exist_as_job_names(context: str) -> None:
    """Branch protection lists job names. A renamed job is an un-required gate.

    GitHub matches a required status check by its context string. Rename
    the job and the context stops reporting; the pull request then blocks
    on a check that will never arrive, and the fix somebody reaches for
    under pressure is to delete the requirement.
    """
    _job_named(context)


def test_every_required_workflow_is_actually_required() -> None:
    """The two lists have to be one list.

    ``REQUIRED_WORKFLOWS`` says which workflows gate a merge; the
    aggregate job's ``AGGREGATED_WORKFLOWS`` says which ones it waits for.
    A workflow in the first and not the second is a suite this file
    reports as gating a pull request while nothing blocks the merge on it
    -- which is the state the whole repository was in until the aggregate
    existed. A workflow in the second and not the first is a gate that
    blocks merges without being declared.
    """
    aggregated = set(gates.AGGREGATED_WORKFLOWS)
    declared = set(REQUIRED_WORKFLOWS) - {DIRECTLY_REQUIRED_WORKFLOW}
    assert aggregated == declared, (
        "backend/scripts/aggregate_pr_gates.py waits for "
        f"{sorted(aggregated)} but REQUIRED_WORKFLOWS declares {sorted(declared)} "
        "as gating a pull request indirectly."
    )
    assert DIRECTLY_REQUIRED_WORKFLOW not in aggregated, (
        "the aggregate must not wait for its own workflow; it would never finish."
    )


def test_required_gates_job_is_named_and_unconditional() -> None:
    """No ``needs:``, no ``if:``, on purpose.

    A job GitHub skips reports the conclusion ``skipped``, and branch
    protection accepts a skipped required check as satisfied. So any
    condition on this job -- including a ``needs:`` on a sibling that
    failed -- is a way for the gate to be satisfied without running. The
    single event test the script does need lives in Python, where it is a
    code path with a test against it rather than an expression nothing
    evaluates.
    """
    job = _job_named("Required gates")
    assert "needs" not in job, (
        "a `needs:` here means a failed dependency skips this job, and a skipped "
        "required check counts as satisfied."
    )
    assert "if" not in job, "an `if:` here is a way for the required check to pass by being skipped."


def test_required_gates_job_can_read_what_it_reports_on() -> None:
    permissions = _job_named("Required gates").get("permissions", {})
    assert permissions.get("actions") == "read", "without actions: read it cannot see the gate conclusions"
    assert permissions.get("pull-requests") == "read", (
        "without pull-requests: read it cannot see the changed files, which is "
        "what decides whether an absent run is legitimate"
    )


def test_frontend_gate_runs_the_locked_quality_contract() -> None:
    """The frontend workflow must execute every command proved locally.

    Registering a workflow with the aggregate only proves that GitHub waited
    for it. Pinning the steps here proves the awaited job actually installs
    the lockfile, lints, runs the tests, and produces a release build.
    """
    workflow = _workflow(".github/workflows/frontend-ci.yml")
    jobs = list(workflow["jobs"].values())
    assert len(jobs) == 1, "the frontend gate should have one all-or-nothing job"
    job = jobs[0]
    assert "if" not in job, "a conditional frontend job can skip the required quality contract"
    assert not job.get("continue-on-error", False), "frontend failures must fail the required job"
    assert job.get("defaults", {}).get("run", {}).get("working-directory") == "frontend"

    command_steps = [step for step in job.get("steps", []) if step.get("run")]
    commands = [step["run"] for step in command_steps]
    assert commands == ["npm ci", "npm run lint", "npm test", "npm run build"]
    for step in command_steps:
        assert "if" not in step, f"{step['run']!r} must run unconditionally"
        assert not step.get("continue-on-error", False), f"{step['run']!r} must block on failure"
        assert step.get("working-directory", "frontend") == "frontend"

    setup_node = [
        step
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/setup-node@")
    ]
    assert len(setup_node) == 1
    with_config = setup_node[0].get("with", {})
    assert str(with_config.get("node-version")) == "22"
    assert with_config.get("cache") == "npm"
    assert with_config.get("cache-dependency-path") == "frontend/package-lock.json"


def test_frontend_gate_runs_for_frontend_prs_and_main_pushes() -> None:
    """The merge gate and post-merge run must share the same narrow paths."""
    triggers = _triggers(_workflow(".github/workflows/frontend-ci.yml"))
    pull_request = triggers.get("pull_request")
    push = triggers.get("push")
    assert isinstance(pull_request, dict)
    assert isinstance(push, dict)
    assert push.get("branches") == ["main"]
    assert push.get("paths") == pull_request.get("paths") == [
        ".github/workflows/frontend-ci.yml",
        "frontend/**",
    ]
    assert "workflow_dispatch" in triggers


def test_frontend_image_publish_workflow_cannot_silently_skip_shipping_changes() -> None:
    """The Pi image workflow must follow every shipping input and smoke first.

    Image publication intentionally happens only after merge, but a changed
    frontend Dockerfile, deploy script, or workflow must still start it on
    ``main``. This keeps a UI commit from becoming an undeployable commit
    merely because CI's path filter did not know its deployment files existed.
    """
    workflow = _workflow(".github/workflows/build-frontend-image.yml")
    triggers = _triggers(workflow)
    push = triggers.get("push")
    assert isinstance(push, dict)
    assert push.get("branches") == ["main"]
    assert set(push.get("paths", [])) == {
        "frontend/**",
        "docs/deployment/frontend-pi.md",
        ".github/workflows/build-frontend-image.yml",
    }
    assert "workflow_dispatch" in triggers

    jobs = workflow["jobs"]
    assert set(jobs) == {"build-and-push"}
    job = jobs["build-and-push"]
    assert job.get("if") == "github.repository == 'TCKDB/TCKDB'"
    steps = job.get("steps", [])
    names = [step.get("name") for step in steps]
    smoke = names.index("Smoke test — SPA fallback and same-origin API proxy")
    publish = names.index("Build and push multi-arch image")
    assert smoke < publish, "the image must serve its SPA and proxy before it is published"
    assert "Build arm64 image before publishing" in names
    assert names.index("Build arm64 image before publishing") < publish

    publish_step = steps[publish]
    assert publish_step["with"]["context"] == "frontend"
    assert publish_step["with"]["file"] == "frontend/Dockerfile"
    assert publish_step["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert publish_step["with"]["push"] is True

    metadata = next(step for step in steps if step.get("id") == "meta")
    assert "type=sha,format=long" in metadata["with"]["tags"]
    smoke_step = steps[smoke]
    command = smoke_step["run"]
    for expected in (
        "frontend/scripts/ops/smoke_api.py",
        "X-Real-IP: 203.0.113.9",
        "X-Forwarded-Proto: https",
        "docker rm -f tckdb-api",
        "old_api_ip=",
        "api-ip-reservation",
        'test "$old_api_ip" != "$new_api_ip"',
        "API_RESPONSE=api-new",
        ".response == \"api-new\"",
    ):
        assert expected in command, f"frontend smoke must prove {expected!r}"

    jq = re.search(r"jq -e \\\n\s+'(?P<program>[^']+)'", command)
    assert jq, "the forwarded-header jq program must be a single shell-safe argument"
    assert re.sub(r"\s+", " ", jq.group("program")).strip() == (
        '(.response == "api-old") and (.headers.x_real_ip == "203.0.113.9") '
        'and (.headers.x_forwarded_proto == "https")'
    )


def test_the_aggregate_and_its_tests_both_run_in_the_unfiltered_workflow() -> None:
    """The aggregate's own tests must not be gated by the aggregate.

    ``backend/tests/scripts/`` is covered by the complement gate, which is
    path-filtered on ``backend/**`` -- so a pull request that broke the
    aggregator while touching backend/ would be reported on by the very
    mechanism it broke. Running these two in the unfiltered workflow makes
    the report independent of its subject.
    """
    text = (REPO_ROOT / DIRECTLY_REQUIRED_WORKFLOW).read_text(encoding="utf-8")
    assert "backend/scripts/aggregate_pr_gates.py" in text, (
        "repo-gate.yml must invoke the aggregate script; a required context "
        "whose job runs nothing is the defect this repository keeps shipping."
    )
    assert "backend/tests/scripts/test_aggregate_pr_gates.py" in text


# ---------------------------------------------------------------------------
# Non-vacuity: the checks above pass trivially if the parser reports
# everything as covered, so pin that each mechanism actually discriminates.
# ---------------------------------------------------------------------------


def test_ignores_are_honoured_by_the_parser() -> None:
    rest = _script_selection("backend/scripts/test-rest.sh")
    assert rest.covers("backend/tests/db/test_identifier_lengths.py")
    assert not rest.covers("backend/tests/api/test_api_health.py")
    assert not rest.covers("backend/tests/services/scientific_read/test_species.py")


def test_prefix_matching_is_segment_wise() -> None:
    assert _is_under("backend/tests/api/test_x.py", "backend/tests/api")
    assert not _is_under("backend/tests/api_extra/test_x.py", "backend/tests/api")
    assert _is_under("backend/tests/api", "backend/tests/api")


def test_bare_pytest_resolves_testpaths_rather_than_the_whole_directory() -> None:
    """The bug that hid the CHEMKIN adapter, pinned as behaviour."""
    assert _testpaths("clients/python") == ["clients/python/tests"]
    assert "clients/python/adapters/chemkin/tests" not in _testpaths("clients/python")


def test_positional_paths_are_taken_only_after_the_pytest_token() -> None:
    selection = Selection("probe")
    _absorb_args(selection, shlex.split("conda run -n tckdb_env pytest -q backend/tests/scripts"), ".")
    assert selection.paths == {"backend/tests/scripts"}, (
        "`-n tckdb_env` must not contribute a path, and the path after pytest must."
    )


def test_launcher_flags_do_not_swallow_the_pytest_token() -> None:
    """``python -m pytest X`` selects X.

    ``-m`` is also pytest's marker option. A parser that starts consuming
    option values before it has seen ``pytest`` treats the word ``pytest``
    as the value of ``-m``, finds no arguments, and falls back to
    ``testpaths`` -- which is how the first draft of this file reported
    the whole repository as covered by a step that runs one test module,
    with all eighteen assertions green.
    """
    selection = Selection("probe")
    _absorb_args(selection, shlex.split("python -m pytest --noconftest -q backend/tests/scripts"), ".")
    assert selection.paths == {"backend/tests/scripts"}

    adapter = Selection("probe")
    _absorb_args(adapter, shlex.split("python -m pytest adapters/chemkin/tests"), "clients/python")
    assert adapter.paths == {"clients/python/adapters/chemkin/tests"}


def test_gate_script_arguments_are_pytest_arguments() -> None:
    """No ``pytest`` token appears on a gate-script line; its flags are still pytest's."""
    selection = Selection("probe")
    _absorb_args(
        selection,
        shlex.split("conda run -n tckdb_env bash backend/scripts/test-api.sh --ignore=tests/api/scientific/"),
        "backend",
        after_pytest=True,
    )
    assert selection.ignored == {"backend/tests/api/scientific"}


def test_unexpanded_shell_fragments_are_not_paths() -> None:
    selection = Selection("probe")
    _absorb_args(selection, shlex.split('pytest "${TCKDB_PYTEST_ARGS[@]}" tests/ "$@"'), "backend")
    assert selection.paths == {"backend/tests"}


def test_syntax_checks_are_not_counted_as_invocations() -> None:
    assert _is_syntax_check(shlex.split("bash -n backend/scripts/test-rest.sh"))
    assert not _is_syntax_check(shlex.split("conda run -n tckdb_env bash backend/scripts/test-rest.sh"))


def test_path_filter_matching_discriminates() -> None:
    assert _workflow_triggers_on(".github/workflows/backend-ci.yml", "backend/tests/db/test_x.py")
    assert not _workflow_triggers_on(".github/workflows/backend-ci.yml", "clients/python/tests/test_x.py")
    assert _workflow_triggers_on(".github/workflows/python-client-ci.yml", "clients/python/tests/test_x.py")
    assert not _workflow_triggers_on(".github/workflows/python-client-ci.yml", "backend/tests/db/test_x.py")
    # No filter at all means every pull request.
    assert _workflow_triggers_on(".github/workflows/repo-gate.yml", "anywhere/test_x.py")


def test_the_walk_reaches_beyond_the_backend() -> None:
    """A walk that stopped at backend/tests/ is the defect, not the design."""
    files = _test_files()
    for expected in (
        "backend/tests/scripts/test_gate_coverage.py",
        "clients/python/tests/test_openapi_parity.py",
        "clients/python/adapters/chemkin/tests/test_parser.py",
        "integrations/mcp/tests/test_config.py",
        "schemas/python/tckdb-schemas/tests/test_import_boundaries.py",
    ):
        assert expected in files, f"{expected} exists but the walk did not find it"


def test_both_pytest_file_spellings_are_walked() -> None:
    assert _is_test_file("test_thing.py")
    assert _is_test_file("thing_test.py")
    assert not _is_test_file("contest_helpers.py")
    assert not _is_test_file("test_fixture.json")
