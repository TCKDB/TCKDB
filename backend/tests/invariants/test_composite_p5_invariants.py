"""Structural guards for assembled composites (ADR 0021, P5).

"Every place must do X" rules the phase adds. A rule that lives only in the call
sites that existed when it was written is the house defect: the next workflow
silently does without it, and an assembled composite is stored with no inputs and
a total nobody checked. Each guard counts what it found before it asserts
anything about it, so a scan that stopped matching cannot pass empty.

1. Every workflow that reports named-composite deposits also finalises assembled
   composites' inputs, and does so **before** the review policy runs (the policy
   can accept the composite, which freezes the rows being written).
2. ``calc_composite_input`` rows are written in exactly one place, and that
   place writes the mirroring ``composite_input`` edge in the same function.
3. ``calc_composite_input`` (a calculation child table) is archived and frozen
   (held for every calculation child table by the P3a guard; named here so a
   removal fails by name).
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.services.archive.registry import INCLUDED_TABLES

_BACKEND = Path(__file__).resolve().parents[2]
_APP = _BACKEND / "app"
_WORKFLOWS = _APP / "workflows"


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def _functions(path: Path) -> list[ast.FunctionDef]:
    tree = ast.parse(path.read_text())
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]


def _own_calls(function: ast.FunctionDef, name: str) -> list[ast.Call]:
    """Calls to ``name`` in ``function``'s own body, not in a nested function's."""
    found: list[ast.Call] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call) and _called_name(child) == name:
                found.append(child)
            visit(child)

    visit(function)
    return found


def test_every_workflow_that_reports_named_composite_deposits_finalises_assembled_inputs_first() -> None:
    reporting = [
        path
        for path in sorted(_WORKFLOWS.glob("*.py"))
        if any(_own_calls(f, "collect_named_composite_deposit_warnings") for f in _functions(path))
    ]
    # conformer, thermo, statmech, transport, network_pdep, computed_species, computed_reaction, transition_state.
    assert len(reporting) >= 8, [p.name for p in reporting]
    missing, late = [], []
    for path in reporting:
        finalising = [f for f in _functions(path) if _own_calls(f, "finalize_composite_inputs")]
        if not finalising:
            missing.append(path.name)
            continue
        for function in finalising:
            finalize_line = min(c.lineno for c in _own_calls(function, "finalize_composite_inputs"))
            policy = [c.lineno for c in _own_calls(function, "apply_review_policy")]
            if policy and min(policy) < finalize_line:
                late.append(f"{path.name}:{function.name}")
    assert not missing, f"report named-composite deposits but never finalise assembled inputs: {missing}"
    assert not late, f"finalise assembled inputs after the review policy (which can freeze them): {late}"


def test_composite_inputs_are_written_in_one_place_with_their_edge() -> None:
    writers = []
    for path in sorted(_APP.rglob("*.py")):
        for function in _functions(path):
            if _own_calls(function, "CalculationCompositeInput"):
                writers.append((path.relative_to(_BACKEND).as_posix(), function))
    # The one writer, plus nothing else: a second would write rows no edge mirrors.
    assert [(p, f.name) for p, f in writers] == [("app/services/composite_input_resolution.py", "_finalize_one")]
    [(_, function)] = writers
    assert _own_calls(function, "add_dependency_edge_idempotent"), "an input row is written without its edge"
    assert _own_calls(function, "assert_dependency_role_type_compatible")


def test_the_input_table_is_archived() -> None:
    assert "calc_composite_input" in INCLUDED_TABLES
