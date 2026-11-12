"""``kinetics`` determination, applicability and protocol declarations.

A determination is immutable from creation, a declaration is a versioned object, an approved row cannot have its
declaration changed, and the checks are the database's.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models.common import SubmissionRecordType
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import make_literature

NEW_COLUMNS = {"determination_id", "representation_role", "applicability_declaration", "protocol_declaration"}
APPLICABILITY = '{"version": 1, "phase": "gas", "attribution": {"origin": "source_publication"}}'
PROTOCOL = '{"version": 1, "method": {"kind": "experimental"}}'


def _reaction_entry(db_session) -> int:
    reaction_id = db_session.scalar(
        text("INSERT INTO chem_reaction (reversible, stoichiometry_hash, public_ref) VALUES (true, :h, :r) RETURNING id"),
        {"h": uuid4().hex * 2, "r": "rxn_" + uuid4().hex[:26]},
    )
    return db_session.scalar(
        text("INSERT INTO reaction_entry (reaction_id, public_ref) VALUES (:rid, :r) RETURNING id"),
        {"rid": reaction_id, "r": "rxe_" + uuid4().hex[:26]},
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
