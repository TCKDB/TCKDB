"""Disposable-database contract for ``d7a3f1b9c284`` (ADR 0021, phase P2).

The revision creates ``composite_scheme``, its term and input tables and the
``level_of_theory_composite`` binding, and binds the existing levels of theory
whose method is a catalogued named composite method. Seeded at its parent:

=====================  =====================================================
seed row               what it pins
=====================  =====================================================
``CBS-QB3``            bound; its scheme states B3LYP/CBSB7 and 0.99
``cbsqb3_stale``       Arkane's spelling left un-re-hashed beside it (a
                       duplicate): bound to the same scheme
``G4(MP2)``            bound to another scheme; internal levels created
``W1U``                bound; the catalogue states no internal level
``merged``             a ``CBS-QB3`` row merged into ``kept``: never bound
``kept``               the row it was merged into: bound
``B3LYP`` / paraskevas  not catalogued: left alone
``B3LYP/CBSB7``        already there but itself merged into ``internal_kept``:
                       the scheme's internal level is the row it was merged
                       into, never the merged row
=====================  =====================================================

No ``lot_hash`` or ``public_ref`` of a seeded row changes. Downgrade drops the
tables and enums; upgrading again binds the same levels to the same schemes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.services.calculation_resolution import _level_of_theory_hash, resolve_level_of_theory_ref
from app.services.composite_scheme_resolution import named_method_definition_hash
from app.services.public_refs import make_content_ref
from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness

_MIGRATION = revision_under_test("d7a3f1b9c284")

_NEW_TABLES = (
    "composite_scheme",
    "composite_scheme_term",
    "composite_scheme_term_input",
    "level_of_theory_composite",
)
_NEW_ENUMS = (
    "composite_scheme_kind",
    "composite_term_operation",
    "energy_component_kind",
    "composite_extrapolation_formula",
    "composite_input_slot",
    "composite_binding_source",
)

#: key -> (method, basis, hash given at seed). ``None`` hash = the application's.
_SEED: dict[str, tuple[str, str | None, str | None]] = {
    "cbs_qb3": ("CBS-QB3", None, None),
    "cbsqb3_stale": ("cbsqb3", None, "f" * 64),
    "g4mp2": ("G4(MP2)", None, None),
    "w1u": ("W1U", None, None),
    "merged": ("CBS-QB3", "zz-merged", None),
    "kept": ("cbs-qb3", "zz-kept", None),
    "b3lyp": ("B3LYP", "def2-tzvp", None),
    "paraskevas": ("cbs-qb3-paraskevas", None, None),
    "internal_b3lyp_cbsb7": ("B3LYP", "CBSB7", None),
    "internal_kept": ("B3LYP", "cbsb7-kept", None),
}
_BOUND = {"cbs_qb3", "cbsqb3_stale", "g4mp2", "w1u", "kept"}
_UNBOUND = {"merged", "b3lyp", "paraskevas", "internal_b3lyp_cbsb7", "internal_kept"}


@pytest.fixture
def harness():
    created = _Harness("composite_scheme")
    yield created
    created.close()


def _seed(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
    for key, (method, basis, stale) in _SEED.items():
        lot_hash = stale or _level_of_theory_hash(LevelOfTheoryRef(method=method, basis=basis))
        ids[key] = conn.scalar(
            text(
                "INSERT INTO level_of_theory (method, basis, lot_hash, public_ref) "
                "VALUES (:m, :b, :h, :r) RETURNING id"
            ),
            {"m": method, "b": basis, "h": lot_hash, "r": make_content_ref("lot", f"{key}:{lot_hash}")},
        )
    conn.execute(
        text("INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:a, :b)"),
        {"a": ids["merged"], "b": ids["kept"]},
    )
    conn.execute(
        text("INSERT INTO level_of_theory_merge (merged_lot_id, into_lot_id) VALUES (:a, :b)"),
        {"a": ids["internal_b3lyp_cbsb7"], "b": ids["internal_kept"]},
    )
    return ids


def _levels(engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, public_ref, lot_hash, method, basis FROM level_of_theory")).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _bindings(engine) -> dict[int, tuple[str, str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.level_of_theory_id, s.name, s.definition_hash, c.binding_source::text "
                "FROM level_of_theory_composite c JOIN composite_scheme s ON s.id = c.scheme_id"
            )
        ).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _exists(engine, kind: str, name: str) -> bool:
    catalog = {"table": "pg_class", "type": "pg_type"}[kind]
    column = "relname" if kind == "table" else "typname"
    with engine.connect() as conn:
        return bool(conn.scalar(text(f"SELECT count(*) FROM {catalog} WHERE {column} = :n"), {"n": name}))


def _alembic_check(harness) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "check"],
        cwd=harness.root,
        env=harness.env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_upgrade_binds_catalogued_levels_and_leaves_the_rest(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    before = _levels(harness.engine)

    completed = harness.run("upgrade", _MIGRATION.revision)

    after = _levels(harness.engine)
    # No hash, ref or name of a level that was already there moves.
    for row_id, row in before.items():
        assert after[row_id] == row, row_id

    bindings = _bindings(harness.engine)
    assert set(bindings) == {ids[k] for k in _BOUND}
    assert not ({ids[k] for k in _UNBOUND} & set(bindings))
    expected = {
        "cbs_qb3": ("CBS-QB3", "cbs-qb3"),
        "cbsqb3_stale": ("CBS-QB3", "cbs-qb3"),
        "kept": ("CBS-QB3", "cbs-qb3"),
        "g4mp2": ("G4(MP2)", "g4mp2"),
        "w1u": ("W1U", "w1u"),
    }
    for key, (name, method_key) in expected.items():
        assert bindings[ids[key]] == (
            name,
            named_method_definition_hash(method_key),
            "named_method_catalogue",
        ), key
    assert "5 level(s) of theory bound to a named-method scheme, 3 scheme(s) created" in completed.stdout

    with harness.engine.connect() as conn:
        schemes = conn.execute(
            text(
                "SELECT s.name, s.kind::text, s.public_ref, s.recipe_zpe_scale_factor, s.source_literature_id, "
                "g.public_ref, g.method, g.basis, f.method, f.basis "
                "FROM composite_scheme s "
                "LEFT JOIN level_of_theory g ON g.id = s.geometry_level_of_theory_id "
                "LEFT JOIN level_of_theory f ON f.id = s.frequency_level_of_theory_id ORDER BY s.name"
            )
        ).all()
        terms = conn.scalar(text("SELECT count(*) FROM composite_scheme_term"))
        inputs = conn.scalar(text("SELECT count(*) FROM composite_scheme_term_input"))
    by_name = {row[0]: row for row in schemes}
    assert set(by_name) == {"CBS-QB3", "G4(MP2)", "W1U"}
    assert terms == 0 and inputs == 0
    cbs = by_name["CBS-QB3"]
    assert cbs[1] == "named_method"
    assert cbs[2] == make_content_ref(
        "csch", f"csch:definition_hash={named_method_definition_hash('cbs-qb3')}"
    )
    assert cbs[3] == 0.99 and cbs[4] is None
    # The B3LYP/CBSB7 row that was already there was merged into another: the
    # scheme names the row it was merged into, never the merged one.
    assert cbs[5] == before[ids["internal_kept"]][0]
    assert cbs[5] != before[ids["internal_b3lyp_cbsb7"]][0]
    assert (cbs[6], cbs[7], cbs[8], cbs[9]) == ("B3LYP", "cbsb7-kept", "B3LYP", "cbsb7-kept")
    assert by_name["G4(MP2)"][3] == 0.9854 and by_name["G4(MP2)"][7] == "6-31G(2df,p)"
    # The catalogue states nothing for W1U: every recipe column is NULL.
    assert by_name["W1U"][3:] == (None, None, None, None, None, None, None)
    # Internal levels created here are ordinary: only G4(MP2)'s (B3LYP/6-31G(2df,p)) is new.
    created = set(after) - set(before)
    assert {after[i][2:] for i in created} == {("B3LYP", "6-31G(2df,p)")}
    for i in created:
        assert after[i][1] == _level_of_theory_hash(LevelOfTheoryRef(method="B3LYP", basis="6-31G(2df,p)"))
        assert i not in bindings

    # The upload path finds the very rows the backfill used: it creates nothing.
    with Session(harness.engine) as session:
        for method, basis in (("CBS-QB3", None), ("B3LYP", "CBSB7"), ("B3LYP", "6-31G(2df,p)"), ("G4(MP2)", None)):
            resolve_level_of_theory_ref(session, LevelOfTheoryRef(method=method, basis=basis))
        session.rollback()
    assert _levels(harness.engine) == after
    assert _bindings(harness.engine) == bindings

    checked = _alembic_check(harness)
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr


def test_downgrade_drops_the_tables_and_enums_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
    before = _levels(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)
    first_bindings = _bindings(harness.engine)
    with harness.engine.connect() as conn:
        first_refs = conn.execute(text("SELECT definition_hash, public_ref FROM composite_scheme")).all()
    first_levels = _levels(harness.engine)

    completed = harness.run("downgrade", _MIGRATION.parent)
    for table in _NEW_TABLES:
        assert not _exists(harness.engine, "table", table), table
    for enum in _NEW_ENUMS:
        assert not _exists(harness.engine, "type", enum), enum
    # The seeded levels are untouched; the internal level the upgrade created stays.
    after_down = _levels(harness.engine)
    for row_id, row in before.items():
        assert after_down[row_id] == row
    assert after_down == first_levels
    assert "dropped 5 binding(s) and 3 scheme(s)" in completed.stdout

    harness.run("upgrade", _MIGRATION.revision)
    assert _bindings(harness.engine) == first_bindings
    assert _levels(harness.engine) == first_levels
    with harness.engine.connect() as conn:
        assert conn.execute(text("SELECT definition_hash, public_ref FROM composite_scheme")).all() == first_refs


def test_upgrade_on_an_empty_database_writes_nothing(harness):
    harness.run("upgrade", _MIGRATION.parent)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert "0 level(s) of theory bound to a named-method scheme, 0 scheme(s) created" in completed.stdout
    assert _levels(harness.engine) == {}
    assert _bindings(harness.engine) == {}


def test_levels_with_no_catalogued_method_are_left_alone(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        for method, basis in (("B3LYP", "def2-tzvp"), ("wb97xd", "def2-tzvp"), ("W2-2", None)):
            h = _level_of_theory_hash(LevelOfTheoryRef(method=method, basis=basis))
            conn.execute(
                text("INSERT INTO level_of_theory (method, basis, lot_hash, public_ref) VALUES (:m, :b, :h, :r)"),
                {"m": method, "b": basis, "h": h, "r": make_content_ref("lot", h)},
            )
    before = _levels(harness.engine)
    completed = harness.run("upgrade", _MIGRATION.revision)
    assert _levels(harness.engine) == before
    assert _bindings(harness.engine) == {}
    assert "0 level(s) of theory bound" in completed.stdout


def _run_merge_script(harness):
    out = None
    for args in ((), ("--commit",)):
        out = subprocess.run(
            [
                "conda", "run", "-n", "tckdb_env", "python",
                str(Path("scripts") / "ops" / "merge_duplicate_levels_of_theory.py"), *args,
            ],
            cwd=harness.root, env=harness.env, capture_output=True, text=True, check=False,
        )
        assert out.returncode == 0, out.stderr[-3000:]
    return out


def test_merge_script_gives_the_holder_the_duplicates_binding_when_only_the_duplicate_is_bound(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text("DELETE FROM level_of_theory_composite WHERE level_of_theory_id = :h"), {"h": ids["cbs_qb3"]}
        )
    duplicate_binding = _bindings(harness.engine)[ids["cbsqb3_stale"]]

    merged = _run_merge_script(harness)

    with harness.engine.connect() as conn:
        pairs = set(conn.execute(text("SELECT merged_lot_id, into_lot_id FROM level_of_theory_merge")).all())
    assert (ids["cbsqb3_stale"], ids["cbs_qb3"]) in pairs, merged.stdout[-3000:]
    bindings = _bindings(harness.engine)
    assert ids["cbsqb3_stale"] not in bindings
    assert bindings[ids["cbs_qb3"]] == duplicate_binding


def test_merge_script_blocks_a_group_whose_levels_are_bound_to_different_schemes(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE level_of_theory_composite SET scheme_id = "
                "(SELECT id FROM composite_scheme WHERE name = 'G4(MP2)') WHERE level_of_theory_id = :h"
            ),
            {"h": ids["cbs_qb3"]},
        )
    before = _bindings(harness.engine)

    merged = _run_merge_script(harness)

    with harness.engine.connect() as conn:
        pairs = set(conn.execute(text("SELECT merged_lot_id, into_lot_id FROM level_of_theory_merge")).all())
    assert (ids["cbsqb3_stale"], ids["cbs_qb3"]) not in pairs
    assert "BLOCKED" in merged.stdout and "differs from the kept row" in merged.stdout
    assert _bindings(harness.engine) == before


def test_merge_script_moves_a_duplicates_binding_to_the_holder(harness):
    """The deploy runbook runs the merge script after upgrades; a binding must not block it."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    assert ids["cbs_qb3"] in _bindings(harness.engine) and ids["cbsqb3_stale"] in _bindings(harness.engine)

    for args in ((), ("--commit",)):
        merged = subprocess.run(
            [
                "conda", "run", "-n", "tckdb_env", "python",
                str(Path("scripts") / "ops" / "merge_duplicate_levels_of_theory.py"), *args,
            ],
            cwd=harness.root, env=harness.env, capture_output=True, text=True, check=False,
        )
        assert merged.returncode == 0, merged.stderr[-3000:]
    with harness.engine.connect() as conn:
        pairs = set(conn.execute(text("SELECT merged_lot_id, into_lot_id FROM level_of_theory_merge")).all())
    assert (ids["cbsqb3_stale"], ids["cbs_qb3"]) in pairs, merged.stdout[-3000:]
    bindings = _bindings(harness.engine)
    # The merged duplicate is no longer bound; the holder still is.
    assert ids["cbsqb3_stale"] not in bindings
    assert ids["cbs_qb3"] in bindings
