"""A transition-state-owned statmech is graded by its own owner-scoped rubric.

Before this change every TS-owned statmech read ``hard_failed`` / ``species_entry_missing`` on
``include=assessments`` and ``include=trust``: the shared species rubric asks for a species entry
first, and a TS-owned record has none. The record is internally consistent and validly owned; the
badge was a defect, not a verdict.

The records here are made through the real reaction bundle (``transition_state.statmech``, #621), so
the read path under test is the public one.
"""

from __future__ import annotations

from tests.api.test_api_ts_contract_621 import _bundle, _ok, _post_bundle, _ts_statmech


def _read(client, ref: str, include: str) -> dict:
    response = client.get(f"/api/v1/scientific/statmech/{ref}?include={include}")
    assert response.status_code == 200, response.text[:1500]
    return response.json()["record"]


def _deposit_ts_statmech(client, **overrides) -> str:
    payload = _bundle()
    payload["transition_state"]["statmech"] = _ts_statmech(**overrides)
    result = _ok(_post_bundle(client, payload))
    return result["transition_state_statmech_ref"]


def test_a_ts_owned_statmech_is_not_hard_failed_for_want_of_a_species_entry(client):
    ref = _deposit_ts_statmech(client)
    record = _read(client, ref, "assessments,trust")

    deterministic = record["assessments"]["deterministic_trust"]
    assert deterministic["grade"] != "hard_failed", deterministic
    assert deterministic.get("hard_fail") is None
    assert deterministic["rubric"] == "computed_ts_statmech"
    assert deterministic["rubric_version"] == "1"
    assert record["trust"]["trust_status"] == deterministic["grade"]


def test_assessments_and_trust_agree_on_the_ts_rubric_and_grade(client):
    ref = _deposit_ts_statmech(client)
    record = _read(client, ref, "assessments,trust")

    deterministic = record["assessments"]["deterministic_trust"]
    evidence = record["trust"]["evidence"]
    assert evidence["rubric"] == "computed_ts_statmech_v1"
    assert evidence["checks"]["transition_state_entry_present"] == "passed"
    assert "species_entry_present" not in evidence["checks"]
    assert record["trust"]["trust_status"] == deterministic["grade"] == evidence["label"]
    # Completeness describes evidence, it does not certify.
    assert record["trust"]["is_certified"] is False


def test_search_and_detail_give_a_ts_record_the_same_verdict(client):
    ref = _deposit_ts_statmech(client)
    detail = _read(client, ref, "assessments,trust")

    search = client.get(f"/api/v1/scientific/statmech/search?statmech_ref={ref}&include=assessments,trust")
    assert search.status_code == 200, search.text[:800]
    (found,) = search.json()["records"]
    assert found["assessments"]["deterministic_trust"] == detail["assessments"]["deterministic_trust"]
    assert found["trust"]["evidence"] == detail["trust"]["evidence"]


def test_a_species_owned_record_in_the_same_database_keeps_the_species_rubric(client, db_session):
    from tests.api.scientific.test_api_scientific_statmech import _make_statmech

    _deposit_ts_statmech(client)
    _, _, species_statmech = _make_statmech(db_session)
    record = _read(client, species_statmech.public_ref, "assessments,trust")

    deterministic = record["assessments"]["deterministic_trust"]
    assert (deterministic["rubric"], deterministic["rubric_version"]) == ("computed_statmech", "2")
    assert record["trust"]["evidence"]["rubric"] == "computed_statmech_v2"
    assert record["trust"]["evidence"]["checks"]["species_entry_present"] == "passed"
    assert "transition_state_entry_present" not in record["trust"]["evidence"]["checks"]
