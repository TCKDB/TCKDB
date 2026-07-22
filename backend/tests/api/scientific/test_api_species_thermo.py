"""API tests for GET /api/v1/scientific/species-entries/{id}/thermo."""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    ScientificOriginKind,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.thermo import Thermo, ThermoSourceCalculation
from app.services.reproducibility_assessment import (
    append_reproducibility_assessment,
)
from app.services.reproducibility_rubric import (
    evaluate_and_append_reproducibility_v1,
)
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    attach_thermo_nasa9,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def _entry(db_session):
    # No fixed smiles: identity is (smiles, charge, multiplicity) (DR-0031)
    # and this helper is called repeatedly to build distinct species.
    species = make_species(db_session, inchi_key=next_inchi_key("THAPI"))
    return make_species_entry(db_session, species)


def test_returns_200_for_valid_species_entry_id(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Phase D: default response identifies the species entry by ref.
    assert body["species_entry_ref"] == entry.public_ref
    assert len(body["records"]) == 1
    assert body["records"][0]["model_kind"] == "scalar"


def test_collapse_first_offset_one_returns_empty(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    response = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"collapse": "first", "offset": 1},
    )

    assert response.status_code == 200
    assert response.json()["records"] == []
    assert response.json()["pagination"]["total"] == 1


def test_returns_404_for_missing_species_entry_id(client, db_session):
    resp = client.get("/api/v1/scientific/species-entries/999999/thermo")
    assert resp.status_code == 404
    assert "species_entry not found" in resp.text


def test_rejects_invalid_pagination(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?limit=999"
    )
    assert resp.status_code == 422


def test_returns_nasa_block_when_present(client, db_session):
    entry = _entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
    )
    body = resp.json()
    assert body["records"][0]["model_kind"] == "nasa"
    nasa = body["records"][0]["nasa"]
    assert nasa["t_low"] == 200.0
    assert nasa["t_high"] == 6000.0


def test_rejects_client_sort(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_temperature_min_greater_than_max_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
        "?temperature_min=3000&temperature_max=300"
    )
    assert resp.status_code == 422
    assert "invalid_temperature_range" in resp.text


def test_unknown_include_token_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_assessments_are_opt_in_and_report_freshness(client, db_session):
    entry = _entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    url = f"/api/v1/scientific/species-entries/{entry.id}/thermo"

    default_record = client.get(url).json()["records"][0]
    assert "assessments" not in default_record

    unassessed = client.get(f"{url}?include=assessments").json()["records"][0][
        "assessments"
    ]
    assert unassessed["deterministic_trust"]["rubric"] == "computed_thermo"
    assert unassessed["deterministic_trust"]["rubric_version"] == "1"
    assert unassessed["reproducibility"] == {
        "state": "unassessed",
        "assessment_ref": None,
        "rubric": None,
        "rubric_version": None,
        "grade": None,
        "assessed_at": None,
    }

    current_row = evaluate_and_append_reproducibility_v1(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
    )
    current = client.get(f"{url}?include=assessments").json()["records"][0][
        "assessments"
    ]["reproducibility"]
    assert current["state"] == "current"
    assert current["assessment_ref"] == current_row.public_ref
    assert current["rubric"] == "tckdb_reproducibility"
    assert current["rubric_version"] == "v1"
    assert current["assessed_at"] is not None

    stale_row = append_reproducibility_assessment(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        grade=ReproducibilityGrade.described,
        rubric_name="tckdb_reproducibility",
        rubric_version="v1",
        context_json={"outdated": True},
        assessor_kind=ReproducibilityAssessorKind.system,
    )
    stale = client.get(f"{url}?include=assessments").json()["records"][0][
        "assessments"
    ]["reproducibility"]
    assert stale["state"] == "stale"
    assert stale["assessment_ref"] == stale_row.public_ref


def test_include_all_does_not_expand_assessments(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)
    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=all"
    ).json()
    assert "assessments" not in body["request"]["include"]
    assert "assessments" not in body["records"][0]


def test_trust_omitted_when_not_requested(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
    ).json()

    assert "trust" not in body["records"][0]


def test_include_trust_returns_fragment(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=trust"
    ).json()

    assert body["request"]["include"] == ["trust"]
    trust = body["records"][0]["trust"]
    assert trust["review_status"] == "not_reviewed"
    assert trust["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert trust["is_certified"] is False
    evidence = trust["evidence"]
    assert evidence["record_type"] == "thermo"
    assert evidence["rubric"] == "computed_thermo_v1"
    assert evidence["rubric_version"] == 1
    assert "scalar_thermo_present" in evidence["passed_checks"]
    assert "source_calculations_present" in evidence["missing_checks"]
    assert "record_id" not in evidence


def test_include_all_does_not_include_trust(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=all"
    ).json()

    assert "trust" not in body["request"]["include"]
    assert "trust" not in body["records"][0]


def test_include_trust_exposes_record_id_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    entry = _entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
        "?include=trust,internal_ids"
    ).json()

    assert body["records"][0]["trust"]["evidence"]["record_id"] == thermo.id


def test_include_trust_nasa_representation_passes(client, db_session):
    entry = _entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=trust"
    ).json()

    evidence = body["records"][0]["trust"]["evidence"]
    assert "nasa_coefficients_present" in evidence["passed_checks"]
    assert "at_least_one_thermo_representation_present" in evidence["passed_checks"]


def test_include_trust_nasa9_only_not_penalized(client, db_session):
    # A NASA-9-only computed thermo (no scalar/nasa7/points) must not be scored
    # as lacking a thermo model. Exercises the read-path eager-load of
    # nasa9_intervals so the pure evaluator sees the representation.
    entry = _entry(db_session)
    thermo = Thermo(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
    )
    db_session.add(thermo)
    db_session.flush()
    attach_thermo_nasa9(db_session, thermo=thermo)

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=trust"
    ).json()

    evidence = body["records"][0]["trust"]["evidence"]
    assert evidence["label"] != "hard_failed"
    assert evidence["hard_fail_reason"] is None
    assert "thermo_model_present" in evidence["passed_checks"]
    assert "at_least_one_thermo_representation_present" in evidence["passed_checks"]
    assert "temperature_range_present_if_applicable" in evidence["passed_checks"]
    # Other representation forms are legitimately absent -> N/A, not missing.
    assert "scalar_thermo_present" in evidence["not_applicable_checks"]
    assert "nasa_coefficients_present" in evidence["not_applicable_checks"]
    assert "thermo_points_present" in evidence["not_applicable_checks"]


def test_include_trust_source_calculation_raises_completeness(client, db_session):
    sparse_entry = _entry(db_session)
    rich_entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=sparse_entry)
    rich = make_thermo_scalar(db_session, species_entry=rich_entry)

    lot = make_lot(db_session)
    calc = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=rich_entry.id,
        lot_id=lot.id,
    )
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=rich.id,
            calculation_id=calc.id,
            role=ThermoCalculationRole.opt,
        )
    )
    db_session.flush()

    sparse_body = client.get(
        f"/api/v1/scientific/species-entries/{sparse_entry.id}/thermo"
        "?include=trust"
    ).json()
    rich_body = client.get(
        f"/api/v1/scientific/species-entries/{rich_entry.id}/thermo?include=trust"
    ).json()

    sparse_score = sparse_body["records"][0]["trust"]["evidence"][
        "evidence_completeness"
    ]
    rich_score = rich_body["records"][0]["trust"]["evidence"][
        "evidence_completeness"
    ]
    assert rich_score > sparse_score


def test_include_trust_no_representation_hard_failed(client, db_session):
    entry = _entry(db_session)
    thermo = Thermo(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
    )
    db_session.add(thermo)
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=trust"
    ).json()

    evidence = body["records"][0]["trust"]["evidence"]
    assert evidence["label"] == "hard_failed"
    assert evidence["hard_fail_reason"] == "no_thermo_representation_present"


def test_include_trust_invalid_temperature_range_hard_failed(client, db_session):
    entry = _entry(db_session)
    make_thermo_scalar(
        db_session,
        species_entry=entry,
        tmin_k=500.0,
        tmax_k=500.0,
    )

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=trust"
    ).json()

    evidence = body["records"][0]["trust"]["evidence"]
    assert evidence["label"] == "hard_failed"
    assert evidence["hard_fail_reason"] == "invalid_temperature_range"


def test_include_trust_does_not_mutate_thermo(client, db_session):
    entry = _entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    before = (thermo.h298_kj_mol, thermo.s298_j_mol_k, thermo.tmin_k, thermo.tmax_k)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=trust"
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(thermo)

    after = (thermo.h298_kj_mol, thermo.s298_j_mol_k, thermo.tmin_k, thermo.tmax_k)
    assert after == before


def test_include_trust_uses_loaded_thermo_path(client, db_session, monkeypatch):
    entry = _entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    def fail_session_id_entrypoint(*args, **kwargs):
        raise AssertionError("read trust path must use loaded thermo")

    monkeypatch.setattr(
        "app.services.trust.evaluator.evaluate_computed_thermo",
        fail_session_id_entrypoint,
    )

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo?include=trust"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"][0]["trust"]["evidence"]["record_type"] == "thermo"
