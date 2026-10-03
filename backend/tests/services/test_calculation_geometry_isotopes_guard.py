"""Every site that runs the composition check also runs the isotope check (#666).

``assert_calculation_geometry_composition`` reads ``D``, ``T`` and ``[2H]`` as
hydrogen, so it cannot see an isotope disagreement; the gap in #666 was exactly
a route that had the first and nothing for the second. This guard is structural,
like ``test_calculation_geometry_composition_guard.py``: a site added with one
check and not the other fails here even if no behavioural test reaches it.

Two directions, because either alone can pass vacuously:

* every function that calls the composition check calls the isotope check;
* every function that constructs a geometry-link row calls the isotope check.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[2] / "app"

_COMPOSITION = "assert_calculation_geometry_composition"
_ISOTOPES = "assert_calculation_geometry_isotopes"
_LINK_CLASSES = {"CalculationInputGeometry", "CalculationOutputGeometry"}

#: ``module::function`` for every function that calls the composition check, as
#: of #666. Listed so a *removed* site is as visible as an unchecked one.
_EXPECTED_SITES = {
    "services/calculation_resolution.py::_persist_irc_result",
    "services/calculation_resolution.py::_persist_path_search_result",
    "services/calculation_resolution.py::attach_calculation_input_geometries",
    "services/calculation_resolution.py::attach_calculation_output_geometries",
    "services/input_geometry_extraction.py::_mint_and_link_extracted_geometry",
    "services/transition_state_resolution.py::persist_ts_calculations",
    "workflows/network_pdep.py::_persist_calculation",
}


def _calls(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(inner, ast.Call)
        and isinstance(inner.func, ast.Name)
        and inner.func.id == name
        for inner in ast.walk(node)
    )


def _functions() -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for path in sorted(_APP.rglob("*.py")):
        if path.name == "__init__.py" or "db/models" in path.as_posix():
            continue
        if path.name == "calculation_geometry_composition.py":
            continue  # defines the checks; does not call them
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[f"{path.relative_to(_APP).as_posix()}::{node.name}"] = node
    return found


def test_every_composition_site_also_checks_isotopes() -> None:
    sites = {k: n for k, n in _functions().items() if _calls(n, _COMPOSITION)}
    assert sites, "found no composition-check sites at all -- the AST walk broke"
    missing = sorted(k for k, n in sites.items() if not _calls(n, _ISOTOPES))
    assert not missing, (
        f"These functions call {_COMPOSITION} but not {_ISOTOPES}: {missing}. "
        "Composition counts D, T and [2H] as hydrogen, so only the isotope "
        "check can refuse a deuterium geometry under a protium species."
    )


def test_every_geometry_link_site_checks_isotopes() -> None:
    sites = {
        k: n
        for k, n in _functions().items()
        if any(_calls(n, cls) for cls in _LINK_CLASSES)
    }
    assert sites, "found no geometry-link write sites at all -- the AST walk broke"
    missing = sorted(k for k, n in sites.items() if not _calls(n, _ISOTOPES))
    assert not missing, f"Geometry-link sites without {_ISOTOPES}: {missing}"


def test_the_known_sites_have_not_silently_disappeared() -> None:
    sites = {k for k, n in _functions().items() if _calls(n, _COMPOSITION)}
    assert sites == _EXPECTED_SITES, (
        f"composition-check sites changed: added={sorted(sites - _EXPECTED_SITES)} "
        f"removed={sorted(_EXPECTED_SITES - sites)}. Update _EXPECTED_SITES and "
        "say why in the commit."
    )
