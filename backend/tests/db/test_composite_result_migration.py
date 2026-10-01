"""Disposable-database contract for ``f3b7d2a9c514`` (ADR 0021, phase P3a).

The revision adds the ``composite`` member of ``calc_type``, the
``composite_assembly`` enum, and ``calc_composite_result`` / ``calc_composite_term``
with the accepted-science guard ``calc_sp_result`` carries.

* upgrade adds all of it and writes no row;
* downgrade refuses, with a count, while any composite calculation or result
  row exists (it deletes nothing), and otherwise removes the two tables, the
  new enum and the ``composite`` member, leaving every other row as it was;
* upgrading again converges, and ``alembic check`` is clean at head;
* the two tables are frozen once their calculation has been accepted, like
  ``calc_sp_result`` (the trigger registry test holds the registry; the
  behaviour is held here, because a registry entry can name a guard that never
  fires).
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationCompositeResult, CalculationCompositeTerm
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    CompositeAssembly,
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

_MIGRATION = revision_under_test("f3b7d2a9c514")
_TABLES = ("calc_composite_result", "calc_composite_term")
_TRIGGERS = (
    ("calc_composite_result", "trg_as_child_calc_composite_result"),
    ("calc_composite_term", "trg_as_child_calc_composite_term"),
    ("calc_composite_result", "trg_as_truncate_calc_composite_result"),
    ("calc_composite_term", "trg_as_truncate_calc_composite_term"),
)


@pytest.fixture
def harness():
    created = _Harness("composite_result")
    yield created
    created.close()


def _exists(engine, kind: str, name: str) -> bool:
    catalog, column = {"table": ("pg_class", "relname"), "type": ("pg_type", "typname")}[kind]
    with engine.connect() as conn:
        return bool(conn.scalar(text(f"SELECT count(*) FROM {catalog} WHERE {column} = :n"), {"n": name}))


def _calc_type_labels(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(text("SELECT unnest(enum_range(NULL::calc_type))::text")))


def _triggers(engine) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND c.relname IN ('calc_composite_result', 'calc_composite_term')"
            )
        ).all()
    return {(r[0], r[1]) for r in rows}


def _seed_calculation(conn, calc_type: str) -> int:
    species_id = conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), 'O', 'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0, 1, "
            "CAST('unspecified' AS stereo_kind)) RETURNING id"
        )
    )
    entry_id = conn.scalar(
        text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id}
    )
    return conn.scalar(
        text(
            "INSERT INTO calculation (type, species_entry_id) "
            "VALUES (CAST(:t AS calc_type), :e) RETURNING id"
        ),
        {"t": calc_type, "e": entry_id},
    )


def _alembic(harness, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["conda", "run", "-n", "tckdb_env", "alembic", *args],
        cwd=harness.root,
        env=harness.env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_upgrade_adds_the_type_the_enum_and_the_tables_and_writes_nothing(harness):
    harness.run("upgrade", _MIGRATION.parent)
    assert "composite" not in _calc_type_labels(harness.engine)
    for table in _TABLES:
        assert not _exists(harness.engine, "table", table)
    assert not _exists(harness.engine, "type", "composite_assembly")

    harness.run("upgrade", _MIGRATION.revision)

    assert "composite" in _calc_type_labels(harness.engine)
    assert _exists(harness.engine, "type", "composite_assembly")
    for table in _TABLES:
        assert _exists(harness.engine, "table", table)
        with harness.engine.connect() as conn:
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    assert _triggers(harness.engine) == set(_TRIGGERS)

    # ``alembic check`` compares with the models, which describe head.
    harness.run("upgrade", "head")
    checked = _alembic(harness, "check")
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr


def test_downgrade_removes_it_all_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.parent)
    with harness.engine.begin() as conn:
        sp_id = _seed_calculation(conn, "sp")
    harness.run("upgrade", _MIGRATION.revision)
    labels_after_upgrade = _calc_type_labels(harness.engine)

    harness.run("downgrade", _MIGRATION.parent)

    assert _calc_type_labels(harness.engine) == [label for label in labels_after_upgrade if label != "composite"]
    assert not _exists(harness.engine, "type", "composite_assembly")
    for table in _TABLES:
        assert not _exists(harness.engine, "table", table)
    assert _triggers(harness.engine) == set()
    with harness.engine.connect() as conn:
        # A row of another type survived the recast of the column.
        assert conn.scalar(text("SELECT type::text FROM calculation WHERE id = :i"), {"i": sp_id}) == "sp"

    harness.run("upgrade", _MIGRATION.revision)
    assert _calc_type_labels(harness.engine) == labels_after_upgrade
    assert _triggers(harness.engine) == set(_TRIGGERS)


def test_downgrade_refuses_while_a_composite_calculation_exists_and_deletes_nothing(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        calc_id = _seed_calculation(conn, "composite")
        conn.execute(
            text(
                "INSERT INTO calc_composite_result (calculation_id, assembly) "
                "VALUES (:c, CAST('program_run' AS composite_assembly))"
            ),
            {"c": calc_id},
        )

    refused = _alembic(harness, "downgrade", _MIGRATION.parent)

    assert refused.returncode != 0
    assert "Cannot downgrade: 1 calculation row(s) of type 'composite'" in refused.stderr + refused.stdout
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM calculation WHERE type = 'composite'")) == 1
        assert conn.scalar(text("SELECT count(*) FROM calc_composite_result")) == 1
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision

    with harness.engine.begin() as conn:
        conn.execute(text("DELETE FROM calc_composite_result"))
        conn.execute(text("DELETE FROM calculation WHERE id = :c"), {"c": calc_id})
    harness.run("downgrade", _MIGRATION.parent)


@pytest.mark.parametrize(
    ("insert", "needle"),
    [
        (
            "INSERT INTO calc_composite_result (calculation_id, assembly) "
            "VALUES (:c, CAST('program_run' AS composite_assembly))",
            "calc_composite_result row(s)",
        ),
        (
            "INSERT INTO calc_composite_term (calculation_id, term_position, value_hartree) VALUES (:c, 0, -1.0)",
            "calc_composite_term row(s)",
        ),
    ],
)
def test_downgrade_refuses_on_stray_result_or_term_rows_too(harness, insert, needle):
    """Rows on a calculation of another type still block: they would be silently dropped."""
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        calc_id = _seed_calculation(conn, "sp")
        conn.execute(text(insert), {"c": calc_id})

    refused = _alembic(harness, "downgrade", _MIGRATION.parent)

    assert refused.returncode != 0
    assert f"Cannot downgrade: 1 {needle}" in refused.stderr + refused.stdout


# -- the accepted-science guard, as behaviour -------------------------------


def _accepted_composite(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CMPFRZ"))
    entry = make_species_entry(db_session, species)
    calc = make_calculation(db_session, type=CalculationType.composite, species_entry_id=entry.id)
    result = CalculationCompositeResult(
        calculation_id=calc.id, assembly=CompositeAssembly.program_run, electronic_energy_hartree=-76.0
    )
    term = CalculationCompositeTerm(calculation_id=calc.id, term_position=0, value_hartree=-76.0)
    db_session.add_all([result, term])
    db_session.flush()
    return calc, result, term


def _approve(db_session, calc) -> None:
    actor = AppUser(username="composite-curator", role=AppUserRole.curator)
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


def test_an_unaccepted_calculations_composite_rows_can_still_change(db_session) -> None:
    _calc, result, term = _accepted_composite(db_session)
    result.electronic_energy_hartree = -76.5
    term.value_hartree = -76.5
    db_session.flush()


def test_an_accepted_calculations_composite_rows_are_frozen(db_session) -> None:
    calc, result, term = _accepted_composite(db_session)
    _approve(db_session, calc)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        result.electronic_energy_hartree = -1.0
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        term.value_hartree = -1.0
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(CalculationCompositeTerm(calculation_id=calc.id, term_position=1, value_hartree=-0.5))
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(term)
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("TRUNCATE calc_composite_term"))
