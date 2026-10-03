"""The HTTP API, the Python client and the MCP tool answer one request identically.

All three are driven against the real application through an ``httpx`` transport bridged to the
test client, so what is compared is what each consumer actually receives: the same outcome, the same
explanation, the same record refs, the same provenance blocks, and the same refusal codes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from tckdb_client import TCKDBClient
from tckdb_client.errors import TCKDBHTTPError

from app.db.models.common import RecordReviewStatus as S
from app.db.models.common import ThermoTargetKind
from tests.services.scientific_read._factories import make_conformer_group
from tests.services.thermo_selection._support import make_thermo, protocol, species_entry_for

_MCP_SRC = Path(__file__).resolve().parents[4] / "integrations" / "mcp" / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool

TOOL = "tckdb_select_species_entry_thermo"
BASE = "http://testserver/api/v1"


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
def methane(db_session):
    return species_entry_for(db_session, "Methane")


@pytest.fixture
def sdk(client):
    with TCKDBClient(BASE, transport=_Bridge(client)) as c:
        yield c


@pytest.fixture
def mcp(client):
    with TCKDBHttpClient(BASE, None, 5.0, transport=_Bridge(client)) as c:
        yield lambda arguments: dispatch_tool(TOOL, arguments, c, Config.from_env(env={}))


def _seed(db_session, methane):
    g4 = make_thermo(db_session, methane, proto=protocol("g4"), age_days=500, status=S.approved)
    g3 = make_thermo(db_session, methane, proto=protocol("g3"), age_days=1, status=S.approved)
    return g4, g3


def _three_ways(client, sdk, mcp, entry, *, target, **options):
    api = client.post(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo/select",
        json={"target": target, **{k: v for k, v in options.items() if k != "profile"}},
        params={"profile": options["profile"]} if "profile" in options else None,
    )
    assert api.status_code == 200, api.text
    return (
        api.json(),
        sdk.select_species_thermo(entry.public_ref, target=target, **options),
        mcp({"species_entry_ref": entry.public_ref, "target": target, **options}),
    )


EQ = {"kind": "equilibrium_ensemble"}


def test_a_preferred_older_g4_is_the_same_answer_through_all_three(client, sdk, mcp, db_session, methane):
    g4, g3 = _seed(db_session, methane)
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, methane, target=EQ)
    assert api == via_sdk == via_mcp
    assert api["outcome"] == "policy_preferred" and api["selection"]["thermo_ref"] == g4.public_ref
    assert api["fronts"] == [[g4.public_ref], [g3.public_ref]]
    # Provenance rides along unchanged: the rule that decided it, and each candidate's declared protocol.
    assert api["policy"]["rules"][0]["rule_id"] == "E1"
    assert {c["thermo_ref"]: c["protocol"]["recipe"]["name"] for c in api["candidates"]} == {
        g4.public_ref: "g4", g3.public_ref: "g3",
    }


def test_an_administrative_first_pick_is_labelled_the_same_way_through_all_three(client, sdk, mcp, db_session, methane):
    make_thermo(db_session, methane, proto=protocol("g3"), age_days=3)
    newer = make_thermo(db_session, methane, proto=protocol("g3"), age_days=1)
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, methane, target=EQ, result_mode="first")
    assert api == via_sdk == via_mcp
    assert api["selection"]["thermo_ref"] == newer.public_ref and api["selection"]["administrative"] is True


def test_the_curated_profile_and_policy_choices_agree_through_all_three(client, sdk, mcp, db_session, methane):
    _seed(db_session, methane)
    make_thermo(db_session, methane, proto=protocol("g4"), age_days=700)  # not reviewed: below the curated floor
    for options in ({"profile": "curated"}, {"policy": "latest"}, {"policy": "most_reviewed", "min_review_status": "approved"}):
        api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, methane, target=EQ, **options)
        assert api == via_sdk == via_mcp, options


def test_a_single_conformer_target_agrees_through_all_three(client, sdk, mcp, db_session, methane):
    group = make_conformer_group(db_session, methane)
    make_thermo(db_session, methane, proto=protocol("g4"), target=ThermoTargetKind.single_conformer, group_id=group.id)
    target = {"kind": "single_conformer", "conformer_group_ref": group.public_ref}
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, methane, target=target)
    assert api == via_sdk == via_mcp and api["outcome"] == "sole_eligible_candidate"


def test_the_manifest_is_the_same_document_through_the_api_and_the_client(client, sdk, db_session, methane):
    _seed(db_session, methane)
    make_thermo(db_session, methane, proto=protocol("g4"), age_days=700)  # not reviewed: hidden under curated
    for profile in (None, "curated"):
        api = client.post(
            f"/api/v1/scientific/species-entries/{methane.public_ref}/thermo/select/manifest",
            json={"target": EQ}, params={"profile": profile} if profile else None,
        )
        assert api.status_code == 200
        via_sdk = sdk.get_species_thermo_selection_manifest(methane.public_ref, target=EQ, profile=profile)
        assert via_sdk == api.json(), profile
    exploratory = sdk.get_species_thermo_selection_manifest(methane.public_ref, target=EQ)
    curated = sdk.get_species_thermo_selection_manifest(methane.public_ref, target=EQ, profile="curated")
    assert exploratory != curated  # the profile reaches the server, so the two documents really differ


@pytest.mark.parametrize(
    ("options", "target", "expected"),
    [
        ({"temperature_k": 300.0}, EQ, "thermo_selection_condition_conflict"),
        ({"phase": "aqueous"}, EQ, "thermo_selection_condition_conflict"),
        ({}, {"kind": "single_conformer"}, "thermo_target_group_required"),
        ({}, {"kind": "equilibrium_ensemble", "conformer_group_ref": "cg_x"}, "thermo_target_group_not_allowed"),
    ],
)
def test_a_refusal_carries_the_same_code_through_all_three(client, sdk, mcp, methane, options, target, expected):
    api = client.post(
        f"/api/v1/scientific/species-entries/{methane.public_ref}/thermo/select", json={"target": target, **options}
    )
    assert api.status_code == 422 and api.json()["code"] == expected
    with pytest.raises(TCKDBHTTPError) as sdk_error:
        sdk.select_species_thermo(methane.public_ref, target=target, **options)
    assert sdk_error.value.status_code == 422 and sdk_error.value.code == expected
    with pytest.raises(MCPToolError) as mcp_error:
        mcp({"species_entry_ref": methane.public_ref, "target": target, **options})
    assert mcp_error.value.to_payload()["code"] == expected
