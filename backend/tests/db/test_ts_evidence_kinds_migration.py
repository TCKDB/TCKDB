"""Disposable-database contract for ``a7d3f1c95e28`` (#621).

The revision widens two deployed tables: ``transition_state_validation_evidence``
gains two kinds (and the columns and child table they need), and
``calc_irc_result`` may leave its direction and branch flags unstated.

What the tests pin, on a real database seeded at the parent:

* rows that existed before mean exactly what they meant, including an IRC
  result whose flags were stated ``false`` (the widening must not turn a stated
  value into an unstated one);
* the defaults are gone, not merely the ``NOT NULL``: an insert that omits a
  flag stores NULL, because a surviving ``DEFAULT false`` would turn every
  unstated flag back into the claim "no such branch";
* the new shape is enforced by the database, not by the service alone;
* the downgrade refuses, with a count, rather than deleting evidence or
  inventing a ``false``, and succeeds once the rows it cannot represent are gone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test

_MIGRATION = revision_under_test("a7d3f1c95e28")


class _Harness:
    def __init__(self, prefix: str):
        from conftest import _database_url, _db_env, scratch_database_name

        self.db_name = scratch_database_name(prefix)
        self.env = _db_env(self.db_name)
        self._url = _database_url
        self._admin = create_engine(
            _database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True
        )
        self._admin_conn = self._admin.connect()
        self._admin_conn.execute(text(f'CREATE DATABASE "{self.db_name}"'))
        self.engine = None
        self.root = Path(__file__).resolve().parents[2]

    def run(self, direction: str, revision: str, *, expect_success: bool = True):
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        completed = subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
            cwd=self.root,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
        )
        if expect_success:
            assert completed.returncode == 0, completed.stderr[-4000:]
        self.engine = create_engine(self._url(self.db_name), pool_pre_ping=True)
        return completed

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        try:
            self._admin_conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name"
                ),
                {"name": self.db_name},
            )
            self._admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{self.db_name}"'))
        finally:
            self._admin_conn.close()
            self._admin.dispose()


@pytest.fixture
def harness():
    created = _Harness("ts_evidence_kinds")
    yield created
    created.close()


def _seed_ts_with_irc(conn, tag: str) -> dict[str, int]:
    """A TS entry with an irc calculation, an irc evidence row and an IRC result."""
    reaction_id = conn.scalar(text("INSERT INTO chem_reaction (reversible) VALUES (true) RETURNING id"))
    reaction_entry_id = conn.scalar(
        text("INSERT INTO reaction_entry (reaction_id) VALUES (:r) RETURNING id"),
        {"r": reaction_id},
    )
    ts_id = conn.scalar(
        text("INSERT INTO transition_state (reaction_entry_id) VALUES (:r) RETURNING id"),
        {"r": reaction_entry_id},
    )
    ts_entry_id = conn.scalar(
        text(
            "INSERT INTO transition_state_entry (transition_state_id, charge, multiplicity) "
            "VALUES (:ts, 0, 2) RETURNING id"
        ),
        {"ts": ts_id},
    )
    irc_calc_id = conn.scalar(
        text(
            "INSERT INTO calculation (type, transition_state_entry_id) "
            "VALUES ('irc', :e) RETURNING id"
        ),
        {"e": ts_entry_id},
    )
    freq_calc_id = conn.scalar(
        text(
            "INSERT INTO calculation (type, transition_state_entry_id) "
            "VALUES ('freq', :e) RETURNING id"
        ),
        {"e": ts_entry_id},
    )
    conn.execute(
        text(
            "INSERT INTO calc_irc_result (calculation_id, direction, has_forward, has_reverse) "
            "VALUES (:c, 'forward', true, false)"
        ),
        {"c": irc_calc_id},
    )
    evidence_id = conn.scalar(
        text(
            "INSERT INTO transition_state_validation_evidence "
            "(transition_state_entry_id, kind, passed, rationale, reconstruction_calculation_id) "
            "VALUES (:e, 'irc', true, :why, :c) RETURNING id"
        ),
        {"e": ts_entry_id, "why": f"{tag} legacy evidence", "c": irc_calc_id},
    )
    return {
        "ts_entry_id": ts_entry_id,
        "irc_calc_id": irc_calc_id,
        "freq_calc_id": freq_calc_id,
        "evidence_id": evidence_id,
    }


def _fails(conn, sql: str, params: dict | None = None) -> None:
    """The statement is refused by the database, inside a savepoint."""
    with pytest.raises(DBAPIError):
        with conn.begin_nested():
            conn.execute(text(sql), params or {})


def test_existing_rows_keep_their_meaning_and_the_new_shape_is_enforced(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seeded = _seed_ts_with_irc(conn, "kinds")

    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        # -- nothing that existed changed ----------------------------------
        result = conn.execute(
            text(
                "SELECT direction, has_forward, has_reverse FROM calc_irc_result "
                "WHERE calculation_id = :c"
            ),
            {"c": seeded["irc_calc_id"]},
        ).one()
        assert (result.direction, result.has_forward, result.has_reverse) == ("forward", True, False), (
            "a stated false must survive the widening as a stated false"
        )
        evidence = conn.execute(
            text(
                "SELECT kind, passed, reconstruction_calculation_id, "
                "imaginary_frequency_count, imaginary_frequency_cm1, mode_displacement_agrees "
                "FROM transition_state_validation_evidence WHERE id = :i"
            ),
            {"i": seeded["evidence_id"]},
        ).one()
        assert (evidence.kind, evidence.passed) == ("irc", True)
        assert evidence.reconstruction_calculation_id == seeded["irc_calc_id"]
        assert (
            evidence.imaginary_frequency_count,
            evidence.imaginary_frequency_cm1,
            evidence.mode_displacement_agrees,
        ) == (None, None, None)

        # -- IRC: nullable, and the default is gone, not just the NOT NULL --
        for column in ("direction", "has_forward", "has_reverse"):
            assert conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'calc_irc_result' AND column_name = :c"
                ),
                {"c": column},
            ) == "YES"
        for column in ("has_forward", "has_reverse"):
            assert conn.scalar(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'calc_irc_result' AND column_name = :c"
                ),
                {"c": column},
            ) is None
        other_irc = conn.scalar(
            text(
                "INSERT INTO calculation (type, transition_state_entry_id) "
                "VALUES ('irc', :e) RETURNING id"
            ),
            {"e": seeded["ts_entry_id"]},
        )
        conn.execute(text("INSERT INTO calc_irc_result (calculation_id) VALUES (:c)"), {"c": other_irc})
        unstated = conn.execute(
            text(
                "SELECT direction, has_forward, has_reverse FROM calc_irc_result "
                "WHERE calculation_id = :c"
            ),
            {"c": other_irc},
        ).one()
        assert (unstated.direction, unstated.has_forward, unstated.has_reverse) == (None, None, None)

        # -- evidence kinds --------------------------------------------------
        insert_evidence = (
            "INSERT INTO transition_state_validation_evidence "
            "(transition_state_entry_id, kind, passed, rationale, reconstruction_calculation_id, "
            " imaginary_frequency_count, imaginary_frequency_cm1, mode_displacement_agrees) "
            "VALUES (:e, :kind, true, 'why', :c, :n, :f, :a)"
        )
        base = {"e": seeded["ts_entry_id"], "c": None, "n": None, "f": None, "a": None}

        # energy_ordering has no record-level source; the others require one.
        with conn.begin_nested():
            conn.execute(text(insert_evidence), {**base, "kind": "energy_ordering"})
        _fails(conn, insert_evidence, {**base, "kind": "energy_ordering", "c": seeded["freq_calc_id"]})
        _fails(conn, insert_evidence, {**base, "kind": "imaginary_mode"})
        _fails(conn, insert_evidence, {**base, "kind": "nmd", "c": seeded["freq_calc_id"]})

        # mode fields belong to imaginary_mode, and are range-checked.
        with conn.begin_nested():
            conn.execute(
                text(insert_evidence),
                {**base, "kind": "imaginary_mode", "c": seeded["freq_calc_id"], "n": 1, "f": -1500.0, "a": True},
            )
        _fails(
            conn,
            insert_evidence,
            {**base, "kind": "energy_ordering", "n": 1},
        )
        _fails(
            conn,
            insert_evidence,
            {**base, "kind": "imaginary_mode", "c": seeded["freq_calc_id"], "n": -1},
        )
        _fails(
            conn,
            insert_evidence,
            {**base, "kind": "imaginary_mode", "c": seeded["freq_calc_id"], "f": 1500.0},
        )

        # participant mappings are an irc claim.
        _fails(
            conn,
            "INSERT INTO transition_state_validation_evidence "
            "(transition_state_entry_id, kind, passed, rationale, reconstruction_calculation_id, "
            " reactant_participant_mapping, product_participant_mapping) "
            "VALUES (:e, 'imaginary_mode', true, 'why', :c, "
            "        CAST('{\"reactant:1\": [1]}' AS jsonb), CAST('{\"product:1\": [1]}' AS jsonb))",
            {"e": seeded["ts_entry_id"], "c": seeded["freq_calc_id"]},
        )

        # -- the energies table ----------------------------------------------
        ordering_id = conn.scalar(
            text(
                "SELECT id FROM transition_state_validation_evidence "
                "WHERE kind = 'energy_ordering' AND transition_state_entry_id = :e"
            ),
            {"e": seeded["ts_entry_id"]},
        )
        insert_energy = (
            "INSERT INTO transition_state_validation_energy "
            "(evidence_id, participant, energy_kind, energy_hartree, source_calculation_id) "
            "VALUES (:i, :p, :k, -40.0, :c)"
        )
        energy = {"i": ordering_id, "p": "ts", "k": "electronic", "c": seeded["freq_calc_id"]}
        with conn.begin_nested():
            conn.execute(text(insert_energy), energy)
        _fails(conn, insert_energy, energy)  # one energy per slot
        for bad in (
            {**energy, "p": "TS"},
            {**energy, "p": "reactant:0"},
            {**energy, "p": "reactant:"},
            {**energy, "p": "product:1x"},
            {**energy, "k": "total"},
            {**energy, "p": "reactant:1", "c": None},
        ):
            _fails(conn, insert_energy, bad)
        with conn.begin_nested():
            conn.execute(text(insert_energy), {**energy, "p": "reactant:12", "k": "e0"})


def test_the_downgrade_refuses_what_the_narrower_schema_cannot_hold(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seeded = _seed_ts_with_irc(conn, "downgrade")
    harness.run("upgrade", _MIGRATION.revision)

    # A new-kind evidence row is refused, not deleted.
    with harness.engine.begin() as conn:
        mode_id = conn.scalar(
            text(
                "INSERT INTO transition_state_validation_evidence "
                "(transition_state_entry_id, kind, passed, rationale, reconstruction_calculation_id) "
                "VALUES (:e, 'imaginary_mode', true, 'why', :c) RETURNING id"
            ),
            {"e": seeded["ts_entry_id"], "c": seeded["freq_calc_id"]},
        )
    refused = harness.run("downgrade", _MIGRATION.parent, expect_success=False)
    assert refused.returncode != 0
    assert "Cannot downgrade: 1 transition_state_validation_evidence" in refused.stderr, refused.stderr[-2000:]
    with harness.engine.begin() as conn:
        assert conn.scalar(
            text("SELECT count(*) FROM transition_state_validation_evidence WHERE id = :i"),
            {"i": mode_id},
        ) == 1
        conn.execute(text("DELETE FROM transition_state_validation_evidence WHERE id = :i"), {"i": mode_id})

    # An unstated IRC flag is refused, not filled in with false.
    with harness.engine.begin() as conn:
        unstated_calc = conn.scalar(
            text(
                "INSERT INTO calculation (type, transition_state_entry_id) "
                "VALUES ('irc', :e) RETURNING id"
            ),
            {"e": seeded["ts_entry_id"]},
        )
        conn.execute(
            text("INSERT INTO calc_irc_result (calculation_id, has_forward) VALUES (:c, true)"),
            {"c": unstated_calc},
        )
    refused = harness.run("downgrade", _MIGRATION.parent, expect_success=False)
    assert refused.returncode != 0
    assert "Cannot downgrade: 1 calc_irc_result" in refused.stderr, refused.stderr[-2000:]
    with harness.engine.begin() as conn:
        conn.execute(text("DELETE FROM calc_irc_result WHERE calculation_id = :c"), {"c": unstated_calc})

    # With nothing the narrower schema cannot hold, the downgrade succeeds and
    # restores the old shape exactly.
    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        for column in ("direction", "has_forward", "has_reverse"):
            assert conn.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'calc_irc_result' AND column_name = :c"
                ),
                {"c": column},
            ) == "NO"
        assert conn.scalar(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'calc_irc_result' AND column_name = 'has_forward'"
            )
        ) == "false"
        assert conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'transition_state_validation_energy'"
            )
        ) == 0
        assert conn.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'transition_state_validation_evidence' "
                "AND column_name = 'reconstruction_calculation_id'"
            )
        ) == "NO"
        # The one evidence row that existed is unchanged.
        assert conn.scalar(
            text("SELECT kind FROM transition_state_validation_evidence WHERE id = :i"),
            {"i": seeded["evidence_id"]},
        ) == "irc"
        _fails(
            conn,
            "INSERT INTO transition_state_validation_evidence "
            "(transition_state_entry_id, kind, passed, rationale, reconstruction_calculation_id) "
            "VALUES (:e, 'imaginary_mode', true, 'why', :c)",
            {"e": seeded["ts_entry_id"], "c": seeded["freq_calc_id"]},
        )

    # And the upgrade is repeatable from there.
    harness.run("upgrade", _MIGRATION.revision)
