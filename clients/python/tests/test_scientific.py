"""Tests for the scientific read/query methods on TCKDBClient.

These exercise request construction and response surfacing only — the
backend behavior (sort order, evidence completeness, provenance shape)
is covered by the backend service- and API-level tests.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tckdb_client.errors import (
    TCKDBHTTPError,
    TCKDBValidationError,
)
from conftest import BASE_URL, make_client


def _ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body or {"records": [], "pagination": {}})


def _split_qs(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


# ===========================================================================
# search_species
# ===========================================================================


def test_search_species_builds_correct_path_and_params():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok({"records": [{"species_id": 12}], "pagination": {"total": 1}})

    client, _ = make_client(handler)
    result = client.search_species(
        smiles="C[CH2]", charge=0, multiplicity=2, limit=25
    )

    assert len(seen) == 1
    req = seen[0]
    parsed = urlsplit(str(req.url))
    assert parsed.path == "/api/v1/scientific/species/search"
    qs = _split_qs(str(req.url))
    assert qs["smiles"] == ["C[CH2]"]
    assert qs["charge"] == ["0"]
    assert qs["multiplicity"] == ["2"]
    assert qs["limit"] == ["25"]
    assert result["records"][0]["species_id"] == 12


def test_search_species_serializes_include_as_repeated_params():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return _ok()

    client, _ = make_client(handler)
    client.search_species(smiles="C", include=["thermo", "statmech"])

    qs = _split_qs(captured[0])
    assert sorted(qs["include"]) == ["statmech", "thermo"]


def test_search_species_drops_none_params():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return _ok()

    client, _ = make_client(handler)
    client.search_species(smiles="C")

    qs = _split_qs(captured[0])
    # Only the explicit smiles should appear; everything else is None and dropped.
    assert qs == {"smiles": ["C"]}


def test_search_species_serializes_bool_as_lowercase_string():
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return _ok()

    client, _ = make_client(handler)
    client.search_species(smiles="C", include_deprecated=True)

    qs = _split_qs(captured[0])
    assert qs["include_deprecated"] == ["true"]


def test_search_species_does_not_send_sort_param():
    """No client-side `sort=` is supported. Method signature has no `sort` arg."""
    import inspect

    sig = inspect.signature(make_client(lambda r: _ok())[0].search_species)
    assert "sort" not in sig.parameters


# ===========================================================================
# search_reactions (POST default + GET option)
# ===========================================================================


def test_search_reactions_defaults_to_post_with_json_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok({"records": [], "pagination": {"total": 0}})

    client, _ = make_client(handler)
    client.search_reactions(
        reactants=["[CH3]", "c1ccccc1"],
        products=["CH4", "[c]1ccccc1"],
        direction="either",
        include=["kinetics"],
    )

    assert seen[0].method == "POST"
    import json

    payload = json.loads(seen[0].content.decode("utf-8"))
    assert payload["reactants"] == ["[CH3]", "c1ccccc1"]
    assert payload["products"] == ["CH4", "[c]1ccccc1"]
    assert payload["direction"] == "either"
    assert payload["include"] == ["kinetics"]


def test_search_reactions_get_form_uses_repeated_query_params():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_reactions(
        reactants=["A1", "A2"],
        products=["B1"],
        method="GET",
    )

    assert seen[0].method == "GET"
    qs = _split_qs(str(seen[0].url))
    assert sorted(qs["reactants"]) == ["A1", "A2"]
    assert qs["products"] == ["B1"]


def test_search_reactions_post_omits_none_keys_from_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.search_reactions(reactants=["A"], products=["B"])

    import json

    payload = json.loads(seen[0].content.decode("utf-8"))
    assert "min_review_status" not in payload
    assert "family" not in payload


def test_search_reactions_signature_has_no_sort_param():
    import inspect

    sig = inspect.signature(make_client(lambda r: _ok())[0].search_reactions)
    assert "sort" not in sig.parameters


# ===========================================================================
# get_reaction_kinetics
# ===========================================================================


def test_get_reaction_kinetics_uses_reaction_entry_id_in_path():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.get_reaction_kinetics(reaction_entry_id=51)

    assert urlsplit(str(seen[0].url)).path == (
        "/api/v1/scientific/reaction-entries/51/kinetics"
    )


def test_get_reaction_kinetics_serializes_temperature_and_collapse():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.get_reaction_kinetics(
        reaction_entry_id=51,
        temperature_min=300.0,
        temperature_max=2000.0,
        collapse="first",
        include=["provenance", "calculations"],
    )

    qs = _split_qs(str(seen[0].url))
    assert qs["temperature_min"] == ["300.0"]
    assert qs["temperature_max"] == ["2000.0"]
    assert qs["collapse"] == ["first"]
    assert sorted(qs["include"]) == ["calculations", "provenance"]


def test_get_reaction_kinetics_returns_non_ts_provenance_nulls():
    """Representative response with non-TS-backed provenance (Phase 2.2 contract)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request": {},
                "reaction_entry_id": 51,
                "review_summary": {"total": 1},
                "records": [
                    {
                        "kinetics_id": 202,
                        "scientific_origin": "experimental",
                        "model_kind": "modified_arrhenius",
                        "review": {"status": "approved"},
                        "parameters": {
                            "A": 1.0e-12,
                            "A_units": "cm3_molecule_s",
                            "n": 0.0,
                            "Ea_kj_mol": 12.3,
                        },
                        "tunneling_model": None,
                        "uncertainty": {
                            "A_uncertainty": None,
                            "A_uncertainty_kind": None,
                            "n_uncertainty": None,
                            "Ea_uncertainty_kj_mol": None,
                        },
                        "evidence_completeness": {
                            "score": 1, "max": 9, "checklist": {}
                        },
                        "provenance": {
                            "transition_state_entry_id": None,
                            "ts_opt_calculation_id": None,
                            "ts_freq_calculation_id": None,
                            "ts_sp_calculation_id": None,
                            "path_search": None,
                            "irc": None,
                            "primary_level_of_theory": None,
                            "primary_software": None,
                            "geometry_validation": None,
                            "scf_stability": None,
                            "literature": {"id": 77, "title": "Example", "year": 1999},
                            "software_release": None,
                            "workflow_tool_release": None,
                        },
                    }
                ],
                "pagination": {"total": 1},
            },
        )

    client, _ = make_client(handler)
    result = client.get_reaction_kinetics(reaction_entry_id=51)

    rec = result["records"][0]
    assert rec["scientific_origin"] == "experimental"
    p = rec["provenance"]
    assert p["transition_state_entry_id"] is None
    assert p["ts_opt_calculation_id"] is None
    assert p["literature"]["id"] == 77


def test_get_reaction_kinetics_signature_has_no_sort_param():
    import inspect

    sig = inspect.signature(make_client(lambda r: _ok())[0].get_reaction_kinetics)
    assert "sort" not in sig.parameters


# ===========================================================================
# get_species_thermo
# ===========================================================================


def test_get_species_thermo_uses_species_entry_id_in_path():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.get_species_thermo(species_entry_id=31)

    assert urlsplit(str(seen[0].url)).path == (
        "/api/v1/scientific/species-entries/31/thermo"
    )


def test_get_species_thermo_serializes_temperature_and_filters():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler)
    client.get_species_thermo(
        species_entry_id=31,
        temperature_min=300,
        temperature_max=3000,
        model_kind="nasa",
        level_of_theory_id=12,
    )

    qs = _split_qs(str(seen[0].url))
    assert qs["temperature_min"] == ["300"]
    assert qs["temperature_max"] == ["3000"]
    assert qs["model_kind"] == ["nasa"]
    assert qs["level_of_theory_id"] == ["12"]


# ===========================================================================
# get_reaction_full
# ===========================================================================


def test_get_reaction_full_serializes_include_and_review():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "request": {},
            "reaction_entry": {"id": 51},
            "review_summary": {"total": 0},
        })

    client, _ = make_client(handler)
    client.get_reaction_full(
        reaction_entry_id=51,
        include=["kinetics", "transition_states", "calculations", "review"],
        include_review="full",
    )

    parsed = urlsplit(str(seen[0].url))
    assert parsed.path == "/api/v1/scientific/reaction-entries/51/full"
    qs = _split_qs(str(seen[0].url))
    assert sorted(qs["include"]) == ["calculations", "kinetics", "review", "transition_states"]
    assert qs["include_review"] == ["full"]


# ===========================================================================
# Error handling + auth header preservation
# ===========================================================================


def test_404_surfaced_as_TCKDBHTTPError():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "reaction_entry not found (reaction_entry_id=999)"})

    client, _ = make_client(handler)
    with pytest.raises(TCKDBHTTPError) as excinfo:
        client.get_reaction_kinetics(reaction_entry_id=999)
    assert excinfo.value.status_code == 404
    assert "reaction_entry not found" in str(excinfo.value)


def test_422_surfaced_as_TCKDBValidationError():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={"detail": "client_sort_not_supported: sort= is not accepted in v0"},
        )

    client, _ = make_client(handler)
    with pytest.raises(TCKDBValidationError) as excinfo:
        client.search_species(smiles="C")
    assert excinfo.value.status_code == 422
    assert "client_sort_not_supported" in str(excinfo.value)


def test_auth_header_preserved_on_scientific_reads():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok()

    client, _ = make_client(handler, api_key="tck_test_key_value_xyz")
    client.search_species(smiles="C")

    assert seen[0].headers.get("x-api-key") == "tck_test_key_value_xyz"


def test_search_species_response_returned_as_raw_dict():
    body = {
        "request": {"filter": {}, "sort": "review_rank,has_entries,created_at,id"},
        "review_summary": {"approved": 0, "total": 1},
        "records": [{"species_id": 12, "canonical_smiles": "C[CH2]"}],
        "pagination": {"offset": 0, "limit": 50, "returned": 1, "total": 1},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client, _ = make_client(handler)
    result = client.search_species(smiles="C[CH2]")
    assert result == body
