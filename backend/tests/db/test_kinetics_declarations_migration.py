"""``kinetics`` determination, applicability and protocol declarations: additive, legacy rows untouched.

Drives the revision's own ``upgrade``/``downgrade`` inside the test transaction (PostgreSQL
DDL is transactional, so the fixture's rollback restores the schema). A row inserted while the
columns do not exist stands for a deployed row; after the upgrade it must read NULL on all
four, keep the digests that stored reviews and assessments are keyed on, and stay frozen if it
was approved. A determination is immutable from creation, and its checks are the database's.
"""

from __future__ import annotations

import hashlib
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
from app.db.models.kinetics import Kinetics
from app.services.consistency.core import encoded, snapshot
from app.services.reproducibility_rubric import _mapped_columns
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import make_literature

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic/versions/c7a2e5d9b148_declare_kinetics_determination.py"
)
NEW_COLUMNS = {"determination_id", "representation_role", "applicability_declaration", "protocol_declaration"}
APPLICABILITY = '{"version": 1, "phase": "gas", "attribution": {"origin": "source_publication"}}'
PROTOCOL = '{"version": 1, "method": {"kind": "experimental"}}'


@pytest.fixture
def migration(db_session, monkeypatch):
    spec = spec_from_file_location("kinetics_declarations_migration", _MIGRATION)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "op",
        Operations(MigrationContext.configure(db_session.connection(), opts={"target_metadata": Base.metadata})),
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


def _legacy_kinetics(db_session, entry_id: int) -> int:
    """A kinetics row as the previous schema stored it (no declaration columns exist)."""
    return db_session.scalar(
        text(
            "INSERT INTO kinetics (public_ref, reaction_entry_id, scientific_origin, a, n, direction) "
            "VALUES (:ref, :entry, 'computed', 1e10, 2, 'forward') RETURNING id"
        ),
        {"ref": "kin_" + uuid4().hex[:26], "entry": entry_id},
    )


def _determination(db_session, entry_id: int, literature_id: int | None, **overrides) -> int:
    values = {
        "ref": "kdet_" + uuid4().hex[:26],
        "entry": entry_id,
        "direction": "forward",
        "kind": "whole_reaction",
        "lit": literature_id,
        "key": "set-A",
        "hash": hashlib.sha256(uuid4().bytes).hexdigest(),
        "ts": None,
    }
    values.update(overrides)
    return db_session.scalar(
        text(
            "INSERT INTO kinetics_determination (public_ref, reaction_entry_id, direction, target_kind, "
            "target_transition_state_entry_id, literature_id, determination_key, identity_hash) "
            "VALUES (:ref, :entry, :direction, :kind, :ts, :lit, :key, :hash) RETURNING id"
        ),
        values,
    )


def _columns(db_session, table: str = "kinetics") -> set[str]:
    return set(
        db_session.scalars(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table}
        )
    )


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_existing_rows_stay_null_and_keep_their_digests(db_session, migration, approved):
    entry = _reaction_entry(db_session)
    if approved:
        # Approval locks the row through the ORM, so it happens while the ORM's columns exist.
        # The row declares nothing, which is exactly what a deployed row looks like; the
        # downgrade then upgrade is the deployment: ADD COLUMN fires no UPDATE trigger, so the
        # accepted-science guard must neither refuse it nor see a mutation.
        legacy_id = _legacy_kinetics(db_session, entry)
        _approve(db_session, SubmissionRecordType.kinetics, legacy_id, _actor(db_session))
        migration.downgrade()
    else:
        migration.downgrade()
        legacy_id = _legacy_kinetics(db_session, entry)
    assert not (NEW_COLUMNS & _columns(db_session))

    migration.upgrade()

    assert NEW_COLUMNS <= _columns(db_session)
    row = db_session.execute(
        text(
            "SELECT determination_id, representation_role, applicability_declaration, protocol_declaration, "
            "direction, a FROM kinetics WHERE id = :id"
        ),
        {"id": legacy_id},
    ).one()
    assert tuple(row) == (None, None, None, None, "forward", 1e10)

    # Neither digest a stored review or assessment is keyed on moves: the new columns hold the
    # value the row implicitly had, so they stay out of both snapshots.
    kinetics = db_session.get(Kinetics, legacy_id)
    assert NEW_COLUMNS.isdisjoint(snapshot(kinetics))
    assert NEW_COLUMNS.isdisjoint(_mapped_columns(kinetics))
    assert "determination" not in encoded(snapshot(kinetics))


def test_an_approved_row_stays_approved_through_the_upgrade(db_session, migration):
    entry = _reaction_entry(db_session)
    legacy_id = _legacy_kinetics(db_session, entry)
    _approve(db_session, SubmissionRecordType.kinetics, legacy_id, _actor(db_session))
    migration.downgrade()
    migration.upgrade()
    assert (
        db_session.scalar(
            text("SELECT status FROM record_review WHERE record_type = 'kinetics' AND record_id = :id"),
            {"id": legacy_id},
        )
        == RecordReviewStatus.approved.value
    )


def test_upgrade_downgrade_upgrade_round_trips_with_declared_rows(db_session, migration, capsys):
    entry = _reaction_entry(db_session)
    literature = make_literature(db_session)
    determination_id = _determination(db_session, entry, literature.id)
    kinetics_id = db_session.scalar(
        text(
            "INSERT INTO kinetics (public_ref, reaction_entry_id, scientific_origin, a, direction, determination_id, "
            "representation_role, applicability_declaration, protocol_declaration) VALUES (:ref, :entry, 'computed', 1e10, "
            "'forward', :det, 'complete', CAST(:app AS jsonb), CAST(:proto AS jsonb)) RETURNING id"
        ),
        {
            "ref": "kin_" + uuid4().hex[:26],
            "entry": entry,
            "det": determination_id,
            "app": APPLICABILITY,
            "proto": PROTOCOL,
        },
    )

    migration.downgrade()
    assert "forgets: 1 kinetics determination(s) and the declarations on 1 kinetics record(s)" in capsys.readouterr().out
    assert not (NEW_COLUMNS & _columns(db_session))
    assert _columns(db_session, "kinetics_determination") == set()
    for typename in ("kinetics_determination_target_kind", "kinetics_representation_role"):
        assert db_session.scalar(text("SELECT count(*) FROM pg_type WHERE typname = :t"), {"t": typename}) == 0
    # The shared enum the revision did not create must survive the downgrade.
    assert db_session.scalar(text("SELECT count(*) FROM pg_type WHERE typname = 'kinetics_direction'")) == 1
    # The row survives; only the declarations are forgotten.
    assert db_session.scalar(text("SELECT count(*) FROM kinetics WHERE id = :id"), {"id": kinetics_id}) == 1

    migration.upgrade()
    assert NEW_COLUMNS <= _columns(db_session)
    assert db_session.scalar(
        text("SELECT determination_id FROM kinetics WHERE id = :id"), {"id": kinetics_id}
    ) is None


def test_downgrade_removes_exactly_what_upgrade_added(db_session, migration):
    migration.downgrade()
    for name in (
        "ck_kinetics_determination_iff_role",
        "ck_kinetics_applicability_declaration_versioned_object",
        "ck_kinetics_protocol_declaration_versioned_object",
        "fk_kinetics_determination_id_kinetics_determination",
    ):
        assert db_session.scalar(text("SELECT count(*) FROM pg_constraint WHERE conname = :n"), {"n": name}) == 0, name
    assert db_session.scalar(
        text("SELECT count(*) FROM pg_proc WHERE proname = 'tckdb_kinetics_determination_immutable'")
    ) == 0
    # The pre-existing kinetics constraints are still there.
    assert db_session.scalar(
        text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_kinetics_degeneracy_finite_positive'")
    ) == 1
    migration.upgrade()


# ---------------------------------------------------------------------------
# the kinetics checks
# ---------------------------------------------------------------------------


def _insert_kinetics(db_session, entry: int, **columns) -> int:
    names = ", ".join(columns)
    marks = ", ".join(
        f"CAST(:{name} AS jsonb)" if name.endswith("_declaration") else f":{name}" for name in columns
    )
    return db_session.scalar(
        text(
            f"INSERT INTO kinetics (public_ref, reaction_entry_id, scientific_origin, a{', ' + names if names else ''}) "
            f"VALUES (:ref, :entry, 'computed', 1e10{', ' + marks if marks else ''}) RETURNING id"
        ),
        {"ref": "kin_" + uuid4().hex[:26], "entry": entry, **columns},
    )


def test_a_determination_and_its_role_are_stated_together(db_session):
    entry = _reaction_entry(db_session)
    determination = _determination(db_session, entry, make_literature(db_session).id)
    with pytest.raises(IntegrityError, match="determination_iff_role"), db_session.begin_nested():
        _insert_kinetics(db_session, entry, determination_id=determination)
    with pytest.raises(IntegrityError, match="determination_iff_role"), db_session.begin_nested():
        _insert_kinetics(db_session, entry, representation_role="complete")
    assert _insert_kinetics(db_session, entry, determination_id=determination, representation_role="complete")
    assert _insert_kinetics(db_session, entry)


@pytest.mark.parametrize(
    "column,bad",
    [
        ("applicability_declaration", '"a string"'),
        ("applicability_declaration", '{"phase": "gas"}'),
        ("applicability_declaration", '{"version": "1"}'),
        ("protocol_declaration", "[]"),
        ("protocol_declaration", '{"method": {}}'),
        ("protocol_declaration", '{"version": true}'),
    ],
)
def test_a_declaration_is_a_versioned_object(db_session, column, bad):
    entry = _reaction_entry(db_session)
    with pytest.raises(IntegrityError, match="versioned_object"), db_session.begin_nested():
        _insert_kinetics(db_session, entry, **{column: bad})


def test_json_null_is_not_a_stated_declaration(db_session):
    entry = _reaction_entry(db_session)
    with pytest.raises(IntegrityError, match="versioned_object"), db_session.begin_nested():
        _insert_kinetics(db_session, entry, protocol_declaration="null")


# ---------------------------------------------------------------------------
# the determination table
# ---------------------------------------------------------------------------


def test_a_determination_is_immutable_from_creation(db_session):
    entry = _reaction_entry(db_session)
    determination = _determination(db_session, entry, make_literature(db_session).id)
    for update in (
        "SET determination_key = 'other'",
        "SET direction = 'reverse'",
        "SET determination_key = determination_key",
    ):
        with pytest.raises(DBAPIError, match="kinetics_determination_is_immutable"), db_session.begin_nested():
            db_session.execute(
                text(f"UPDATE kinetics_determination {update} WHERE id = :id"), {"id": determination}
            )
    assert db_session.scalar(
        text("SELECT determination_key FROM kinetics_determination WHERE id = :id"), {"id": determination}
    ) == "set-A"


def test_a_determination_stays_immutable_while_shared(db_session):
    entry = _reaction_entry(db_session)
    determination = _determination(db_session, entry, make_literature(db_session).id)
    for _ in range(2):
        _insert_kinetics(db_session, entry, determination_id=determination, representation_role="complete")
    with pytest.raises(DBAPIError, match="kinetics_determination_is_immutable"), db_session.begin_nested():
        db_session.execute(
            text("UPDATE kinetics_determination SET determination_key = 'x' WHERE id = :id"), {"id": determination}
        )


def test_a_determination_a_record_cites_cannot_be_deleted_but_an_orphan_can(db_session):
    entry = _reaction_entry(db_session)
    literature = make_literature(db_session).id
    cited = _determination(db_session, entry, literature)
    _insert_kinetics(db_session, entry, determination_id=cited, representation_role="complete")
    # The immutability trigger refuses UPDATE only; what protects a cited determination from deletion is
    # the foreign key, so every record that states it keeps what it states.
    with pytest.raises(IntegrityError, match="fk_kinetics_determination_id"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM kinetics_determination WHERE id = :id"), {"id": cited})
    # A determination no record cites (left behind when its only record was rolled back or removed) states
    # nothing for anyone, so removing it changes no record.
    orphan = _determination(db_session, entry, literature, key="orphan")
    db_session.execute(text("DELETE FROM kinetics_determination WHERE id = :id"), {"id": orphan})
    assert db_session.scalar(text("SELECT count(*) FROM kinetics_determination WHERE id = :id"), {"id": orphan}) == 0


def test_a_determination_needs_a_source_and_a_bounded_key_and_a_hash(db_session):
    entry = _reaction_entry(db_session)
    literature = make_literature(db_session).id
    with pytest.raises(IntegrityError, match="source_required"), db_session.begin_nested():
        _determination(db_session, entry, None)
    with pytest.raises(IntegrityError, match="key_bounded"), db_session.begin_nested():
        _determination(db_session, entry, literature, key="   ")
    with pytest.raises(IntegrityError, match="key_bounded"), db_session.begin_nested():
        _determination(db_session, entry, literature, key="k" * 129)
    with pytest.raises(IntegrityError, match="identity_hash_sha256_hex"), db_session.begin_nested():
        _determination(db_session, entry, literature, hash="not-a-hash")
    assert _determination(db_session, entry, literature, key="k" * 128)


def test_a_target_names_what_its_kind_needs(db_session):
    entry = _reaction_entry(db_session)
    literature = make_literature(db_session).id
    with pytest.raises(IntegrityError, match="target_matches_kind"), db_session.begin_nested():
        _determination(db_session, entry, literature, kind="resolved_channel")
    with pytest.raises(IntegrityError, match="target_matches_kind"), db_session.begin_nested():
        _determination(db_session, entry, literature, kind="whole_reaction", ts=1)


def test_the_same_content_hash_cannot_be_stored_twice(db_session):
    entry = _reaction_entry(db_session)
    literature = make_literature(db_session).id
    digest = hashlib.sha256(b"one determination").hexdigest()
    _determination(db_session, entry, literature, hash=digest)
    with pytest.raises(IntegrityError, match="identity_hash"), db_session.begin_nested():
        _determination(db_session, entry, literature, hash=digest, key="set-B")


# ---------------------------------------------------------------------------
# accepted-science: the new columns are frozen with the row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "assignment",
    [
        "applicability_declaration = CAST('{\"version\": 1, \"phase\": \"gas\", \"attribution\": {\"origin\": \"source_publication\"}}' AS jsonb)",
        "protocol_declaration = CAST('{\"version\": 1, \"method\": {\"kind\": \"experimental\"}}' AS jsonb)",
        "representation_role = 'additive_component'",
    ],
    ids=["applicability", "protocol", "role"],
)
def test_an_approved_row_cannot_have_a_declaration_changed(db_session, assignment):
    entry = _reaction_entry(db_session)
    determination = _determination(db_session, entry, make_literature(db_session).id)
    kinetics_id = _insert_kinetics(
        db_session, entry, determination_id=determination, representation_role="complete"
    )
    _approve(db_session, SubmissionRecordType.kinetics, kinetics_id, _actor(db_session))
    with pytest.raises(DBAPIError, match="accepted"), db_session.begin_nested():
        db_session.execute(text(f"UPDATE kinetics SET {assignment} WHERE id = :id"), {"id": kinetics_id})
