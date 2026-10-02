"""Disposable-database contract for ``b4d8e2f6a1c9`` (ADR 0021, phase P5).

The revision adds the ``composite_input`` member of ``calculation_dependency_role``
and ``calc_composite_input``, frozen with the accepted-science guard.

* upgrade adds both and writes no row; upgrade -> downgrade -> upgrade converges
  and ``alembic check`` is clean at head (the model and the revision agree);
* downgrade refuses, with a count, while an input row or a ``composite_input``
  edge exists, and deletes nothing;
* downgrade otherwise restores the four partial unique indexes on
  ``dependency_role`` that the enum rebuild forces it to drop;
* the table is frozen once its composite calculation is accepted, and the
  cited calculation is *not* an owner (accepting an input freezes nothing).
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationCompositeInput
from app.db.models.common import (
    AppUserRole,
    CalculationType,
    CompositeInputSlot,
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

_MIGRATION = revision_under_test("b4d8e2f6a1c9")
_TABLE = "calc_composite_input"
_TRIGGERS = {
    (_TABLE, "trg_as_child_calc_composite_input"),
    (_TABLE, "trg_as_truncate_calc_composite_input"),
}
_ROLE_INDEXES = {
    "uq_calculation_dependency_child_calculation_id_freq_on",
    "uq_calculation_dependency_child_calculation_id_optimized_from",
    "uq_calculation_dependency_child_calculation_id_scan_parent",
    "uq_calculation_dependency_child_calculation_id_single_point_on",
}


@pytest.fixture
def harness():
    created = _Harness("composite_input")
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


def _role_labels(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(text("SELECT unnest(enum_range(NULL::calculation_dependency_role))::text")))


def _triggers(engine) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                f"WHERE NOT t.tgisinternal AND c.relname = '{_TABLE}'"
            )
        ).all()
    return {(r[0], r[1]) for r in rows}


def _role_indexes(engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'calculation_dependency' "
                "AND indexname LIKE 'uq_calculation_dependency_child_calculation_id_%'"
            )
        ).all()
    return {r[0]: r[1] for r in rows}


def _seed_two_calculations(conn) -> tuple[int, int]:
    species_id = conn.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES (CAST('molecule' AS molecule_kind), 'O', 'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0, 1, "
            "CAST('unspecified' AS stereo_kind)) RETURNING id"
        )
    )
    entry_id = conn.scalar(text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id})
    ids = [
        conn.scalar(
            text("INSERT INTO calculation (type, species_entry_id) VALUES (CAST(:t AS calc_type), :e) RETURNING id"),
            {"t": calc_type, "e": entry_id},
        )
        for calc_type in ("sp", "composite")
    ]
    return ids[0], ids[1]


def test_upgrade_adds_the_role_and_the_table_and_writes_nothing(harness):
    harness.run("upgrade", _MIGRATION.parent)
    assert "composite_input" not in _role_labels(harness.engine)
    assert not _table_exists(harness.engine)

    harness.run("upgrade", _MIGRATION.revision)

    assert "composite_input" in _role_labels(harness.engine)
    assert _table_exists(harness.engine)
    with harness.engine.connect() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0
    assert _triggers(harness.engine) == _TRIGGERS

    harness.run("upgrade", "head")
    checked = _alembic(harness, "check")
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr


def test_downgrade_removes_it_all_restores_the_indexes_and_upgrade_again_converges(harness):
    harness.run("upgrade", _MIGRATION.parent)
    indexes_before = _role_indexes(harness.engine)
    labels_before = _role_labels(harness.engine)
    assert set(indexes_before) == _ROLE_INDEXES
    with harness.engine.begin() as conn:
        sp_id, composite_id = _seed_two_calculations(conn)
        conn.execute(
            text(
                "INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role) "
                "VALUES (:p, :c, CAST('single_point_on' AS calculation_dependency_role))"
            ),
            {"p": sp_id, "c": composite_id},
        )

    harness.run("upgrade", _MIGRATION.revision)
    harness.run("downgrade", _MIGRATION.parent)

    assert _role_labels(harness.engine) == labels_before
    assert not _table_exists(harness.engine)
    assert _triggers(harness.engine) == set()
    # The recast kept the rows, and the four partial unique indexes are back, verbatim.
    assert _role_indexes(harness.engine) == indexes_before
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT dependency_role::text FROM calculation_dependency")) == "single_point_on"

    harness.run("upgrade", _MIGRATION.revision)
    assert "composite_input" in _role_labels(harness.engine)
    assert _triggers(harness.engine) == _TRIGGERS


def test_downgrade_refuses_while_an_input_row_exists_and_deletes_nothing(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        sp_id, composite_id = _seed_two_calculations(conn)
        conn.execute(
            text(
                "INSERT INTO calc_composite_input (calculation_id, term_position, slot, input_calculation_id) "
                "VALUES (:c, 0, CAST('value' AS composite_input_slot), :s)"
            ),
            {"c": composite_id, "s": sp_id},
        )

    refused = _alembic(harness, "downgrade", _MIGRATION.parent)

    assert refused.returncode != 0
    assert "Cannot downgrade: 1 calc_composite_input row(s)" in refused.stderr + refused.stdout
    with harness.engine.connect() as conn:
        assert conn.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 1
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision


def test_downgrade_refuses_while_a_composite_input_edge_exists(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        sp_id, composite_id = _seed_two_calculations(conn)
        conn.execute(
            text(
                "INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role) "
                "VALUES (:p, :c, CAST('composite_input' AS calculation_dependency_role))"
            ),
            {"p": sp_id, "c": composite_id},
        )

    refused = _alembic(harness, "downgrade", _MIGRATION.parent)

    assert refused.returncode != 0
    assert "edge(s) with role 'composite_input'" in refused.stderr + refused.stdout


@pytest.mark.parametrize(
    "bad_row",
    [
        "(:c, 0, CAST('cardinal' AS composite_input_slot), :s, NULL)",  # a cardinal slot without its number
        "(:c, 0, CAST('value' AS composite_input_slot), :s, 3)",  # a number on a slot that has none
        "(:c, 0, CAST('value' AS composite_input_slot), :c, NULL)",  # a calculation its own input
        "(:c, -1, CAST('value' AS composite_input_slot), :s, NULL)",  # a negative position
    ],
    ids=["cardinal_without_number", "number_on_value", "own_input", "negative_position"],
)
def test_the_table_refuses_what_its_checks_name(harness, bad_row):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        sp_id, composite_id = _seed_two_calculations(conn)
    with pytest.raises(DBAPIError), harness.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO calc_composite_input "
                "(calculation_id, term_position, slot, input_calculation_id, cardinal_number) "
                f"VALUES {bad_row}"
            ),
            {"c": composite_id, "s": sp_id},
        )


# -- the accepted-science guard, as behaviour --------------------------------


def _composite_with_input(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CMPINP"))
    entry = make_species_entry(db_session, species)
    composite = make_calculation(db_session, type=CalculationType.composite, species_entry_id=entry.id)
    sp = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    row = CalculationCompositeInput(
        calculation_id=composite.id,
        term_position=0,
        slot=CompositeInputSlot.value,
        input_calculation_id=sp.id,
    )
    db_session.add(row)
    db_session.flush()
    return composite, sp, row


def _approve(db_session, calc) -> None:
    actor = AppUser(username=f"composite-input-curator-{calc.id}", role=AppUserRole.curator)
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


def test_an_unaccepted_composites_inputs_can_still_change(db_session):
    _composite, sp, row = _composite_with_input(db_session)
    row.cardinal_number = None
    db_session.delete(row)
    db_session.flush()
    assert sp.id is not None


def test_an_accepted_composites_inputs_are_frozen(db_session):
    composite, sp, row = _composite_with_input(db_session)
    _approve(db_session, composite)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(row)
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            CalculationCompositeInput(
                calculation_id=composite.id,
                term_position=1,
                slot=CompositeInputSlot.value,
                input_calculation_id=sp.id,
            )
        )
        db_session.flush()
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text(f"TRUNCATE {_TABLE}"))


def test_accepting_the_cited_calculation_freezes_nothing_about_the_composite(db_session):
    """The input is a citation, not an owner: only the composite's own acceptance locks the rows."""
    _composite, sp, row = _composite_with_input(db_session)
    _approve(db_session, sp)
    db_session.delete(row)
    db_session.flush()


# -- energy_component_kind gains correlation_excluding_triples ---------------

_COMPONENT = "correlation_excluding_triples"


def _component_labels(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.scalars(text("SELECT unnest(enum_range(NULL::energy_component_kind))::text")))


def _seed_scheme_term(conn, component: str) -> None:
    scheme_id = conn.scalar(
        text(
            "INSERT INTO composite_scheme (kind, name, definition_hash, public_ref) "
            "VALUES (CAST('extrapolation' AS composite_scheme_kind), 'x', repeat('a', 64), 'csch_migration_probe') "
            "RETURNING id"
        )
    )
    conn.execute(
        text(
            "INSERT INTO composite_scheme_term (scheme_id, position, operation, energy_component) "
            "VALUES (:s, 0, CAST('value' AS composite_term_operation), CAST(:c AS energy_component_kind))"
        ),
        {"s": scheme_id, "c": component},
    )


def test_the_component_enum_gains_the_ccsd_only_member_and_downgrade_restores_it(harness):
    harness.run("upgrade", _MIGRATION.parent)
    before = _component_labels(harness.engine)
    assert _COMPONENT not in before
    harness.run("upgrade", _MIGRATION.revision)
    assert _component_labels(harness.engine) == [*before, _COMPONENT]
    harness.run("downgrade", _MIGRATION.parent)
    assert _component_labels(harness.engine) == before
    harness.run("upgrade", _MIGRATION.revision)
    assert _COMPONENT in _component_labels(harness.engine)
    harness.run("upgrade", "head")
    checked = _alembic(harness, "check")
    assert checked.returncode == 0, checked.stderr[-2000:] + checked.stdout[-2000:]


def test_downgrade_keeps_rows_that_use_the_older_components_through_the_recast(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        sp_id, _ = _seed_two_calculations(conn)
        conn.execute(
            text(
                "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
                "VALUES (:c, CAST('correlation' AS energy_component_kind), -0.3)"
            ),
            {"c": sp_id},
        )
        _seed_scheme_term(conn, "correlation")
    harness.run("downgrade", _MIGRATION.parent)
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT component::text FROM calc_sp_energy_component")) == "correlation"
        assert conn.scalar(text("SELECT energy_component::text FROM composite_scheme_term")) == "correlation"


def test_downgrade_refuses_while_a_scheme_term_reads_the_ccsd_only_component(harness):
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        _seed_scheme_term(conn, _COMPONENT)
    refused = _alembic(harness, "downgrade", _MIGRATION.parent)
    assert refused.returncode != 0
    assert f"composite_scheme_term.energy_component value(s) '{_COMPONENT}'" in refused.stderr + refused.stdout
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == _MIGRATION.revision
        assert conn.scalar(text("SELECT count(*) FROM composite_scheme_term")) == 1


def test_downgrade_refuses_while_a_single_point_stores_the_derived_component(harness):
    """The wire never lets one in; the refusal is for a row that got there past it."""
    harness.run("upgrade", _MIGRATION.revision)
    with harness.engine.begin() as conn:
        sp_id, _ = _seed_two_calculations(conn)
        conn.execute(
            text(
                "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
                "VALUES (:c, CAST(:k AS energy_component_kind), -0.3)"
            ),
            {"c": sp_id, "k": _COMPONENT},
        )
    refused = _alembic(harness, "downgrade", _MIGRATION.parent)
    assert refused.returncode != 0
    assert f"calc_sp_energy_component.component value(s) '{_COMPONENT}'" in refused.stderr + refused.stdout
