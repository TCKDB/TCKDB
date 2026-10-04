"""Shared AST walk for the two geometry-link guards (#680).

Both guards used to ask, per *function*, "does it call the checker anywhere?".
That is how IRC points stayed unchecked while the guard was green:
``_persist_irc_result`` called both checks, but only inside the
forward/reverse branch, and the TS-marker point's ``calc_irc_point`` row was
written outside it.

The question asked here is per *construction site*: for every call that builds a
geometry-bearing ORM row, some statement **before it, in the same block or an
enclosing one,** must contain a call to the checker, naming the same
``geometry_id`` expression as the row does.

Stated limits, so nobody reads more into a green than is there:

* This is a structural approximation of "the check dominates the write", not
  proof. A checker call nested in an ``if`` that precedes the write counts, even
  though the ``if`` may not run; the repository's sites use the same condition
  for the check and the write (``geometry_id not in linked_geometry_ids``), and
  a different condition is exactly what a reviewer should look at.
* The ``geometry_id`` comparison is textual (``ast.dump``) on the keyword
  argument, so ``geometry_id=geom.id`` on the row and ``geometry_id=geometry.id``
  on the check do not match even if they are the same value. That is the right
  failure: it asks the author to make the correspondence visible.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

#: Every ORM class that attaches a stored geometry to a calculation.
LINK_CLASSES = {
    "CalculationInputGeometry",
    "CalculationOutputGeometry",
    "CalculationHessian",
    "CalculationScanPoint",
    "CalculationIRCPoint",
    "CalculationPathSearchPoint",
}


def _modules():
    for path in sorted(APP.rglob("*.py")):
        if path.name == "__init__.py" or "db/models" in path.as_posix():
            continue
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _is_call_to(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name


def _geometry_id_arg(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "geometry_id":
            return ast.dump(keyword.value)
    return None


def _statement_lists(node: ast.AST):
    for field in ("body", "orelse", "finalbody"):
        stmts = getattr(node, field, None)
        if isinstance(stmts, list):
            yield stmts
    for handler in getattr(node, "handlers", []) or []:
        yield handler.body


def _checked_before(construction: ast.Call, parents, checker: str) -> bool:
    wanted = _geometry_id_arg(construction)
    node: ast.AST = construction
    while node in parents:
        parent = parents[node]
        if isinstance(node, ast.stmt):
            for stmts in _statement_lists(parent):
                if node not in stmts:
                    continue
                for earlier in stmts[: stmts.index(node)]:
                    for inner in ast.walk(earlier):
                        if _is_call_to(inner, checker) and _geometry_id_arg(inner) == wanted:
                            return True
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
        node = parent
    return False


def construction_sites() -> dict[str, list[tuple[ast.Call, dict]]]:
    """Map ``module::function`` to ``[(construction call, parent map)]``.

    The function is the innermost one enclosing the construction.
    """

    found: dict[str, list[tuple[ast.Call, dict]]] = {}
    for path, tree in _modules():
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in LINK_CLASSES
            ):
                continue
            enclosing = node
            while enclosing in parents and not isinstance(
                enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                enclosing = parents[enclosing]
            name = enclosing.name if isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"
            key = f"{path.relative_to(APP).as_posix()}::{name}"
            found.setdefault(key, []).append((node, parents))
    return found


def unchecked_constructions(checker: str) -> list[str]:
    """``module::function:line Class`` for each construction with no prior ``checker`` call."""

    missing: list[str] = []
    for key, sites in construction_sites().items():
        for call, parents in sites:
            if not _checked_before(call, parents, checker):
                missing.append(f"{key}:{call.lineno} {call.func.id}")  # type: ignore[attr-defined]
    return sorted(missing)
