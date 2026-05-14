"""Tests for chemistry-first scientific search methods on TCKDBClient.

Covers ``search_thermo`` and ``search_kinetics`` — request construction,
GET vs POST behavior, parameter serialization, and response surfacing.
The backend behavior (composition order, evidence completeness, etc.) is
covered by the backend service- and API-level tests.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tckdb_client.errors import (
    TCKDBHTTPError,
    TCKDBValidationError,
)
from conftest import make_client


def _ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body or {"records": [], "pagination": {}})


def _split_qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


# ===========================================================================
# search_thermo
# ===========================================================================


def test_search_thermo_defaults_to_post_with_json_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_thermo(
        smiles="C[CH2]",
        temperature_min=300,
        temperature_max=3000,
        include=["provenance", "review"],
        collapse="first",
    )

    assert seen[0].method == "POST"
    assert urlsplit(str(seen[0].url)).path == "/api/v1/scientific/thermo/search"
    payload = json.loads(seen[0].content.decode("utf-8"))
    assert payload["smiles"] == "C[CH2]"
    assert payload["temperature_min"] == 300
    assert payload["temperature_max"] == 3000
    assert payload["include"] == ["provenance", "review"]
    assert payload["collapse"] == "first"


def test_search_thermo_post_omits_none_keys():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_thermo(smiles="C")

    payload = json.loads(seen[0].content.decode("utf-8"))
    assert payload == {"smiles": "C"}


def test_search_thermo_get_form_uses_query_params():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_thermo(
        smiles="O",
        temperature_min=300,
        include=["provenance"],
        method="GET",
    )

    assert seen[0].method == "GET"
    qs = _split_qs(str(seen[0].url))
    assert qs["smiles"] == ["O"]
    assert qs["temperature_min"] == ["300"]
    assert qs["include"] == ["provenance"]


def test_search_thermo_returns_raw_dict():
    body = {
        "request": {"filter": {"smiles": "X"}, "sort": "...", "collapse": "all", "include": []},
        "review_summary": {"approved": 0, "total": 1},
        "records": [
            {
                "species": {
                    "species_id": 12,
                    "canonical_smiles": "X",
                    "inchi_key": "...",
                    "charge": 0,
                    "multiplicity": 1,
                    "species_entry_id": 31,
                    "species_entry_kind": "minimum",
                    "electronic_state_kind": "ground",
                    "species_entry_review": {"status": "not_reviewed"},
                },
                "thermo": {
                    "thermo_id": 88,
                    "scientific_origin": "computed",
                    "model_kind": "scalar",
                    "review": {"status": "not_reviewed"},
                    "evidence_completeness": {"score": 1, "max": 8, "checklist": {}},
                    "provenance": {},
                },
            }
        ],
        "pagination": {"offset": 0, "limit": 50, "returned": 1, "total": 1},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client, _ = make_client(handler)
    result = client.search_thermo(smiles="X")
    assert result == body


# ===========================================================================
# search_kinetics
# ===========================================================================


def test_search_kinetics_defaults_to_post_with_json_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_kinetics(
        reactants=["[CH3]", "c1ccccc1"],
        products=["CH4", "[c]1ccccc1"],
        direction="either",
        temperature_min=300,
        temperature_max=2000,
        include=["provenance"],
    )

    assert seen[0].method == "POST"
    assert urlsplit(str(seen[0].url)).path == "/api/v1/scientific/kinetics/search"
    payload = json.loads(seen[0].content.decode("utf-8"))
    assert payload["reactants"] == ["[CH3]", "c1ccccc1"]
    assert payload["products"] == ["CH4", "[c]1ccccc1"]
    assert payload["direction"] == "either"
    assert payload["temperature_min"] == 300
    assert payload["temperature_max"] == 2000
    assert payload["include"] == ["provenance"]


def test_search_kinetics_get_form_uses_repeated_query_params():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_kinetics(
        reactants=["A1", "A2"],
        products=["B1"],
        direction="forward",
        method="GET",
    )

    assert seen[0].method == "GET"
    qs = _split_qs(str(seen[0].url))
    assert sorted(qs["reactants"]) == ["A1", "A2"]
    assert qs["products"] == ["B1"]
    assert qs["direction"] == ["forward"]


def test_search_kinetics_signature_has_no_sort_param():
    import inspect

    sig = inspect.signature(make_client(lambda r: _ok())[0].search_kinetics)
    assert "sort" not in sig.parameters


def test_search_thermo_signature_has_no_sort_param():
    import inspect

    sig = inspect.signature(make_client(lambda r: _ok())[0].search_thermo)
    assert "sort" not in sig.parameters


# ===========================================================================
# Error surfacing + auth header preservation
# ===========================================================================


def test_search_thermo_404_surfaces_as_TCKDBHTTPError():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    client, _ = make_client(handler)
    with pytest.raises(TCKDBHTTPError) as excinfo:
        client.search_thermo(smiles="X")
    assert excinfo.value.status_code == 404


def test_search_kinetics_422_surfaces_as_TCKDBValidationError():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": "invalid_temperature_range: ..."},
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBValidationError) as excinfo:
        client.search_kinetics(reactants=["A"], products=["B"])
    assert excinfo.value.status_code == 422
    assert "invalid_temperature_range" in str(excinfo.value)


def test_search_methods_preserve_auth_header():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler, api_key="tck_test_key_value_xyz")
    client.search_thermo(smiles="C")
    client.search_kinetics(reactants=["A"], products=["B"])

    for req in seen:
        assert req.headers.get("x-api-key") == "tck_test_key_value_xyz"


# ===========================================================================
# search_species_calculations (Phase 7)
# ===========================================================================


def test_search_species_calculations_defaults_to_post_with_json_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_species_calculations(
        smiles="C[CH2]",
        calculation_type="sp",
        ranking="lowest_energy",
        collapse="first",
        include=["provenance", "conformers", "review"],
    )

    assert seen[0].method == "POST"
    assert urlsplit(str(seen[0].url)).path == (
        "/api/v1/scientific/species-calculations/search"
    )
    payload = json.loads(seen[0].content.decode("utf-8"))
    assert payload["smiles"] == "C[CH2]"
    assert payload["calculation_type"] == "sp"
    assert payload["ranking"] == "lowest_energy"
    assert payload["collapse"] == "first"
    assert payload["include"] == ["provenance", "conformers", "review"]


def test_search_species_calculations_get_form_uses_query_params():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_species_calculations(
        smiles="O",
        calculation_type="sp",
        ranking="lowest_energy",
        method_http="GET",
    )

    assert seen[0].method == "GET"
    qs = _split_qs(str(seen[0].url))
    assert qs["smiles"] == ["O"]
    assert qs["calculation_type"] == ["sp"]
    assert qs["ranking"] == ["lowest_energy"]


def test_search_species_calculations_method_kwarg_does_not_collide_with_lot_method():
    """``method_http`` (HTTP) and ``method`` (LoT method filter) coexist."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_species_calculations(
        smiles="X",
        method="wb97xd",
        basis="def2tzvp",
        method_http="GET",
    )
    qs = _split_qs(str(seen[0].url))
    assert qs["method"] == ["wb97xd"]
    assert qs["basis"] == ["def2tzvp"]


def test_search_species_calculations_signature_has_no_sort_param():
    import inspect

    sig = inspect.signature(
        make_client(lambda r: _ok())[0].search_species_calculations
    )
    assert "sort" not in sig.parameters


def test_search_species_calculations_422_surfaces_unsupported_ranking():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": (
                    "unsupported_ranking_for_calculation_type: ranking="
                    "lowest_energy requires calculation_type=sp or "
                    "calculation_type=opt."
                )
            },
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBValidationError) as excinfo:
        client.search_species_calculations(
            smiles="X", calculation_type="freq", ranking="lowest_energy"
        )
    assert excinfo.value.status_code == 422
    assert "unsupported_ranking_for_calculation_type" in str(excinfo.value)


def test_search_species_calculations_404_surfaces_TCKDBHTTPError():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "species_entry not found"})

    client, _ = make_client(handler)
    with pytest.raises(TCKDBHTTPError) as excinfo:
        client.search_species_calculations(species_entry_id=999_999)
    assert excinfo.value.status_code == 404


def test_search_species_calculations_preserves_auth_header():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler, api_key="tck_test_key_value_xyz")
    client.search_species_calculations(smiles="C")
    assert seen[0].headers.get("x-api-key") == "tck_test_key_value_xyz"


def test_search_species_calculations_post_omits_none_keys():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_species_calculations(smiles="C")

    payload = json.loads(seen[0].content.decode("utf-8"))
    assert payload == {"smiles": "C"}
