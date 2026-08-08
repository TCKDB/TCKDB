"""Drift guards for the scientific check register.

The register's whole value is that it cannot quietly stop describing the
code. These tests are what makes that true:

* a registered Python check must resolve to a real callable;
* a registered database constraint or trigger must exist in the live
  PostgreSQL schema, queried from ``pg_constraint`` / ``pg_trigger``
  rather than trusted as a string;
* a declared code must appear verbatim in the source of the module
  defining the enforcing function — so renaming the code string fails
  here even though the declaration sits next to it;
* every file constructing a ``ScientificCheck`` must be a declared
  module, so forgetting to register one is caught rather than silently
  omitted;
* the generated document must be in sync with the declarations.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.scientific_checks import (
    CheckTier,
    ConstantThreshold,
    DatabaseConstraint,
    DesignPosition,
    ProvenanceThreshold,
    PythonCheck,
    ScientificCheck,
)
from app.scientific_checks.declarations import DECLARING_MODULES, register

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
GENERATOR = BACKEND_ROOT / "scripts" / "generate_scientific_check_register.py"
REGISTER_DOC = REPO_ROOT / "docs" / "guides" / "scientific_check_register.md"

REGISTER = register()


def test_register_is_non_empty_and_proportionate() -> None:
    """The register must stay a filter, not a dump of every validator.

    The backend holds roughly 326 Pydantic validators and 225 check
    constraints. If this count approaches that order the inclusion test
    has stopped being applied and every entry's claim is diluted.
    """
    assert 10 <= len(REGISTER) <= 40, (
        f"{len(REGISTER)} entries. Below ~10 the register is not describing "
        "the system; above ~40 the inclusion test ('could this check be wrong "
        "in an interesting way?') has stopped being applied."
    )


def test_every_python_check_resolves_to_real_code() -> None:
    """Each ``PythonCheck`` names a live callable with locatable source."""
    for check in REGISTER:
        for site in check.enforced_by:
            if not isinstance(site, PythonCheck):
                continue
            assert callable(site.func), f"{check.asserts!r}: {site.func!r} is not callable"
            source_file = inspect.getsourcefile(site.func)
            assert source_file is not None, f"{check.asserts!r}: no source file"
            assert Path(source_file).exists()
            # Resolving the location is the guard: a renamed or deleted
            # function cannot be imported by the declaring module at all,
            # so this test never even runs — which is the point.
            assert ":" in site.location


def _source_without_declarations(module) -> str:
    """Module source with every ``ScientificCheck(...)`` literal blanked out.

    Without this the code check is tautological: a declaration sitting in
    the same module as its check would satisfy the assertion by quoting
    itself, and renaming the real code string would go unnoticed.
    """
    source = inspect.getsource(module)
    lines = source.splitlines()
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        callee = value.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        if name != "ScientificCheck":
            continue
        end = node.end_lineno or node.lineno
        for index in range(node.lineno - 1, end):
            lines[index] = ""
    return "\n".join(lines)


def test_every_declared_code_appears_in_the_enforcing_module() -> None:
    """A declared code must be a string the enforcing module actually holds.

    Checked against the module that defines the *function*, with the
    register declarations themselves stripped, so the assertion can only
    be satisfied by the code string the check really uses.
    """
    for check in REGISTER:
        if not check.emitted or not check.codes:
            continue
        python_sites = [s for s in check.enforced_by if isinstance(s, PythonCheck)]
        if not python_sites:
            continue
        sources = []
        for site in python_sites:
            module = inspect.getmodule(site.func)
            assert module is not None
            sources.append(_source_without_declarations(module))
        for code in check.codes:
            # Bare substring: codes are routinely embedded mid-message
            # ("... (species_geometry_composition_mismatch). A deposited
            # structure must ..."), so a quote-delimited match would miss
            # them. The tokens are long and distinctive enough that an
            # accidental match is not a real risk.
            assert any(code in src for src in sources), (
                f"Code {code!r} is declared as emitted for {check.asserts!r}, "
                "but no module defining its enforcing function contains that "
                "literal string outside the register declaration itself. "
                "Either the code was renamed and the register not updated, or "
                "the entry should set emitted=False."
            )


def test_declaring_modules_covers_every_file_that_declares_a_check() -> None:
    """No file may construct a ``ScientificCheck`` without being collected."""
    declared_files = {
        Path(inspect.getsourcefile(module)).resolve()  # type: ignore[arg-type]
        for module in DECLARING_MODULES
    }
    found = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            # --untracked matters: a declaration added in a brand-new file
            # is exactly the case this guard exists for, and plain
            # ``git grep`` only searches tracked files.
            "--untracked",
            "-E",
            r"^[A-Z_]+ = ScientificCheck\(",
            "--",
            "*.py",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    constructing = {
        (REPO_ROOT / line).resolve()
        for line in found.stdout.splitlines()
        if line.strip()
    }
    missing = constructing - declared_files
    assert not missing, (
        "These files construct a ScientificCheck but are not in "
        "DECLARING_MODULES, so their entries are silently absent from the "
        f"register: {sorted(str(p.relative_to(REPO_ROOT)) for p in missing)}"
    )


def test_entries_are_uniquely_addressable() -> None:
    """No two entries share a (group, sort_key) slot, and codes stay local.

    A code may legitimately appear in more than one entry — ADR 0008 has
    ``n_imag_contradicts_minimum`` naming the *finding* while the declared
    stationary-point kind decides the tier — but a code scattered across
    unrelated groups would mean two different claims share one label.
    """
    slots = [(check.group, check.sort_key) for check in REGISTER]
    assert len(slots) == len(set(slots)), "duplicate (group, sort_key) slot"

    groups_by_code: dict[str, set[str]] = {}
    for check in REGISTER:
        for code in check.codes:
            groups_by_code.setdefault(code, set()).add(check.group)
    scattered = {code: groups for code, groups in groups_by_code.items() if len(groups) > 1}
    assert not scattered, f"codes shared across unrelated groups: {scattered}"


def test_every_entry_states_its_tier_rationale_and_enforcement() -> None:
    """Membership is the claim, so no entry may be a stub."""
    for check in REGISTER:
        assert check.enforced_by, f"{check.asserts!r} names no enforcement site"
        assert len(check.asserts) > 30, f"{check.asserts!r} is not a sentence"
        assert len(check.tier_rationale) > 60, (
            f"{check.asserts!r} has no real ADR 0008 tier justification"
        )
        assert check.adr, f"{check.asserts!r} cites no ADR"
        if check.tier is CheckTier.block:
            assert check.escape_hatch is not None, (
                f"{check.asserts!r} blocks but records no escape hatch. If "
                "there genuinely is none, say so explicitly — a blocking "
                "check with no documented door is exactly what a referee will "
                "probe."
            )


def test_generated_document_is_in_sync() -> None:
    """The committed register must match what the declarations render."""
    assert REGISTER_DOC.exists(), f"{REGISTER_DOC} has not been generated"
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"{REGISTER_DOC} is out of date with the declarations.\n"
        f"{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Database-level entries, verified against live schema metadata
# ---------------------------------------------------------------------------


def _database_constraints() -> list[tuple[ScientificCheck, DatabaseConstraint]]:
    return [
        (check, site)
        for check in REGISTER
        for site in check.enforced_by
        if isinstance(site, DatabaseConstraint)
    ]


def test_there_are_database_entries_to_verify() -> None:
    """Guard the guard: an empty list would make the next test vacuous."""
    assert _database_constraints(), (
        "No DatabaseConstraint entries. Either the register lost its "
        "database-level checks or the collection broke; either way the "
        "existence check below would pass vacuously."
    )


@pytest.mark.parametrize(
    ("check", "site"),
    _database_constraints(),
    ids=lambda value: value.name if isinstance(value, DatabaseConstraint) else "",
)
def test_database_object_exists_in_live_schema(
    check: ScientificCheck, site: DatabaseConstraint, db_session
) -> None:
    """A registered constraint or trigger must exist in PostgreSQL.

    Queried from the catalog, not from ``__table_args__``: the ORM holds
    the short name that ``NAMING_CONVENTION`` expands, so only the
    database knows what the object is really called.
    """
    if site.kind == "trigger":
        found = db_session.execute(
            text(
                "SELECT count(*) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND t.tgname = :name AND c.relname = :table"
            ),
            {"name": site.name, "table": site.table},
        ).scalar_one()
        assert found == 1, (
            f"Trigger {site.name!r} on {site.table!r} is registered as "
            f"enforcing {check.asserts!r}, but no such trigger exists in the "
            "live schema."
        )
        return

    found = db_session.execute(
        text(
            "SELECT count(*) FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "WHERE con.conname = :name AND c.relname = :table"
        ),
        {"name": site.name, "table": site.table},
    ).scalar_one()
    assert found == 1, (
        f"Constraint {site.name!r} on {site.table!r} is registered as "
        f"enforcing {check.asserts!r}, but no such constraint exists in the "
        "live schema. Note the register must carry the *expanded* name "
        "PostgreSQL holds, not the short form in __table_args__."
    )


def _provenance_thresholds() -> list[tuple[ScientificCheck, ProvenanceThreshold]]:
    return [
        (check, threshold)
        for check in REGISTER
        for threshold in check.thresholds
        if isinstance(threshold, ProvenanceThreshold)
    ]


def test_there_are_provenance_thresholds_to_verify() -> None:
    """Guard the guard: an empty list makes the vocab check below vacuous."""
    assert _provenance_thresholds(), (
        "No ProvenanceThreshold entries. Either ADR 0012's tau lost its "
        "declaration or collection broke; either way the parameter-key "
        "existence check would pass vacuously."
    )


@pytest.mark.parametrize(
    ("check", "threshold"),
    _provenance_thresholds(),
    ids=lambda value: value.name if isinstance(value, ProvenanceThreshold) else "",
)
def test_provenance_threshold_keys_exist_in_the_parameter_vocabulary(
    check: ScientificCheck, threshold: ProvenanceThreshold, db_session
) -> None:
    """Every key a provenance threshold reads must be a real vocab key.

    This is the guard that makes a provenance-derived threshold as
    checkable as a constant. ``calculation_parameter.canonical_key`` is
    FK-constrained against ``calculation_parameter_vocab``, so a key that
    was renamed, or never seeded, can never be written — and the
    threshold would then resolve to its fallback forever while the
    register went on claiming it keys on that provenance. Queried from
    the live table rather than from the parser's mapping, because only
    the database decides what a parameter row may reference.
    """
    for key in threshold.parameter_keys:
        found = db_session.execute(
            text(
                "SELECT count(*) FROM calculation_parameter_vocab "
                "WHERE canonical_key = :key"
            ),
            {"key": key},
        ).scalar_one()
        assert found == 1, (
            f"{check.asserts!r} declares that its {threshold.name!r} threshold "
            f"is resolved from the recorded parameter {key!r}, but no such "
            f"canonical key exists in calculation_parameter_vocab. No "
            f"calculation_parameter row can reference it, so the threshold "
            f"would silently resolve to its fallback on every record."
        )


def test_provenance_thresholds_resolve_and_state_their_fallback() -> None:
    """A provenance threshold must be live code with a documented fallback."""
    for check, threshold in _provenance_thresholds():
        assert callable(threshold.resolver), (
            f"{check.asserts!r}: {threshold.resolver!r} is not callable"
        )
        assert ":" in threshold.location
        assert threshold.parameter_keys, (
            f"{check.asserts!r}: a provenance threshold that reads no recorded "
            "parameter is a constant with extra steps"
        )
        assert threshold.values, (
            f"{check.asserts!r}: no per-protocol values, so a reader cannot "
            "see what the threshold ever resolves to"
        )
        assert len(threshold.fallback) > 60, (
            f"{check.asserts!r}: a provenance-derived threshold must say what "
            "happens when the provenance is missing. That is the case a "
            "referee will probe, and silence there is worse than a constant."
        )


def test_constant_thresholds_justify_their_number() -> None:
    """A constant in the register must say why it is that number."""
    for check in REGISTER:
        for threshold in check.thresholds:
            if not isinstance(threshold, ConstantThreshold):
                continue
            assert threshold.unit, f"{check.asserts!r}: {threshold.name} has no unit"
            assert len(threshold.rationale) > 40, (
                f"{check.asserts!r}: {threshold.name} states a number with no "
                "justification, which is the shape that invites a referee to "
                "ask where it came from."
            )


def test_design_positions_are_prose_not_placeholders() -> None:
    """A ``DesignPosition`` cannot be drift-guarded, so it must be specific."""
    for check in REGISTER:
        for site in check.enforced_by:
            if isinstance(site, DesignPosition):
                assert len(site.where) > 40, (
                    f"{check.asserts!r} names a design position too vague to "
                    "be checkable by a reader: " + site.where
                )
