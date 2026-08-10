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

#: Checked by default.
#:
#: ``app`` because it is the deployed code. ``scripts`` because several of
#: them write to a real database (``bulk_load_arc.py``,
#: ``seed_scientific_demo_data.py``) and one of them says in its own comment
#: that the cluster it connects to is ``SQL_ASCII``.
#:
#: ``tests`` is deliberately NOT checked. Test data is the one place where
#: non-ASCII is the point: ``tests/schemas/test_artifact_in_schema.py``
#: uploads ``café.log`` and ``tests/services/test_idempotency.py`` hashes
#: ``haséaccent`` precisely to prove the system handles them. Linting those
#: would mean annotating the tests that prove the encoding works, in order
#: to protect against an encoding problem. And nothing under ``tests``
#: executes in a deployment.
DEFAULT_TARGETS = ("app", "scripts")

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
        try:
            shown = self.path.relative_to(root)
        except ValueError:
            shown = self.path
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

    def _record(self, node: ast.AST, kind: str, anchor: int) -> None:
        for constant in _string_constants(node):
            self.hits.setdefault(id(constant), (constant, kind, anchor))

    def visit_Raise(self, node: ast.Raise) -> None:
        if node.exc is not None:
            self._record(node.exc, "raised message", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_log_call(node):
            self._record(node, "log message", node.lineno)
        for keyword in node.keywords:
            if keyword.arg in _TEXT_KEYWORDS:
                self._record(keyword.value, f"{keyword.arg}= argument", node.lineno)
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


def check_path(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    files = sorted(path.rglob("*.py")) if path.is_dir() else [path]
    for file in files:
        findings.extend(check_source(file.read_text(encoding="utf-8"), file))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets",
        nargs="*",
        help="files or directories to check (default: app, scripts)",
    )
    args = parser.parse_args(argv)

    targets = [Path(t) for t in args.targets] or [
        BACKEND_ROOT / name for name in DEFAULT_TARGETS
    ]

    findings: list[Finding] = []
    for target in targets:
        if not target.exists():
            print(f"error: no such path: {target}", file=sys.stderr)
            return 2
        findings.extend(check_path(target))

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
