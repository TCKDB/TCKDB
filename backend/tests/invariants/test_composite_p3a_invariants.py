"""Structural guards for the composite calculation type (ADR 0021, P3a).

Four "every place must do X" rules the phase adds. A rule that lives only in the
call sites that existed when it was written is the house defect: the next call
site silently does without it. Each guard counts what it found before it
asserts anything about it, so a scan that stopped matching cannot pass empty.

1. Every table that holds a calculation's own result rows is **archived** and
   **frozen once the calculation is accepted** (``calc_sp_result`` is; so must
   ``calc_composite_result`` be).
2. Every caller of ``assert_role_consistency`` says where the warn-tier findings
   about composite links go, and names its ``sp``-and-``composite`` refusal code.
3. Every module that reports the converged-opt warning also reports the
   named-composite-deposit warning (they are two findings about one deposit).
4. Every reader that calls ``derive_levels`` states its ``composites`` (R1's
   energy priority), and the readers with a session load the recipe levels.
"""

from __future__ import annotations

import ast
import runpy
from pathlib import Path

from app.db import models as _models  # noqa: F401
from app.db.base import Base
from app.services.archive.registry import INCLUDED_TABLES

_BACKEND = Path(__file__).resolve().parents[2]
_APP = _BACKEND / "app"
_VERSIONS = _BACKEND / "alembic" / "versions"

#: The revisions that register accepted-science child guards, in the order they
#: landed. ``d4e9b1c7a253`` takes one entry back out of ``c6f2a9d4e7b1``'s.
_GUARD_REVISIONS = (
    "b6c1f4a8e703_freeze_declared_atom_maps.py",
    "a1f6c3e9b527_freeze_evidence_under_accepted_roots.py",
    "a7d3f1c95e28_ts_evidence_kinds_and_unstated_irc_direction.py",
    "e5b2d8a4c613_sp_energy_components_and_core_treatment.py",
    "f3b7d2a9c514_composite_calculation_type_and_result.py",
)


def _guarded_tables() -> set[str]:
    root = runpy.run_path(str(_VERSIONS / "c6f2a9d4e7b1_enforce_accepted_science_immutability.py"))
    removed = set(runpy.run_path(str(_VERSIONS / "d4e9b1c7a253_scf_stability_provenance_is_not_ownership.py"))["_REMOVED_CHILDREN"])
    guarded = {t for (t, r, c) in root["_DIRECT_CHILDREN"] if (t, r, c) not in removed}
    guarded |= {item[0] for item in root["_VIA_CHILDREN"]}
    for name in _GUARD_REVISIONS:
        extension = runpy.run_path(str(_VERSIONS / name))
        guarded |= {t for t, _, _ in extension["_DIRECT_CHILDREN"]}
        guarded |= {item[0] for item in extension["_VIA_CHILDREN"]}
    return guarded


def _calculation_child_tables() -> list[str]:
    """Tables whose primary key includes a foreign key to ``calculation.id``."""
    found = []
    for name, table in Base.metadata.tables.items():
        if name == "calculation":
            continue
        primary_key = {column.name for column in table.primary_key.columns}
        if any(
            column.name in primary_key
            and any(fk.column.table.name == "calculation" and fk.column.name == "id" for fk in column.foreign_keys)
            for column in table.columns
        ):
            found.append(name)
    return sorted(found)


def test_every_calculation_result_table_is_archived_and_frozen_once_accepted() -> None:
    tables = _calculation_child_tables()
    # The scan found the corpus: the result tables known before this phase, and this phase's.
    assert len(tables) >= 25, tables
    assert {"calc_sp_result", "calc_opt_result", "calc_composite_result", "calc_composite_term"} <= set(tables)

    guarded = _guarded_tables()
    not_archived = [t for t in tables if t not in INCLUDED_TABLES]
    not_frozen = [t for t in tables if t not in guarded]
    assert not not_archived, f"calculation child tables missing from the archive registry: {not_archived}"
    assert not not_frozen, f"calculation child tables with no accepted-science guard: {not_frozen}"


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _calls(name: str) -> list[tuple[Path, ast.Call]]:
    out: list[tuple[Path, ast.Call]] = []
    for path in sorted(_APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if called == name:
                out.append((path, node))
    return out


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    return next((kw for kw in call.keywords if kw.arg == name), None)


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def test_every_role_consistency_caller_reports_its_warnings_and_names_its_composite_code() -> None:
    sites = _calls("assert_role_consistency")
    # thermo (standalone), statmech (standalone), computed-species x2, computed-reaction x2.
    assert len(sites) >= 6, [str(p) for p, _ in sites]
    for path, call in sites:
        warnings = _keyword(call, "warnings")
        assert warnings is not None and not _is_none(warnings.value), (
            f"{path.relative_to(_BACKEND)}:{call.lineno} does not pass its warning list to "
            "assert_role_consistency; the legacy-role and recipe-frequency warnings would be dropped"
        )
        code = _keyword(call, "sp_and_composite_code")
        assert code is not None, f"{path.relative_to(_BACKEND)}:{call.lineno} names no sp-and-composite code"


def test_every_module_that_reports_the_converged_opt_warning_reports_the_named_composite_one() -> None:
    converged = {path for path, _ in _calls("collect_converged_opt_energy_warnings")}
    composite = {path for path, _ in _calls("collect_named_composite_deposit_warnings")}
    # conformer, transition state, computed-species, computed-reaction (and the definition's own module).
    workflows = {p for p in converged if p.parent.name == "workflows"}
    assert len(workflows) >= 4, sorted(p.name for p in workflows)
    missing = sorted(p.name for p in workflows - composite)
    assert not missing, f"report converged-opt warnings but not named-composite deposits: {missing}"


def test_every_levels_reader_states_its_composites_and_the_session_ones_load_the_recipe() -> None:
    sites = [(p, c) for p, c in _calls("derive_levels") if p.name != "calculation_levels.py"]
    assert len(sites) >= 3, [str(p) for p, _ in sites]
    for path, call in sites:
        assert _keyword(call, "composites") is not None, (
            f"{path.relative_to(_BACKEND)}:{call.lineno} calls derive_levels without composites=; "
            "a linked composite energy would be invisible to R1 there"
        )
    # Thermo and statmech read linked calculations from the database: they must load
    # the composite recipe levels, or a composite record loses its geometry/frequency source.
    for name in ("thermo.py", "statmech.py"):
        path = _APP / "services" / "scientific_read" / name
        assert "composite_role_facts" in path.read_text(), f"{name} never loads composite_role_facts"


def test_every_workflow_that_persists_a_calculation_reports_named_composite_deposits() -> None:
    """Inline calculations too: thermo, statmech, transport and network persist their own."""
    persisting = set()
    for name in ("resolve_and_persist_calculation_with_results", "persist_additional_calculations", "_persist_calculation"):
        persisting |= {path for path, _ in _calls(name) if path.parent.name == "workflows"}
    reporting = {path for path, _ in _calls("collect_named_composite_deposit_warnings")}
    # conformer, thermo, statmech, transport, network, computed-species, computed-reaction, transition-state.
    assert len(persisting) >= 7, sorted(p.name for p in persisting)
    missing = sorted(p.name for p in persisting - reporting)
    assert not missing, f"persist calculations but never report named-composite deposits: {missing}"
