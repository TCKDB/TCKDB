"""Disposable-database contract for ``c5e1a8d3f6b9`` (composite-levels plan P6).

The revision adds ``energy_correction_scheme.frequency_level_of_theory_id`` (a
nullable FK to ``level_of_theory``) and replaces the two partial unique
identity indexes with versions that include it, still ``NULLS NOT DISTINCT``.

What is pinned:

* existing rows keep their id, ref and identity (all have a NULL frequency
  level, and NULL equals NULL under NULLS NOT DISTINCT);
* the two indexes now distinguish rows by frequency level and still refuse a
  true duplicate, including two rows with no frequency level;
* the downgrade refuses, with counts, when rows differing only in frequency
  level would collide under the older indexes, and changes nothing when it
  refuses.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.db._migration_chain import revision_under_test
from tests.db.test_thermo_model_kind_backfill_migration import _MigrationHarness

_MIGRATION = revision_under_test("c5e1a8d3f6b9")
_COLUMN = "frequency_level_of_theory_id"
_LEGACY = "uq_energy_correction_scheme_identity"
_REVISED = "uq_energy_correction_scheme_identity_revised"


@pytest.fixture
def harness():
    created = _MigrationHarness("scheme_frequency_level")
    yield created
    created.close()


def _columns(conn) -> set[str]:
    return set(
        conn.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'energy_correction_scheme'"
            )
        )
    )


def _indexdef(conn, name: str) -> str | None:
    return conn.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = :n"),
        {"n": name},
    )


def _lot(conn, method: str) -> int:
    return conn.scalar(
        text(
            "INSERT INTO level_of_theory (method, lot_hash, public_ref) "
            "VALUES (:m, :h, :r) RETURNING id"
        ),
        {"m": method, "h": f"{method:0<64}"[:64], "r": f"lot_{method}"},
    )


def _scheme(
    conn,
    name: str,
    *,
    ref: str,
    energy_lot: int | None = None,
    freq_lot: int | None = None,
    data_revision: str | None = None,
) -> int:
    columns = ["kind", "name", "public_ref", "level_of_theory_id"]
    values = [
        "CAST('bac_petersson' AS energy_correction_scheme_kind)",
        ":name",
        ":ref",
        ":lot",
    ]
    params: dict = {"name": name, "ref": ref, "lot": energy_lot}
    if freq_lot is not None:
        columns.append(_COLUMN)
        values.append(":freq")
        params["freq"] = freq_lot
    if data_revision is not None:
        columns.append("data_revision")
        values.append(":rev")
        params["rev"] = data_revision
    return conn.scalar(
        text(
            f"INSERT INTO energy_correction_scheme ({', '.join(columns)}) "
            f"VALUES ({', '.join(values)}) RETURNING id"
        ),
        params,
    )


def _existing_rows(conn) -> tuple:
    return tuple(
        tuple(row)
        for row in conn.execute(
            text(
                "SELECT id, kind, name, public_ref, level_of_theory_id, source_literature_id, "
                "software_release_id, workflow_tool_release_id, data_revision, units, note "
                "FROM energy_correction_scheme ORDER BY id"
            )
        )
    )


def test_upgrade_keeps_every_existing_row_and_its_identity(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        assert _COLUMN not in _columns(conn)
        energy = _lot(conn, "ccsdtf12")
        _scheme(conn, "Old A", ref="ecs_old_a", energy_lot=energy)
        _scheme(conn, "Old B", ref="ecs_old_b", energy_lot=energy, data_revision="a" * 40)
        before = _existing_rows(conn)

    completed = harness.run("upgrade", _MIGRATION.revision, check=False)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"

    with harness.engine.connect() as conn:
        assert _COLUMN in _columns(conn)
        assert _existing_rows(conn) == before
        assert conn.scalar(text(f"SELECT count(*) FROM energy_correction_scheme WHERE {_COLUMN} IS NOT NULL")) == 0
        legacy = _indexdef(conn, _LEGACY)
        revised = _indexdef(conn, _REVISED)
        assert _COLUMN in legacy and "WHERE (data_revision IS NULL)" in legacy
        assert "NULLS NOT DISTINCT" in legacy
        assert _COLUMN in revised and "WHERE (data_revision IS NOT NULL)" in revised
        assert "NULLS NOT DISTINCT" in revised
        assert "workflow_tool_release_id" in legacy
        assert "workflow_tool_release_id" not in revised


def test_the_indexes_distinguish_frequency_levels_and_still_refuse_duplicates(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        energy = _lot(conn, "ccsdtf12")
        freq_a = _lot(conn, "b3lypa")
        freq_b = _lot(conn, "b3lypb")
        _scheme(conn, "Same", ref="ecs_none", energy_lot=energy)
        _scheme(conn, "Same", ref="ecs_a", energy_lot=energy, freq_lot=freq_a)
        _scheme(conn, "Same", ref="ecs_b", energy_lot=energy, freq_lot=freq_b)
        _scheme(conn, "Same", ref="ecs_rev_a", energy_lot=energy, freq_lot=freq_a, data_revision="a" * 40)
        _scheme(conn, "Same", ref="ecs_rev_b", energy_lot=energy, freq_lot=freq_b, data_revision="a" * 40)

    with harness.engine.connect() as conn:
        energy = conn.scalar(text("SELECT id FROM level_of_theory WHERE method = 'ccsdtf12'"))
        freq_a = conn.scalar(text("SELECT id FROM level_of_theory WHERE method = 'b3lypa'"))

    for ref, kwargs, index in (
        ("ecs_dup_none", {}, _LEGACY),
        ("ecs_dup_a", {"freq_lot": freq_a}, _LEGACY),
        ("ecs_dup_rev_a", {"freq_lot": freq_a, "data_revision": "a" * 40}, _REVISED),
    ):
        with pytest.raises(IntegrityError) as refused:
            with harness.engine.begin() as conn:
                _scheme(conn, "Same", ref=ref, energy_lot=energy, **kwargs)
        assert refused.value.orig.diag.constraint_name == index, ref


def test_downgrade_restores_the_older_indexes_when_nothing_collides(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        energy = _lot(conn, "ccsdtf12")
        freq_a = _lot(conn, "b3lypa")
        _scheme(conn, "Kept", ref="ecs_kept", energy_lot=energy)
        _scheme(conn, "Keyed", ref="ecs_keyed", energy_lot=energy, freq_lot=freq_a)

    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert "downgrade forgets: 1 scheme frequency level(s)" in completed.stdout

    with harness.engine.connect() as conn:
        assert _COLUMN not in _columns(conn)
        assert _COLUMN not in _indexdef(conn, _LEGACY)
        assert _COLUMN not in _indexdef(conn, _REVISED)
        assert conn.scalar(text("SELECT count(*) FROM energy_correction_scheme")) == 2

    harness.run("upgrade", _MIGRATION.revision)


@pytest.mark.parametrize("data_revision", [None, "b" * 40])
def test_downgrade_refuses_with_counts_when_frequency_levels_would_collide(
    harness, data_revision
):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        energy = _lot(conn, "ccsdtf12")
        freq_a = _lot(conn, "b3lypa")
        freq_b = _lot(conn, "b3lypb")
        for ref, freq in (("ecs_twin_a", freq_a), ("ecs_twin_b", freq_b), ("ecs_twin_c", None)):
            _scheme(
                conn, "Twin", ref=ref, energy_lot=energy, freq_lot=freq, data_revision=data_revision
            )
        _scheme(conn, "Solo", ref="ecs_solo", energy_lot=energy)

    completed = harness.run("downgrade", _MIGRATION.parent, check=False)
    assert completed.returncode != 0
    # One colliding group of three rows; the unrelated scheme is not counted.
    assert "1 group(s) (3 rows)" in completed.stderr, completed.stderr
    assert "frequency_level_of_theory_id" in completed.stderr

    with harness.engine.connect() as conn:
        # The refusal comes before any DDL: nothing was changed.
        assert _COLUMN in _columns(conn)
        assert _COLUMN in _indexdef(conn, _LEGACY)
        assert _COLUMN in _indexdef(conn, _REVISED)
        assert conn.scalar(text("SELECT count(*) FROM energy_correction_scheme")) == 4
