"""API tests for GET /api/v1/scientific/species-entries/{id}/thermo."""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    ScientificOriginKind,
    ThermoCalculationRole,
)
from app.db.models.thermo import Thermo, ThermoSourceCalculation
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
)


def _entry(db_session):
    species = make_species(
        db_session, smiles="CC", inchi_key=next_inchi_key("THAPI")
    )
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
