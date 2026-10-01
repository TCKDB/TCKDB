"""``kinetics.t0_k``: existing rows mean 1 K, and nothing else is touched (#620).

A row stored before the column existed was ``A * T**n``, which is T0 = 1 K, so
the server default *is* the backfill and there is no data step to get wrong.
This drives the revision's own ``upgrade``/``downgrade`` inside the test
transaction (DDL is transactional in PostgreSQL, so the fixture's rollback
restores the schema), with a row that predates the column, an approved row
that the accepted-science guards protect, and a row that departs from the
default.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.base import Base
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.db.test_accepted_science_immutability import _actor, _approve

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/f1c8a4d7b263_add_kinetics_arrhenius_reference_temperature.py"
)


@pytest.fixture
def migration(db_session, monkeypatch):
    spec = spec_from_file_location("kinetics_t0_migration", _MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "op",
        Operations(
            MigrationContext.configure(
                db_session.connection(), opts={"target_metadata": Base.metadata}
            )
        ),
    )
    return module


def _reaction_entry(db_session) -> int:
    reaction_id = db_session.scalar(
        text("INSERT INTO chem_reaction (reversible, stoichiometry_hash, public_ref) VALUES (true, :h, :r) RETURNING id"),
        {"h": uuid4().hex * 2, "r": "rxn_" + uuid4().hex[:26]},
    )
    return db_session.scalar(
        text("INSERT INTO reaction_entry (reaction_id, public_ref) VALUES (:rid, :r) RETURNING id"),
        {"rid": reaction_id, "r": "rxe_" + uuid4().hex[:26]},
    )


def _kinetics(db_session, entry_id: int, *, with_t0: float | None) -> int:
    columns, values = "", ""
    params = {"entry": entry_id, "ref": "kin_" + uuid4().hex[:26]}
    if with_t0 is not None:
        columns, values, params["t0"] = ", t0_k", ", :t0", with_t0
    return db_session.scalar(
        text(
            f"INSERT INTO kinetics (public_ref, reaction_entry_id, scientific_origin, a, n{columns}) "
            f"VALUES (:ref, :entry, 'computed', 1e10, 2{values}) RETURNING id"
        ),
        params,
    )


def _t0(db_session, kinetics_id: int) -> float:
    return db_session.scalar(text("SELECT t0_k FROM kinetics WHERE id = :id"), {"id": kinetics_id})


def test_a_row_that_predates_the_column_reads_as_one_kelvin(db_session, migration):
    entry = _reaction_entry(db_session)
    migration.downgrade()
    assert db_session.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'kinetics' AND column_name = 't0_k'"
        )
    ) == 0

    # Inserted while the column does not exist: this is a deployed row.
    old_id = db_session.scalar(
        text(
            "INSERT INTO kinetics (public_ref, reaction_entry_id, scientific_origin, a, n) "
            "VALUES (:ref, :entry, 'computed', 1e10, 2) RETURNING id"
        ),
        {"ref": "kin_" + uuid4().hex[:26], "entry": entry},
    )
    migration.upgrade()

    assert _t0(db_session, old_id) == 1.0
    # The rest of the row is exactly as deposited: a is still A at T0 = 1 K.
    assert tuple(
        db_session.execute(text("SELECT a, n FROM kinetics WHERE id = :id"), {"id": old_id}).one()
    ) == (1e10, 2)


def test_an_approved_row_survives_the_upgrade_unchanged(db_session, migration):
    """ADD COLUMN ... DEFAULT fires no UPDATE trigger, so the accepted-science
    guards neither refuse the upgrade nor see a mutation of approved science."""
    entry = _reaction_entry(db_session)
    kinetics_id = _kinetics(db_session, entry, with_t0=None)
    _approve(db_session, SubmissionRecordType.kinetics, kinetics_id, _actor(db_session))

    migration.downgrade()
    migration.upgrade()

    assert _t0(db_session, kinetics_id) == 1.0
    assert (
        db_session.scalar(
            text("SELECT status FROM record_review WHERE record_type = 'kinetics' AND record_id = :id"),
            {"id": kinetics_id},
        )
        == RecordReviewStatus.approved.value
    )


def test_an_approved_row_cannot_have_its_t0_changed_afterwards(db_session):
    """Changing T0 changes what ``a`` means, so the existing whole-row guard on
    accepted kinetics must cover the new column without being told about it."""
    entry = _reaction_entry(db_session)
    kinetics_id = _kinetics(db_session, entry, with_t0=298.0)
    _approve(db_session, SubmissionRecordType.kinetics, kinetics_id, _actor(db_session))

    with pytest.raises(DBAPIError, match="accepted"), db_session.begin_nested():
        db_session.execute(text("UPDATE kinetics SET t0_k = 1 WHERE id = :id"), {"id": kinetics_id})
    assert _t0(db_session, kinetics_id) == 298.0


def test_the_column_contract_after_upgrade(db_session, migration):
    migration.downgrade()
    migration.upgrade()
    column = db_session.execute(
        text(
            "SELECT is_nullable, column_default, data_type FROM information_schema.columns "
            "WHERE table_name = 'kinetics' AND column_name = 't0_k'"
        )
    ).one()
    assert (column.is_nullable, column.column_default, column.data_type) == (
        "NO",
        "'1'::double precision",
        "double precision",
    )
    assert db_session.scalar(
        text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_kinetics_t0_k_finite_positive'")
    ) == 1

    entry = _reaction_entry(db_session)
    with pytest.raises(IntegrityError, match="t0_k_finite_positive"), db_session.begin_nested():
        _kinetics(db_session, entry, with_t0=0.0)


def test_downgrade_removes_exactly_what_upgrade_added(db_session, migration):
    migration.downgrade()
    assert db_session.scalar(
        text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_kinetics_t0_k_finite_positive'")
    ) == 0
    assert db_session.scalar(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'kinetics' AND column_name = 't0_k'"
        )
    ) == 0
    # The pre-existing kinetics constraints are still there.
    assert db_session.scalar(
        text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_kinetics_degeneracy_finite_positive'")
    ) == 1
    migration.upgrade()
