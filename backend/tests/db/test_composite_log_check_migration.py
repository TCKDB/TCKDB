"""Disposable-database contract for ``a9c3e7b1d5f2`` (ADR 0021, phase P7a).

The revision adds ``calc_composite_log_check``: what comparing a program-run composite
calculation with an attached output log concluded, recorded at upload so a read never parses a log.

* upgrade adds the enum and the table and writes no row; upgrade -> downgrade -> upgrade converges
  and ``alembic check`` is clean at head (the model and the revision agree);
* downgrade refuses, with a count, while a row exists, and deletes nothing;
* the table refuses a digest that is not 64 lowercase hex digits and a repeat of
  ``(calculation, digest)``;
* the table is frozen once its calculation is accepted (the accepted-science guard).
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationCompositeLogCheck
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    CompositeLogOutcome,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.db._migration_chain import revision_under_test
from tests.db.test_level_of_theory_basis_identity_migration import _Harness
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_MIGRATION = revision_under_test("a9c3e7b1d5f2")
_TABLE = "calc_composite_log_check"
_ENUM = "composite_log_outcome"
_TRIGGERS = {
    (_TABLE, "trg_as_child_calc_composite_log_check"),
    (_TABLE, "trg_as_truncate_calc_composite_log_check"),
}
_SHA = "ab" * 32


@pytest.fixture
def harness():
    created = _Harness("composite_log_check")
    yield created
    created.close()


def _alembic(harness, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", *args],
        cwd=harness.root,
        env=harness.env,
        capture_output=True,
        text=True,
        check=False,
    )


def _table_exists(engine) -> bool:
    with engine.connect() as conn:
        return bool(conn.scalar(text("SELECT count(*) FROM pg_class WHERE relname = :n"), {"n": _TABLE}))


def _enum_labels(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(text(f"SELECT unnest(enum_range(NULL::{_ENUM}))::text")))


def _triggers(engine) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                f"WHERE NOT t.tgisinternal AND c.relname = '{_TABLE}'"
            )
        ).all()
    return {(r[0], r[1]) for r in rows}


def _seed_composite(conn) -> int:
    species_id = conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), 'O', 'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0, 1, "
            "CAST('unspecified' AS stereo_kind)) RETURNING id"
        )
    )
    entry_id = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id})
    return conn.scalar(
        text("INSERT INTO calculation (type, species_entry_id) VALUES (CAST('composite' AS calc_type), :e) RETURNING id"),
        {"e": entry_id},
    )


def test_upgrade_adds_the_enum_and_the_table_and_writes_nothing(harness):
    harness.run("upgrade", _MIGRATION.parent)
    assert not _table_exists(harness.engine)
    assert _enum_labels_or_none(harness.engine) is None

    harness.run("upgrade", _MIGRATION.revision)

    assert _table_exists(harness.engine)
    assert _enum_labels(harness.engine) == [o.value for o in CompositeLogOutcome]
    with harness.engine.connect() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0
    assert _triggers(harness.engine) == _TRIGGERS

    harness.run("upgrade", "head")
    checked = _alembic(harness, "check")
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr


def _enum_labels_or_none(engine):
    with engine.connect() as conn:
        exists = conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = :n"), {"n": _ENUM})
    return _enum_labels(engine) if exists else None


def test_downgrade_removes_it_all_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.revision)
    harness.run("downgrade", _MIGRATION.parent)
    assert not _table_exists(harness.engine)
    assert _triggers(harness.engine) == set()
    assert _enum_labels_or_none(harness.engine) is None
    harness.run("upgrade", _MIGRATION.revision)
    assert _table_exists(harness.engine)
    assert _triggers(harness.engine) == _TRIGGERS


def test_downgrade_refuses_while_a_row_exists_and_deletes_nothing(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        calc_id = _seed_composite(conn)
        conn.execute(
            text(
                f"INSERT INTO {_TABLE} (calculation_id, artifact_sha256, outcome) "
                f"VALUES (:c, :s, CAST('confirmed' AS {_ENUM}))"
            ),
            {"c": calc_id, "s": _SHA},
        )

    refused = _alembic(harness, "downgrade", _MIGRATION.parent)

    assert refused.returncode != 0
    assert f"Cannot downgrade: 1 {_TABLE} row(s)" in refused.stderr + refused.stdout
    with harness.engine.connect() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 1
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision


@pytest.mark.parametrize(
    "digest",
    ["AB" * 32, "ab" * 31, "ab" * 33, "g" * 64, ""],
    ids=["uppercase", "short", "long", "not_hex", "empty"],
)
def test_the_table_refuses_a_digest_that_is_not_lowercase_sha256_hex(harness, digest):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        calc_id = _seed_composite(conn)
    with pytest.raises(DBAPIError), harness.engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {_TABLE} (calculation_id, artifact_sha256, outcome) "
                f"VALUES (:c, :s, CAST('confirmed' AS {_ENUM}))"
            ),
            {"c": calc_id, "s": digest},
        )


def test_the_table_refuses_a_second_observation_of_the_same_log(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        calc_id = _seed_composite(conn)
        conn.execute(
            text(f"INSERT INTO {_TABLE} (calculation_id, artifact_sha256, outcome) VALUES (:c, :s, CAST('confirmed' AS {_ENUM}))"),
            {"c": calc_id, "s": _SHA},
        )
    with pytest.raises(DBAPIError), harness.engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO {_TABLE} (calculation_id, artifact_sha256, outcome) VALUES (:c, :s, CAST('mismatch' AS {_ENUM}))"),
            {"c": calc_id, "s": _SHA},
        )


# -- the accepted-science guard, as behaviour --------------------------------


def _composite_with_check(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CMPLOG"))
    entry = make_species_entry(db_session, species)
    composite = make_calculation(db_session, type=CalculationType.composite, species_entry_id=entry.id)
    row = CalculationCompositeLogCheck(
        calculation_id=composite.id, artifact_sha256=_SHA, outcome=CompositeLogOutcome.confirmed
    )
    db_session.add(row)
    db_session.flush()
    return composite, row


def _approve(db_session, calc) -> None:
    actor = AppUser(username=f"composite-log-curator-{calc.id}", role=AppUserRole.curator)
    db_session.add(actor)
    db_session.flush()
    ensure_record_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id)
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )


def test_an_unaccepted_calculations_log_check_can_still_change(db_session):
    _composite, row = _composite_with_check(db_session)
    db_session.delete(row)
    db_session.flush()


def test_an_accepted_calculations_log_check_is_frozen(db_session):
    composite, row = _composite_with_check(db_session)
    _approve(db_session, composite)
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            CalculationCompositeLogCheck(
                calculation_id=composite.id, artifact_sha256="cd" * 32, outcome=CompositeLogOutcome.mismatch
            )
        )
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text(f"TRUNCATE {_TABLE}"))
