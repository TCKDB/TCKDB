"""The HTTP API, the Python client and the MCP tool answer one kinetics selection request identically.

All three are driven against the real application through an ``httpx`` transport bridged to the test client, so what
is compared is what each consumer actually receives: the same outcome, the same explanation, the same refs, the same
protocol and applicability blocks, and the same refusal codes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from tckdb_client import TCKDBClient
from tckdb_client.errors import TCKDBHTTPError

from app.db.models.common import RecordReviewStatus as S
from app.services.kinetics_selection import selection as selection_module
from tests.services.kinetics_selection.test_engine import LabelRule
from tests.services.kinetics_selection.test_service import declared, determination
from tests.services.scientific_read._factories import make_species, next_inchi_key

_MCP_SRC = Path(__file__).resolve().parents[4] / "integrations" / "mcp" / "src"
if str(_MCP_SRC) not in sys.path:
    sys.path.insert(0, str(_MCP_SRC))

from tckdb_mcp.config import Config
from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.http_client import TCKDBHttpClient
from tckdb_mcp.server import dispatch_tool

TOOL = "tckdb_select_reaction_entry_kinetics"
BASE = "http://testserver/api/v1"
PREFERRED = {"version": 1, "method_kind": "saddle_point_tst"}
YIELDING = {"version": 1, "method_kind": "variational_tst"}
RULE = LabelRule("TEST-R1", {"saddle_point_tst"}, {"variational_tst"})
QUESTION = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}


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
def sdk(client):
    with TCKDBClient(BASE, transport=_Bridge(client)) as c:
        yield c


@pytest.fixture
def mcp(client):
    with TCKDBHttpClient(BASE, None, 5.0, transport=_Bridge(client)) as c:
        yield lambda arguments: dispatch_tool(TOOL, arguments, c, Config.from_env(env={}))


def _record(session, world, key, protocol, **kw):
    det = determination(session, world, key)

    def attach(k):
        k.protocol_declaration = protocol
        session.flush()

    return declared(session, world, det, children=attach, **kw), det


@pytest.fixture
def two(db_session, world):
    return (
        _record(db_session, world, "good", PREFERRED, status=S.not_reviewed),
        _record(db_session, world, "other", YIELDING, status=S.approved),
    )


def _three_ways(client, sdk, mcp, entry, **options):
    question = {**QUESTION, **options}
    profile = question.pop("profile", None)
    api = client.post(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics/select",
        json=question, params={"profile": profile} if profile else None,
    )
    assert api.status_code == 200, api.text
    return (
        api.json(),
        sdk.select_reaction_kinetics(entry.public_ref, **question, profile=profile),
        mcp({"reaction_entry_ref": entry.public_ref, **question, **({"profile": profile} if profile else {})}),
    )


def test_an_unranked_population_is_the_same_answer_through_all_three(client, sdk, mcp, world, two):
    (good, det_good), (other, det_other) = two
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world.entry)
    assert api == via_sdk == via_mcp
    assert api["outcome"] == "incomparable_alternatives" and api["selection"] is None
    assert {c["kinetics_ref"] for c in api["candidates"]} == {good.public_ref, other.public_ref}
    # Provenance rides along unchanged: the registry entries consulted and each candidate's declared protocol.
    assert api["policy"]["rules"][0]["rule_id"] == "K-XYG3-B3LYP-BARRIER"
    assert {c["determination_ref"]: c["protocol"]["method_kind"] for c in api["candidates"]} == {
        det_good.public_ref: "saddle_point_tst", det_other.public_ref: "variational_tst",
    }


def test_a_scoped_preference_is_the_same_answer_through_all_three(client, sdk, mcp, world, two, monkeypatch):
    monkeypatch.setattr(selection_module, "default_rules", lambda: (RULE,))
    (good, det_good), _ = two
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world.entry)
    assert api == via_sdk == via_mcp
    assert api["outcome"] == "policy_preferred" and api["selection"]["determination_ref"] == det_good.public_ref


def test_an_administrative_first_pick_is_labelled_the_same_way_through_all_three(client, sdk, mcp, world, two):
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world.entry, mode="first")
    assert api == via_sdk == via_mcp
    assert api["selection"]["basis"] == "administrative_first" and api["selection"]["administrative"] is True


def test_the_curated_profile_and_policy_choices_agree_through_all_three(client, sdk, mcp, world, two):
    for options in ({"profile": "curated"}, {"policy": "latest"}, {"policy": "most_reviewed", "min_review_status": "approved"}):
        api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world.entry, **options)
        assert api == via_sdk == via_mcp, options


def test_a_collider_mixture_agrees_through_all_three(client, sdk, mcp, db_session, world):
    n2 = make_species(db_session, smiles="N#N", multiplicity=1, inchi_key=next_inchi_key("KPN"))
    ar = make_species(db_session, smiles="[Ar]", multiplicity=1, inchi_key=next_inchi_key("KPA"))
    options = {
        "pressure": {"kind": "finite", "min_bar": 1.0, "max_bar": 10.0},
        "collider": {"components": [
            {"species_ref": n2.public_ref, "mole_fraction": 0.79}, {"species_ref": ar.public_ref, "mole_fraction": 0.21},
        ]},
    }
    api, via_sdk, via_mcp = _three_ways(client, sdk, mcp, world.entry, **options)
    assert api == via_sdk == via_mcp and api["outcome"] == "no_applicable_candidate"
    assert api["request"]["collider"] == options["collider"]


def test_the_manifest_is_the_same_document_through_the_api_and_the_client(client, sdk, world, two):
    for profile in (None, "curated"):
        api = client.post(
            f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics/select/manifest",
            json=QUESTION, params={"profile": profile} if profile else None,
        )
        assert api.status_code == 200
        via_sdk = sdk.get_reaction_kinetics_selection_manifest(world.entry.public_ref, **QUESTION, profile=profile)
        assert via_sdk == api.json(), profile
    exploratory = sdk.get_reaction_kinetics_selection_manifest(world.entry.public_ref, **QUESTION)
    curated = sdk.get_reaction_kinetics_selection_manifest(world.entry.public_ref, **QUESTION, profile="curated")
    assert exploratory != curated  # the profile reaches the server, so the two documents really differ


@pytest.mark.parametrize(
    "options",
    [
        {"phase": "liquid"},
        {"pressure": {"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}},  # a finite pressure needs a collider
        {"temperature_min_k": 2000.0},  # min above max
        {"target": {"kind": "resolved_channel"}},  # a resolved channel needs a locator
    ],
)
def test_a_refusal_carries_the_same_status_and_code_through_all_three(client, sdk, mcp, world, options):
    question = {**QUESTION, **options}
    api = client.post(f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics/select", json=question)
    assert api.status_code == 422
    expected = api.json()["code"]
    with pytest.raises(TCKDBHTTPError) as sdk_error:
        sdk.select_reaction_kinetics(world.entry.public_ref, **question)
    assert sdk_error.value.status_code == 422 and sdk_error.value.code == expected
    with pytest.raises(MCPToolError) as mcp_error:
        mcp({"reaction_entry_ref": world.entry.public_ref, **question})
    assert mcp_error.value.to_payload()["code"] == expected


def test_the_population_cap_is_one_refusal_through_all_three_and_nothing_pages_it(client, sdk, mcp, world, two, monkeypatch):
    from app.services.kinetics_selection import public as public_module

    monkeypatch.setattr(public_module, "MAX_CANDIDATES", 1)
    api = client.post(f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics/select", json=QUESTION)
    assert api.status_code == 422 and api.json()["code"] == "kinetics_selection_population_too_large"
    with pytest.raises(TCKDBHTTPError) as sdk_error:
        sdk.select_reaction_kinetics(world.entry.public_ref, **QUESTION)
    assert sdk_error.value.code == "kinetics_selection_population_too_large"
    assert sdk_error.value.status_code == 422
    with pytest.raises(MCPToolError) as mcp_error:
        mcp({"reaction_entry_ref": world.entry.public_ref, **QUESTION})
    assert mcp_error.value.to_payload()["code"] == "kinetics_selection_population_too_large"
