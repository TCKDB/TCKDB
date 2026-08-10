"""Tests for HTTP status / transport-exception → MCP error mapping."""

from __future__ import annotations

import httpx
import pytest

from tckdb_mcp.config import Config
from tckdb_mcp.errors import (
    MCPToolError,
    invalid_input,
    map_http_status,
    map_httpx_exception,
)
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.tools import species as species_tool


def _client(handler) -> TCKDBHttpClient:
    transport = httpx.MockTransport(handler)
    return TCKDBHttpClient(
        base_url="http://127.0.0.1:8010/api/v1",
        api_key=None,
        timeout_seconds=2.0,
        transport=transport,
    )


# ---------------------------------------------------------------------------
# Pure mapping tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_input"),
        (401, "auth_required"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (422, "invalid_input"),
        (429, "rate_limited"),
        (500, "service_unavailable"),
        (502, "service_unavailable"),
        (503, "service_unavailable"),
        (504, "service_unavailable"),
    ],
)
def test_map_http_status_table(status: int, code: str) -> None:
    err = map_http_status(status, "some detail")
    assert err.code == code
    assert err.http_status == status
    assert err.detail == "some detail"


def test_map_http_status_unknown_falls_through_to_internal_error() -> None:
    err = map_http_status(418, "i'm a teapot")
    assert err.code == "internal_error"
    assert err.http_status == 418


def test_map_http_status_blank_detail_falls_back_to_status_string() -> None:
    err = map_http_status(503, "")
    assert err.detail == "HTTP 503"


def test_invalid_input_helper() -> None:
    err = invalid_input("bad include token: foo")
    assert err.code == "invalid_input"
    assert err.http_status == 422
    assert "foo" in err.detail


def test_map_httpx_timeout_exception() -> None:
    err = map_httpx_exception(httpx.ReadTimeout("read timeout"))
    assert err.code == "timeout"
    assert err.http_status is None


def test_map_httpx_transport_error() -> None:
    err = map_httpx_exception(httpx.ConnectError("conn refused"))
    assert err.code == "network_error"
    assert err.http_status is None


def test_map_httpx_unexpected_exception() -> None:
    err = map_httpx_exception(RuntimeError("oops"))
    assert err.code == "internal_error"


def test_to_payload_shape() -> None:
    err = MCPToolError("not_found", "no such ref", http_status=404)
    assert err.to_payload() == {
        "code": "not_found",
        "detail": "no such ref",
        "http_status": 404,
        # Nothing chose a code above this error, so the specific and the
        # coarse answer are the same fact rather than a missing one.
        "status_class": "not_found",
        "context": {},
    }


def test_to_payload_carries_the_server_code_and_its_context() -> None:
    err = map_http_status(
        422,
        "Reaction is not element-balanced (reaction_mass_balance_failed).",
        code="reaction_mass_balance_failed",
        context={"reactant_atoms": {"C": 1, "H": 5}, "product_atoms": {"C": 1, "H": 3}},
    )
    payload = err.to_payload()
    assert payload["code"] == "reaction_mass_balance_failed"
    assert payload["status_class"] == "invalid_input"
    assert payload["http_status"] == 422
    assert payload["context"]["reactant_atoms"] == {"C": 1, "H": 5}


@pytest.mark.parametrize(
    ("status", "status_class"),
    [(400, "invalid_input"), (404, "not_found"), (409, "conflict"), (422, "invalid_input")],
)
def test_a_server_code_always_wins_over_the_status_bucket(
    status: int, status_class: str
) -> None:
    """The status is a bucket; the code is the answer.

    Deriving ``code`` from the status was the defect: every refusal a
    ``422`` can carry — a bad include token, a geometry that disagrees
    with its SMILES, a reaction that does not balance — collapsed into
    one value, and the agent was left grepping English for the
    difference. The bucket is still reported, under its own name.
    """
    err = map_http_status(status, "some detail", code="something_specific")
    assert err.code == "something_specific"
    assert err.status_class == status_class


def test_two_different_refusals_at_one_status_stay_distinguishable() -> None:
    """The regression, stated as the property it broke."""
    first = map_http_status(422, "a", code="species_geometry_composition_mismatch")
    second = map_http_status(422, "b", code="reaction_mass_balance_failed")
    assert first.code != second.code
    assert first.status_class == second.status_class == "invalid_input"


def test_a_body_with_no_code_still_falls_back_to_the_status_bucket() -> None:
    """Not every non-2xx body is ours -- a proxy's 502 page carries nothing."""
    err = map_http_status(502, "Bad Gateway")
    assert err.code == "service_unavailable"
    assert err.status_class == "service_unavailable"
    assert err.context == {}


def test_context_is_not_scrubbed_the_way_detail_is() -> None:
    """Masking a fact would defeat the field that exists to carry facts.

    ``detail`` is prose and a bare six-digit integer in it might be a row
    id, so it is masked. ``context`` is typed, deliberate, and its values
    are what the agent is supposed to act on; a pressure of 1013250 Pa
    must survive.
    """
    err = map_http_status(
        422,
        "value 1013250 is out of range",
        code="value_out_of_range",
        context={"pressure_pa": 1013250, "note": "1013250"},
    )
    assert "1013250" not in err.detail
    assert err.context["pressure_pa"] == 1013250
    assert err.context["note"] == "1013250"


def test_detail_scrubs_large_integers() -> None:
    """Defensive: any large bare integer in detail is masked."""
    err = map_http_status(422, "violates constraint on row 1234567")
    assert "1234567" not in err.detail
    assert "<id>" in err.detail


# ---------------------------------------------------------------------------
# End-to-end mapping through the HTTP wrapper
# ---------------------------------------------------------------------------


def test_http_422_from_server_reports_the_server_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": "unknown_include_token: 'foo' not legal for species_search",
                "code": "unknown_include_token",
            },
        )

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "unknown_include_token"
    assert excinfo.value.status_class == "invalid_input"
    assert excinfo.value.http_status == 422
    assert "unknown_include_token" in excinfo.value.detail
    client.close()


def test_a_scientific_refusal_survives_the_whole_wrapper() -> None:
    """The end-to-end shape of what #115 made possible and this restores.

    Through the real request path, not the mapping function: an agent
    that deposits an unbalanced reaction is told which scientific rule
    refused it, and gets the element counts as data rather than as a
    sentence to parse.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "code": "reaction_mass_balance_failed",
                "detail": (
                    "Reaction is not element-balanced "
                    "(reaction_mass_balance_failed)."
                ),
                "context": {
                    "reactant_atoms": {"C": 1, "H": 5},
                    "product_atoms": {"C": 1, "H": 3},
                },
            },
        )

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    err = excinfo.value
    assert err.code == "reaction_mass_balance_failed"
    assert err.status_class == "invalid_input"
    assert err.context["reactant_atoms"] == {"C": 1, "H": 5}
    client.close()


def test_a_named_database_constraint_survives_as_a_conflict() -> None:
    """A 409 that names its chemistry must not flatten back to ``conflict``.

    The 409 path is the one the backend only just started naming, so it
    is the one most likely to be dropped again by a wrapper that assumes
    a conflict is a conflict.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "code": "atom_map_element_not_conserved",
                "detail": (
                    "An atom map pairs two atoms of different elements. An "
                    "atom does not change element on the way across a "
                    "reaction."
                ),
                "category": "integrity_error",
                "context": {"constraint": "ck_reaction_atom_map_pair_element_matches"},
            },
        )

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    err = excinfo.value
    assert err.code == "atom_map_element_not_conserved"
    assert err.status_class == "conflict"
    assert err.http_status == 409
    assert err.context["constraint"] == "ck_reaction_atom_map_pair_element_matches"
    client.close()


def test_a_body_that_carries_only_a_code_still_says_something() -> None:
    """No prose is not the same as no information."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"code": "unsupported_filter"})

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "unsupported_filter"
    assert excinfo.value.detail == "unsupported_filter"
    client.close()


def test_a_non_json_error_page_degrades_to_the_status_bucket() -> None:
    """An nginx 502 has no envelope; the wrapper must not require one."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>502 Bad Gateway</html>")

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "service_unavailable"
    assert excinfo.value.status_class == "service_unavailable"
    assert excinfo.value.context == {}
    client.close()


def test_http_404_from_server_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "species_entry not found"})

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(
            client,
            Config.from_env(env={}),
            {"species_entry_ref": "spe_doesnotexist"},
        )
    assert excinfo.value.code == "not_found"
    assert excinfo.value.http_status == 404
    client.close()


def test_http_503_from_server_maps_to_service_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "query timeout"})

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "service_unavailable"
    client.close()


def test_timeout_maps_to_timeout_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout")

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "timeout"
    assert excinfo.value.http_status is None
    client.close()


def test_network_failure_maps_to_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection refused")

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "network_error"
    assert excinfo.value.http_status is None
    client.close()


def test_html_response_body_falls_back_to_status_detail() -> None:
    """Non-JSON 500 responses (e.g. proxy HTML) still produce a clean envelope."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>boom</html>")

    client = _client(handler)
    with pytest.raises(MCPToolError) as excinfo:
        species_tool.run(client, Config.from_env(env={}), {"smiles": "CCO"})
    assert excinfo.value.code == "service_unavailable"
    assert excinfo.value.http_status == 500
    client.close()
