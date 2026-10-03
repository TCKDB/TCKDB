"""``thermo`` target and protocol declarations: additive columns, legacy rows untouched.

Drives the revision's own ``upgrade``/``downgrade`` inside the test transaction
(PostgreSQL DDL is transactional, so the fixture's rollback restores the
schema). A row inserted while the columns do not exist stands for a deployed
row; after the upgrade it must read NULL on all three, keep the digests that
stored reviews are keyed on, and stay frozen if it was approved.
"""

from __future__ import annotations

import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.common import ScientificOriginKind, SubmissionRecordType
from app.db.models.thermo import Thermo
from app.services.consistency.core import encoded, thermo_inputs
from app.services.reproducibility_rubric import _mapped_columns
from tests.db._migration_chain import revision_under_test
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.db.test_level_of_theory_basis_identity_migration import _Harness
from tests.services.scientific_read._factories import (
    make_conformer_group,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_MIGRATION = Path(__file__).resolve().parents[2] / "alembic/versions/b3d8f1a6c924_declare_thermo_target_and_protocol.py"
NEW_COLUMNS = {"thermodynamic_target_kind", "target_conformer_group_id", "protocol_declaration"}
PROTOCOL = '{"version": 1, "recipe": {"name": "g4"}}'


@pytest.fixture
def migration(db_session, monkeypatch):
    spec = spec_from_file_location("thermo_declarations_migration", _MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
    )
    return module


def _entry(db_session, tag: str):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


def _legacy_thermo(db_session, entry_id: int) -> int:
    """A thermo row as the previous schema stored it (no declaration columns exist)."""
    return db_session.scalar(
        text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol, "
            "enthalpy_reference_kind) VALUES (:ref, :entry, 'computed', -74.5, 'formation_298k') RETURNING id"
        ),
        {"ref": "thm_" + uuid4().hex[:26], "entry": entry_id},
    )


def _columns(db_session) -> set[str]:
    return set(
        db_session.scalars(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'thermo'")
        )
    )


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_existing_rows_stay_null_and_keep_their_digests(db_session, migration, approved):
    entry = _entry(db_session, "THMDECLMIG")
    if approved:
        # Approval locks the row through the ORM, so it has to happen while the
        # ORM's columns exist. The row carries no declaration, which is exactly
        # what a deployed row looks like; the downgrade then upgrade is the
        # deployment: ADD COLUMN fires no UPDATE trigger, so the accepted-science
        # guard must neither refuse it nor see a mutation.
        legacy_id = _legacy_thermo(db_session, entry.id)
        _approve(db_session, SubmissionRecordType.thermo, legacy_id, _actor(db_session))
        migration.downgrade()
    else:
        migration.downgrade()
        legacy_id = _legacy_thermo(db_session, entry.id)
    assert not (NEW_COLUMNS & _columns(db_session))

    migration.upgrade()

    assert NEW_COLUMNS <= _columns(db_session)
    row = db_session.execute(
        text(
            "SELECT thermodynamic_target_kind, target_conformer_group_id, protocol_declaration, "
            "h298_kj_mol, enthalpy_reference_kind FROM thermo WHERE id = :id"
        ),
        {"id": legacy_id},
    ).one()
    assert tuple(row) == (None, None, None, -74.5, "formation_298k")

    # Neither digest a stored review is keyed on moves: the new columns hold the
    # value the row implicitly had, so they stay out of both snapshots.
    thermo = db_session.get(Thermo, legacy_id)
    assert NEW_COLUMNS.isdisjoint(thermo_inputs(thermo))
    assert NEW_COLUMNS.isdisjoint(_mapped_columns(thermo))


def test_upgrade_downgrade_upgrade_round_trips_with_declared_rows(db_session, migration):
    entry = _entry(db_session, "THMDECLRT")
    group = make_conformer_group(db_session, entry)
    thermo_id = db_session.scalar(
        text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, thermodynamic_target_kind, "
            "target_conformer_group_id, protocol_declaration) VALUES (:ref, :entry, 'computed', "
            "'single_conformer', :group, CAST(:protocol AS jsonb)) RETURNING id"
        ),
        {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id, "group": group.id, "protocol": PROTOCOL},
    )

    migration.downgrade()
    assert not (NEW_COLUMNS & _columns(db_session))
    assert db_session.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'thermo_target_kind'")) == 0
    # The row survives; only the declarations are forgotten.
    assert db_session.scalar(text("SELECT count(*) FROM thermo WHERE id = :id"), {"id": thermo_id}) == 1

    migration.upgrade()
    assert NEW_COLUMNS <= _columns(db_session)
    assert db_session.scalar(
        text("SELECT thermodynamic_target_kind FROM thermo WHERE id = :id"), {"id": thermo_id}
    ) is None


def test_downgrade_removes_exactly_what_upgrade_added(db_session, migration):
    migration.downgrade()
    for name in (
        "ck_thermo_target_group_iff_single_conformer",
        "ck_thermo_protocol_declaration_versioned_object",
        "fk_thermo_target_conformer_group_id_conformer_group",
    ):
        assert db_session.scalar(text("SELECT count(*) FROM pg_constraint WHERE conname = :n"), {"n": name}) == 0
    assert db_session.scalar(text("SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_thermo_target_conformer_group_id'")) == 0
    # The enthalpy declaration this table already had is untouched.
    assert "enthalpy_reference_kind" in _columns(db_session)
    assert db_session.scalar(
        text("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_as_root_thermo'")
    ) == 1
    migration.upgrade()


def test_the_group_is_named_exactly_when_the_kind_is_single_conformer(db_session):
    entry = _entry(db_session, "THMDECLCK")
    group = make_conformer_group(db_session, entry)

    def insert(kind: str | None, group_id: int | None):
        return db_session.execute(
            text(
                "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, "
                "thermodynamic_target_kind, target_conformer_group_id) "
                "VALUES (:ref, :entry, 'computed', CAST(:kind AS thermo_target_kind), :group)"
            ),
            {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id, "kind": kind, "group": group_id},
        )

    insert(None, None)
    insert("equilibrium_ensemble", None)
    insert("single_conformer", group.id)
    for kind, group_id in (
        ("single_conformer", None),
        ("equilibrium_ensemble", group.id),
        # The case a plain ``=`` would let through: a group on a row with no kind
        # evaluates to NULL, and a CHECK passes on NULL.
        (None, group.id),
    ):
        with pytest.raises(IntegrityError, match="target_group_iff_single_conformer"), db_session.begin_nested():
            insert(kind, group_id)


def test_a_protocol_declaration_must_be_a_versioned_object(db_session):
    entry = _entry(db_session, "THMDECLPR")

    def insert(protocol: str):
        return db_session.execute(
            text(
                "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, protocol_declaration) "
                "VALUES (:ref, :entry, 'computed', CAST(:protocol AS jsonb))"
            ),
            {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id, "protocol": protocol},
        )

    insert(PROTOCOL)
    for bad in ("[]", "null", '"g4"', '{"recipe": {}}', '{"version": "1"}'):
        with pytest.raises(IntegrityError, match="protocol_declaration_versioned_object"), db_session.begin_nested():
            insert(bad)


def test_orm_none_is_sql_null_never_json_null(db_session):
    """``Thermo(protocol_declaration=None)`` must store "not stated", not a stored JSON ``null``."""
    entry = _entry(db_session, "THMDECLNULL")
    thermo = Thermo(
        species_entry_id=entry.id, scientific_origin=ScientificOriginKind.computed, protocol_declaration=None
    )
    db_session.add(thermo)
    db_session.flush()
    assert db_session.scalar(
        text("SELECT protocol_declaration IS NULL FROM thermo WHERE id = :id"), {"id": thermo.id}
    ) is True


@pytest.mark.parametrize(
    "update",
    [
        "SET thermodynamic_target_kind = 'equilibrium_ensemble'",
        "SET protocol_declaration = CAST('{\"version\": 1, \"recipe\": {\"name\": \"g3\"}}' AS jsonb)",
        "SET target_conformer_group_id = NULL, thermodynamic_target_kind = NULL",
    ],
    ids=["target_kind", "protocol", "target_group"],
)
def test_an_approved_declaration_cannot_be_changed_in_place(db_session, update):
    """The whole-row guard on accepted thermo covers the new columns without being told about them."""
    entry = _entry(db_session, "THMDECLIMM")
    group = make_conformer_group(db_session, entry)
    thermo_id = db_session.scalar(
        text(
            "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, thermodynamic_target_kind, "
            "target_conformer_group_id, protocol_declaration) VALUES (:ref, :entry, 'computed', "
            "'single_conformer', :group, CAST(:protocol AS jsonb)) RETURNING id"
        ),
        {"ref": "thm_" + uuid4().hex[:26], "entry": entry.id, "group": group.id, "protocol": PROTOCOL},
    )
    _approve(db_session, SubmissionRecordType.thermo, thermo_id, _actor(db_session))

    with pytest.raises(DBAPIError, match="accepted"), db_session.begin_nested():
        db_session.execute(text(f"UPDATE thermo {update} WHERE id = :id"), {"id": thermo_id})

    stored = db_session.execute(
        text("SELECT thermodynamic_target_kind, protocol_declaration FROM thermo WHERE id = :id"),
        {"id": thermo_id},
    ).one()
    assert stored.thermodynamic_target_kind == "single_conformer"
    assert stored.protocol_declaration == {"version": 1, "recipe": {"name": "g4"}}


def test_an_unapproved_row_may_still_be_edited(db_session):
    """The guard is for accepted science only; a draft declaration is correctable."""
    entry = _entry(db_session, "THMDECLDRAFT")
    thermo_id = _legacy_thermo(db_session, entry.id)
    db_session.execute(
        text("UPDATE thermo SET thermodynamic_target_kind = 'equilibrium_ensemble' WHERE id = :id"),
        {"id": thermo_id},
    )
    assert db_session.scalar(
        text("SELECT thermodynamic_target_kind FROM thermo WHERE id = :id"), {"id": thermo_id}
    ) == "equilibrium_ensemble"


def test_a_declared_row_hashes_differently_from_an_undeclared_one(db_session):
    """Per the enthalpy-reference precedent: absent stays out of the digest, declared goes in."""
    entry = _entry(db_session, "THMDECLHASH")
    thermo_id = _legacy_thermo(db_session, entry.id)
    thermo = db_session.get(Thermo, thermo_id)
    undeclared = (encoded(thermo_inputs(thermo)), _mapped_columns(thermo))

    thermo.thermodynamic_target_kind = "equilibrium_ensemble"
    db_session.flush()
    assert encoded(thermo_inputs(thermo)) != undeclared[0]
    assert _mapped_columns(thermo) != undeclared[1]
    assert thermo_inputs(thermo)["thermodynamic_target_kind"] == "equilibrium_ensemble"

    thermo.thermodynamic_target_kind = None
    thermo.protocol_declaration = {"version": 1, "recipe": {"name": "g4"}}
    db_session.flush()
    assert encoded(thermo_inputs(thermo)) != undeclared[0]
    assert thermo_inputs(thermo)["protocol_declaration"] == {"recipe": {"name": "g4"}, "version": 1}

    thermo.protocol_declaration = None
    db_session.flush()
    assert (encoded(thermo_inputs(thermo)), _mapped_columns(thermo)) == undeclared


# ---------------------------------------------------------------------------
# The real alembic run, on a disposable database that holds existing thermo rows
# ---------------------------------------------------------------------------

_REVISION = revision_under_test("b3d8f1a6c924")


@pytest.fixture
def harness():
    created = _Harness("thermo_declarations")
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


def _seed_thermo_rows(engine) -> list[int]:
    """Two thermo rows exactly as the previous schema stored them: one scalar, one entropy-only."""
    with engine.begin() as conn:
        species_id = conn.scalar(
            text(
                "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                "VALUES (CAST('molecule' AS molecule_kind), 'C', 'VNWKTOKETHGBQD-UHFFFAOYSA-N', 0, 1, "
                "CAST('unspecified' AS stereo_kind)) RETURNING id"
            )
        )
        entry_id = conn.scalar(
            text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id}
        )
        ids = []
        for h298, s298 in ((-74.6, None), (None, 186.3)):
            ids.append(
                conn.scalar(
                    text(
                        "INSERT INTO thermo (public_ref, species_entry_id, scientific_origin, h298_kj_mol, "
                        "s298_j_mol_k, enthalpy_reference_kind) VALUES (:ref, :entry, 'computed', :h, :s, "
                        "CAST(:kind AS enthalpy_reference_kind)) RETURNING id"
                    ),
                    {
                        "ref": "thm_" + uuid4().hex[:26],
                        "entry": entry_id,
                        "h": h298,
                        "s": s298,
                        "kind": "formation_298k" if h298 is not None else None,
                    },
                )
            )
    return ids


def _digests(engine, ids: list[int]) -> dict[int, tuple[str, str]]:
    with Session(engine) as session:
        return {
            thermo_id: (
                encoded(thermo_inputs(session.get(Thermo, thermo_id))),
                encoded(_mapped_columns(session.get(Thermo, thermo_id))),
            )
            for thermo_id in ids
        }


def test_real_upgrade_downgrade_upgrade_keeps_existing_rows_null_and_their_digests(harness):
    harness.run("upgrade", _REVISION.parent)
    ids = _seed_thermo_rows(harness.engine)

    harness.run("upgrade", _REVISION.revision)
    before = _digests(harness.engine, ids)
    with harness.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT thermodynamic_target_kind, target_conformer_group_id, protocol_declaration "
                "FROM thermo ORDER BY id"
            )
        ).all()
    assert [tuple(r) for r in rows] == [(None, None, None)] * 2

    downgraded = _alembic(harness, "downgrade", _REVISION.parent)
    assert downgraded.returncode == 0, downgraded.stderr[-3000:]
    assert "forgets: 0 thermo target/protocol declaration(s)" in downgraded.stdout + downgraded.stderr
    with harness.engine.connect() as conn:
        # The rows are still there, without the columns.
        assert conn.scalar(text("SELECT count(*) FROM thermo")) == 2
        assert not (NEW_COLUMNS & set(conn.scalars(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'thermo'")
        )))

    harness.run("upgrade", _REVISION.revision)
    assert _digests(harness.engine, ids) == before
    with harness.engine.connect() as conn:
        assert conn.scalar(
            text("SELECT count(*) FROM thermo WHERE thermodynamic_target_kind IS NOT NULL "
                 "OR target_conformer_group_id IS NOT NULL OR protocol_declaration IS NOT NULL")
        ) == 0
        assert conn.scalar(text("SELECT h298_kj_mol FROM thermo WHERE id = :i"), {"i": ids[0]}) == -74.6


def test_real_downgrade_says_how_many_declarations_it_forgets(harness):
    harness.run("upgrade", _REVISION.revision)
    ids = _seed_thermo_rows(harness.engine)
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE thermo SET thermodynamic_target_kind = 'equilibrium_ensemble', "
                "protocol_declaration = CAST(:p AS jsonb) WHERE id = :i"
            ),
            {"p": PROTOCOL, "i": ids[0]},
        )
    downgraded = _alembic(harness, "downgrade", _REVISION.parent)
    assert downgraded.returncode == 0, downgraded.stderr[-3000:]
    assert "forgets: 1 thermo target/protocol declaration(s)" in downgraded.stdout + downgraded.stderr


def test_alembic_check_is_clean_at_head(harness):
    harness.run("upgrade", "head")
    checked = _alembic(harness, "check")
    assert checked.returncode == 0, checked.stderr[-3000:] + checked.stdout[-3000:]
    assert "No new upgrade operations detected" in checked.stdout + checked.stderr
