"""The version guard has to fire on the case git merges silently.

THE CASE THAT MATTERS
    Two branches start from ``version = "0.46.0"``. Both bump to
    ``0.47.0``. The first merges. When the second merges, both sides hold
    the byte-identical version line, so git reports no conflict and does
    not list the file in the merge stat -- and one version number now
    describes two different packages.

    ``test_equal_version_already_claimed_on_main_fails`` is that case, and
    it is the reason this file exists. Note what it costs the naive design:
    the second branch's bump *is* monotonic against its own merge base
    (0.47.0 > 0.46.0), so a check that only compares against the merge base
    passes it. Catching it needs the separate novelty scan over main's
    history. ``test_the_collision_case_survives_a_merge_base_only_check``
    pins that distinction, so nobody simplifies the guard back into the
    hole it was written to close.

WHY THESE TESTS BUILD REAL GIT REPOSITORIES
    The subject is git's own behaviour across a merge base, and a mocked
    ``git`` would be a mock of the exact thing in doubt. Each test creates a
    throwaway repository under ``tmp_path`` with the real package layout,
    commits, branches, and runs the checker against it. Nothing here reads
    or depends on the state of the repository it ships in, except the
    wiring tests at the bottom, which read the workflow file on purpose.

VACUITY
    A guard is maximally exposed to passing without checking anything, so
    the assertions below are on the *verdict and the reason*, never on the
    mere absence of a failure: every negative case asserts the specific
    finding code, and every positive case asserts the package was actually
    examined and seen to change. ``test_main_refuses_when_nothing_was_examined``
    covers the degenerate case directly.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "repo-gate.yml"


def _load_module(name: str, path: Path):
    """Import by path.

    This file is run by the repo gate with ``--noconftest`` from the
    repository root, where ``from scripts import ...`` is unavailable, and
    also by the backend complement gate with ``backend/`` as the rootdir.
    Importing by path is the only spelling that works in both. Same
    reasoning, and same helper, as ``test_gate_coverage.py``.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because ``@dataclass`` resolves its own
    # module out of ``sys.modules`` while the class body is being processed,
    # and raises AttributeError on None if it is not there yet.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_module(
    "check_package_version_bump",
    BACKEND_ROOT / "scripts" / "check_package_version_bump.py",
)


# ---------------------------------------------------------------------------
# Semantic version ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lower", "higher"),
    [
        # The lexical trap, stated first because it is the one that would
        # silently pass a downgrade: "0.10.0" < "0.9.0" as strings.
        pytest.param("0.9.0", "0.10.0", id="lexical-trap-minor"),
        pytest.param("0.9.9", "0.10.0", id="lexical-trap-carry"),
        pytest.param("0.46.0", "0.47.0", id="the-incident"),
        pytest.param("1.9.0", "1.10.0", id="lexical-trap-in-1x"),
        pytest.param("0.2.0", "0.2.1", id="patch"),
        pytest.param("0.2.9", "0.3.0", id="minor-over-patch"),
        pytest.param("0.99.0", "1.0.0", id="major"),
        pytest.param("1.0.0", "1.0.0.post1", id="post-outranks-final"),
        pytest.param("1.0.0rc1", "1.0.0", id="final-outranks-rc"),
        pytest.param("1.0.0a1", "1.0.0b1", id="beta-outranks-alpha"),
        pytest.param("1.0.0b1", "1.0.0rc1", id="rc-outranks-beta"),
        pytest.param("1.0.0.dev1", "1.0.0a1", id="dev-precedes-pre"),
        pytest.param("1.0.0a1", "1.0.0a2", id="pre-number"),
    ],
)
def test_version_ordering_is_semantic_not_lexical(lower: str, higher: str) -> None:
    assert checker.compare_versions(higher, lower) == 1, (lower, higher)
    assert checker.compare_versions(lower, higher) == -1, (lower, higher)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        pytest.param("0.9", "0.9.0", id="padded-equal"),
        pytest.param("1.0.0", "1.0.0", id="identical"),
        pytest.param("1.2.3", "v1.2.3", id="v-prefix-ignored"),
    ],
)
def test_equivalent_spellings_rank_equal(a: str, b: str) -> None:
    assert checker.compare_versions(a, b) == 0


def test_lexical_comparison_would_have_got_this_wrong() -> None:
    """Pin the trap itself, so the semantic key is not 'simplified' away."""
    assert "0.10.0" < "0.9.0"  # string comparison, the wrong answer
    assert checker.compare_versions("0.10.0", "0.9.0") == 1  # the right one


@pytest.mark.parametrize(
    "bad",
    ["", "latest", "1.0.0-alpha+deadbeef+x", "not.a.version", "0.1.x", "1..0"],
)
def test_unrankable_versions_are_refused_not_guessed(bad: str) -> None:
    with pytest.raises(checker.VersionError):
        checker.version_key(bad)


# ---------------------------------------------------------------------------
# A throwaway repository with the real package layout
# ---------------------------------------------------------------------------

PKG = checker.PACKAGES[0]  # tckdb-client
assert PKG.name == "tckdb-client"
PYPROJECT = PKG.pyproject
MODULE = PKG.dist_paths[0] + "/tckdb_client/__init__.py"


def _pyproject(version: str) -> str:
    return (
        "[project]\n"
        'name = "tckdb-client"\n'
        f'version = "{version}"\n'
        'requires-python = ">=3.10"\n'
    )


class Repo:
    """A real git repository, built commit by commit."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self._git("init", "-q", "-b", "main", ".")
        self._git("config", "user.email", "gate@example.invalid")
        self._git("config", "user.name", "gate")
        self._git("config", "commit.gpgsign", "false")

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"git {args}: {proc.stderr}"
        return proc.stdout

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str, *, version: str | None = None, body: str | None = None):
        """Commit a package state. ``version`` and ``body`` are independent."""
        if version is not None:
            self.write(PYPROJECT, _pyproject(version))
        if body is not None:
            self.write(MODULE, body)
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)
        return self._git("rev-parse", "HEAD").strip()

    def branch(self, name: str) -> None:
        self._git("checkout", "-q", "-b", name)

    def checkout(self, name: str) -> None:
        self._git("checkout", "-q", name)

    def tag(self, name: str, message: str = "pinned") -> None:
        self._git("tag", "-a", name, "-m", message)

    @property
    def git(self):
        return checker.Git(str(self.root))


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """A repository whose main branch holds tckdb-client 0.46.0."""
    r = Repo(tmp_path / "repo")
    r.commit("baseline", version="0.46.0", body="VALUE = 1\n")
    return r


def _check(repo: Repo, head: str = "feat", base: str = "main", main: str | None = None):
    return checker.check(repo.git, base_ref=base, head_ref=head, main_ref=main or base)


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


# ---------------------------------------------------------------------------
# The four cases the brief requires, each with its verdict
# ---------------------------------------------------------------------------


def test_case_1_version_raised_passes(repo: Repo) -> None:
    """Package changed, version raised. PASS."""
    repo.branch("feat")
    repo.commit("feature", version="0.47.0", body="VALUE = 2\n")

    report = _check(repo)

    # Prove the check actually looked at something. A report with an empty
    # `examined` would pass `ok` trivially.
    assert PKG.name in report.examined
    assert PKG.name in report.changed, "the guard did not notice the package changed"
    assert report.findings == []
    assert report.ok


def test_case_2_version_unchanged_while_package_changed_fails(repo: Repo) -> None:
    """Package changed, version left alone. FAIL -- the standing rule."""
    repo.branch("feat")
    repo.commit("feature without a bump", body="VALUE = 2\n")

    report = _check(repo)

    assert PKG.name in report.changed
    assert _codes(report) == {"not-bumped"}
    assert not report.ok
    message = report.findings[0].message
    assert "0.46.0" in message
    assert "Raise it" in message


def test_case_3_version_lowered_fails(repo: Repo) -> None:
    """Package changed, version moved backwards. FAIL."""
    repo.branch("feat")
    repo.commit("feature with a downgrade", version="0.45.0", body="VALUE = 2\n")

    report = _check(repo)

    assert PKG.name in report.changed
    assert _codes(report) == {"lowered"}
    assert not report.ok
    assert "LOWER" in report.findings[0].message


def test_case_3b_version_lowered_with_no_content_change_fails(repo: Repo) -> None:
    """A number must never go backwards, bump or no bump. FAIL."""
    repo.branch("feat")
    repo.commit("gratuitous downgrade", version="0.45.0")

    report = _check(repo)

    assert PKG.name not in report.changed  # content genuinely identical
    assert _codes(report) == {"lowered"}
    assert not report.ok


def test_case_4_equal_version_already_claimed_on_main_fails(repo: Repo) -> None:
    """THE case that slipped through.

    Both branches bump 0.46.0 -> 0.47.0 from the same starting point. The
    first has already merged. The second is still monotonic against its own
    merge base, so only the novelty scan can catch it.
    """
    base = repo._git("rev-parse", "HEAD").strip()

    # Agent B branches, bumps to 0.47.0 with its own contents.
    repo.branch("feat")
    repo.commit("agent B feature", version="0.47.0", body="VALUE = 'B'\n")

    # Agent A merged first: main now also holds 0.47.0, different contents.
    repo.checkout("main")
    repo.commit("agent A feature (merged first)", version="0.47.0", body="VALUE = 'A'\n")

    report = _check(repo, head="feat", base="main", main="main")

    assert report.merge_base == base, "monotonicity must be measured from the branch point"
    assert PKG.name in report.changed
    assert _codes(report) == {"version-already-claimed"}
    assert not report.ok
    message = report.findings[0].message
    assert "0.47.0" in message
    assert "DIFFERENT contents" in message


def test_the_collision_case_survives_a_merge_base_only_check(repo: Repo) -> None:
    """A merge-base-only guard passes the collision. Pin why both refs exist.

    If someone ever 'simplifies' the checker by dropping the novelty scan,
    this test is the one that explains what was lost: the bump in case 4 is
    perfectly monotonic against the merge base.
    """
    repo.branch("feat")
    repo.commit("agent B feature", version="0.47.0", body="VALUE = 'B'\n")
    repo.checkout("main")
    repo.commit("agent A feature", version="0.47.0", body="VALUE = 'A'\n")

    git = repo.git
    merge_base = git.merge_base("main", "feat")
    at_base = checker.read_state(git, merge_base, PKG)
    at_head = checker.read_state(git, git.rev_parse("feat"), PKG)

    # Monotonicity alone says this branch is fine...
    assert checker.compare_versions(at_head.version, at_base.version) == 1
    # ...and it is not.
    assert not _check(repo, head="feat", base="main", main="main").ok


# ---------------------------------------------------------------------------
# Merge base, not main's tip
# ---------------------------------------------------------------------------


def test_a_branch_merely_behind_main_is_not_failed(repo: Repo) -> None:
    """Main moving on must not fail a correct bump made from the branch point.

    This is why monotonicity uses the merge base. Against main's tip, this
    branch's 0.47.0 would be compared with 0.48.0 and refused for something
    its author did not do.
    """
    repo.branch("feat")
    repo.commit("a correct bump from the branch point", version="0.47.0", body="VALUE = 2\n")

    repo.checkout("main")
    # Main advances, in a way that does not collide: different version.
    repo.commit("unrelated work", version="0.48.0", body="OTHER = 1\n")

    report = _check(repo, head="feat", base="main", main="main")

    assert PKG.name in report.changed
    assert report.findings == [], "a branch that is merely behind must not be failed"
    assert report.ok


def test_novelty_scan_sees_main_beyond_the_merge_base(repo: Repo) -> None:
    """And this is why novelty does NOT use the merge base.

    The colliding version is not on the merge base's history at all; it
    arrived on main afterwards, which is what made the collision possible.
    """
    repo.branch("feat")
    repo.commit("feature", version="0.47.0", body="VALUE = 'B'\n")
    repo.checkout("main")
    repo.commit("collides", version="0.47.0", body="VALUE = 'A'\n")

    git = repo.git
    merge_base = git.merge_base("main", "feat")

    # Confirm the premise: 0.47.0 is genuinely absent from the merge base's
    # own history, so a scan rooted there could not have found it.
    history = git.touching_commits(merge_base, (PKG.pyproject, *PKG.dist_paths))
    versions = {checker.read_state(git, c, PKG).version for c in history}
    assert "0.47.0" not in versions

    assert _codes(_check(repo, head="feat", base="main", main="main")) == {
        "version-already-claimed"
    }


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def test_reusing_a_tagged_version_fails(tmp_path: Path) -> None:
    """A tag is a pin something downstream may hold. Reusing it is refused.

    This is the real ``tckdb-schemas-v0.8.0`` shape: the tag pins 0.8.0
    "for tckdb-adapters/tckdb_arc (Phase 1)", and a later change shipped
    different contents under the same number. Constructed so that
    monotonicity *passes* -- otherwise this would be testing the bump check
    again rather than the tag check.
    """
    r = Repo(tmp_path / "tagged")
    r.commit("baseline", version="0.45.0", body="VALUE = 1\n")

    # A release branch cut 0.46.0 and pinned it.
    r.branch("rel")
    r.commit("the release that was pinned", version="0.46.0", body="VALUE = 'TAGGED'\n")
    r.tag("tckdb-client-v0.46.0", "pinned for a downstream adapter")

    # main never saw 0.46.0, so the novelty scan over main cannot catch this.
    r.checkout("main")
    r.branch("feat")
    r.commit("different contents, same number", version="0.46.0", body="VALUE = 'NEW'\n")

    report = checker.check(r.git, base_ref="main", head_ref="feat", main_ref="main")

    # Monotonicity is satisfied: 0.46.0 > 0.45.0 at the merge base.
    assert checker.compare_versions("0.46.0", "0.45.0") == 1
    assert _codes(report) == {"version-already-tagged"}
    assert not report.ok
    assert "tckdb-client-v0.46.0" in report.findings[0].message


def test_an_untouched_package_is_not_failed_for_history_it_did_not_make(
    repo: Repo,
) -> None:
    """Scope: the gate polices this change, not the baseline.

    ``--audit`` on the real origin/main finds five colliding tckdb-schemas
    versions and a tckdb-mcp that has never been bumped. If the novelty scan
    ran for packages a pull request did not touch, the gate would be red on
    an untouched main -- red for everybody, which is how a gate stops being
    required.
    """
    # main already contains a collision: 0.46.0 with two different contents.
    repo.commit("silently changed under the same number", body="VALUE = 999\n")
    repo.branch("feat")
    # The branch touches nothing in the package.
    repo.write("README.md", "unrelated\n")
    repo._git("add", "-A")
    repo._git("commit", "-q", "-m", "docs only")

    report = _check(repo, head="feat", base="main", main="main")

    assert PKG.name in report.examined, "the package must still be examined"
    assert PKG.name not in report.changed
    assert report.findings == []
    assert report.ok


# ---------------------------------------------------------------------------
# What counts as a change
# ---------------------------------------------------------------------------


def test_a_bump_alone_does_not_justify_itself(repo: Repo) -> None:
    """The version line is excluded from the content digest.

    If it were not, every bump would register as a content change and the
    monotonicity check would be vacuous -- satisfied by the very edit it is
    supposed to be judging.
    """
    repo.branch("feat")
    repo.commit("bump only", version="0.47.0")

    report = _check(repo)

    assert PKG.name not in report.changed
    assert report.ok


def test_a_dependency_change_counts_as_a_change(repo: Repo) -> None:
    """pyproject minus the version line is in scope: a pin change ships."""
    repo.branch("feat")
    repo.write(
        PYPROJECT,
        _pyproject("0.46.0") + 'dependencies = ["tckdb-schemas>=0.35.0"]\n',
    )
    repo._git("add", "-A")
    repo._git("commit", "-q", "-m", "raise the schemas floor")

    report = _check(repo)

    assert PKG.name in report.changed
    assert _codes(report) == {"not-bumped"}


def test_a_readme_change_does_not_demand_a_bump(repo: Repo) -> None:
    """The line is drawn at the importable tree, on purpose.

    A checker that demands a version bump for a README typo is a checker
    that gets switched off, and then it is not guarding the enum members
    either. Same reasoning as check_runtime_ascii.py's docstring/prose
    carve-out.
    """
    repo.branch("feat")
    repo.write("clients/python/README.md", "a typo fix\n")
    repo._git("add", "-A")
    repo._git("commit", "-q", "-m", "fix a typo")

    report = _check(repo)

    assert PKG.name not in report.changed
    assert report.ok


def test_a_test_only_change_does_not_demand_a_bump(repo: Repo) -> None:
    """tests/ is not in the wheel."""
    repo.branch("feat")
    repo.write("clients/python/tests/test_thing.py", "def test_x():\n    pass\n")
    repo._git("add", "-A")
    repo._git("commit", "-q", "-m", "add a test")

    report = _check(repo)

    assert PKG.name not in report.changed
    assert report.ok


def test_a_new_package_is_examined_and_novelty_checked(repo: Repo) -> None:
    """A package absent at the merge base still gets its number checked."""
    repo.branch("feat")
    repo.write(
        checker.PACKAGES[1].pyproject,
        '[project]\nname = "tckdb-schemas"\nversion = "0.1.0"\n',
    )
    repo.write(
        checker.PACKAGES[1].dist_paths[0] + "/__init__.py",
        "VALUE = 1\n",
    )
    repo._git("add", "-A")
    repo._git("commit", "-q", "-m", "add the schemas package")

    report = _check(repo)

    assert "tckdb-schemas" in report.examined
    assert "tckdb-schemas" in report.changed
    assert report.ok


# ---------------------------------------------------------------------------
# Vacuity: the guard must refuse to pass having checked nothing
# ---------------------------------------------------------------------------


def test_main_refuses_when_nothing_was_examined(tmp_path: Path, capsys) -> None:
    """A repository with no covered package must not report success.

    This is the trivial-pass shape the guard is most exposed to: walk an
    empty list, find no violations, exit 0.
    """
    r = Repo(tmp_path / "empty")
    r.write("unrelated.txt", "hello\n")
    r._git("add", "-A")
    r._git("commit", "-q", "-m", "nothing to do with any package")

    exit_code = checker.main(
        ["--repo", str(r.root), "--base-ref", "main", "--head-ref", "main"]
    )

    assert exit_code == 1, "a check that examined nothing must not pass"
    assert "Nothing was verified" in capsys.readouterr().err


def test_main_exits_nonzero_on_a_violation(repo: Repo, capsys) -> None:
    """The gate's exit status, not just the report object, must fail."""
    repo.branch("feat")
    repo.commit("no bump", body="VALUE = 2\n")

    exit_code = checker.main(
        ["--repo", str(repo.root), "--base-ref", "main", "--head-ref", "feat"]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "not-bumped" in err
    assert "without a conflict" in err, "the failure must explain why it exists"


def test_main_exits_zero_on_a_correct_bump(repo: Repo, capsys) -> None:
    repo.branch("feat")
    repo.commit("correct", version="0.47.0", body="VALUE = 2\n")

    exit_code = checker.main(
        ["--repo", str(repo.root), "--base-ref", "main", "--head-ref", "feat"]
    )

    assert exit_code == 0
    assert "tckdb-client" in capsys.readouterr().out


def test_an_unresolvable_novelty_ref_fails_instead_of_passing(repo: Repo) -> None:
    """The novelty scan must never be silently skipped.

    If a missing remote-tracking branch made the scan return "no conflict",
    the one case this guard exists for would report a pass. Refusing is the
    only safe direction.
    """
    repo.branch("feat")
    repo.commit("feature", version="0.47.0", body="VALUE = 2\n")

    with pytest.raises(RuntimeError, match="cannot resolve the novelty ref"):
        checker.check(
            repo.git,
            base_ref="main",
            head_ref="feat",
            main_ref="origin/does-not-exist",
        )


def test_a_shallow_clone_fails_loudly(tmp_path: Path) -> None:
    """No merge base means the check cannot run, and must say so.

    ``actions/checkout@v4`` defaults to ``fetch-depth: 1``. If that default
    ever comes back, this check must fail rather than silently find nothing.
    """
    a = Repo(tmp_path / "a")
    a.commit("a", version="0.1.0", body="A = 1\n")
    a._git("checkout", "-q", "--orphan", "unrelated")
    a.commit("b", version="0.2.0", body="B = 1\n")

    with pytest.raises(RuntimeError, match="no merge base"):
        checker.check(a.git, base_ref="main", head_ref="unrelated")


# ---------------------------------------------------------------------------
# Coverage: aimed at every package, not at fewer than it should be
# ---------------------------------------------------------------------------


def test_every_pyproject_is_covered_or_excluded() -> None:
    """The recurring defect here is a correct check aimed too narrowly.

    ``check_runtime_ascii.py`` records four separate occasions on which one
    of this repository's checkers was pointed at fewer trees than it should
    have been, each found by a person noticing months later. This test is
    the cheap way not to repeat it.
    """
    covered = {p.pyproject for p in checker.PACKAGES}
    excluded = set(checker.EXCLUDED_PYPROJECTS)

    # Relative parts, not absolute: a git worktree of this repository lives
    # under .claude/worktrees/, so filtering on path.parts silently excluded
    # every file in the tree and left this test asserting over an empty set.
    # That is the house defect -- a check that passes having verified nothing
    # -- and it happened while writing this very test.
    ignored = {".git", "node_modules", "build", "dist", ".venv", ".claude", ".tox"}
    found = set()
    for path in REPO_ROOT.rglob("pyproject.toml"):
        rel_parts = path.relative_to(REPO_ROOT).parts
        if any(part in ignored for part in rel_parts):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.endswith(".egg-info/pyproject.toml"):
            continue
        found.add(rel)

    # Guard the guard: if the walk finds nothing, the two assertions below
    # are both vacuously true.
    assert len(found) >= len(covered), (
        f"the pyproject walk found only {sorted(found)}, fewer than the "
        f"{len(covered)} packages already known to be covered. The walk is "
        f"broken, and this test would pass having checked nothing."
    )

    unaccounted = found - covered - excluded
    assert not unaccounted, (
        "these pyproject.toml files are neither covered by PACKAGES nor "
        "listed in EXCLUDED_PYPROJECTS with a reason, so a version collision "
        "in them would go unnoticed:\n  " + "\n  ".join(sorted(unaccounted))
    )

    stale = (covered | excluded) - found
    assert not stale, (
        "these are declared but do not exist; the registry has drifted:\n  "
        + "\n  ".join(sorted(stale))
    )


def test_every_excluded_pyproject_states_a_reason() -> None:
    for path, reason in checker.EXCLUDED_PYPROJECTS.items():
        assert len(reason) > 40, f"{path} needs a real reason, not {reason!r}"


def test_covered_dist_paths_exist_for_the_current_layout() -> None:
    """Each covered package must have at least one real importable tree.

    A typo in ``dist_paths`` would make the content digest depend on the
    pyproject alone, and the guard would stop noticing code changes -- while
    still passing.
    """
    for package in checker.PACKAGES:
        assert (REPO_ROOT / package.pyproject).is_file(), package.pyproject
        present = [p for p in package.dist_paths if (REPO_ROOT / p).is_dir()]
        assert present, (
            f"{package.name}: none of dist_paths={package.dist_paths} exists, "
            f"so the guard would not see a code change in it"
        )


# ---------------------------------------------------------------------------
# Wiring: the gate has to actually run, with enough history to run on
# ---------------------------------------------------------------------------


def test_the_gate_runs_in_the_unfiltered_workflow() -> None:
    """The guard must run on every pull request.

    It cannot live in a path-filtered workflow: a pull request that changes
    only ``integrations/mcp/`` would not start ``python-client-ci.yml``, and
    the collision it is guarding against would sail through unexamined.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "backend/scripts/check_package_version_bump.py" in text, (
        "the version guard is not invoked by repo-gate.yml, the one workflow "
        "with no path filter"
    )
    assert "backend/tests/scripts/test_check_package_version_bump.py" in text, (
        "the guard's own tests must run in the unfiltered workflow too; a "
        "defect in the guard is a defect in the gate"
    )


def test_the_gate_has_the_history_it_needs() -> None:
    """A merge base needs full history, and checkout defaults to depth 1."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text, (
        "repo-gate.yml must check out full history, or git merge-base has "
        "nothing to compute against and the guard cannot run"
    )
    assert "fetch-tags: true" in text, (
        "fetch-depth: 0 does not fetch tags -- actions/checkout passes "
        "--no-tags unless asked. Without tags the guard's tag check sees an "
        "empty list, reports no conflict, and is silently inert"
    )


def test_the_gate_passes_a_base_and_a_separate_novelty_ref() -> None:
    """The two refs answer different questions and must not be collapsed.

    A single ref cannot do both jobs: monotonicity needs the branch point
    (or a branch that is merely behind gets blamed for someone else's merge)
    and novelty needs the current tip (or the colliding version, which lands
    after the branch point, is invisible). If a future edit passes only one,
    the guard silently loses one of its two halves.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    invocation = text.split("check_package_version_bump.py", 1)[1]
    # The gate step, not the pytest step: take the text after the script name.
    assert "--base-ref" in invocation, "the merge-base comparison has no base"
    assert "--main-ref" in invocation, (
        "no --main-ref is passed, so the novelty scan falls back to the base "
        "sha and cannot see a sibling that merged after this branch ran"
    )
    assert "pull_request.base.sha" in invocation, (
        "--base-ref should be the event's base sha; merge-base is computed "
        "from it inside the script"
    )
