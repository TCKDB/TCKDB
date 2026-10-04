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

#: Every ORM class that attaches a stored geometry to a calculation. The first
#: two are the input/output links; the rest are the geometry-bearing children
#: #680 found unchecked (a Hessian's frame, a scan point) plus the IRC and
#: path-search points, which share a function with an output link but are
#: named here so removing that link cannot silently drop their check.
_LINK_CLASSES = {
    "CalculationInputGeometry",
    "CalculationOutputGeometry",
    "CalculationHessian",
    "CalculationScanPoint",
    "CalculationIRCPoint",
    "CalculationPathSearchPoint",
}
_CHECKER = "assert_calculation_geometry_composition"

#: The construction sites as of #143, as ``module::function``. Listed so that
#: a *removed* check is as visible as an added-and-unchecked one: if this set
#: shrinks, someone deleted a write path and should say so.
_EXPECTED_SITES = {
    "services/calculation_resolution.py::_persist_irc_result",
    "services/calculation_resolution.py::_persist_path_search_result",
    "services/calculation_resolution.py::attach_calculation_input_geometries",
    "services/calculation_resolution.py::attach_calculation_output_geometries",
    "services/calculation_resolution.py::persist_calculation_result",
    "services/calculation_scan_resolution.py::persist_calculation_scan",
    "services/hessian_extraction.py::_insert",
    "services/input_geometry_extraction.py::_mint_and_link_extracted_geometry",
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
        "These functions attach a geometry to a calculation (an input/output "
        "link, a Hessian, a scan, IRC or path-search point) without calling "
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


#: Tables that hold a ``geometry.id`` foreign key *and* a ``calculation_id``,
#: and so could attach a geometry to a calculation, but deliberately do not,
#: with the reason.
_EXEMPT_TABLES = {
    "calc_geometry_validation": (
        "records a comparison between geometries that are ALREADY linked to the "
        "calculation (it reads them back from the input/output links), so it "
        "attaches nothing new"
    ),
}


def _geometry_bearing_calculation_tables() -> set[str]:
    from app.db import models  # noqa: F401  (registers every table)
    from app.db.base import Base

    found: set[str] = set()
    for table in Base.metadata.tables.values():
        if "calculation_id" not in table.c:
            continue
        if any(fk.column.table.name == "geometry" for fk in table.foreign_keys):
            found.add(table.name)
    return found


def test_every_geometry_bearing_calculation_table_is_covered_or_exempt() -> None:
    """A new geometry-bearing child of ``calculation`` cannot slip past the checks.

    Derived from the schema, not from the list above: a future table with a
    ``calculation_id`` and a geometry foreign key fails here until its class is
    added to ``_LINK_CLASSES`` (and so to the structural test) or it is exempted
    with a reason.
    """

    from app.db.base import Base

    class_for_table = {
        mapper.local_table.name: mapper.class_.__name__
        for mapper in Base.registry.mappers
    }
    tables = _geometry_bearing_calculation_tables()
    assert tables, "found no geometry-bearing calculation tables -- the metadata walk broke"
    uncovered = sorted(
        table
        for table in tables
        if table not in _EXEMPT_TABLES and class_for_table[table] not in _LINK_CLASSES
    )
    assert not uncovered, (
        f"These tables link a geometry to a calculation but their ORM class is "
        f"not in _LINK_CLASSES: {uncovered}. Add the class (and check it at its "
        "write site), or exempt the table with a reason."
    )
    stale = sorted(t for t in _EXEMPT_TABLES if t not in tables)
    assert not stale, f"_EXEMPT_TABLES names tables that no longer qualify: {stale}"
