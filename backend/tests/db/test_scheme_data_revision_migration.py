"""Disposable-database contract for ``f2c8a5d1e9b7`` (#619).

The revision adds three things and splits one index:

* ``energy_correction_scheme.data_revision`` and the partial unique index it
  selects (identity with a revision, tool build out of the key);
* ``energy_correction_scheme.atom_params_applied_as`` (enum, nullable);
* ``thermo.energy_level_of_theory_id`` and ``statmech.energy_level_of_theory_id``
  (nullable FKs).

Seeded at the parent, including an **approved** thermo and statmech row: both
tables are accepted-science tables whose row triggers refuse an UPDATE, so the
test shows the DDL runs against a database that holds approved science and
leaves those rows byte-identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.db._migration_chain import revision_under_test
from tests.db.test_thermo_model_kind_backfill_migration import (
    _MigrationHarness,
    _new_species_entry,
)

_MIGRATION = revision_under_test("f2c8a5d1e9b7")
_REVISION_FILE = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob("f2c8a5d1e9b7_*.py")
)

_NEW_COLUMNS = {
    "energy_correction_scheme": {"data_revision", "atom_params_applied_as"},
    "thermo": {"energy_level_of_theory_id"},
    "statmech": {"energy_level_of_theory_id"},
}


@pytest.fixture
def harness():
    created = _MigrationHarness("scheme_data_revision")
    yield created
    created.close()


def _columns(conn, table: str) -> set[str]:
    return set(
        conn.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        )
    )


def _indexdef(conn, name: str) -> str | None:
    return conn.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"),
        {"n": name},
    )


def _scheme(conn, name: str, *, ref: str, data_revision: str | None = None, **extra) -> int:
    columns = ["kind", "name", "public_ref", *extra]
    values = ["CAST('atom_energy' AS energy_correction_scheme_kind)", ":name", ":ref"]
    params = {"name": name, "ref": ref, **extra}
    if data_revision is not None:
        columns.append("data_revision")
        values.append(":rev")
        params["rev"] = data_revision
    values += [f":{key}" for key in extra]
    return conn.scalar(
        text(
            f"INSERT INTO energy_correction_scheme ({', '.join(columns)}) "
            f"VALUES ({', '.join(values)}) RETURNING id"
        ),
        params,
    )


def _seed(conn) -> dict[str, int]:
    ids: dict[str, int] = {}
    entry = _new_species_entry(conn, "CC", "AAAAAAAAAAAAAA-UHFFFAOYSA-N")
    ids["thermo"] = conn.scalar(
        text(
            "INSERT INTO thermo (species_entry_id, scientific_origin) "
            "VALUES (:e, CAST('computed' AS scientific_origin_kind)) RETURNING id"
        ),
        {"e": entry},
    )
    ids["statmech"] = conn.scalar(
        text(
            "INSERT INTO statmech (species_entry_id, scientific_origin) "
            "VALUES (:e, CAST('computed' AS scientific_origin_kind)) RETURNING id"
        ),
        {"e": entry},
    )
    curator = conn.scalar(
        text(
            "INSERT INTO app_user (username, role, is_active) "
            "VALUES ('scheme-revision-approver', 'curator', true) RETURNING id"
        )
    )
    for record_type in ("thermo", "statmech"):
        conn.execute(
            text(
                "INSERT INTO record_review "
                "(record_type, record_id, status, reviewed_by, reviewed_at, first_approved_at) "
                "VALUES (CAST(:t AS submission_record_type), :id, "
                "        CAST('approved' AS record_review_status), :u, now(), now())"
            ),
            {"t": record_type, "id": ids[record_type], "u": curator},
        )
    ids["scheme_a"] = _scheme(conn, "Existing scheme A", ref="ecs_seed_a")
    ids["scheme_b"] = _scheme(conn, "Existing scheme B", ref="ecs_seed_b")
    return ids


def _snapshot(conn, ids: dict[str, int]) -> dict[str, tuple]:
    return {
        "thermo": tuple(conn.execute(text("SELECT * FROM thermo WHERE id = :i"), {"i": ids["thermo"]}).one()),
        "statmech": tuple(conn.execute(text("SELECT * FROM statmech WHERE id = :i"), {"i": ids["statmech"]}).one()),
        "schemes": tuple(
            tuple(row)
            for row in conn.execute(
                text(
                    "SELECT id, kind, name, public_ref, level_of_theory_id, source_literature_id, "
                    "software_release_id, workflow_tool_release_id, units, note "
                    "FROM energy_correction_scheme ORDER BY id"
                )
            )
        ),
    }


def test_upgrade_runs_over_approved_science_and_keeps_every_existing_row(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            assert not (columns & _columns(conn, table))
        ids = _seed(conn)
        before = _snapshot(conn, ids)

    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, (
        "the upgrade failed against approved thermo/statmech rows:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )

    with harness.engine.connect() as conn:
        for table, columns in _NEW_COLUMNS.items():
            assert columns <= _columns(conn, table)
        after = _snapshot(conn, ids)
        # Existing rows: same ids, refs, identity columns. Only new columns are
        # added, all NULL, and the approved rows are untouched.
        assert after["schemes"] == before["schemes"]
        for table in ("thermo", "statmech"):
            # New columns are appended; every pre-existing column is unchanged.
            assert after[table][: len(before[table])] == before[table]
        assert conn.scalar(text("SELECT count(*) FROM energy_correction_scheme WHERE data_revision IS NOT NULL")) == 0
        assert conn.scalar(text("SELECT count(*) FROM energy_correction_scheme WHERE atom_params_applied_as IS NOT NULL")) == 0
        for table in ("thermo", "statmech"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table} WHERE energy_level_of_theory_id IS NOT NULL")) == 0

        legacy = _indexdef(conn, "uq_energy_correction_scheme_identity")
        revised = _indexdef(conn, "uq_energy_correction_scheme_identity_revised")
        assert "WHERE (data_revision IS NULL)" in legacy and "workflow_tool_release_id" in legacy
        assert "WHERE (data_revision IS NOT NULL)" in revised
        assert "workflow_tool_release_id" not in revised and "data_revision" in revised


def test_the_two_indexes_enforce_the_two_identities(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        _scheme(conn, "Same", ref="ecs_unrevised")
        _scheme(conn, "Same", ref="ecs_revised_a", data_revision="a" * 40)
        # Unrevised and revised rows with otherwise identical identity coexist.
        _scheme(conn, "Same", ref="ecs_revised_b", data_revision="b" * 40)

    with pytest.raises(IntegrityError) as dup_unrevised:
        with harness.engine.begin() as conn:
            _scheme(conn, "Same", ref="ecs_unrevised_again")
    assert dup_unrevised.value.orig.diag.constraint_name == "uq_energy_correction_scheme_identity"

    with pytest.raises(IntegrityError) as dup_revised:
        with harness.engine.begin() as conn:
            _scheme(conn, "Same", ref="ecs_revised_again", data_revision="a" * 40)
    assert dup_revised.value.orig.diag.constraint_name == "uq_energy_correction_scheme_identity_revised"


def test_downgrade_restores_the_single_index_and_forgets_the_new_columns(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        _scheme(conn, "Kept", ref="ecs_kept")
        _scheme(conn, "Revised", ref="ecs_rev", data_revision="c" * 40)

    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert "downgrade forgets: 1 scheme data revision(s)" in completed.stdout

    with harness.engine.connect() as conn:
        for table, columns in _NEW_COLUMNS.items():
            assert not (columns & _columns(conn, table))
        assert _indexdef(conn, "uq_energy_correction_scheme_identity_revised") is None
        assert "WHERE" not in _indexdef(conn, "uq_energy_correction_scheme_identity")
        assert conn.scalar(text("SELECT count(*) FROM energy_correction_scheme")) == 2
        assert conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'atom_param_application'")) == 0

    harness.run("upgrade", _MIGRATION.revision)


def test_downgrade_refuses_when_two_revised_rows_would_collide(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        _scheme(conn, "Twin", ref="ecs_twin_a", data_revision="a" * 40)
        _scheme(conn, "Twin", ref="ecs_twin_b", data_revision="b" * 40)

    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    assert completed.returncode != 0
    assert "differ only in data_revision" in completed.stderr

    with harness.engine.connect() as conn:
        # Nothing was changed: the refusal comes before any DDL.
        assert _NEW_COLUMNS["energy_correction_scheme"] <= _columns(conn, "energy_correction_scheme")
        assert conn.scalar(text("SELECT count(*) FROM energy_correction_scheme")) == 2
