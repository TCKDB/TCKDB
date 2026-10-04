"""Disposable-database contract for ``e5b9c2a7d4f1`` (network declarations and determinations).

The revision adds ``network_kinetics_determination``, three nullable declaration columns on
``network_solve`` and three nullable grouping columns on ``network_kinetics``.

* upgrade writes nothing: a solve and a fit deposited before keep every new column NULL (never a
  default claim) and there are no determinations;
* the database enforces the shape: a role without a determination, a determination without a
  declared representation, a declaration that is not a versioned object (including one with no
  ``version`` key, where a bare NULL predicate would pass) and two fits of one determination with
  one key are refused; a determination is immutable and its key is unique within a solve;
* the accepted-science guards are installed on the new table;
* downgrade removes everything it added and prints what it forgets; upgrade again converges;
* ``alembic check`` is clean at head (the model and the revision agree).
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test
from tests.db.test_ts_evidence_kinds_migration import _Harness

_MIGRATION = revision_under_test("e5b9c2a7d4f1")
_TABLE = "network_kinetics_determination"
_SOLVE_COLUMNS = {"target_declaration", "protocol_declaration", "validation_declaration"}
_FIT_COLUMNS = {"determination_id", "representation_role", "representation_declaration"}


@pytest.fixture
def harness():
    created = _Harness("network_declarations")
    yield created
    created.close()


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
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
    """A network with two states, one channel, one solve and one legacy fit."""
    network_id = conn.scalar(
        text("INSERT INTO network (name, public_ref) VALUES ('declarations migration', :r) RETURNING id"),
        {"r": f"net_decmig{tag}"},
    )
    solve_id = conn.scalar(
        text(
            "INSERT INTO network_solve (network_id, public_ref, me_method, tmin_k, tmax_k, pmin_bar, pmax_bar) "
            "VALUES (:n, :r, 'reservoir_state', 300, 2000, 0.01, 100) RETURNING id"
        ),
        {"n": network_id, "r": f"nsolve_decmig{tag}"},
    )
    states = [
        conn.scalar(
            text(
                "INSERT INTO network_state (network_id, kind, composition_hash, label) "
                "VALUES (:n, 'well', :h, :l) RETURNING id"
            ),
            {"n": network_id, "h": f"{tag}{i}".ljust(64, "0"), "l": f"w{i}"},
        )
        for i in range(2)
    ]
    # A computed solve must commit the master-equation inputs its kind asserts.
    for state in states:
        conn.execute(
            text(
                "INSERT INTO network_solve_state_energy (solve_id, state_id, energy_kj_mol, "
                "energy_zero_convention, correction_convention) "
                "VALUES (:s, :st, -120.0, 'entrance_channel', 'electronic_only')"
            ),
            {"s": solve_id, "st": state},
        )
    conn.execute(
        text("INSERT INTO network_solve_energy_transfer (solve_id, scope) VALUES (:s, 'network_wide')"),
        {"s": solve_id},
    )
    channel_id = conn.scalar(
        text(
            "INSERT INTO network_channel (network_id, source_state_id, sink_state_id, kind, channel_key) "
            "VALUES (:n, :a, :b, 'isomerization', 'w0=>w1') RETURNING id"
        ),
        {"n": network_id, "a": states[0], "b": states[1]},
    )
    fit_id = conn.scalar(
        text(
            "INSERT INTO network_kinetics (channel_id, solve_id, model_kind, public_ref) "
            "VALUES (:c, :s, 'plog', :r) RETURNING id"
        ),
        {"c": channel_id, "s": solve_id, "r": f"nkin_decmig{tag}"},
    )
    return {"solve_id": solve_id, "channel_id": channel_id, "fit_id": fit_id}


def _refused(conn, sql: str, params: dict) -> None:
    with pytest.raises(DBAPIError):
        with conn.begin_nested():
            conn.execute(text(sql), params)


_OBSERVABLE = '{"version": 1, "observable": "product_resolved_coefficient"}'


def _determination(conn, seeded: dict[str, int], key: str, digest: str) -> int:
    return conn.scalar(
        text(
            f"INSERT INTO {_TABLE} (solve_id, channel_id, determination_key, observable_declaration, "
            "identity_hash, public_ref) VALUES (:s, :c, :k, CAST(:o AS jsonb), :h, :r) RETURNING id"
        ),
        {
            "s": seeded["solve_id"],
            "c": seeded["channel_id"],
            "k": key,
            "o": _OBSERVABLE,
            "h": digest * 64,
            "r": f"nkdet_{key}",
        },
    )


def test_upgrade_leaves_legacy_rows_undeclared_and_enforces_the_shape(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    assert _TABLE not in _tables(harness.engine)
    with harness.engine.begin() as conn:
        seeded = _seed(conn, "up")

    harness.run("upgrade", _MIGRATION.revision)

    assert _TABLE in _tables(harness.engine)
    assert _SOLVE_COLUMNS <= _columns(harness.engine, "network_solve")
    assert _FIT_COLUMNS <= _columns(harness.engine, "network_kinetics")
    assert _triggers(harness.engine) == {
        f"trg_as_child_{_TABLE}",
        f"trg_as_truncate_{_TABLE}",
        f"trg_{_TABLE}_immutable",
    }
    with harness.engine.begin() as conn:
        solve = conn.execute(
            text("SELECT target_declaration, protocol_declaration, validation_declaration FROM network_solve")
        ).one()
        assert tuple(solve) == (None, None, None)  # SQL NULL, not the JSON value null
        fit = conn.execute(
            text("SELECT determination_id, representation_role, representation_declaration FROM network_kinetics")
        ).one()
        assert tuple(fit) == (None, None, None)
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0

        # A declaration is a versioned object; a missing ``version`` key must not pass as NULL.
        update = "UPDATE network_solve SET {c} = CAST(:v AS jsonb) WHERE id = :s"
        for column in sorted(_SOLVE_COLUMNS):
            sql = update.format(c=column)
            with conn.begin_nested():
                conn.execute(text(sql), {"v": '{"version": 1}', "s": seeded["solve_id"]})
            _refused(conn, sql, {"v": '{"claim_origin": "source_publication"}', "s": seeded["solve_id"]})
            _refused(conn, sql, {"v": '[1]', "s": seeded["solve_id"]})
            _refused(conn, sql, {"v": '{"version": "1"}', "s": seeded["solve_id"]})

        first = _determination(conn, seeded, "d1", "a")
        _determination(conn, seeded, "d2", "b")
        # A key names one determination of a solve; the identity digest is unique and well formed.
        _refused(
            conn,
            f"INSERT INTO {_TABLE} (solve_id, channel_id, determination_key, observable_declaration, "
            "identity_hash, public_ref) VALUES (:s, :c, 'd1', CAST(:o AS jsonb), :h, 'nkdet_dup')",
            {"s": seeded["solve_id"], "c": seeded["channel_id"], "o": _OBSERVABLE, "h": "c" * 64},
        )
        _refused(
            conn,
            f"INSERT INTO {_TABLE} (solve_id, channel_id, determination_key, observable_declaration, "
            "identity_hash, public_ref) VALUES (:s, :c, 'd3', CAST(:o AS jsonb), :h, 'nkdet_d3')",
            {"s": seeded["solve_id"], "c": seeded["channel_id"], "o": _OBSERVABLE, "h": "a" * 64},
        )
        _refused(
            conn,
            f"INSERT INTO {_TABLE} (solve_id, channel_id, determination_key, observable_declaration, "
            "identity_hash, public_ref) VALUES (:s, :c, 'd4', CAST(:o AS jsonb), :h, 'nkdet_d4')",
            {"s": seeded["solve_id"], "c": seeded["channel_id"], "o": _OBSERVABLE, "h": "NOT-A-HASH"},
        )
        _refused(
            conn,
            f"INSERT INTO {_TABLE} (solve_id, channel_id, determination_key, observable_declaration, "
            "identity_hash, public_ref) VALUES (:s, :c, '  ', CAST(:o AS jsonb), :h, 'nkdet_d5')",
            {"s": seeded["solve_id"], "c": seeded["channel_id"], "o": _OBSERVABLE, "h": "d" * 64},
        )
        _refused(
            conn,
            f"INSERT INTO {_TABLE} (solve_id, channel_id, determination_key, observable_declaration, "
            "identity_hash, public_ref) VALUES (:s, :c, 'd6', CAST(:o AS jsonb), :h, 'nkdet_d6')",
            {"s": seeded["solve_id"], "c": seeded["channel_id"], "o": '{"observable": "x"}', "h": "e" * 64},
        )
        # Identity is immutable from creation, whichever column an UPDATE names.
        _refused(conn, f"UPDATE {_TABLE} SET determination_key = 'renamed' WHERE id = :i", {"i": first})

        fit_update = (
            "UPDATE network_kinetics SET determination_id = :d, representation_role = :r, "
            "representation_declaration = CAST(:x AS jsonb) WHERE id = :f"
        )
        ok = {"d": first, "r": "complete", "x": '{"version": 1, "key": "k"}', "f": seeded["fit_id"]}
        _refused(conn, fit_update, {**ok, "r": None})  # a determination states its role
        _refused(conn, fit_update, {**ok, "x": None})  # and its representation
        _refused(conn, fit_update, {**ok, "x": '{"version": 1}'})  # which carries a key
        _refused(conn, fit_update, {**ok, "r": "preferred"})  # from the closed vocabulary
        _refused(
            conn,
            "UPDATE network_kinetics SET representation_role = 'complete' WHERE id = :f",
            {"f": seeded["fit_id"]},
        )  # a role without a determination
        with conn.begin_nested():
            conn.execute(text(fit_update), ok)
        # A second fit of one determination cannot reuse the key.
        second_fit = conn.scalar(
            text(
                "INSERT INTO network_kinetics (channel_id, solve_id, model_kind, public_ref) "
                "VALUES (:c, :s, 'chebyshev', 'nkin_decmig_second') RETURNING id"
            ),
            {"c": seeded["channel_id"], "s": seeded["solve_id"]},
        )
        _refused(conn, fit_update, {**ok, "f": second_fit})
        with conn.begin_nested():
            conn.execute(text(fit_update), {**ok, "f": second_fit, "x": '{"version": 1, "key": "other"}'})

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


def test_downgrade_forgets_what_it_added_and_upgrade_converges(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seeded = _seed(conn, "down")
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        _determination(conn, seeded, "d1", "a")

    harness.run("downgrade", _MIGRATION.parent)

    assert _TABLE not in _tables(harness.engine)
    assert not _SOLVE_COLUMNS & _columns(harness.engine, "network_solve")
    assert not _FIT_COLUMNS & _columns(harness.engine, "network_kinetics")
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'network_representation_role'")) == 0
    with harness.engine.begin() as conn:
        # The pre-existing network rows are untouched.
        assert conn.scalar(text("SELECT count(*) FROM network_kinetics")) == 1
    harness.run("upgrade", _MIGRATION.revision)
    assert _TABLE in _tables(harness.engine)
    assert _FIT_COLUMNS <= _columns(harness.engine, "network_kinetics")
