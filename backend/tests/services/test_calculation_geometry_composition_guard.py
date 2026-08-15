"""Every site that links a geometry to a calculation must check its composition.

The rule in ``app.services.calculation_geometry_composition`` is only as good
as its coverage, and coverage here is not a property of one function: rows in
``calculation_input_geometry`` and ``calculation_output_geometry`` are inserted
from eight places across four modules, which is exactly the shape that let the
gap exist in the first place — ``attach_calculation_output_geometries`` was
never the only writer, and a reader who found the check there would reasonably
conclude the seam was covered.

This guard makes the omission loud instead of silent. It parses the source for
constructions of the two ORM link classes and requires each enclosing function
to call ``assert_calculation_geometry_composition``. It is the same device the
scientific-check register uses to stop a declaration going unregistered, and it
is deliberately structural rather than behavioural: a new write path added
without a check fails here even if no test happens to exercise it.

If a future site legitimately cannot check — it links a geometry before the
calculation's owner is known, say — the honest fix is to make that explicit
here with the reason, not to widen the pattern.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

_LINK_CLASSES = {"CalculationInputGeometry", "CalculationOutputGeometry"}
_CHECKER = "assert_calculation_geometry_composition"

#: The construction sites as of #143, as ``module::function``. Listed so that
#: a *removed* check is as visible as an added-and-unchecked one: if this set
#: shrinks, someone deleted a write path and should say so.
_EXPECTED_SITES = {
    "services/calculation_resolution.py::_persist_irc_result",
    "services/calculation_resolution.py::_persist_path_search_result",
    "services/calculation_resolution.py::attach_calculation_input_geometries",
    "services/calculation_resolution.py::attach_calculation_output_geometries",
    "services/transition_state_resolution.py::persist_ts_calculations",
    "workflows/network_pdep.py::_persist_calculation",
}


def _enclosing_functions_that_construct_links() -> dict[str, ast.FunctionDef]:
    """Map ``module::function`` to the function node, for every write site."""

    found: dict[str, ast.FunctionDef] = {}
    for path in sorted(_APP.rglob("*.py")):
        if path.name == "__init__.py" or "db/models" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            constructs = any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id in _LINK_CLASSES
                for inner in ast.walk(node)
            )
            if constructs:
                key = f"{path.relative_to(_APP).as_posix()}::{node.name}"
                found[key] = node
    return found


def test_every_geometry_link_site_checks_composition() -> None:
    sites = _enclosing_functions_that_construct_links()
    assert sites, "found no geometry-link write sites at all — the AST walk broke"

    unchecked = sorted(
        key
        for key, node in sites.items()
        if not any(
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == _CHECKER
            for inner in ast.walk(node)
        )
    )
    assert not unchecked, (
        "These functions insert a calculation_input_geometry or "
        "calculation_output_geometry row without calling "
        f"{_CHECKER}: {unchecked}. A geometry linked to a calculation must be "
        "made of the atoms of the subject that calculation is filed under; see "
        "backend/docs/specs/calculation_geometry_composition.md."
    )


def test_the_known_write_sites_have_not_silently_disappeared() -> None:
    """A shrinking set means a write path was removed, which is also news."""

    sites = set(_enclosing_functions_that_construct_links())
    missing = sorted(_EXPECTED_SITES - sites)
    assert not missing, (
        f"These geometry-link write sites no longer exist: {missing}. If that "
        "is intended, update _EXPECTED_SITES and say why in the commit."
    )
