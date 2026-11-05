"""Disposable-database contract for ``d5f2b8c1a704`` (keywords decision, Stage 2).

The revision drops ``level_of_theory.keywords``, adds three nullable enum columns for the typed method qualifiers and
adds ``window`` to ``core_treatment``. The hash formula changed (the ``keywords`` key left the payload), so every stored
``lot_hash`` is stale under it: the revision refuses to run while ``level_of_theory`` has a row, in both directions.
There is no re-key and no backfill. What is held here:

* on an empty table, upgrade adds exactly the three columns and ``window`` and drops ``keywords``; downgrade restores the
  previous columns; upgrade again converges;
* a row at the parent makes ``upgrade`` fail with a message that names the reseed, and the schema is left as it was;
* a row at the revision makes ``downgrade`` fail the same way, and the schema is left as it was.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness

_MIGRATION = revision_under_test("d5f2b8c1a704")
_NEW_COLUMNS = {"relativistic_hamiltonian", "pno_truncation", "ri_approximation"}


@pytest.fixture
def harness():
    created = _Harness("lot_qualifiers")
    yield created
    created.close()


def _columns(engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'level_of_theory'")
            )
        )


def _core_values(engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.scalars(text("SELECT unnest(enum_range(NULL::core_treatment))::text")))


def _insert_level(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO level_of_theory (method, lot_hash, public_ref) VALUES ('b3lyp', :h, 'lot_x')"),
            {"h": "0" * 64},
        )


def _run_expecting_failure(harness, direction: str, revision: str):
    import subprocess

    if harness.engine is not None:
        harness.engine.dispose()
    completed = subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", direction, revision],
        cwd=harness.root, env=harness.env, check=False, capture_output=True, text=True,
    )
    assert completed.returncode != 0
    from conftest import _database_url
    from sqlalchemy import create_engine

    harness.engine = create_engine(_database_url(harness.db_name), pool_pre_ping=True)
    return completed.stdout + completed.stderr


def test_upgrade_on_an_empty_table_swaps_keywords_for_the_typed_columns(harness):
    harness.run("upgrade", _MIGRATION.parent)
    before = _columns(harness.engine)
    assert "keywords" in before and not (_NEW_COLUMNS & before)
    assert _core_values(harness.engine) == {"frozen_core", "all_electron"}

    harness.run("upgrade", _MIGRATION.revision)
    after = _columns(harness.engine)
    assert after == (before - {"keywords"}) | _NEW_COLUMNS
    assert _core_values(harness.engine) == {"frozen_core", "all_electron", "window"}


def test_downgrade_then_upgrade_converges_on_an_empty_table(harness):
    harness.run("upgrade", _MIGRATION.revision)
    at_revision = _columns(harness.engine)
    harness.run("downgrade", _MIGRATION.parent)
    assert "keywords" in _columns(harness.engine) and not (_NEW_COLUMNS & _columns(harness.engine))
    with harness.engine.connect() as conn:
        # The new types are gone with their columns; ``window`` stays in core_treatment (a value cannot be dropped).
        assert conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'ri_approximation'")) == 0
    harness.run("upgrade", _MIGRATION.revision)
    assert _columns(harness.engine) == at_revision


def test_upgrade_refuses_while_a_level_of_theory_exists_and_names_the_reseed(harness):
    harness.run("upgrade", _MIGRATION.parent)
    _insert_level(harness.engine)
    before = _columns(harness.engine)
    output = _run_expecting_failure(harness, "upgrade", _MIGRATION.revision)
    assert "reseed" in output.lower() and "level_of_theory holds 1 row" in output
    assert _columns(harness.engine) == before
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM level_of_theory")) == 1


def test_downgrade_refuses_while_a_level_of_theory_exists(harness):
    harness.run("upgrade", _MIGRATION.revision)
    _insert_level(harness.engine)
    before = _columns(harness.engine)
    output = _run_expecting_failure(harness, "downgrade", _MIGRATION.parent)
    assert "reseed" in output.lower()
    assert _columns(harness.engine) == before


def test_a_fresh_head_matches_the_models(harness):
    """``alembic check`` on an empty database taken to head: this revision's DDL is what the models say."""
    import subprocess

    harness.run("upgrade", "head")
    checked = subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", "check"],
        cwd=harness.root, env=harness.env, capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr
    assert not (_NEW_COLUMNS - _columns(harness.engine)) and "keywords" not in _columns(harness.engine)
