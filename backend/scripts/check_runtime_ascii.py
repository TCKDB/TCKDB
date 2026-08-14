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
    Three trees: ``backend/app``, ``backend/scripts`` and the wire package
    ``schemas/python/tckdb-schemas/tckdb_schemas``. The wire package was
    added late and should have been there from the first commit: it raises
    the validation errors a client reads and builds the ``message=``
    strings that are written to ``upload_warning.message``. It had nine
    violations on the day it was first scanned, all of them at emission
    sites, in the one package this check had never been pointed at.

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

USAGE
    python scripts/check_runtime_ascii.py [PATH ...]

    Defaults to the packaged sources. Exits 1 and prints one line per
    finding, in ``path:line: text`` form.
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
DEFAULT_TARGETS = ("app", "scripts", WIRE_PACKAGE_TARGET)


def default_target_paths() -> list[Path]:
    """The default targets as absolute paths."""
    return [(BACKEND_ROOT / name).resolve() for name in DEFAULT_TARGETS]

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
    args = parser.parse_args(argv)

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
