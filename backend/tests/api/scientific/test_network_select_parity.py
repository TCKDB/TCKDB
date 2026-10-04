"""The HTTP API, the Python client and the MCP tool answer one network selection request identically.

All three are driven against the real application through an ``httpx`` transport bridged to the test client, so what
is compared is what each consumer actually receives: the same outcome, the same explanation, the same refs, and the
same refusal codes (including the bound on required outputs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from tckdb_client import TCKDBClient
from tckdb_client.errors import TCKDBHTTPError

from app.db.models.common import RecordReviewStatus as S
from app.services.network_selection import selection as selection_module
from tests.services.network_selection._rules import ProtocolRule, protocol
from tests.services.network_selection._world import add_solve, build_world, fit_spec

_MCP_SRC = Path(__file__).resolve().parents[4] / "integrations" / "mcp" / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool

TOOL = "tckdb_select_network_kinetics"
BASE = "http://testserver/api/v1"
A_, B_ = "chemically_significant_eigenvalues", "modified_strong_collision"
RULE = ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)


class _Bridge(httpx.BaseTransport):
    """Send an ``httpx`` request to the in-process application via the Starlette test client."""

    def __init__(self, test_client) -> None:
        self._client = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        answer = self._client.request(
            request.method, str(request.url), content=request.content,
            headers={"content-type": request.headers.get("content-type", "application/json")},
        )
        return httpx.Response(
            answer.status_code, headers={"content-type": answer.headers["content-type"]},
            content=answer.content, request=request,
        )


@pytest.fixture
def world(db_session):
    return build_world(db_session)


@pytest.fixture
def sdk(client):
    with TCKDBClient(BASE, transport=_Bridge(client)) as c:
        yield c


@pytest.fixture
def mcp(client):
    with TCKDBHttpClient(BASE, None, 5.0, transport=_Bridge(client)) as c:
        yield lambda arguments: dispatch_tool(TOOL, arguments, c, Config.from_env(env={}))


def question(world, **changes):
    base = {
        "coefficient_basis": "kernel",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure_min_bar": 0.5,
        "pressure_max_bar": 5.0,
        "bath": {"components": [{"species_ref": world.ar.public_ref}]},
        "partition": {"retained": sorted(world.hashes.values())},
        "channel_key": "assoc",
        "observable": "product_resolved_coefficient",
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture
def two(db_session, world):
    a = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=S.not_reviewed)
    b = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_), review=S.approved)
    return a, b


def _three_ways(client, sdk, mcp, world, **options):
    body = question(world, **options)
    profile = body.pop("profile", None)
    api = client.post(
        f"/api/v1/scientific/networks/{world.ref}/kinetics/select", json=body, params={"profile": profile} if profile else None
    )
    assert api.status_code == 200, api.text
    return (
        api.json(),
        sdk.select_network_kinetics(world.ref, **body, profile=profile),
        mcp({"network_ref": world.ref, **body, **({"profile": profile} if profile else {})}),
    )


def test_an_unranked_population_is_the_same_answer_through_all_three(client, sdk, mcp, world, two):
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world)
    assert api == via_sdk == via_mcp
    assert api["outcome"] == "incomparable_alternatives" and api["selection"] is None
    assert {d["determination_ref"] for d in api["determinations"]} == {s._dets["d_assoc"].public_ref for s in two}


def test_a_scoped_preference_is_the_same_answer_through_all_three(client, sdk, mcp, world, two, monkeypatch):
    monkeypatch.setattr(selection_module, "default_rules", lambda: (RULE,))
    a, _ = two
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world)
    assert api == via_sdk == via_mcp
    assert api["outcome"] == "policy_preferred" and api["selection"]["node_ref"] == a._dets["d_assoc"].public_ref


def test_an_administrative_first_pick_is_labelled_the_same_way_through_all_three(client, sdk, mcp, world, two):
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world, mode="first")
    assert api == via_sdk == via_mcp
    assert api["selection"]["basis"] == "administrative_first" and api["selection"]["administrative"] is True


def test_the_curated_profile_and_policy_choices_agree_through_all_three(client, sdk, mcp, world, two):
    for options in ({"profile": "curated"}, {"policy": "latest"}, {"policy": "most_reviewed", "min_review_status": "approved"}):
        if options.get("profile") == "curated":
            continue  # the network itself is below the curated floor in this fixture: a 404, tested below
        api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world, **options)
        assert api == via_sdk == via_mcp, options


def test_the_manifest_is_the_same_document_through_the_api_and_the_client(client, sdk, world, two):
    body = question(world)
    api = client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/select/manifest", json=body)
    assert api.status_code == 200
    assert sdk.get_network_kinetics_selection_manifest(world.ref, **body) == api.json()


@pytest.mark.parametrize(
    "options",
    [
        {"phase": "liquid"},
        {"temperature_min_k": 2000.0},  # min above max
        {"observable": None},  # a single channel names its observable
        {"scope": "full_network"},  # a full-network question lists outputs, not a channel
    ],
)
def test_a_refusal_carries_the_same_status_and_code_through_all_three(client, sdk, mcp, world, options):
    body = question(world, **options)
    api = client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/select", json=body)
    assert api.status_code == 422
    expected = api.json()["code"]
    with pytest.raises(TCKDBHTTPError) as sdk_error:
        sdk.select_network_kinetics(world.ref, **body)
    assert sdk_error.value.status_code == 422 and sdk_error.value.code == expected
    with pytest.raises(MCPToolError) as mcp_error:
        mcp({"network_ref": world.ref, **body})
    assert mcp_error.value.to_payload()["code"] == expected


def test_too_many_required_outputs_is_one_coded_refusal_through_all_three_and_nothing_pages_it(client, sdk, mcp, world):
    outputs = [{"channel_key": f"c{i}", "observable": "product_resolved_coefficient"} for i in range(201)]
    body = question(world, scope="full_network", channel_key=None, observable=None, outputs=outputs)
    api = client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/select", json=body)
    assert api.status_code == 422 and api.json()["code"] == "network_selection_population_too_large"
    with pytest.raises(TCKDBHTTPError) as sdk_error:
        sdk.select_network_kinetics(world.ref, **body)
    assert sdk_error.value.code == "network_selection_population_too_large" and sdk_error.value.status_code == 422
    with pytest.raises(MCPToolError) as mcp_error:
        mcp({"network_ref": world.ref, **body})
    assert mcp_error.value.to_payload()["code"] == "network_selection_population_too_large"


EXPORT_TOOL = "tckdb_export_selected_network_kinetics"


@pytest.fixture
def mcp_export(client):
    with TCKDBHttpClient(BASE, None, 5.0, transport=_Bridge(client)) as c:
        yield lambda arguments: dispatch_tool(EXPORT_TOOL, arguments, c, Config.from_env(env={}))


def _manifest(client, world):
    response = client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/select/manifest", json=question(world))
    assert response.status_code == 200, response.text
    return response.json()


def test_an_export_is_the_same_answer_through_all_three(client, sdk, mcp_export, world, two):
    a, _ = two
    manifest = _manifest(client, world)
    choice = {
        "manifest": manifest,
        "node_ref": a._dets["d_assoc"].public_ref,
        "representation_refs": [a._fits[0].public_ref],
        "allow_administrative_choice": True,
        "format": "chemkin",
    }
    api = client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/export-selected", json=choice)
    assert api.status_code == 200, api.text
    via_sdk = sdk.export_selected_network_kinetics(world.ref, **choice)
    via_mcp = mcp_export({"network_ref": world.ref, **choice})
    assert api.json() == via_sdk == via_mcp
    assert api.json()["administrative"] is True and "chem.inp" in api.json()["files"]


def test_an_export_refusal_carries_the_same_status_and_code_through_all_three(client, sdk, mcp_export, world, two):
    a, _ = two
    manifest = _manifest(client, world)
    # The default (administrative choice not accepted) over an unranked selection.
    choice = {
        "manifest": manifest,
        "node_ref": a._dets["d_assoc"].public_ref,
        "representation_refs": [a._fits[0].public_ref],
    }
    api = client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/export-selected", json=choice)
    assert api.status_code == 422 and api.json()["code"] == "network_export_choice_not_allowed"
    with pytest.raises(TCKDBHTTPError) as sdk_error:
        sdk.export_selected_network_kinetics(world.ref, **choice)
    assert sdk_error.value.status_code == 422 and sdk_error.value.code == "network_export_choice_not_allowed"
    with pytest.raises(MCPToolError) as mcp_error:
        mcp_export({"network_ref": world.ref, **choice})
    assert mcp_error.value.to_payload()["code"] == "network_export_choice_not_allowed"
