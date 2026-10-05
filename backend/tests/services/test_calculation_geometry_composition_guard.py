"""Every site that links a geometry to a calculation must check its composition.

The rule in ``app.services.calculation_geometry_composition`` is only as good
as its coverage, and coverage here is not a property of one function: geometry
rows reach a calculation from many places across several modules, which is
exactly the shape that let the gap exist in the first place --
``attach_calculation_output_geometries`` was never the only writer, and a reader
who found the check there would reasonably conclude the seam was covered.

This guard makes the omission loud instead of silent. It parses the source for
constructions of the geometry-bearing ORM classes and requires **each
construction** (not each enclosing function) to be preceded by a call to
``assert_calculation_geometry_composition`` on the same ``geometry_id``; see
``_geometry_link_guard`` for what that does and does not prove. Per function was
not enough: ``_persist_irc_result`` called the check, but only on the
forward/reverse branch, and the TS-marker point was written outside it (#680).

If a future site legitimately cannot check -- it links a geometry before the
calculation's owner is known, say -- the honest fix is to make that explicit
here with the reason, not to widen the pattern.
"""

from __future__ import annotations

from tests.services._geometry_link_guard import (
    LINK_CLASSES,
    construction_sites,
    unchecked_constructions,
)

_CHECKER = "assert_calculation_geometry_composition"

#: The construction sites, as ``module::function``. Listed so that a *removed*
#: check is as visible as an added-and-unchecked one: if this set shrinks,
#: someone deleted a write path and should say so.
_EXPECTED_SITES = {
    "services/calculation_resolution.py::_persist_irc_result",
    "services/calculation_resolution.py::_persist_path_search_result",
    "services/calculation_resolution.py::attach_calculation_input_geometries",
    "services/calculation_resolution.py::attach_calculation_output_geometries",
    "services/calculation_resolution.py::persist_calculation_result",
    "services/calculation_scan_resolution.py::persist_calculation_scan",
    "services/hessian_extraction.py::_insert",
    "services/input_geometry_extraction.py::_mint_and_link_extracted_geometry",
    "services/structure_determination_resolution.py::persist_structure_determinations",
    "services/transition_state_resolution.py::persist_ts_calculations",
    "workflows/network_pdep.py::_persist_calculation",
}


def test_every_geometry_link_construction_is_preceded_by_the_composition_check() -> None:
    assert construction_sites(), "found no geometry-link write sites at all -- the AST walk broke"

    unchecked = unchecked_constructions(_CHECKER)
    assert not unchecked, (
        "These constructions attach a geometry to a calculation (an input/output "
        "link, a Hessian, a scan, IRC or path-search point) with no preceding "
        f"{_CHECKER} on the same geometry_id: {unchecked}. A geometry linked to "
        "a calculation must be made of the atoms of the subject that calculation "
        "is filed under; see backend/docs/specs/calculation_geometry_composition.md."
    )


def test_the_known_write_sites_have_not_silently_disappeared() -> None:
    """A shrinking set means a write path was removed, which is also news."""

    sites = set(construction_sites())
    missing = sorted(_EXPECTED_SITES - sites)
    assert not missing, (
        f"These geometry-link write sites no longer exist: {missing}. If that "
        "is intended, update _EXPECTED_SITES and say why in the commit."
    )


def test_an_unchecked_site_in_a_function_that_checks_elsewhere_is_caught() -> None:
    """The guard's own mutation: the #680 IRC shape, in miniature.

    A function that calls the checker inside one branch and builds a row outside
    it passes a per-function guard and fails this one.
    """

    import ast

    from tests.services import _geometry_link_guard as g

    source = (
        "def persist(session, calc, points):\n"
        "    for point in points:\n"
        "        session.add(CalculationIRCPoint(geometry_id=point.gid))\n"
        "        if point.role:\n"
        f"            {_CHECKER}(session, calc=calc, geometry_id=point.gid)\n"
        "            session.add(CalculationOutputGeometry(geometry_id=point.gid))\n"
    )
    tree = ast.parse(source)
    parents = g._parents(tree)
    verdicts = {
        node.func.id: g._checked_before(node, parents, _CHECKER)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in LINK_CLASSES
    }
    assert verdicts == {"CalculationIRCPoint": False, "CalculationOutputGeometry": True}


#: Tables with a foreign key to ``geometry`` AND a foreign key to ``calculation``
#: (by foreign-key target, whatever the column is called: ``calculation_id``,
#: ``source_calculation_id``, ``reconstruction_calculation_id``), that could
#: attach a geometry to a calculation but deliberately do not, with the reason.
_EXEMPT_TABLES = {
    "calc_geometry_validation": (
        "records a comparison between geometries that are ALREADY linked to the "
        "calculation (it reads them back from the input/output links), so it "
        "attaches nothing new"
    ),
    "structure_evidence_finding": (
        "a finding names exactly one subject, a geometry OR a calculation OR a determination "
        "(ck_structure_evidence_finding_subject_matches_scope makes the columns mutually exclusive), so it never links "
        "a geometry to a calculation, and nothing writes one yet"
    ),
    "transition_state_validation_evidence": (
        "its saddle-point geometry (transition_state_geometry_id) is the TS "
        "calculation's own input geometry, which was composition- and "
        "isotope-checked when it was linked; the row names it, it does not "
        "attach a new one"
    ),
}


def _geometry_bearing_calculation_tables() -> set[str]:
    from app.db import models  # noqa: F401  (registers every table)
    from app.db.base import Base

    found: set[str] = set()
    for table in Base.metadata.tables.values():
        targets = {fk.column.table.name for fk in table.foreign_keys}
        if "calculation" in targets and "geometry" in targets:
            found.add(table.name)
    return found


def test_every_geometry_bearing_calculation_table_is_covered_or_exempt() -> None:
    """A new geometry-bearing child of ``calculation`` cannot slip past the checks.

    Derived from the schema by foreign-key target, not from the list above and
    not from a column name: a future table with *any* foreign key to
    ``calculation`` and one to ``geometry`` fails here until its class is added
    to the guard's link classes (and so to the structural test) or it is
    exempted with a reason.
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
        if table not in _EXEMPT_TABLES and class_for_table[table] not in LINK_CLASSES
    )
    assert not uncovered, (
        f"These tables link a geometry to a calculation but their ORM class is "
        f"not in the guard's LINK_CLASSES: {uncovered}. Add the class (and check "
        "it at its write site), or exempt the table with a reason."
    )
    stale = sorted(t for t in _EXEMPT_TABLES if t not in tables)
    assert not stale, f"_EXEMPT_TABLES names tables that no longer qualify: {stale}"
