"""Disposable-database contract for ``d7a1c4e9b258`` (#678).

The revision adds ``network_solve_state_energy_source`` (one calculation per participant of a state
energy) and three nullable columns on ``network_solve_state_energy`` recording whether the stated
energy was held against the sum of its sources.

* upgrade writes nothing: a state energy deposited before keeps all three columns NULL ("the comparison
  did not exist", never a pass) and has no participant-source rows;
* the shape is enforced by the database: a status outside the two values, a ``not_compared`` with no
  reason, a reason on any other status and a reason outside the fixed tokens are refused; a source
  row must name a participant of that very state and an existing state energy;
* the accepted-science guards are installed on the new table;
* downgrade refuses, with the counts, while a source row or a comparison outcome exists, deletes
  nothing, and once those are cleared removes the table and columns; upgrade again converges;
* ``alembic check`` is clean at head (the model and the revision agree).
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test
from tests.db.test_ts_evidence_kinds_migration import _Harness

_MIGRATION = revision_under_test("d7a1c4e9b258")
_TABLE = "network_solve_state_energy_source"
_ENERGY = "network_solve_state_energy"
_COLUMNS = {"source_sum_comparison", "source_sum_not_compared_reason", "energy_precision_kj_mol"}


@pytest.fixture
def harness():
    created = _Harness("state_energy_sources")
    yield created
    created.close()


def _columns(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": _ENERGY},
            )
        )


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.scalars(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")))


def _triggers(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text("SELECT tgname FROM pg_trigger WHERE tgrelid = to_regclass(:t) AND NOT tgisinternal"),
                {"t": f"public.{_TABLE}"},
            )
        )


def _seed(conn, tag: str) -> dict[str, int]:
    """A bimolecular network state with two participants and a legacy single-source energy."""
    network_id = conn.scalar(
        text("INSERT INTO network (name, public_ref) VALUES ('sources migration', :r) RETURNING id"),
        {"r": f"net_srcmig{tag}"},
    )
    solve_id = conn.scalar(
        text(
            "INSERT INTO network_solve (network_id, public_ref, me_method, tmin_k, tmax_k, pmin_bar, pmax_bar) "
            "VALUES (:n, :r, 'reservoir_state', 300, 2000, 0.01, 100) RETURNING id"
        ),
        {"n": network_id, "r": f"nsolve_srcmig{tag}"},
    )
    state_id = conn.scalar(
        text(
            "INSERT INTO network_state (network_id, kind, composition_hash, label) "
            "VALUES (:n, 'bimolecular', :h, 'A + B') RETURNING id"
        ),
        {"n": network_id, "h": f"s{tag}".ljust(64, "0")},
    )
    other_state_id = conn.scalar(
        text(
            "INSERT INTO network_state (network_id, kind, composition_hash, label) "
            "VALUES (:n, 'bimolecular', :h, 'C') RETURNING id"
        ),
        {"n": network_id, "h": f"w{tag}".ljust(64, "0")},
    )
    entries: dict[str, int] = {}
    for name, smiles in (("a", "[H][H]"), ("b", "N#N"), ("c", "[Ar]")):
        species_id = conn.scalar(
            text(
                "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                "VALUES ('molecule', :s, :k, 0, 1, 'achiral') RETURNING id"
            ),
            {"s": smiles, "k": f"{name}{tag}".upper().ljust(27, "A")[:27]},
        )
        entries[name] = conn.scalar(
            text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id}
        )
    for name in ("a", "b"):
        conn.execute(
            text("INSERT INTO network_state_participant (state_id, species_entry_id, stoichiometry) VALUES (:s, :e, 1)"),
            {"s": state_id, "e": entries[name]},
        )
    conn.execute(
        text("INSERT INTO network_state_participant (state_id, species_entry_id, stoichiometry) VALUES (:s, :e, 1)"),
        {"s": other_state_id, "e": entries["c"]},
    )
    calculations = {
        name: conn.scalar(
            text("INSERT INTO calculation (type, species_entry_id) VALUES ('sp', :e) RETURNING id"),
            {"e": entries[name]},
        )
        for name in ("a", "b")
    }
    for state, calc in ((state_id, calculations["a"]), (other_state_id, None)):
        conn.execute(
            text(
                f"INSERT INTO {_ENERGY} (solve_id, state_id, energy_kj_mol, energy_zero_convention, "
                "correction_convention, source_calculation_id) "
                "VALUES (:s, :st, -120.0, 'entrance_channel', 'electronic_only', :c)"
            ),
            {"s": solve_id, "st": state, "c": calc},
        )
    return {
        "solve_id": solve_id,
        "state_id": state_id,
        "other_state_id": other_state_id,
        "entry_a": entries["a"],
        "entry_b": entries["b"],
        "entry_c": entries["c"],
        "calc_a": calculations["a"],
        "calc_b": calculations["b"],
    }


def _refused(conn, sql: str, params: dict) -> None:
    with pytest.raises(DBAPIError):
        with conn.begin_nested():
            conn.execute(text(sql), params)


def test_upgrade_leaves_earlier_energies_unrecorded_and_enforces_the_shape(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    assert _TABLE not in _tables(harness.engine)
    assert not _COLUMNS & _columns(harness.engine)
    with harness.engine.begin() as conn:
        seeded = _seed(conn, "up")

    harness.run("upgrade", _MIGRATION.revision)

    assert _TABLE in _tables(harness.engine)
    assert _COLUMNS <= _columns(harness.engine)
    assert _triggers(harness.engine) == {f"trg_as_child_{_TABLE}", f"trg_as_truncate_{_TABLE}"}
    with harness.engine.begin() as conn:
        rows = conn.execute(
            text(f"SELECT source_sum_comparison, source_sum_not_compared_reason, source_calculation_id FROM {_ENERGY}")
        ).all()
        assert len(rows) == 2
        assert {(r[0], r[1]) for r in rows} == {(None, None)}  # not assessed, not a pass
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0

        update = f"UPDATE {_ENERGY} SET source_sum_comparison = :s, source_sum_not_compared_reason = :r WHERE state_id = :st"
        base = {"st": seeded["other_state_id"], "s": "agrees", "r": None}
        with conn.begin_nested():
            conn.execute(text(update), base)
        with conn.begin_nested():
            conn.execute(text(update), {**base, "s": "not_compared", "r": "sources_incomplete"})
        _refused(conn, update, {**base, "s": "matches"})
        _refused(conn, update, {**base, "s": "not_compared", "r": None})
        _refused(conn, update, {**base, "s": "agrees", "r": "sources_incomplete"})
        _refused(conn, update, {**base, "s": None, "r": "sources_incomplete"})
        _refused(conn, update, {**base, "s": "not_compared", "r": "because"})
        with conn.begin_nested():
            conn.execute(text(update), {**base, "s": "not_compared", "r": "no_second_state_on_the_same_zero"})
        with conn.begin_nested():
            conn.execute(text(update), {**base, "s": "not_compared", "r": "stated_precision_unknown"})

        # The stated rounding unit is positive and finite; NULL is "not stated".
        precision = f"UPDATE {_ENERGY} SET energy_precision_kj_mol = :p WHERE state_id = :st"
        with conn.begin_nested():
            conn.execute(text(precision), {"st": seeded["other_state_id"], "p": 0.1})
        _refused(conn, precision, {"st": seeded["other_state_id"], "p": 0.0})
        _refused(conn, precision, {"st": seeded["other_state_id"], "p": -1.0})
        _refused(conn, precision, {"st": seeded["other_state_id"], "p": float("inf")})

        insert = (
            f"INSERT INTO {_TABLE} (solve_id, state_id, species_entry_id, calculation_id) VALUES (:s, :st, :e, :c)"
        )
        ok = {"s": seeded["solve_id"], "st": seeded["state_id"], "e": seeded["entry_a"], "c": seeded["calc_a"]}
        with conn.begin_nested():
            conn.execute(text(insert), ok)
        with conn.begin_nested():
            conn.execute(text(insert), {**ok, "e": seeded["entry_b"], "c": seeded["calc_b"]})
        # One row per participant of a state energy.
        _refused(conn, insert, ok)
        # The species must be a participant of that very state: c belongs to the other state.
        _refused(conn, insert, {**ok, "e": seeded["entry_c"]})
        # The state energy must exist.
        _refused(conn, insert, {**ok, "st": seeded["other_state_id"] + 1000})
        # The calculation must exist.
        _refused(conn, insert, {**ok, "e": seeded["entry_b"], "c": seeded["calc_b"] + 1000})

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


def test_downgrade_refuses_while_sources_or_outcomes_exist_then_converges(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seeded = _seed(conn, "down")
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {_TABLE} (solve_id, state_id, species_entry_id, calculation_id) VALUES (:s, :st, :e, :c)"
            ),
            {"s": seeded["solve_id"], "st": seeded["state_id"], "e": seeded["entry_a"], "c": seeded["calc_a"]},
        )
        conn.execute(
            text(f"UPDATE {_ENERGY} SET source_sum_comparison = 'agrees' WHERE state_id = :st"),
            {"st": seeded["state_id"]},
        )

    refused = harness.run("downgrade", _MIGRATION.parent, expect_success=False)
    assert refused.returncode != 0
    assert f"Cannot downgrade: 1 {_TABLE} row(s) and 1 {_ENERGY} row(s)" in refused.stderr, refused.stderr[-2000:]
    assert _TABLE in _tables(harness.engine) and _COLUMNS <= _columns(harness.engine)
    with harness.engine.begin() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 1
        conn.execute(text(f"DELETE FROM {_TABLE}"))
        conn.execute(text(f"UPDATE {_ENERGY} SET source_sum_comparison = NULL"))

    harness.run("downgrade", _MIGRATION.parent)
    assert _TABLE not in _tables(harness.engine)
    assert not _COLUMNS & _columns(harness.engine)
    with harness.engine.begin() as conn:
        # The pre-existing network rows are untouched.
        assert conn.scalar(text(f"SELECT count(*) FROM {_ENERGY}")) == 2
    harness.run("upgrade", _MIGRATION.revision)
    assert _TABLE in _tables(harness.engine) and _COLUMNS <= _columns(harness.engine)
