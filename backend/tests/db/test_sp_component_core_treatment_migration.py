"""Disposable-database contract for ``e5b2d8a4c613`` (ADR 0021, phase P4).

The revision adds ``level_of_theory.core_treatment`` and the
``calc_sp_energy_component`` table. Seeded at its parent, with levels of theory
that already carry hashes, a stale hash, a merge and a calculation:

* **No ``lot_hash`` or ``public_ref`` moves.** A NULL core treatment adds no key to
  the hash, so upgrading re-keys nothing; every seeded row still equals the
  application's hash for the same level.
* **Round trip.** Upgrade, downgrade and upgrade again converge on the same data,
  and ``alembic check`` finds no drift against the models.
* **Downgrade refuses to lose data.** It stops while a level states a core
  treatment or a component row exists, and drops cleanly once neither does.
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.services.calculation_resolution import _level_of_theory_hash
from app.services.public_refs import make_content_ref
from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness

_MIGRATION = revision_under_test("e5b2d8a4c613")

#: key -> (method, basis, hash given at seed). ``None`` = the application's.
_SEED: dict[str, tuple[str, str | None, str | None]] = {
    "wb97xd": ("wb97xd", "def2tzvp", None),
    "ccsdt": ("CCSD(T)", "cc-pCVTZ", None),
    "f12": ("CCSD(T)-F12", "cc-pVTZ-F12", None),
    "cbs": ("CBS-QB3", None, None),
    "stale": ("b3lyp", "Def2TZVP", "e" * 64),  # a duplicate left un-re-hashed, as after #574
}


@pytest.fixture
def harness():
    created = _Harness("sp_components")
    yield created
    created.close()


def _seed(conn) -> dict[str, int]:
    species_id = conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), 'O', 'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0, 1, "
            "CAST('unspecified' AS stereo_kind)) RETURNING id"
        )
    )
    entry_id = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id})
    ids: dict[str, int] = {}
    for key, (method, basis, stale) in _SEED.items():
        lot_hash = stale or _level_of_theory_hash(LevelOfTheoryRef(method=method, basis=basis))
        ids[key] = conn.scalar(
            text(
                "INSERT INTO level_of_theory (method, basis, lot_hash, public_ref) "
                "VALUES (:m, :b, :h, :r) RETURNING id"
            ),
            {"m": method, "b": basis, "h": lot_hash, "r": make_content_ref("lot", f"lot_hash:{lot_hash}")},
        )
    ids["calc"] = conn.scalar(
        text(
            "INSERT INTO calculation (type, species_entry_id, lot_id) "
            "VALUES (CAST('sp' AS calc_type), :e, :l) RETURNING id"
        ),
        {"e": entry_id, "l": ids["ccsdt"]},
    )
    return ids


def _levels(engine) -> dict[int, tuple]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, public_ref, lot_hash, method, basis FROM level_of_theory")).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _exists(engine, kind: str, name: str) -> bool:
    catalog = {"table": "pg_class", "type": "pg_type"}[kind]
    column = "relname" if kind == "table" else "typname"
    with engine.connect() as conn:
        return bool(conn.scalar(text(f"SELECT count(*) FROM {catalog} WHERE {column} = :n"), {"n": name}))


def _has_column(engine, table: str, column: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            )
        )


def _triggers(engine) -> set[str]:
    with engine.connect() as conn:
        return {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE c.relname = 'calc_sp_energy_component' AND NOT t.tgisinternal"
                )
            )
        }


def _alembic_check(harness) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "check"],
        cwd=harness.root,
        env=harness.env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_expecting_failure(harness, direction: str, revision: str) -> subprocess.CompletedProcess:
    if harness.engine is not None:
        harness.engine.dispose()
    return subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
        cwd=harness.root,
        env=harness.env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_upgrade_adds_the_column_and_table_and_rekeys_nothing(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    before = _levels(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)

    after = _levels(harness.engine)
    assert after == before  # no hash, ref, method or basis moved, and no row appeared or vanished
    # Every seeded hash still equals the application's for that level (except the
    # deliberately stale row, which the revision must leave exactly as it was).
    for key, (method, basis, stale) in _SEED.items():
        expected = stale or _level_of_theory_hash(LevelOfTheoryRef(method=method, basis=basis))
        assert after[ids[key]][1] == expected, key
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM level_of_theory WHERE core_treatment IS NOT NULL")) == 0
        assert conn.scalar(text("SELECT count(*) FROM calc_sp_energy_component")) == 0
    assert _has_column(harness.engine, "level_of_theory", "core_treatment")
    assert _exists(harness.engine, "table", "calc_sp_energy_component")
    assert _exists(harness.engine, "type", "core_treatment")
    assert _triggers(harness.engine) == {
        "trg_as_child_calc_sp_energy_component",
        "trg_as_truncate_calc_sp_energy_component",
    }

    checked = _alembic_check(harness)
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr


def test_upgrade_downgrade_upgrade_converges_on_the_same_data(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
    before = _levels(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)
    harness.run("downgrade", _MIGRATION.parent)
    assert not _has_column(harness.engine, "level_of_theory", "core_treatment")
    assert not _exists(harness.engine, "table", "calc_sp_energy_component")
    assert not _exists(harness.engine, "type", "core_treatment")
    # energy_component_kind belongs to the parent revision and must survive.
    assert _exists(harness.engine, "type", "energy_component_kind")
    assert _levels(harness.engine) == before

    harness.run("upgrade", _MIGRATION.revision)
    assert _levels(harness.engine) == before
    assert _exists(harness.engine, "table", "calc_sp_energy_component")


def test_downgrade_refuses_while_a_level_states_a_core_treatment(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text("UPDATE level_of_theory SET core_treatment = CAST('frozen_core' AS core_treatment) WHERE id = :i"),
            {"i": ids["ccsdt"]},
        )

    refused = _run_expecting_failure(harness, "downgrade", _MIGRATION.parent)

    assert refused.returncode != 0
    message = refused.stderr + refused.stdout
    assert "stated core_treatment" in message
    # Actionable: nulling the column leaves a stale hash, so it says to re-key or merge.
    assert "stale hash" in message and "merge" in message
    # Nothing was dropped.
    harness.run("upgrade", _MIGRATION.revision)  # a no-op that reopens the engine
    assert _has_column(harness.engine, "level_of_theory", "core_treatment")

    with harness.engine.begin() as conn:
        conn.execute(text("UPDATE level_of_theory SET core_treatment = NULL"))
    harness.run("downgrade", _MIGRATION.parent)
    assert not _has_column(harness.engine, "level_of_theory", "core_treatment")


def test_downgrade_refuses_while_components_exist(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        ids = _seed(conn)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
                "VALUES (:c, CAST('reference' AS energy_component_kind), -75.9)"
            ),
            {"c": ids["calc"]},
        )

    refused = _run_expecting_failure(harness, "downgrade", _MIGRATION.parent)

    assert refused.returncode != 0
    message = refused.stderr + refused.stdout
    assert "calc_sp_energy_component row(s)" in message
    # Actionable: accepted rows cannot be deleted while the freeze trigger stands.
    assert "trg_as_child_calc_sp_energy_component" in message and "freeze trigger" in message
    harness.run("upgrade", _MIGRATION.revision)
    assert _exists(harness.engine, "table", "calc_sp_energy_component")
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM calc_sp_energy_component")) == 1


def test_the_merge_script_still_runs_on_a_database_that_has_not_reached_the_revision(harness):
    """It regroups by core treatment where the column exists and runs without it where it does not."""
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        _seed(conn)
    for revision in (_MIGRATION.parent, _MIGRATION.revision):
        harness.run("upgrade", revision)
        ran = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "python", "scripts/ops/merge_duplicate_levels_of_theory.py"],
            cwd=harness.root,
            env=harness.env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert ran.returncode == 0, (revision, ran.stderr[-2000:])
