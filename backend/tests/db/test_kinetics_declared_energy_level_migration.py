"""``kinetics.energy_level_of_theory_id``: additive, no backfill, legacy rows untouched.

Drives the revision's own ``upgrade``/``downgrade`` inside the test transaction (PostgreSQL DDL
is transactional, so the fixture's rollback restores the schema). A row inserted while the
column does not exist stands for a deployed row; after the upgrade it reads NULL, keeps the
digests stored reviews and assessments are keyed on, and stays approved if it was.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.db.base import Base
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.services.consistency.core import snapshot
from app.services.reproducibility_rubric import _mapped_columns
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.db.test_kinetics_declarations_migration import _columns, _legacy_kinetics, _reaction_entry

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/a3e7c1d9b542_declare_kinetics_energy_level_of_theory.py"
)
_COLUMN = "energy_level_of_theory_id"
_FK = "fk_kinetics_energy_level_of_theory_id_level_of_theory"
_INDEX = "ix_kinetics_energy_level_of_theory_id"


@pytest.fixture
def migration(db_session, monkeypatch):
    spec = spec_from_file_location("kinetics_energy_level_migration", _MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
    )
    return module


def _count(db_session, query: str, **params) -> int:
    return db_session.scalar(text(query), params)


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_existing_rows_stay_null_and_keep_their_digests(db_session, migration, approved):
    entry = _reaction_entry(db_session)
    if approved:
        legacy_id = _legacy_kinetics(db_session, entry)
        _approve(db_session, SubmissionRecordType.kinetics, legacy_id, _actor(db_session))
        migration.downgrade()
    else:
        migration.downgrade()
        legacy_id = _legacy_kinetics(db_session, entry)
    assert _COLUMN not in _columns(db_session)

    migration.upgrade()

    assert _COLUMN in _columns(db_session)
    # No backfill: the declared level is not recoverable, so a row that never declared one reads NULL.
    assert db_session.scalar(text("SELECT energy_level_of_theory_id FROM kinetics WHERE id = :i"), {"i": legacy_id}) is None
    kinetics = db_session.get(Kinetics, legacy_id)
    assert _COLUMN not in snapshot(kinetics)
    assert _COLUMN not in _mapped_columns(kinetics)
    if approved:
        status = db_session.scalar(
            text("SELECT status FROM record_review WHERE record_type = 'kinetics' AND record_id = :i"), {"i": legacy_id}
        )
        assert status == RecordReviewStatus.approved.value


def test_the_column_is_a_deferrable_indexed_foreign_key_to_level_of_theory(db_session):
    assert _count(db_session, "SELECT count(*) FROM pg_constraint WHERE conname = :n", n=_FK) == 1
    deferrable = db_session.scalar(
        text("SELECT condeferrable AND NOT condeferred FROM pg_constraint WHERE conname = :n"), {"n": _FK}
    )
    assert deferrable is True
    assert _count(db_session, "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=_INDEX) == 1
    assert db_session.scalar(
        text("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'kinetics' AND column_name = :c"),
        {"c": _COLUMN},
    ) == "YES"


def test_downgrade_removes_exactly_what_upgrade_added_and_reports_what_it_forgets(db_session, migration, capsys):
    entry = _reaction_entry(db_session)
    legacy_id = _legacy_kinetics(db_session, entry)
    lot_id = db_session.scalar(
        text(
            "INSERT INTO level_of_theory (public_ref, method, lot_hash) VALUES (:r, 'b3lyp', :h) RETURNING id"
        ),
        {"r": "lot_" + "k" * 26, "h": "e" * 64},
    )
    db_session.execute(
        text("UPDATE kinetics SET energy_level_of_theory_id = :l WHERE id = :i"), {"l": lot_id, "i": legacy_id}
    )

    migration.downgrade()

    assert "forgets: 1 kinetics declared energy level(s)" in capsys.readouterr().out
    assert _COLUMN not in _columns(db_session)
    assert _count(db_session, "SELECT count(*) FROM pg_constraint WHERE conname = :n", n=_FK) == 0
    assert _count(db_session, "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=_INDEX) == 0
    # The row survives; only the declaration is forgotten.
    assert _count(db_session, "SELECT count(*) FROM kinetics WHERE id = :i", i=legacy_id) == 1

    migration.upgrade()
    assert db_session.scalar(text("SELECT energy_level_of_theory_id FROM kinetics WHERE id = :i"), {"i": legacy_id}) is None
