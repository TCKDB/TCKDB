"""Scientific read methods are anonymous-friendly.

Policy:

- Scientific read methods (search_*, get_species_thermo, get_reaction_*,
  get_geometry) must NOT raise client-side when no api_key is configured.
- When no api_key is configured, the outgoing request must NOT carry an
  ``X-API-Key`` header.
- When an api_key IS configured, the outgoing request MUST carry the
  ``X-API-Key`` header (authenticated deployments still get a billable
  identity on reads).
- Upload/write/admin methods remain authenticated by default and still
  raise ``TCKDBAuthenticationError`` when no api_key is configured.

The client is not an abuse-control boundary. Hosted deployments enforce
abuse limits server-side (rate limits, pagination caps, query timeouts,
monitoring).
"""

from __future__ import annotations

import httpx
import pytest

from tckdb_client.client import API_KEY_HEADER
from tckdb_client.errors import TCKDBAuthenticationError
from conftest import make_client


def _ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body or {"records": [], "pagination": {}})


# ---------------------------------------------------------------------
# Anonymous mode: no api_key configured
# ---------------------------------------------------------------------

ANONYMOUS_READ_CALLS = [
    ("search_species", lambda c: c.search_species(smiles="O")),
    ("search_reactions_get", lambda c: c.search_reactions(reactants=["O"], method="GET")),
    ("search_reactions_post", lambda c: c.search_reactions(reactants=["O"], method="POST")),
    ("search_thermo_get", lambda c: c.search_thermo(smiles="O", method="GET")),
    ("search_thermo_post", lambda c: c.search_thermo(smiles="O", method="POST")),
    ("search_kinetics_get", lambda c: c.search_kinetics(reactants=["O"], method="GET")),
    ("search_kinetics_post", lambda c: c.search_kinetics(reactants=["O"], method="POST")),
    (
        "search_species_calculations_get",
        lambda c: c.search_species_calculations(smiles="O", method="GET"),
    ),
    (
        "search_species_calculations_post",
        lambda c: c.search_species_calculations(smiles="O", method="POST"),
    ),
    ("get_species_thermo", lambda c: c.get_species_thermo("spc_x")),
    ("get_reaction_kinetics", lambda c: c.get_reaction_kinetics("rxn_x")),
    ("get_reaction_full", lambda c: c.get_reaction_full("rxn_x")),
    ("get_geometry", lambda c: c.get_geometry("geom_x")),
]


@pytest.mark.parametrize("name,call", ANONYMOUS_READ_CALLS, ids=[n for n, _ in ANONYMOUS_READ_CALLS])
def test_scientific_reads_work_without_api_key(name: str, call) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler, api_key=None)
    call(client)
    assert recorder.requests, f"{name}: request not sent"
    assert API_KEY_HEADER.lower() not in recorder.last.headers, (
        f"{name}: client must not send X-API-Key when api_key is None"
    )


@pytest.mark.parametrize("name,call", ANONYMOUS_READ_CALLS, ids=[n for n, _ in ANONYMOUS_READ_CALLS])
def test_scientific_reads_send_api_key_when_configured(name: str, call) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()

    client, recorder = make_client(handler, api_key="tck_test_key_value_xyz")
    call(client)
    assert recorder.last.headers.get(API_KEY_HEADER.lower()) == "tck_test_key_value_xyz", (
        f"{name}: client must forward X-API-Key when configured"
    )


# ---------------------------------------------------------------------
# Writes/admin still gated client-side
# ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,call",
    [
        ("me", lambda c: c.me()),
        ("upload", lambda c: c.upload("thermo", {"a": 1})),
        ("bundle_dry_run", lambda c: c.bundle_dry_run({"records": []})),
        (
            "bundle_submit",
            lambda c: c.bundle_submit(
                {"records": []}, idempotency_key="abcdefghij1234567890"
            ),
        ),
        ("post_json", lambda c: c.post_json("/uploads/thermo", {"a": 1})),
        ("get_json", lambda c: c.get_json("/auth/me")),
    ],
    ids=["me", "upload", "bundle_dry_run", "bundle_submit", "post_json", "get_json"],
)
def test_write_and_admin_methods_still_require_api_key(name: str, call) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok()  # should never be reached

    client, recorder = make_client(handler, api_key=None)
    with pytest.raises(TCKDBAuthenticationError):
        call(client)
    assert recorder.requests == [], (
        f"{name}: must raise before sending when api_key is None"
    )
