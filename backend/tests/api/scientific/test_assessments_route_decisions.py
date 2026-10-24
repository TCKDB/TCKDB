"""Where ``check_inputs`` is honoured, refused, and what a curated read may learn from it."""

from __future__ import annotations

import pytest

from app.db.models.common import CalculationType, RecordReviewStatus, SubmissionRecordType, ThermoCalculationRole
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    set_review,
)

API = "/api/v1/scientific"


def _approved_thermo_with_a_hidden_source(db_session):
    entry = make_species_entry(db_session, make_species(db_session))
    set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id, status=RecordReviewStatus.approved)
    lot = make_lot(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    visible = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id, lot_id=lot.id)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=visible.id, status=RecordReviewStatus.approved)
    hidden = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id)  # not reviewed
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=visible, role=ThermoCalculationRole.opt)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=hidden, role=ThermoCalculationRole.sp)
    evaluate_and_append_reproducibility(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id)
    # Approved last: an accepted record's source links are immutable.
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id, status=RecordReviewStatus.approved)
    db_session.expire_all()
    return entry, thermo, hidden


def _checked(client, entry, profile: str):
    response = client.get(
        f"{API}/species-entries/{entry.public_ref}/thermo?include=assessments&check_inputs=true&limit=10&profile={profile}"
    )
    assert response.status_code == 200, response.text
    records = response.json()["records"]
    assert len(records) == 1, "the approved thermo must be visible under the profile"
    return records[0]["assessments"]["reproducibility"]


def test_a_hidden_source_calculation_cannot_move_a_visible_verdict_under_curated(client, db_session):
    entry, _, hidden = _approved_thermo_with_a_hidden_source(db_session)
    before = _checked(client, entry, "curated")
    hidden.lot_id = make_lot(db_session, method="a-different-method").id
    db_session.flush()
    db_session.expire_all()
    after = _checked(client, entry, "curated")
    assert after == before, "editing a hidden calculation changed what a curated read says about a visible record"
    assert after["made_under"]["state"] == "inputs_unchecked"
    assert after["made_under"]["inputs_checked"] is False
    assert after["made_under"]["inputs_unchecked_reason"] == "curated_profile"


def test_the_same_edit_is_visible_to_an_exploratory_check(client, db_session):
    """The control: the input check does see the edit when nothing is hidden, so the curated result above is a decision."""
    entry, _, hidden = _approved_thermo_with_a_hidden_source(db_session)
    before = _checked(client, entry, "exploratory")
    assert before["made_under"]["state"] == "current"
    hidden.lot_id = make_lot(db_session, method="a-different-method").id
    db_session.flush()
    db_session.expire_all()
    assert _checked(client, entry, "exploratory")["made_under"]["state"] == "stale"


def test_a_bad_ref_is_a_404_before_check_inputs_is_judged(client):
    for path in ("species-entries/spe_doesnotexist0000000000000000/thermo", "reaction-entries/rxe_doesnotexist000000000000000/kinetics"):
        response = client.get(f"{API}/{path}?include=assessments&check_inputs=true&limit=50")
        assert response.status_code == 404, (path, response.status_code, response.text)


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/thermo/search?species_entry_ref=spe_x"),
        ("get", "/kinetics/search?reaction_ref=rxn_x"),
        ("get", "/statmech/search?method=x"),
        ("get", "/transport/search?method=x"),
        ("get", "/statmech/00000000"),
        ("get", "/transport/00000000"),
    ],
)
def test_check_inputs_is_refused_where_it_is_not_supported(client, method, path):
    response = getattr(client, method)(f"{API}{path}{'&' if '?' in path else '?'}include=assessments&check_inputs=true")
    assert response.status_code == 422, response.text
    assert "check_inputs_not_supported" in response.text


@pytest.mark.parametrize("path", ["/thermo/search", "/kinetics/search", "/statmech/search", "/transport/search"])
def test_check_inputs_in_a_search_body_is_refused_too(client, path):
    response = client.post(f"{API}{path}", json={"include": ["assessments"], "check_inputs": True})
    assert response.status_code == 422, response.text
    assert "check_inputs_not_supported" in response.text


@pytest.mark.parametrize(
    "path, body",
    [
        ("/thermo/search", {"software": '"check_inputs": true', "include": ["assessments"]}),
        ("/kinetics/search", {"software": 'x "check_inputs":true y', "include": ["assessments"]}),
        ("/transport/search", {"method": '"check_inputs": true', "include": ["assessments"]}),
        ("/statmech/search", {"method": '"check_inputs": true', "include": ["assessments"]}),
    ],
)
def test_a_string_value_that_contains_the_text_is_not_a_check_inputs_key(client, path, body):
    """The refusal reads the parsed body's keys, not its text: a filter value that spells the text out is just a value."""
    response = client.post(f"{API}{path}", json=body)
    assert "check_inputs_not_supported" not in response.text, response.text


def test_only_a_top_level_key_is_refused(client):
    nested = client.post(
        f"{API}/transport/search", json={"method": "x", "include": ["assessments"], "extra": {"check_inputs": True}}
    )
    assert "check_inputs_not_supported" not in nested.text  # a nested key is the model's own business (statmech forbids extras)
    top = client.post(f"{API}/transport/search", json={"method": "x", "include": ["assessments"], "check_inputs": False})
    assert top.status_code == 422 and "check_inputs_not_supported" in top.text
