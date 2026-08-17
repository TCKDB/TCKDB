#!/usr/bin/env python3
"""Refuse a distributed package version that two different packages could claim.

WHY
    On 2026-08-16 two agents branched from ``clients/python/pyproject.toml``
    at ``0.46.0``. Both followed the standing rule that a change to
    ``tckdb-client`` bumps the version, and both chose ``0.47.0``. The first
    merged. The second still carried ``-0.46.0 / +0.47.0``.

    Git did not notice. Both sides of the merge held the byte-identical line
    ``version = "0.47.0"``, so there was no conflict, no entry in the merge
    stat, and nothing for a reviewer scanning the diff to catch. One version
    number then described two different packages.

    That is not hypothetical here. Walking the first-parent history of
    ``main`` (this file's ``--audit`` mode does it) finds five versions of
    ``tckdb-schemas`` that have *already* shipped with two or three
    different distributed contents each: ``0.2.0``, ``0.8.0``, ``0.14.0``,
    ``0.30.0`` and ``0.33.0``. The ``0.2.0`` and ``0.8.0`` collisions are
    substantive -- new enum members and changed upload-schema fields, not
    typography. And ``0.8.0`` is the version the annotated tag
    ``tckdb-schemas-v0.8.0`` pins "for tckdb-adapters/tckdb_arc (Phase 1)",
    so an ARC-side pin of ``tckdb-schemas==0.8.0`` resolves to one of two
    genuinely different packages depending on when it was fetched.

WHAT IS CHECKED
    For each covered package (see ``PACKAGES``), given a base ref and a head
    ref:

    1. **Monotonicity, against the merge base.** If the package's
       distributed content changed between the merge base and the head, the
       head's version must be *strictly greater* than the merge base's.
       This catches "changed the package, forgot to bump" and "bumped
       downwards".

    2. **Novelty, against the whole of main's history.** The head's version
       must not already be claimed, anywhere on ``--main-ref``'s first-parent
       history, by a *different* content. This is the check that catches the
       collision above, and it is the reason (2) cannot be folded into (1) --
       see MERGE BASE VERSUS TIP.

    3. **Novelty, against release tags.** If a tag ``<name>-v<version>``
       exists and its content differs from the head's, the head is trying to
       reuse a number something has already been pinned to.

MERGE BASE VERSUS TIP, AND WHY BOTH REFS ARE NEEDED
    These two comparisons genuinely need different references, and getting
    that wrong is the whole subtlety.

    Monotonicity must use the **merge base**, not main's tip. Against the
    tip, a branch that is merely *behind* main gets failed for a bump it
    made correctly from where it started, and the message blames the author
    for someone else's merge. The merge base is the only ref that answers
    the question actually being asked of the author: "did *you* raise the
    number from where *you* branched?"

    Novelty must use main's **tip history**, not the merge base. In the
    incident, the colliding ``0.47.0`` was not on the merge base's history
    at all -- it arrived on main *after* the branch point, which is exactly
    what made the collision possible. A check confined to the merge base
    cannot see it. So: monotonicity asks "did you bump?", novelty asks "is
    the number you chose taken?", and only the pair is complete.

WHAT COUNTS AS "THE PACKAGE CHANGED"
    The importable tree that goes into the wheel (``Package.dist_paths``,
    derived from each project's ``[tool.setuptools.packages.find]``), plus
    ``pyproject.toml`` with the ``version =`` line removed -- so a bump on
    its own is not self-justifying, and a dependency-pin change is.

    Deliberately *out* of scope: ``tests/``, ``docs/``, ``README.md``,
    notebooks and examples. None of them is importable from the installed
    distribution. A README typo does technically change the sdist's
    long_description, and a checker that demands a version bump for a typo
    is a checker that gets switched off within a week -- at which point it
    is not guarding the enum members either. The same reasoning is written
    out at more length in ``check_runtime_ascii.py``; this file draws the
    line in the same place and for the same reason.

WHY NOT BUMP AT MERGE TIME INSTEAD
    Deciding the number when the merge happens would remove the race
    outright: there is only one merge at a time, so there is no second
    author to collide with. It is rejected here for three reasons, and the
    trade is real rather than obvious.

    It contradicts the standing rule that every change to the client bumps
    the version, which is a rule contributors already follow and which
    keeps the number reviewable *in the diff that earns it*. It moves the
    decision to a moment when nobody is looking at the change -- the number
    would be chosen by whoever presses merge, or by a bot, and a wrong one
    would land unreviewed. And it needs write access from CI to the
    protected branch, which is a much larger permission than this check's
    ``contents: read``.

    Monotonicity plus novelty gets the same guarantee while leaving the
    number where an author and a reviewer can both see it. If the volume of
    concurrent client changes ever makes the rebase-and-renumber loop the
    dominant cost, merge-time assignment is the right thing to revisit.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Package:
    """A distribution whose version number must not be reused."""

    name: str
    pyproject: str
    # The importable trees that land in the wheel. A tuple because a layout
    # can change (``tckdb-schemas`` was flat, ``tckdb-client`` is src/) and
    # the history walk has to recognise the package on both sides of such a
    # move; every path that exists at a given commit contributes.
    dist_paths: tuple[str, ...]


PACKAGES: tuple[Package, ...] = (
    Package(
        name="tckdb-client",
        pyproject="clients/python/pyproject.toml",
        dist_paths=("clients/python/src",),
    ),
    Package(
        name="tckdb-schemas",
        pyproject="schemas/python/tckdb-schemas/pyproject.toml",
        # Flat layout today (``where = ["."]``, ``include =
        # ["tckdb_schemas*"]``); ``src/`` is listed because the history walk
        # reaches commits from before the package settled, and a path that
        # does not exist at a commit is simply skipped.
        dist_paths=(
            "schemas/python/tckdb-schemas/tckdb_schemas",
            "schemas/python/tckdb-schemas/src",
        ),
    ),
    Package(
        name="tckdb-chemkin",
        pyproject="clients/python/adapters/chemkin/pyproject.toml",
        dist_paths=("clients/python/adapters/chemkin/tckdb_chemkin",),
    ),
    Package(
        name="tckdb-mcp",
        pyproject="integrations/mcp/pyproject.toml",
        dist_paths=("integrations/mcp/src/tckdb_mcp",),
    ),
)

# Every ``pyproject.toml`` in the repository is either covered above or named
# here with a reason. ``test_every_pyproject_is_covered_or_excluded`` fails if
# a new one appears in neither list, because the recurring defect in this
# repository's checkers is not being wrong -- it is being aimed at fewer
# places than they should have been, and nobody noticing for months.
EXCLUDED_PYPROJECTS: dict[str, str] = {
    "backend/pyproject.toml": (
        "The application, not a distribution. Nothing installs tckdb-backend "
        "from an index, so no third party can hold a pin that two different "
        "trees could satisfy; its version has been an inert 0.1.0 since it "
        "was written. If the backend is ever published as a wheel, move it "
        "into PACKAGES."
    ),
}


class VersionError(ValueError):
    """A version string this checker will not rank."""


# PEP 440, minus epochs and local versions: a release segment, then an
# optional pre-release, post-release and dev-release. Anything outside this
# is refused by name rather than ranked on a guess -- mis-ordering a version
# is worse than declining to order it, because a wrong order silently
# *passes* the branch it should have stopped.
_VERSION_RE = re.compile(
    r"""^\s*v?
    (?P<release>\d+(?:\.\d+)*)
    (?:[-_.]?(?P<pre_l>a|b|c|rc|alpha|beta|pre|preview)[-_.]?(?P<pre_n>\d+)?)?
    (?:[-_.]?post[-_.]?(?P<post_n>\d+)?)?
    (?:[-_.]?dev[-_.]?(?P<dev_n>\d+)?)?
    \s*$""",
    re.VERBOSE | re.IGNORECASE,
)

_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}

# Sorts after any real pre-release number, so a final release outranks its
# own pre-releases. Sorts before nothing else, so it is only ever compared
# inside the same release segment.
_FINAL = (1, 0, 0)
_DEV_ONLY = (-1, 0, 0)
_NO_DEV = float("inf")


def version_key(raw: str) -> tuple:
    """Return a sortable key for ``raw``.

    Semantic, not lexical: ``0.10.0`` must outrank ``0.9.0`` even though it
    sorts before it as a string. That trap is the reason this function
    exists rather than a ``>`` on two strings.
    """
    if not isinstance(raw, str):
        raise VersionError(f"version must be a string, got {type(raw).__name__}")
    match = _VERSION_RE.match(raw)
    if match is None:
        raise VersionError(
            f"cannot rank the version {raw!r}. This checker accepts a PEP 440 "
            f"release with optional pre/post/dev segments and nothing else. "
            f"It refuses rather than guessing an order, because a guessed "
            f"order passes the change it should have refused. Extend "
            f"_VERSION_RE and its tests if this spelling is intended."
        )
    release = tuple(int(part) for part in match.group("release").split("."))
    # Pad so 0.9 and 0.9.0 rank equal instead of by length.
    release = release + (0,) * (8 - len(release)) if len(release) < 8 else release

    pre_l, pre_n = match.group("pre_l"), match.group("pre_n")
    post_n, dev_n = match.group("post_n"), match.group("dev_n")

    if pre_l is not None:
        pre = (0, _PRE_RANK[pre_l.lower()], int(pre_n or 0))
    elif post_n is None and dev_n is not None:
        # 1.0.dev1 precedes 1.0a1, which precedes 1.0.
        pre = _DEV_ONLY
    else:
        pre = _FINAL

    post = -1 if post_n is None else int(post_n)
    dev = _NO_DEV if dev_n is None else int(dev_n)
    return (release, pre, post, dev)


def compare_versions(left: str, right: str) -> int:
    """-1, 0 or 1 as ``left`` is less than, equal to or greater than ``right``."""
    a, b = version_key(left), version_key(right)
    return (a > b) - (a < b)


class Git:
    """The handful of git queries this checker makes, against one repo."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def run(self, *args: str, allow_fail: bool = False) -> str | None:
        proc = subprocess.run(
            ["git", "-C", self.repo, *args],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            if allow_fail:
                return None
            raise RuntimeError(
                f"git {' '.join(args)} failed in {self.repo}: {proc.stderr.strip()}"
            )
        return proc.stdout

    def rev_parse(self, rev: str) -> str | None:
        out = self.run("rev-parse", "--verify", "--quiet", rev, allow_fail=True)
        return out.strip() if out else None

    def merge_base(self, a: str, b: str) -> str | None:
        out = self.run("merge-base", a, b, allow_fail=True)
        return out.strip() if out else None

    def show(self, commit: str, path: str) -> str | None:
        return self.run("show", f"{commit}:{path}", allow_fail=True)

    def tree_id(self, commit: str, path: str) -> str | None:
        out = self.run("rev-parse", "--verify", "--quiet", f"{commit}:{path}", allow_fail=True)
        return out.strip() if out else None

    def touching_commits(self, ref: str, paths: tuple[str, ...]) -> list[str]:
        """First-parent commits on ``ref`` that touched any of ``paths``.

        First-parent because that is what "shipped on main" means under a
        squash/merge workflow: the sequence of states main actually held.
        Only commits that touched the package can change its (version,
        content) pair, so walking these instead of all of history keeps the
        novelty scan to tens of commits rather than hundreds.
        """
        out = self.run("log", "--first-parent", "--format=%H", ref, "--", *paths, allow_fail=True)
        return out.split() if out else []

    def subject(self, commit: str) -> str:
        out = self.run("log", "-1", "--format=%s", commit, allow_fail=True)
        return (out or "").strip()

    def tags(self) -> list[str]:
        out = self.run("tag", "-l", allow_fail=True)
        return out.split() if out else []


_VERSION_LINE_RE = re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


@dataclass(frozen=True)
class State:
    """A package's version and distributed content at one commit."""

    version: str
    digest: str


def read_state(git: Git, commit: str, package: Package) -> State | None:
    """The package's (version, content-digest) at ``commit``, or None if absent."""
    blob = git.show(commit, package.pyproject)
    if blob is None:
        return None
    match = _VERSION_LINE_RE.search(blob)
    if match is None:
        return None
    version = match.group(1)

    parts = []
    for path in package.dist_paths:
        tree = git.tree_id(commit, path)
        if tree is not None:
            parts.append(f"{path}={tree}")
    # A version bump must not count as a content change on its own, or every
    # bump would justify itself and check (1) would be vacuous.
    parts.append("pyproject=" + _VERSION_LINE_RE.sub("", blob))
    digest = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()
    return State(version=version, digest=digest)


@dataclass
class Finding:
    package: str
    code: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    # Packages whose distributed content changed between merge base and head.
    changed: list[str] = field(default_factory=list)
    # Packages present at the head and therefore actually examined. A guard
    # that examined nothing passes trivially, so callers assert on this.
    examined: list[str] = field(default_factory=list)
    merge_base: str | None = None

    @property
    def ok(self) -> bool:
        return not self.findings


def check(
    git: Git,
    base_ref: str,
    head_ref: str,
    main_ref: str | None = None,
    packages: tuple[Package, ...] = PACKAGES,
) -> Report:
    """Run all three checks for every package present at ``head_ref``."""
    report = Report()

    head = git.rev_parse(head_ref)
    if head is None:
        raise RuntimeError(f"cannot resolve head ref {head_ref!r}")
    base = git.rev_parse(base_ref)
    if base is None:
        raise RuntimeError(f"cannot resolve base ref {base_ref!r}")

    merge_base = git.merge_base(base, head)
    if merge_base is None:
        raise RuntimeError(
            f"no merge base between {base_ref!r} and {head_ref!r}. A shallow "
            f"clone is the usual cause: this check needs fetch-depth: 0."
        )
    report.merge_base = merge_base

    novelty_ref = main_ref or base_ref
    # Resolved here, loudly, rather than inside the per-package scan. If an
    # unresolvable ref made the novelty scan quietly return "no conflict",
    # the collision this whole file exists to catch would report a pass --
    # a gate that verified nothing while looking green.
    if git.rev_parse(novelty_ref) is None:
        raise RuntimeError(
            f"cannot resolve the novelty ref {novelty_ref!r}. This is the ref "
            f"whose history must not already claim the head's version, so the "
            f"check cannot be skipped: refusing rather than passing. In CI, "
            f"fetch-depth: 0 is what makes the remote-tracking branch exist."
        )

    for package in packages:
        head_state = read_state(git, head, package)
        if head_state is None:
            continue
        report.examined.append(package.name)
        base_state = read_state(git, merge_base, package)
        findings_before = len(report.findings)

        content_changed = base_state is None or base_state.digest != head_state.digest
        if content_changed:
            report.changed.append(package.name)

        # (1) Monotonicity against the merge base.
        if base_state is not None:
            try:
                order = compare_versions(head_state.version, base_state.version)
            except VersionError as exc:
                report.findings.append(
                    Finding(package.name, "unrankable-version", str(exc))
                )
                continue
            if content_changed and order <= 0:
                verb = (
                    "is unchanged from"
                    if order == 0
                    else "is LOWER than"
                )
                report.findings.append(
                    Finding(
                        package.name,
                        "not-bumped" if order == 0 else "lowered",
                        f"{package.name}: the distributed package changed, but "
                        f"the version {head_state.version} {verb} the version "
                        f"{base_state.version} at the merge base "
                        f"({merge_base[:9]}). Raise it.",
                    )
                )
            elif not content_changed and order < 0:
                report.findings.append(
                    Finding(
                        package.name,
                        "lowered",
                        f"{package.name}: the version was lowered from "
                        f"{base_state.version} at the merge base "
                        f"({merge_base[:9]}) to {head_state.version}, and the "
                        f"package's contents did not change. A version number "
                        f"must never go backwards.",
                    )
                )

        # (2) and (3) police *this* change, so they only run for a package
        # this change actually touched. Two reasons, and the second is what
        # makes the guard usable at all.
        #
        # It is the honest scope: a pull request that does not touch
        # tckdb-mcp has not made tckdb-mcp's history any worse, and failing
        # it for a collision someone else shipped tells the author to fix
        # something they cannot fix from their branch.
        #
        # And the baseline is already dirty. ``--audit`` on origin/main finds
        # five colliding tckdb-schemas versions plus tckdb-mcp 0.1.0 with
        # four distinct contents (it has never been bumped). Unscoped, this
        # check would fail on an untouched main, which means it would fail on
        # every pull request -- and a gate that is red for everybody is a
        # gate that gets switched off or marked non-required within a week.
        # Recording those collisions is ``--audit``'s job; this is the gate.
        if not content_changed:
            continue

        # One finding per package, and monotonicity is the one that names the
        # fix. When a change did not bump at all, the old number is
        # necessarily also "already claimed with different contents" and
        # probably also tagged -- three findings describing one mistake, of
        # which only the first tells the author what to do. Report that one.
        if len(report.findings) > findings_before:
            continue

        # (2) Novelty against main's first-parent history.
        claimed_by = _find_conflicting_claim(git, novelty_ref, package, head_state)
        if claimed_by is not None:
            commit, subject = claimed_by
            report.findings.append(
                Finding(
                    package.name,
                    "version-already-claimed",
                    f"{package.name}: version {head_state.version} is already "
                    f"on {novelty_ref}'s history at {commit[:9]} "
                    f"({subject!r}) with DIFFERENT contents. Two packages "
                    f"would claim one number, and git will not conflict on "
                    f"it because both sides spell the version line "
                    f"identically. Pick the next unused version.",
                )
            )

        # (3) Novelty against release tags.
        tag_conflict = _find_conflicting_tag(git, package, head_state)
        if tag_conflict is not None:
            tag = tag_conflict
            report.findings.append(
                Finding(
                    package.name,
                    "version-already-tagged",
                    f"{package.name}: the tag {tag} already pins version "
                    f"{head_state.version} to different contents. Something "
                    f"downstream may hold that pin. Pick the next unused "
                    f"version.",
                )
            )

    return report


def _find_conflicting_claim(
    git: Git,
    ref: str,
    package: Package,
    head_state: State,
) -> tuple[str, str] | None:
    """A commit on ``ref`` claiming head's version with a different digest.

    ``ref`` is resolved by the caller, which raises if it cannot be. Do not
    add a "return None if unresolvable" shortcut here: that turns a broken
    checkout into a silent pass.
    """
    paths = (package.pyproject, *package.dist_paths)
    for commit in git.touching_commits(ref, paths):
        state = read_state(git, commit, package)
        if state is None:
            continue
        if state.version != head_state.version:
            continue
        if state.digest != head_state.digest:
            return commit, git.subject(commit)
    return None


def _find_conflicting_tag(git: Git, package: Package, head_state: State) -> str | None:
    """A ``<name>-v<version>`` tag pinning head's version to other contents."""
    wanted = f"{package.name}-v{head_state.version}"
    if wanted not in git.tags():
        return None
    commit = git.rev_parse(wanted + "^{commit}")
    if commit is None:
        return None
    state = read_state(git, commit, package)
    if state is None or state.digest == head_state.digest:
        return None
    return wanted


def audit(git: Git, ref: str, packages: tuple[Package, ...] = PACKAGES) -> dict:
    """Report versions already shipped with more than one content on ``ref``.

    Read-only history archaeology. This is what found the five
    ``tckdb-schemas`` collisions named in the module docstring; it does not
    repair them, because a version number that has already been fetched
    cannot be recalled.
    """
    results: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for package in packages:
        paths = (package.pyproject, *package.dist_paths)
        commits = list(reversed(git.touching_commits(ref, paths)))
        by_version: dict[str, dict[str, list[str]]] = {}
        for commit in commits:
            state = read_state(git, commit, package)
            if state is None:
                continue
            by_version.setdefault(state.version, {}).setdefault(state.digest, []).append(commit)
        collisions = {
            version: [(digest, commits_[0]) for digest, commits_ in digests.items()]
            for version, digests in by_version.items()
            if len(digests) > 1
        }
        if collisions:
            results[package.name] = collisions
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".", help="repository to inspect")
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="the pull request's base. The merge base of this and --head-ref "
        "is what monotonicity is measured against.",
    )
    parser.add_argument("--head-ref", default="HEAD", help="the pull request's head")
    parser.add_argument(
        "--main-ref",
        default=None,
        help="the branch whose whole history must not already claim the "
        "head's version. Defaults to --base-ref. This is deliberately not "
        "the merge base; see MERGE BASE VERSUS TIP in the module docstring.",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="report versions already shipped with more than one content, and "
        "exit 0 regardless. History archaeology, not a gate.",
    )
    args = parser.parse_args(argv)

    git = Git(args.repo)

    if args.audit:
        ref = args.main_ref or args.base_ref
        collisions = audit(git, ref)
        if not collisions:
            print(f"audit: no version on {ref} shipped with two contents.")
            return 0
        for name, versions in collisions.items():
            print(f"{name}: {len(versions)} version(s) shipped with >1 content")
            for version, states in versions.items():
                print(f"  {version}: {len(states)} distinct contents")
                for digest, commit in states:
                    print(f"    {digest[:8]}  first at {commit[:9]}  {git.subject(commit)[:60]}")
        return 0

    report = check(git, args.base_ref, args.head_ref, args.main_ref)

    if not report.examined:
        print(
            "check_package_version_bump: no covered package was present at "
            f"{args.head_ref}. Nothing was verified.",
            file=sys.stderr,
        )
        return 1

    if report.ok:
        if report.changed:
            print(
                "Version bump OK for: "
                + ", ".join(report.changed)
                + f" (merge base {report.merge_base[:9]})"
            )
        else:
            print(
                f"No covered package changed since the merge base "
                f"({report.merge_base[:9]}); "
                f"{len(report.examined)} package(s) examined."
            )
        return 0

    print("", file=sys.stderr)
    print("Package version check FAILED.", file=sys.stderr)
    print("", file=sys.stderr)
    for finding in report.findings:
        print(f"  [{finding.code}] {finding.message}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Why this exists: two branches can bump to the same number from the "
        "same starting point, and git merges the identical version line "
        "without a conflict and without listing the file in the merge stat. "
        "See the module docstring of backend/scripts/check_package_version_bump.py.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
