"""``thermo.thermodynamic_target_declared_by``: existing rows read depositor and are not updated."""

from __future__ import annotations

import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.base import Base
from app.db.models.common import SubmissionRecordType
from tests.db._migration_chain import revision_under_test
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.db.test_thermo_declarations_migration import _alembic, _seed_thermo_rows, harness  # noqa: F401
from tests.services.scientific_read._factories import make_species, make_species_entry, next_inchi_key

_REVISION = revision_under_test("e7a3c1d9f482")


def test_existing_rows_read_depositor_and_keep_their_data(harness):  # noqa: F811
    harness.run("upgrade", _REVISION.parent)
    ids = _seed_thermo_rows(harness.engine)
    with harness.engine.begin() as conn:
        conn.execute(
            text("UPDATE thermo SET thermodynamic_target_kind = 'equilibrium_ensemble' WHERE id = :i"), {"i": ids[0]}
        )

    harness.run("upgrade", _REVISION.revision)
    with harness.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT thermodynamic_target_kind, thermodynamic_target_declared_by FROM thermo ORDER BY id")
        ).all()
        assert [tuple(r) for r in rows] == [("equilibrium_ensemble", "depositor"), (None, "depositor")]
        assert conn.scalar(text("SELECT h298_kj_mol FROM thermo WHERE id = :i"), {"i": ids[0]}) == -74.6


def test_the_database_refuses_an_unknown_source(harness):  # noqa: F811
    harness.run("upgrade", _REVISION.revision)
    ids = _seed_thermo_rows(harness.engine)
    with pytest.raises(Exception, match="thermo_declaration_source"):
        with harness.engine.begin() as conn:
            conn.execute(
                text("UPDATE thermo SET thermodynamic_target_declared_by = 'someone' WHERE id = :i"), {"i": ids[0]}
            )


def test_downgrade_says_how_many_chemkin_declarations_it_forgets_and_drops_the_type(harness):  # noqa: F811
    harness.run("upgrade", _REVISION.revision)
    ids = _seed_thermo_rows(harness.engine)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE thermo SET thermodynamic_target_kind = 'equilibrium_ensemble', "
                "thermodynamic_target_declared_by = 'chemkin_format' WHERE id = :i"
            ),
            {"i": ids[0]},
        )
    downgraded = _alembic(harness, "downgrade", _REVISION.parent)
    assert downgraded.returncode == 0, downgraded.stderr[-3000:]
    output = downgraded.stdout + downgraded.stderr
    assert re.search(r"forgets: 1 CHEMKIN-format target declaration", output)
    assert "re-upgrading attributes these rows to the depositor" in output
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'thermo_declaration_source'")) == 0
        assert conn.scalar(text("SELECT count(*) FROM thermo")) == 2


_MIGRATION = Path(__file__).resolve().parents[2] / "alembic/versions/e7a3c1d9f482_thermo_target_declared_by.py"


@pytest.fixture
def migration(db_session, monkeypatch):
    spec = spec_from_file_location("thermo_target_declared_by_migration", _MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
    )
    return module


def test_an_approved_row_gets_the_default_without_any_update_firing(db_session, migration):
    """An approved row gets the default through ADD COLUMN, with no UPDATE for the guard to refuse."""
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("THMDECLBY")))
    row_id = db_session.scalar(
        text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol, "
            "enthalpy_reference_kind, thermodynamic_target_kind) "
            "VALUES (:ref, :entry, 'computed', -74.5, 'formation_298k', 'equilibrium_ensemble') RETURNING id"
        ),
        {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id},
    )
    _approve(db_session, SubmissionRecordType.thermo, row_id, _actor(db_session))

    migration.downgrade()  # the deployed shape: no column
    migration.upgrade()  # must not raise the accepted-science refusal

    row = db_session.execute(
        text(
            "SELECT thermodynamic_target_kind, thermodynamic_target_declared_by, h298_kj_mol "
            "FROM thermo WHERE id = :id"
        ),
        {"id": row_id},
    ).one()
    assert tuple(row) == ("equilibrium_ensemble", "depositor", -74.5)
    # The row really is accepted: the guard still refuses an UPDATE of it.
    with pytest.raises(DBAPIError, match="accepted"), db_session.begin_nested():
        db_session.execute(text("UPDATE thermo SET h298_kj_mol = 0 WHERE id = :id"), {"id": row_id})
