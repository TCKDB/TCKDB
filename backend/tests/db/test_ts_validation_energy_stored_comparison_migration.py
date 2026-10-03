"""Disposable-database contract for ``c4b8e2f6a713`` (#638).

The revision adds two nullable columns to ``transition_state_validation_energy``:
``stored_energy_comparison`` (``agrees`` / ``not_compared``) and ``not_compared_reason``.

* upgrade writes nothing: an energy deposited before keeps both columns NULL, which reads as
  "the comparison did not exist" and never as a pass;
* the shape is enforced by the database: a status outside the two values, a ``not_compared`` with
  no reason, a reason on any other status, a reason outside the fixed token set, and a ZPE scale
  factor that is not positive or sits on an ``electronic`` energy are all refused;
* downgrade refuses, with a count, while a row records an outcome, and deletes nothing; once the
  outcome is cleared it removes the columns, and upgrade again converges;
* ``alembic check`` is clean at head (the model and the revision agree).
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test
from tests.db.test_ts_evidence_kinds_migration import _Harness, _seed_ts_with_irc

_MIGRATION = revision_under_test("c4b8e2f6a713")
_TABLE = "transition_state_validation_energy"
_COLUMNS = {"stored_energy_comparison", "not_compared_reason", "zpe_scale_factor"}


@pytest.fixture
def harness():
    created = _Harness("ts_energy_stored_cmp")
    yield created
    created.close()


def _columns(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": _TABLE},
            )
        )


def _seed_energy(conn, seeded: dict[str, int]) -> int:
    ordering_id = conn.scalar(
        text(
            "INSERT INTO transition_state_validation_evidence "
            "(transition_state_entry_id, kind, passed, rationale) "
            "VALUES (:e, 'energy_ordering', true, 'legacy ordering') RETURNING id"
        ),
        {"e": seeded["ts_entry_id"]},
    )
    return conn.scalar(
        text(
            f"INSERT INTO {_TABLE} (evidence_id, participant, energy_kind, energy_hartree, source_calculation_id) "
            "VALUES (:i, 'ts', 'electronic', -40.0, :c) RETURNING id"
        ),
        {"i": ordering_id, "c": seeded["freq_calc_id"]},
    )


def _refused(conn, sql: str, params: dict) -> None:
    with pytest.raises(DBAPIError):
        with conn.begin_nested():
            conn.execute(text(sql), params)


def test_upgrade_leaves_earlier_energies_unrecorded_and_enforces_the_shape(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    assert not _COLUMNS & _columns(harness.engine)
    with harness.engine.begin() as conn:
        seeded = _seed_ts_with_irc(conn, "cmp")
        legacy_id = _seed_energy(conn, seeded)

    harness.run("upgrade", _MIGRATION.revision)

    assert _COLUMNS <= _columns(harness.engine)
    with harness.engine.begin() as conn:
        legacy = conn.execute(
            text(f"SELECT stored_energy_comparison, not_compared_reason FROM {_TABLE} WHERE id = :i"),
            {"i": legacy_id},
        ).one()
        assert tuple(legacy) == (None, None)

        insert = (
            f"INSERT INTO {_TABLE} (evidence_id, participant, energy_kind, energy_hartree, "
            "source_calculation_id, stored_energy_comparison, not_compared_reason) "
            "SELECT evidence_id, :p, 'electronic', -1.0, source_calculation_id, :s, :r FROM "
            f"{_TABLE} WHERE id = :i"
        )
        base = {"i": legacy_id, "p": "reactant:1", "s": "agrees", "r": None}
        with conn.begin_nested():
            conn.execute(text(insert), base)
        with conn.begin_nested():
            conn.execute(
                text(insert), {**base, "p": "reactant:2", "s": "not_compared", "r": "zpe_not_stated"}
            )
        _refused(conn, insert, {**base, "p": "product:1", "s": "matches"})
        _refused(conn, insert, {**base, "p": "product:1", "s": "not_compared", "r": None})
        _refused(conn, insert, {**base, "p": "product:1", "s": "agrees", "r": "zpe_not_stated"})
        # An unrecorded status may not carry a reason either.
        _refused(conn, insert, {**base, "p": "product:1", "s": None, "r": "zpe_not_stated"})
        # The reason is one of a fixed set of tokens, the new one among them.
        _refused(conn, insert, {**base, "p": "product:1", "s": "not_compared", "r": "because"})
        with conn.begin_nested():
            conn.execute(
                text(insert), {**base, "p": "product:1", "s": "not_compared", "r": "zpe_scaling_unstated"}
            )

        # A ZPE scale factor belongs to an E0 and is positive and finite.
        scaled = (
            f"INSERT INTO {_TABLE} (evidence_id, participant, energy_kind, energy_hartree, "
            "source_calculation_id, zpe_scale_factor) "
            "SELECT evidence_id, 'product:2', :k, -1.0, source_calculation_id, :f FROM "
            f"{_TABLE} WHERE id = :i"
        )
        with conn.begin_nested():
            conn.execute(text(scaled), {"i": legacy_id, "k": "e0", "f": 0.98})
        _refused(conn, scaled, {"i": legacy_id, "k": "electronic", "f": 0.98})
        _refused(conn, scaled, {"i": legacy_id, "k": "e0", "f": 0.0})
        _refused(conn, scaled, {"i": legacy_id, "k": "e0", "f": -1.0})

    harness.run("upgrade", "head")
    checked = subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "check"],
        cwd=harness.root,
        env=harness.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr


def test_downgrade_refuses_while_an_outcome_is_recorded_then_converges(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seeded = _seed_ts_with_irc(conn, "down")
        energy_id = _seed_energy(conn, seeded)
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {_TABLE} SET stored_energy_comparison = 'agrees' WHERE id = :i"), {"i": energy_id}
        )

    refused = harness.run("downgrade", _MIGRATION.parent, expect_success=False)
    assert refused.returncode != 0
    assert f"Cannot downgrade: 1 {_TABLE}" in refused.stderr, refused.stderr[-2000:]
    assert _COLUMNS <= _columns(harness.engine)
    with harness.engine.begin() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 1
        conn.execute(text(f"UPDATE {_TABLE} SET stored_energy_comparison = NULL WHERE id = :i"), {"i": energy_id})

    harness.run("downgrade", _MIGRATION.parent)
    assert not _COLUMNS & _columns(harness.engine)
    with harness.engine.begin() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 1
    harness.run("upgrade", _MIGRATION.revision)
    assert _COLUMNS <= _columns(harness.engine)
