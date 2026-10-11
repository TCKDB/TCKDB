"""A released statmech record ships the model it declares, with public refs only and its sources cited as provenance."""

from __future__ import annotations

import json

import pytest

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    StatmechModelScopeKind,
    StatmechModelSourceRole,
    SubmissionRecordType,
)
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember, StatmechModelSource
from app.services.record_review import set_record_review_status
from app.services.release.records import RECORD_VALUE_TABLES, cited_calculation_ids, serialize_records
from tests.db.test_statmech_model_declaration_db import _entry
from tests.services.scientific_read._factories import (
    make_calculation,
    make_conformer_group,
    make_literature,
    make_lot,
    make_statmech,
)


@pytest.fixture
def released(db_session, curator):
    entry = _entry(db_session)
    host = make_statmech(db_session, species_entry=entry)
    sibling = make_statmech(db_session, species_entry=entry)
    group = make_conformer_group(db_session, entry)
    freq = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=make_lot(db_session).id)
    declaration = StatmechModelDeclaration(
        statmech_id=host.id,
        schema_version=1,
        scope_kind=StatmechModelScopeKind.explicit_equilibrium_ensemble,
        literature_id=make_literature(db_session).id,
        recipe={"energy_zero": "e0"},
        content_hash="a" * 64,
    )
    db_session.add(declaration)
    db_session.flush()
    db_session.add_all(
        [
            StatmechModelMember(
                declaration_id=declaration.id,
                member_key="m1",
                member_statmech_id=sibling.id,
                conformer_group_id=group.id,
                counting_multiplicity=2,
                relative_energy_kj_mol=1.5,
            ),
            StatmechModelSource(
                declaration_id=declaration.id,
                source_key="curv",
                role=StatmechModelSourceRole.curvature,
                calculation_id=freq.id,
                member_key=None,
            ),
        ]
    )
    db_session.flush()
    set_record_review_status(
        db_session, record_type=SubmissionRecordType.statmech, record_id=host.id, status=RecordReviewStatus.approved, actor=curator
    )
    return {"host": host, "sibling": sibling, "group": group, "freq": freq, "declaration": declaration}


def test_the_declaration_is_a_value_table_of_a_statmech_record():
    tables = {t.table: t for t in RECORD_VALUE_TABLES[SubmissionRecordType.statmech]}
    declaration = tables["statmech_model_declaration"]
    assert {c.table for c in declaration.children} == {"statmech_model_member", "statmech_model_source"}


def test_a_released_record_ships_its_declaration_members_and_sources_by_public_ref(db_session, released):
    host = released["host"]
    shipped = serialize_records(db_session, record_type=SubmissionRecordType.statmech, record_ids=[host.id])[host.id]
    (declaration,) = shipped["statmech_model_declaration"]
    assert declaration["public_ref"] == released["declaration"].public_ref
    assert declaration["scope_kind"] == "explicit_equilibrium_ensemble" and declaration["recipe"] == {"energy_zero": "e0"}
    (member,) = declaration["statmech_model_member"]
    assert member["member_key"] == "m1" and member["counting_multiplicity"] == 2 and member["relative_energy_kj_mol"] == 1.5
    assert released["sibling"].public_ref in json.dumps(member)  # cited by ref; citing is not selecting
    assert released["group"].public_ref in json.dumps(member)
    (source,) = declaration["statmech_model_source"]
    assert source["role"] == "curvature" and released["freq"].public_ref in json.dumps(source)


def test_no_row_id_ships_and_a_member_is_cited_not_embedded(db_session, released):
    host = released["host"]
    shipped = serialize_records(db_session, record_type=SubmissionRecordType.statmech, record_ids=[host.id])[host.id]
    (declaration,) = shipped["statmech_model_declaration"]
    for node in (declaration, *declaration["statmech_model_member"], *declaration["statmech_model_source"]):
        assert not [k for k in node if k.endswith("_id")], node
    (member,) = declaration["statmech_model_member"]
    assert "statmech_model_declaration" not in json.dumps(member)  # the member's own declaration is not nested here


def test_the_calculations_a_declared_model_rests_on_are_cited_provenance(db_session, released):
    host = released["host"]
    cited = cited_calculation_ids(db_session, record_type=SubmissionRecordType.statmech, record_ids=[host.id])
    assert cited[host.id] == [released["freq"].id]


def test_a_record_without_a_declaration_ships_exactly_what_it_always_shipped(db_session, curator):
    legacy = make_statmech(db_session, species_entry=_entry(db_session))
    shipped = serialize_records(db_session, record_type=SubmissionRecordType.statmech, record_ids=[legacy.id])[legacy.id]
    assert shipped["statmech_model_declaration"] == []
