"""Disposable-database contract for ``a7d2c9e5b413`` (#659, the ``exponential_sqrt`` formula).

* upgrade adds the member to ``composite_extrapolation_formula`` and writes no row;
* a scheme term can then carry the formula and alpha (the ``exponent`` column);
* downgrade refuses, with a count and deleting nothing, while a term uses it, and
  otherwise removes the member leaving every other term as it was;
* upgrading again converges, and ``alembic check`` is clean at head.
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text

from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness

_MIGRATION = revision_under_test("a7d2c9e5b413")
_ORIGINAL = ["inverse_power", "inverse_power_shifted_half", "karton_martin_scf", "exponential_three_point"]


@pytest.fixture
def harness():
    created = _Harness("exp_sqrt_formula")
    yield created
    created.close()


def _labels(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(text("SELECT unnest(enum_range(NULL::composite_extrapolation_formula))::text")))


def _seed_term(conn, formula: str, exponent: float | None) -> int:
    scheme_id = conn.scalar(
        text(
            "INSERT INTO composite_scheme (kind, name, definition_hash) "
            "VALUES (CAST('extrapolation' AS composite_scheme_kind), 'seed', repeat(:h, 64)) RETURNING id"
        ),
        {"h": formula[:1]},
    )
    return conn.scalar(
        text(
            "INSERT INTO composite_scheme_term (scheme_id, position, operation, energy_component, formula, exponent) "
            "VALUES (:s, 0, CAST('extrapolation' AS composite_term_operation), "
            "CAST('reference' AS energy_component_kind), CAST(:f AS composite_extrapolation_formula), :x) RETURNING id"
        ),
        {"s": scheme_id, "f": formula, "x": exponent},
    )


def test_upgrade_adds_the_member_and_a_term_can_carry_alpha(harness):
    harness.run("upgrade", _MIGRATION.parent)
    assert _labels(harness.engine) == _ORIGINAL

    harness.run("upgrade", _MIGRATION.revision)
    assert _labels(harness.engine) == [*_ORIGINAL, "exponential_sqrt"]
    with harness.engine.begin() as conn:
        term = _seed_term(conn, "exponential_sqrt", 5.46)
    with harness.engine.connect() as conn:
        row = conn.execute(
            text("SELECT formula::text, exponent FROM composite_scheme_term WHERE id = :i"), {"i": term}
        ).one()
    assert (row[0], row[1]) == ("exponential_sqrt", 5.46)

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


def test_downgrade_refuses_while_a_term_uses_the_formula_and_deletes_nothing(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        _seed_term(conn, "exponential_sqrt", 4.42)

    refused = subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "downgrade", _MIGRATION.parent],
        cwd=harness.root,
        env=harness.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode != 0
    assert "Cannot downgrade: 1 composite_scheme_term row(s) use formula exponential_sqrt" in (
        refused.stderr + refused.stdout
    )
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM composite_scheme_term WHERE formula = 'exponential_sqrt'")) == 1
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision


def test_downgrade_removes_the_member_keeps_other_terms_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        kept = _seed_term(conn, "inverse_power", 3.0)

    harness.run("downgrade", _MIGRATION.parent)
    assert _labels(harness.engine) == _ORIGINAL
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT formula::text FROM composite_scheme_term WHERE id = :i"), {"i": kept}) == (
            "inverse_power"
        )

    harness.run("upgrade", _MIGRATION.revision)
    assert _labels(harness.engine) == [*_ORIGINAL, "exponential_sqrt"]
