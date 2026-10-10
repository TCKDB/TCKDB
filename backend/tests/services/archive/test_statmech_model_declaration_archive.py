"""An archive that holds declared statmech models restores them whole, and the freeze survives the restore."""

from __future__ import annotations

import io

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.common import StatmechModelScopeKind, StatmechModelSourceRole, SubmissionRecordType
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember, StatmechModelSource
from app.services.archive import restore_archive, write_archive
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.db.test_statmech_model_declaration_db import _entry
from tests.services.archive.test_archive import _empty_archive_tables
from tests.services.scientific_read._factories import make_conformer_group, make_literature, make_statmech

TABLES = ("statmech_model_declaration", "statmech_model_member", "statmech_model_source")


def _make(session):
    entry = _entry(session)
    host = make_statmech(session, species_entry=entry)
    sibling = make_statmech(session, species_entry=entry)
    group = make_conformer_group(session, entry)
    lit = make_literature(session)
    declaration = StatmechModelDeclaration(
        statmech_id=host.id,
        schema_version=1,
        scope_kind=StatmechModelScopeKind.explicit_equilibrium_ensemble,
        literature_id=lit.id,
        recipe={"energy_zero": "e0", "rotors": [{"torsion_index": 1, "representation": "free_rotor"}]},
        content_hash="b" * 64,
    )
    session.add(declaration)
    session.flush()
    session.add_all(
        [
            StatmechModelMember(
                declaration_id=declaration.id, member_key="m1", member_statmech_id=sibling.id, conformer_group_id=group.id, counting_multiplicity=2
            ),
            StatmechModelSource(
                declaration_id=declaration.id,
                source_key="imp",
                role=StatmechModelSourceRole.parameters,
                literature_id=lit.id,
                member_key="m1",
            ),
        ]
    )
    session.flush()
    return host, declaration


def _rows(session) -> dict[str, list[tuple]]:
    return {
        "declaration": session.execute(
            text("SELECT public_ref, statmech_id, scope_kind::text, recipe::text, content_hash FROM statmech_model_declaration ORDER BY id")
        ).all(),
        "member": session.execute(
            text("SELECT member_key, member_statmech_id, conformer_group_id, counting_multiplicity FROM statmech_model_member ORDER BY id")
        ).all(),
        "source": session.execute(
            text("SELECT source_key, role::text, literature_id, member_key FROM statmech_model_source ORDER BY id")
        ).all(),
    }


def _round_trip(session) -> None:
    archive = io.BytesIO()
    write_archive(session, archive)
    frozen = archive.getvalue()
    _empty_archive_tables(session)
    for table in TABLES:
        assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    restore_archive(session, io.BytesIO(frozen))
    session.expunge_all()


def test_declarations_members_and_sources_survive_a_round_trip(db_session):
    _make(db_session)
    before = _rows(db_session)
    assert all(before.values())
    _round_trip(db_session)
    assert _rows(db_session) == before


def test_the_freeze_of_an_approved_declaring_record_survives_the_restore(db_session):
    host, declaration = _make(db_session)
    _approve(db_session, SubmissionRecordType.statmech, host.id, _actor(db_session))
    before = _rows(db_session)
    _round_trip(db_session)
    assert _rows(db_session) == before
    with pytest.raises(DBAPIError, match="accepted statmech record .* is immutable"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM statmech_model_source"))
    with pytest.raises(DBAPIError, match="accepted statmech record .* is immutable"), db_session.begin_nested():
        db_session.execute(text("UPDATE statmech_model_declaration SET recipe = '{}'::jsonb WHERE id = :i"), {"i": declaration.id})
