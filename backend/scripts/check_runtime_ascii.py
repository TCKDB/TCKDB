#!/usr/bin/env python3
"""Refuse non-ASCII in strings that leave the process.

WHY
    On 2026-08-04 an em dash in an upload-warning message rolled back a
    whole upload against a ``SQL_ASCII`` database. The character carried
    no meaning: it was a typographic habit, and an ASCII hyphen would
    have said the same thing. That database has since been converted to
    UTF-8 and the deployment now pins its encoding (see
    ``backend/Dockerfile`` and ``docker-compose.yml``), so this check is
    the second layer rather than the only one -- but the first layer is
    a property of a host, and a host is exactly the thing that gets
    rebuilt by someone in a hurry.

WHAT IS IN SCOPE, AND WHY IT IS DRAWN THIS NARROWLY
    Six trees; see ``DEFAULT_TARGETS``. Three of them are backend-side
    (``backend/app``, ``backend/scripts``, and the wire package
    ``schemas/python/tckdb-schemas/tckdb_schemas``); three are the
    shipped Python that runs on a *contributor's* machine
    (``clients/python/src/tckdb_client``, the CHEMKIN adapter
    ``clients/python/adapters/chemkin/tckdb_chemkin``, and the MCP
    integration ``integrations/mcp/src/tckdb_mcp``).

    Each of those was added after a measurement, not before one. The
    wire package had nine violations on the day it was first scanned.
    The client builders had eight and the CHEMKIN adapter two, all of
    them at emission sites, in packages this check had never been
    pointed at. ``tckdb_mcp`` had none: it is here for coverage, so the
    next string written into it is checked by the pull request that
    writes it, and nothing in this file's history should be read as
    having repaired anything there.

    That is the recurring shape, and it is the reason for ``--audit``
    below: every one of these was a correct check aimed at fewer places
    than it should have been, and in each case the omission was found
    by a person noticing, months late, rather than by the check saying
    so.

    Only string literals that are structurally *at an emission site*:
    the argument of a ``raise``, the argument of a logging call, or a
    ``message=`` / ``detail=`` / ``reason=`` / ``msg=`` keyword. Those
    are the strings that reach a database column, an HTTP response body
    or a log record.

    Everything else is left alone on purpose. Docstrings, comments,
    module-level prose, generated documentation and test data all use
    typography correctly and none of them is ever written anywhere but
    a document. A checker that fires on an em dash in a docstring gets
    switched off inside a week, and then it is not protecting the error
    messages either. Precision here is not politeness; it is the only
    way the rule survives.

    The cost of the narrow rule is real and worth stating: a literal
    assigned to a local and *then* interpolated into a raise is not
    seen. Extending to that needs dataflow, and the checker would start
    guessing. Every finding it reports is a certainty.

SHELL SCRIPTS
    ``*.sh`` under the same targets is checked too, by a much cruder
    rule: **any non-ASCII character on a line that is not a whole-line
    comment**. Shell has no AST to walk here, and the distinction the
    Python rule draws -- emission site versus prose -- maps onto shell
    almost exactly as "code versus comment", because a shell script's
    code is nearly all `echo`, `printf` and heredocs. Comments are left
    alone for the same reason docstrings are: this repo's shell headers
    are long explanatory prose written with em dashes, and a checker
    that fires on those gets switched off.

    This was added because it was missing and the miss was concrete:
    ``tckdb_auth.sh``'s ``mask_key`` built its output with U+2026, and
    nothing had ever looked at it, because this script only ever walked
    ``*.py``. The masked key goes to a terminal, so the character cost
    nothing that time -- but ``tckdb_doctor.sh`` prints diagnostics that
    get pasted into issues, and the deploy and alert scripts under
    ``scripts/ops/`` write text that reaches a log.

    Known limitation, stated rather than hidden: a line *inside a
    heredoc* that begins with ``#`` is content, not a comment, and this
    rule skips it. Recognising that needs heredoc tracking, which is the
    point at which a shell "parser" starts guessing.

ESCAPE HATCH
    ``# tckdb: allow-non-ascii`` on the offending line, or on the line
    the enclosing statement starts. Some emitted strings legitimately
    need a non-ASCII character -- an error quoting a unit symbol, a
    parser reporting the token it failed on. Say so in place.

SAYING WHAT IS *NOT* SCANNED
    A target list is a list somebody forgets to extend, and this one was
    forgotten five times. So the list is not the only record: every
    ``*.py`` and ``*.sh`` in the repository is either under a target or
    named in ``UNSCANNED_BY_DESIGN`` with a reason. ``--audit`` proves
    it and prints the exclusions; ``tests/scripts/test_check_runtime_ascii.py``
    runs the audit, so a *new* tree of Python that nothing scans fails a
    pull request on the day it is added rather than being discovered by
    an incident.

    The audit deliberately does not decide whether a tree *should* be
    scanned. It only refuses to let one be absent from both lists, which
    is the state that made every previous miss invisible.

USAGE
    python scripts/check_runtime_ascii.py [PATH ...]
    python scripts/check_runtime_ascii.py --audit

    Defaults to the packaged sources. Exits 1 and prints one line per
    finding, in ``path:line: text`` form. ``--audit`` exits 1 naming any
    source file that is neither scanned nor declared out of scope.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

#: The repository root. The wire package is a sibling of ``backend``, so the
#: default targets are expressed relative to ``BACKEND_ROOT`` and resolved
#: here rather than against the current directory: CI runs this script with
#: ``working-directory: backend`` and a developer runs it from wherever they
#: happen to be, and the two must scan the same tree.
REPO_ROOT = BACKEND_ROOT.parent

#: Where the wire package's sources live, relative to ``BACKEND_ROOT``.
WIRE_PACKAGE_TARGET = "../schemas/python/tckdb-schemas/tckdb_schemas"

#: The Python client a contributor installs, relative to ``BACKEND_ROOT``.
CLIENT_PACKAGE_TARGET = "../clients/python/src/tckdb_client"

#: The CHEMKIN importer adapter, which ships and versions separately from
#: the client (``clients/python/adapters/chemkin/pyproject.toml``).
CHEMKIN_ADAPTER_TARGET = "../clients/python/adapters/chemkin/tckdb_chemkin"

#: The MCP integration. Added at zero violations -- coverage, not repair.
MCP_INTEGRATION_TARGET = "../integrations/mcp/src/tckdb_mcp"

#: Frontend deployment and smoke helpers execute in CI or on the deployment
# host, so their shell/Python diagnostics are runtime text rather than docs.
FRONTEND_SCRIPTS_TARGET = "../frontend/scripts"

#: Checked by default, as paths relative to ``BACKEND_ROOT``.
#:
#: ``app`` because it is the deployed code. ``scripts`` because several of
#: them write to a real database (``bulk_load_arc.py``,
#: ``seed_scientific_demo_data.py``) and one of them says in its own comment
#: that the cluster it connects to is ``SQL_ASCII``.
#:
#: ``tckdb_schemas`` because it was left out and the omission was not a
#: judgement, it was an oversight: the wire package raises validation errors
#: and builds the ``message=`` strings that become ``UploadWarning.message``
#: rows. A database column and a client-facing string are exactly what this
#: check was written for, and until now it was the one package that produced
#: them and was never looked at. Nine violations were sitting there.
#:
#: ``tests`` is deliberately NOT checked. Test data is the one place where
#: non-ASCII is the point: ``tests/schemas/test_artifact_in_schema.py``
#: uploads ``café.log`` and ``tests/services/test_idempotency.py`` hashes
#: ``haséaccent`` precisely to prove the system handles them. Linting those
#: would mean annotating the tests that prove the encoding works, in order
#: to protect against an encoding problem. And nothing under ``tests``
#: executes in a deployment. The same reasoning excludes the wire package's
#: own ``tests/``, which is why the target is the package directory and not
#: the distribution directory above it.
#:
#: ``tckdb_client`` and ``tckdb_chemkin`` for the same reason the wire
#: package was added, one layer further out: their strings are the ones a
#: contributor reads in a terminal, and the adapter's are accumulated into
#: ``NormalizedReaction.warnings`` and carried up through an import run.
#: Eight and two violations respectively when first scanned.
#:
#: ``tckdb_mcp`` scanned clean when it was added. It is on the list so that
#: the *next* string written there is checked; nothing was repaired in it.
DEFAULT_TARGETS = (
    "app",
    "scripts",
    WIRE_PACKAGE_TARGET,
    CLIENT_PACKAGE_TARGET,
    CHEMKIN_ADAPTER_TARGET,
    MCP_INTEGRATION_TARGET,
    FRONTEND_SCRIPTS_TARGET,
)


def default_target_paths() -> list[Path]:
    """The default targets as absolute paths."""
    return [(BACKEND_ROOT / name).resolve() for name in DEFAULT_TARGETS]


#: Source suffixes the audit accounts for. Exactly what :func:`walk` reads,
#: so "scanned" and "audited" cannot drift apart.
SOURCE_SUFFIXES = (".py", ".sh")

#: Never walked: version control, virtualenvs, caches, build output, and
#: anything under a dot-directory (which is how ``.git``, ``.venv`` and
#: ``.claude/worktrees`` are excluded without naming each one).
_AUDIT_SKIP_DIRS = frozenset({"node_modules", "__pycache__", "build", "dist"})

#: Why the audit passes over a tests tree. Stated once because it is the
#: same reason every time, and it is the reason given in this file's header
#: for scanning the wire *package* rather than its distribution directory.
TEST_TREE_REASON = (
    "test data is the one place non-ASCII is the point, and nothing under "
    "tests/ runs in a deployment"
)

#: Source files no target covers, and why. Keys are repository-relative
#: POSIX paths; a file is declared if it equals a key or lies under one.
#:
#: This exists so that "not scanned" is a decision on the record rather than
#: an absence nobody can see. ``--audit`` fails on any source file that is in
#: neither this table nor a target, which is the state every one of the five
#: 2026-08 encoding misses was in.
UNSCANNED_BY_DESIGN: tuple[tuple[str, str], ...] = (
    (
        "backend/alembic",
        "migration revisions. Four raise-site em dashes are sitting here "
        "(b6e1d3a9c740:144, c4d8f1b2a9e6:183, d3a7f1c9b284:352, "
        "e3f4a5b6c7d8:628), printed to an operator's terminal by a "
        "downgrade guard. They are recorded rather than fixed because "
        ".claude/rules/migration-rules.md forbids mutating a revision that "
        "has been applied to a deployed database. Scanning this tree means "
        "settling that question first",
    ),
    (
        "backend/main.py",
        "the ASGI entry point: three statements and no string literal at "
        "all. Everything it serves is emitted from backend/app, which is "
        "scanned",
    ),
    (
        "clients/python/examples",
        "demo programs that print to a terminal. Out of charter for the "
        "same reason a docstring is: nothing here reaches a database "
        "column, a response body or a log",
    ),
    (
        "clients/python/scripts",
        "one generator that rewrites a checked-in Markdown document; its "
        "output is prose in a document, not an emitted string",
    ),
    (
        "examples/clients",
        "demo programs, as clients/python/examples",
    ),
)


def _is_test_path(relative: str) -> bool:
    """Whether *relative* is test scaffolding rather than shipped code."""
    parts = relative.split("/")
    return "tests" in parts or parts[-1] == "conftest.py"


def audit_reason(relative: str) -> str | None:
    """Why *relative* is not scanned, or ``None`` if nothing says."""
    if _is_test_path(relative):
        return TEST_TREE_REASON
    for prefix, reason in UNSCANNED_BY_DESIGN:
        if relative == prefix or relative.startswith(prefix + "/"):
            return reason
    return None


def repository_sources() -> list[str]:
    """Every source file in the repository, repository-relative."""
    out: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if path.suffix not in SOURCE_SUFFIXES or path.is_dir():
            continue
        parts = path.relative_to(REPO_ROOT).parts
        if any(
            part.startswith(".") or part in _AUDIT_SKIP_DIRS or part.endswith(".egg-info")
            for part in parts[:-1]
        ):
            continue
        out.append("/".join(parts))
    return sorted(out)


def audit() -> tuple[list[str], dict[str, int]]:
    """Split the repository's sources into undeclared and declared-by-reason.

    Returns the source files that are neither scanned nor declared, and a
    count of declared files per ``UNSCANNED_BY_DESIGN`` prefix (plus the
    test trees under their shared reason). A declaration matching nothing
    is reported as a count of zero: a stale exclusion is the same failure
    as a target that expands to no files, one list drifting away from the
    tree it describes.
    """
    scanned = default_target_paths()
    undeclared: list[str] = []
    counts: dict[str, int] = {"tests/ trees": 0}
    for prefix, _ in UNSCANNED_BY_DESIGN:
        counts[prefix] = 0

    for relative in repository_sources():
        absolute = REPO_ROOT / relative
        if any(target in absolute.parents or target == absolute for target in scanned):
            continue
        if _is_test_path(relative):
            counts["tests/ trees"] += 1
            continue
        for prefix, _ in UNSCANNED_BY_DESIGN:
            if relative == prefix or relative.startswith(prefix + "/"):
                counts[prefix] += 1
                break
        else:
            undeclared.append(relative)
    return undeclared, counts


def run_audit() -> int:
    """Print the coverage account and fail on anything undeclared."""
    undeclared, counts = audit()
    for target in DEFAULT_TARGETS:
        resolved = (BACKEND_ROOT / target).resolve()
        print(f"scanned    {len(walk(resolved)):>4}  {_relative(resolved)}")
    for label, count in counts.items():
        print(f"declared   {count:>4}  {label}")

    empty = [label for label, count in counts.items() if count == 0]
    if empty:
        print(
            "error: declared out of scope but matching no file: "
            + ", ".join(empty)
            + "\n       A stale exclusion hides the tree it used to name.",
            file=sys.stderr,
        )
        return 1
    if undeclared:
        for relative in undeclared:
            print(f"undeclared {'':>4}  {relative}")
        print(
            f"\n{len(undeclared)} source file(s) are neither scanned by "
            f"{Path(__file__).name} nor named in UNSCANNED_BY_DESIGN.\n"
            "Add the tree to DEFAULT_TARGETS, or say in UNSCANNED_BY_DESIGN "
            "why it is not scanned. Silence is what let five encoding misses "
            "through.",
            file=sys.stderr,
        )
        return 1
    print("audit: every source file is scanned or declared out of scope")
    return 0


ALLOW_MARKER = "# tckdb: allow-non-ascii"

#: Method names that emit a log record.
_LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)

#: Receivers whose logging methods count. Matched on the last dotted name, so
#: ``logger``, ``self.logger``, ``app.api.logger`` and ``_logger`` all qualify.
_LOG_RECEIVERS = frozenset({"logger", "log", "logging", "_logger", "warnings", "LOGGER"})

#: Calls that return a logger, for the unbound
#: ``logging.getLogger(__name__).warning(...)`` shape.
_LOG_FACTORIES = frozenset({"getLogger"})

#: Local accumulators that are returned to a caller and end up in a payload
#: or a response. ``warnings.append("...")`` in the cccbdb builders flows into
#: ``PayloadBundle.warnings`` and out through the upload path, which is an
#: emission site by any reading -- it was missed once, and the miss was then
#: written up as "a parser token matched against HTML", which it is not.
#: Matched on the receiver name because that is what distinguishes an
#: accumulator from ``some_list.append``.
_ACCUMULATOR_RECEIVERS = frozenset({"warnings", "errors", "messages", "notes"})

#: Keyword arguments carrying human-readable text that gets stored or
#: returned. ``message`` is the one that mattered: ``UploadWarning(message=...)``
#: rows are written to the database and served back to clients, and an
#: ``UploadWarning`` message is what the 2026-08-04 rollback was carrying.
_TEXT_KEYWORDS = frozenset({"message", "detail", "reason", "msg"})


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    kind: str
    text: str
    characters: str

    def render(self, root: Path) -> str:
        shown = _relative(self.path)
        snippet = self.text if len(self.text) <= 80 else self.text[:77] + "..."
        return (
            f"{shown}:{self.line}: non-ASCII {self.characters!r} in a "
            f"{self.kind}: {snippet!r}"
        )


def _string_constants(node: ast.AST) -> list[ast.Constant]:
    """Every ``str`` constant under *node*, including f-string fragments."""
    out: list[ast.Constant] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            out.append(child)
    return out


def _is_log_call(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _LOG_METHODS:
        return False
    receiver = func.value
    if isinstance(receiver, ast.Name):
        name = receiver.id
    elif isinstance(receiver, ast.Attribute):
        name = receiver.attr
    elif isinstance(receiver, ast.Call):
        # logging.getLogger(__name__).warning("...") -- the logger is never
        # bound to a name, so the receiver is the getLogger call itself.
        inner = receiver.func
        name = inner.attr if isinstance(inner, ast.Attribute) else getattr(inner, "id", "")
        return name in _LOG_FACTORIES
    else:
        return False
    return name in _LOG_RECEIVERS


def _is_accumulator_append(call: ast.Call) -> bool:
    """``warnings.append("...")`` and friends."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "append":
        return False
    receiver = func.value
    if isinstance(receiver, ast.Name):
        return receiver.id in _ACCUMULATOR_RECEIVERS
    if isinstance(receiver, ast.Attribute):
        return receiver.attr in _ACCUMULATOR_RECEIVERS
    return False


class _Visitor(ast.NodeVisitor):
    """Collect string constants sitting at an emission site.

    Nodes are recorded by identity so a literal reachable two ways (a
    ``message=`` keyword inside a ``raise``, say) is reported once.
    """

    def __init__(self) -> None:
        #: constant id -> (constant, kind, anchor line). The anchor is the
        #: line the emitting statement starts on, which is where a reader
        #: most naturally writes the allow-marker: adjacent string fragments
        #: are folded by the parser into one constant whose own lineno is the
        #: *first fragment*, several lines below the `raise`.
        self.hits: dict[int, tuple[ast.Constant, str, int]] = {}

    def _record(self, node: ast.AST, kind: str, anchor: int, *, specific: bool = False) -> None:
        """Record every literal under *node* as sitting at an emission site.

        A literal is often reachable two ways -- ``warnings.append(
        UploadWarning(message="..."))`` is an accumulator *and* a
        ``message=`` keyword -- and it must be reported once. The
        keyword attribution is the more useful of the two in the output,
        so *specific* records are allowed to replace a container-level
        one; nothing else overwrites.
        """
        for constant in _string_constants(node):
            key = id(constant)
            if key not in self.hits or specific:
                self.hits[key] = (constant, kind, anchor)

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            self._record(node.exc, "raised message", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_log_call(node):
            self._record(node, "log message", node.lineno)
        if _is_accumulator_append(node):
            self._record(node, "accumulated warning", node.lineno)
        for keyword in node.keywords:
            if keyword.arg in _TEXT_KEYWORDS:
                self._record(
                    keyword.value,
                    f"{keyword.arg}= argument",
                    node.lineno,
                    specific=True,
                )
        self.generic_visit(node)


def _allowed_lines(source: str) -> set[int]:
    return {
        number
        for number, line in enumerate(source.splitlines(), start=1)
        if ALLOW_MARKER in line
    }


def check_source(source: str, path: Path) -> list[Finding]:
    """Return findings for one module's *source*."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [
            Finding(path, exc.lineno or 0, "unparseable module", str(exc.msg), "")
        ]

    visitor = _Visitor()
    visitor.visit(tree)
    allowed = _allowed_lines(source)

    findings: list[Finding] = []
    for constant, kind, anchor in visitor.hits.values():
        offenders = sorted({c for c in constant.value if ord(c) > 127})
        if not offenders:
            continue
        # Accept the marker anywhere across the literal, and on the line the
        # emitting statement opens on. Both are places a reader would put it,
        # and neither reaches past this statement into the next one.
        span = set(range(constant.lineno, (constant.end_lineno or constant.lineno) + 1))
        span.add(anchor)
        if allowed.intersection(span):
            continue
        findings.append(
            Finding(
                path=path,
                line=constant.lineno,
                kind=kind,
                text=constant.value,
                characters="".join(offenders),
            )
        )
    return sorted(findings, key=lambda f: (str(f.path), f.line))


def check_shell_source(source: str, path: Path) -> list[Finding]:
    """Return findings for one shell script's *source*.

    One finding per offending line: shell has no literal to attribute a
    character to, so the line is the unit.
    """
    findings: list[Finding] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        if ALLOW_MARKER in line:
            continue
        offenders = sorted({c for c in line if ord(c) > 127})
        if not offenders:
            continue
        findings.append(
            Finding(
                path=path,
                line=number,
                kind="shell line",
                text=line.strip(),
                characters="".join(offenders),
            )
        )
    return findings


def check_file(path: Path) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".sh":
        return check_shell_source(source, path)
    return check_source(source, path)


def _relative(path: Path) -> Path:
    """*path* against the nearest of the roots, for readable output."""
    for base in (BACKEND_ROOT, REPO_ROOT):
        try:
            return path.relative_to(base)
        except ValueError:
            continue
    return path


def walk(path: Path) -> list[Path]:
    """The files *path* expands to: every ``*.py`` and ``*.sh`` beneath it."""
    if path.is_dir():
        return sorted({*path.rglob("*.py"), *path.rglob("*.sh")})
    return [path]


def check_path(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for file in walk(path):
        findings.extend(check_file(file))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets",
        nargs="*",
        help=(
            "files or directories to check "
            f"(default: {', '.join(DEFAULT_TARGETS)})"
        ),
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help=(
            "check no source file in the repository is missing from both "
            "the target list and UNSCANNED_BY_DESIGN, and print the account"
        ),
    )
    args = parser.parse_args(argv)

    if args.audit:
        if args.targets:
            parser.error("--audit takes no paths: it looks at the whole repository")
        return run_audit()

    targets = [Path(t) for t in args.targets] or default_target_paths()

    findings: list[Finding] = []
    for target in targets:
        if not target.exists():
            print(f"error: no such path: {target}", file=sys.stderr)
            return 2
        walked = walk(target)
        if target.is_dir() and not walked:
            # Guard the guard. A target that expands to nothing makes this
            # check pass without reading a line, which is indistinguishable
            # from a clean tree in the CI log. A mistyped or moved directory
            # is the way a check quietly stops checking.
            print(
                f"error: {target} contains no *.py or *.sh to check",
                file=sys.stderr,
            )
            return 2
        for file in walked:
            findings.extend(check_file(file))
        # Say what was read. A silent success cannot be told apart from a
        # success over nothing, and "the gate is green" is worth exactly as
        # much as the tree it looked at -- which is the defect this target
        # list had for as long as it existed.
        print(f"checked {len(walked)} file(s) under {_relative(target)}")

    if not findings:
        return 0

    for finding in findings:
        print(finding.render(BACKEND_ROOT))
    print(
        f"\n{len(findings)} non-ASCII character(s) in strings that reach a "
        f"database, a client or a log.\n"
        f"Replace them with ASCII (-- for an em dash, ... for an ellipsis, "
        f"-> for an arrow), or, if the character is load-bearing, mark the "
        f"line with `{ALLOW_MARKER}`.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
