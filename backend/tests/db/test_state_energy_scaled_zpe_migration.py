"""Disposable-database contract for ``e5b2c8a1d7f3`` (#693).

The revision lets a network state energy cite the frequency calculation whose zero-point energy is
added to a participant's sp energy (``network_solve_state_energy_source.zpe_calculation_id``) and
state the factor the ZPEs were scaled with (``network_solve_state_energy.zpe_scale_factor``), and
admits three more reasons a comparison was not made.

* upgrade writes nothing: earlier rows keep both columns NULL, and every earlier reason token stays valid;
* the database enforces the shape: a scale factor must be positive and finite, a ZPE calculation must
  exist, and only the fixed reason tokens are accepted;
* downgrade refuses, with the counts, while a ZPE calculation is cited or a scale factor / new reason is
  recorded, deletes nothing, and once those are cleared removes the columns and restores the old token
  check; upgrade again converges;
* ``alembic check`` is clean at head (the model and the revision agree).
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test
from tests.db.test_state_energy_participant_sources_migration import _columns, _refused, _seed
from tests.db.test_ts_evidence_kinds_migration import _Harness

_MIGRATION = revision_under_test("e5b2c8a1d7f3")
_SOURCE = "network_solve_state_energy_source"
_ENERGY = "network_solve_state_energy"


@pytest.fixture
def harness():
    created = _Harness("state_energy_zpe")
    yield created
    created.close()


def _source_columns(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": _SOURCE}
            )
        )


def test_upgrade_adds_nullable_columns_and_enforces_their_shape(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    assert "zpe_scale_factor" not in _columns(harness.engine)
    assert "zpe_calculation_id" not in _source_columns(harness.engine)
    with harness.engine.begin() as conn:
        seeded = _seed(conn, "zup")

    harness.run("upgrade", _MIGRATION.revision)

    assert "zpe_scale_factor" in _columns(harness.engine)
    assert "zpe_calculation_id" in _source_columns(harness.engine)
    with harness.engine.begin() as conn:
        # Nothing is invented for rows that predate the columns.
        assert conn.scalar(text(f"SELECT count(*) FROM {_ENERGY} WHERE zpe_scale_factor IS NOT NULL")) == 0

        scale = f"UPDATE {_ENERGY} SET zpe_scale_factor = :s WHERE state_id = :st"
        base = {"st": seeded["other_state_id"]}
        with conn.begin_nested():
            conn.execute(text(scale), {**base, "s": 0.9854})
        _refused(conn, scale, {**base, "s": 0.0})
        _refused(conn, scale, {**base, "s": -0.98})
        _refused(conn, scale, {**base, "s": float("inf")})
        with conn.begin_nested():
            conn.execute(text(scale), {**base, "s": None})

        reason = (
            f"UPDATE {_ENERGY} SET source_sum_comparison = 'not_compared', source_sum_not_compared_reason = :r "
            "WHERE state_id = :st"
        )
        for token in (
            "zpe_not_in_source",  # an earlier token stays valid
            "stated_precision_unknown",
            "zpe_not_stated",
            "geometry_not_paired",
            "zpe_scaling_unstated",
        ):
            with conn.begin_nested():
                conn.execute(text(reason), {**base, "r": token})
        _refused(conn, reason, {**base, "r": "zpe_unscaled"})

        insert = (
            f"INSERT INTO {_SOURCE} (solve_id, state_id, species_entry_id, calculation_id, zpe_calculation_id) "
            "VALUES (:s, :st, :e, :c, :z)"
        )
        ok = {
            "s": seeded["solve_id"],
            "st": seeded["state_id"],
            "e": seeded["entry_a"],
            "c": seeded["calc_a"],
            "z": seeded["calc_b"],
        }
        with conn.begin_nested():
            conn.execute(text(insert), ok)
        # The ZPE calculation must exist.
        _refused(conn, insert, {**ok, "e": seeded["entry_b"], "c": seeded["calc_b"], "z": seeded["calc_b"] + 1000})
        # It may be absent.
        with conn.begin_nested():
            conn.execute(text(insert), {**ok, "e": seeded["entry_b"], "c": seeded["calc_b"], "z": None})

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


def test_downgrade_refuses_while_zpe_is_recorded_then_converges(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        seeded = _seed(conn, "zdown")
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {_SOURCE} (solve_id, state_id, species_entry_id, calculation_id, zpe_calculation_id) "
                "VALUES (:s, :st, :e, :c, :z)"
            ),
            {
                "s": seeded["solve_id"],
                "st": seeded["state_id"],
                "e": seeded["entry_a"],
                "c": seeded["calc_a"],
                "z": seeded["calc_b"],
            },
        )
        conn.execute(
            text(f"UPDATE {_ENERGY} SET zpe_scale_factor = 0.98 WHERE state_id = :st"), {"st": seeded["state_id"]}
        )

    refused = harness.run("downgrade", _MIGRATION.parent, expect_success=False)
    assert refused.returncode != 0
    assert f"Cannot downgrade: 1 {_SOURCE} row(s) cite" in refused.stderr, refused.stderr[-2000:]
    assert "zpe_scale_factor" in _columns(harness.engine)

    with harness.engine.begin() as conn:
        conn.execute(text(f"UPDATE {_SOURCE} SET zpe_calculation_id = NULL"))
        conn.execute(text(f"UPDATE {_ENERGY} SET zpe_scale_factor = NULL"))
        conn.execute(
            text(
                f"UPDATE {_ENERGY} SET source_sum_comparison = 'not_compared', "
                "source_sum_not_compared_reason = 'geometry_not_paired' WHERE state_id = :st"
            ),
            {"st": seeded["state_id"]},
        )
    # A recorded new reason alone also refuses: the old token check could not hold it.
    refused_again = harness.run("downgrade", _MIGRATION.parent, expect_success=False)
    assert refused_again.returncode != 0
    with harness.engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {_ENERGY} SET source_sum_comparison = NULL, source_sum_not_compared_reason = NULL")
        )

    harness.run("downgrade", _MIGRATION.parent)
    assert "zpe_scale_factor" not in _columns(harness.engine)
    assert "zpe_calculation_id" not in _source_columns(harness.engine)
    with harness.engine.begin() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_ENERGY}")) == 2
        # The old token check is back: a new reason is refused again.
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(
                    text(
                        f"UPDATE {_ENERGY} SET source_sum_comparison = 'not_compared', "
                        "source_sum_not_compared_reason = 'zpe_scaling_unstated'"
                    )
                )
    harness.run("upgrade", _MIGRATION.revision)
    assert "zpe_scale_factor" in _columns(harness.engine)
