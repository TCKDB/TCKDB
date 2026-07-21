"""Regression tests for the machine-consumer query contract."""

from __future__ import annotations

from jsonschema import Draft202012Validator

from app.api.error_contract import validation_detail_code
from app.api.public_openapi import project_hosted_openapi
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _assert_unsupported_filter(response, *names: str) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "unsupported_filter"
    assert body["context"]["filters"] == sorted(names)
    assert body["context"]["endpoint"].startswith("/scientific/")
    assert body["detail"].startswith("unsupported_filter: ")


def test_legacy_coded_value_error_is_promoted_to_structured_envelope(
    client,
    db_session,
):
    species = make_species(
        db_session,
        smiles="P",
        inchi_key=next_inchi_key("ERR"),
    )
    make_species_entry(db_session, species)

    response = client.get("/api/v1/scientific/species/search?smiles=P&include=banana")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "unknown_include_token"
    assert body["detail"].startswith("unknown_include_token: ")
    assert body["context"] == {}


def test_native_request_validation_uses_structured_envelope(client):
    response = client.get("/api/v1/scientific/species/search?smiles=C&limit=999")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "request_validation_error"
    assert isinstance(body["detail"], list)
    assert body["context"] == {}


def test_request_validation_code_cannot_come_from_caller_input(client):
    response = client.post(
        "/api/v1/scientific/kinetics/search",
        json={"limit": "attacker_code: stuff"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_error"


def test_multiple_coded_validation_failures_keep_the_generic_code():
    details = [
        {"msg": "Value error, first_conflict: one"},
        {"msg": "Value error, first_conflict: two"},
    ]

    assert (
        validation_detail_code(details, fallback="request_validation_error")
        == "request_validation_error"
    )


def test_species_inchi_fails_closed_even_when_smiles_is_supplied(client):
    response = client.get("/api/v1/scientific/species/search?smiles=C&inchi=InChI%3D1S%2FCH4%2Fh1H4")

    _assert_unsupported_filter(response, "inchi")


def test_composed_species_searches_inherit_inchi_fail_closed(client):
    urls = (
        "/api/v1/scientific/thermo/search?smiles=C&inchi=ignored",
        "/api/v1/scientific/species-calculations/search?smiles=C&inchi=ignored",
    )

    for url in urls:
        _assert_unsupported_filter(client.get(url), "inchi")


def test_species_calculation_scientific_origin_fails_closed(client):
    response = client.get(
        "/api/v1/scientific/species-calculations/search"
        "?smiles=C&scientific_origin=computed"
    )

    _assert_unsupported_filter(response, "scientific_origin")


def test_frequency_scale_factor_deferred_filters_fail_closed(client):
    response = client.get(
        "/api/v1/scientific/frequency-scale-factors/search?method=b3lyp&model_kind=harmonic&software_version=16"
    )

    _assert_unsupported_filter(response, "model_kind", "software_version")


def test_energy_correction_scheme_deferred_filters_fail_closed(client):
    response = client.post(
        "/api/v1/scientific/energy-correction-schemes/search",
        json={
            "name": "demo",
            "software": "gaussian",
            "software_version": "16",
            "used_by_thermo": False,
        },
    )

    _assert_unsupported_filter(
        response,
        "software",
        "software_version",
        "used_by_thermo",
    )


def test_hosted_openapi_marks_policy_hidden_ids_optional(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    species = schemas["SpeciesScientificRecord"]

    assert "species_id" not in species["required"]
    assert species["properties"]["species_id"]["x-tckdb-policy-hidden"] is True
    assert "level_of_theory_id" not in schemas["LevelOfTheorySummary"].get("required", [])


def test_hosted_openapi_projects_nested_inline_id_schemas():
    schema = {
        "paths": {
            "/api/v1/scientific/example": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Envelope"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "Envelope": {
                    "type": "object",
                    "properties": {
                        "record": {
                            "type": "object",
                            "required": ["record_id", "record_ref"],
                            "properties": {
                                "record_id": {"type": "integer"},
                                "record_ref": {"type": "string"},
                            },
                        }
                    },
                }
            }
        },
    }

    projected = project_hosted_openapi(schema)
    record = projected["components"]["schemas"]["Envelope"]["properties"]["record"]
    assert record["required"] == ["record_ref"]
    assert record["properties"]["record_id"]["x-tckdb-policy-hidden"] is True


def test_actual_hosted_species_json_validates_against_openapi(
    client,
    db_session,
):
    species = make_species(
        db_session,
        smiles="CO",
        inchi_key=next_inchi_key("OAS"),
    )
    make_species_entry(db_session, species)
    response = client.get("/api/v1/scientific/species/search?smiles=CO")
    assert response.status_code == 200, response.text

    openapi = client.get("/openapi.json").json()
    validation_root = {
        "$ref": "#/components/schemas/ScientificSpeciesSearchResponse",
        "components": openapi["components"],
    }
    Draft202012Validator(validation_root).validate(response.json())
