"""``?profile=`` pass-through for every typed scientific method.

Stage 3's contract is that a consumer can tell curated results from
exploratory ones. A typed client that cannot pass ``profile=curated`` makes
that feature reachable only by dropping to raw HTTP, which defeats the point
of the echo — so every typed method whose backend operation publishes the
knob must forward it, and this module proves it method by method rather than
trusting a convention.

The companion assertion is the negative one: ``?release=`` is **rejected**
with a 422 on the entire ``/scientific/*`` surface
(``app/services/scientific_read/profile.py::resolve_read_profile``), including
the release endpoints themselves — a release is read through its path handle,
never through this query parameter. So no typed method takes ``release``, and
:func:`test_no_typed_method_accepts_a_release_parameter` keeps it that way.

Everything drives ``httpx.MockTransport``; no live server is involved.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from conftest import make_client
from tckdb_client import TCKDBClient
from tckdb_client._parity import COVERAGE, find_openapi_golden, load_openapi

# ---------------------------------------------------------------------------
# Which methods must forward the knob — derived from the OpenAPI golden
# ---------------------------------------------------------------------------


def _operations_publishing(parameter: str) -> set[tuple[str, str]]:
    path = find_openapi_golden()
    assert path is not None, "the OpenAPI golden must be discoverable"
    spec = load_openapi(path)
    found: set[tuple[str, str]] = set()
    for route, item in spec["paths"].items():
        for verb, operation in item.items():
            if verb.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            names = {
                p.get("name") for p in (operation.get("parameters") or [])
            }
            if parameter in names:
                found.add((verb.upper(), route))
    return found


def _typed_methods_for(parameter: str) -> list[str]:
    operations = _operations_publishing(parameter)
    methods = {
        entry.client_method
        for entry in COVERAGE
        if entry.status == "typed"
        and entry.key in operations
        and entry.client_method
    }
    return sorted(methods)


PROFILE_METHODS = _typed_methods_for("profile")

#: Methods whose first parameter is a required handle, and a value for it.
_REQUIRED_ARGUMENTS: dict[str, tuple] = {
    "download_artifact": ("a" * 64,),
    "export_chemkin": ({"species_refs": ["spc_1"]},),
    "get_calculation": ("calc_1",),
    "get_calculation_irc": ("calc_1",),
    "get_calculation_path_search": ("calc_1",),
    "get_calculation_scan": ("calc_1",),
    "get_conformer_group": ("cgrp_1",),
    "get_conformer_observation": ("cobs_1",),
    "get_energy_correction_scheme": ("ecs_1",),
    "get_frequency_scale_factor": ("fsf_1",),
    "get_geometry": ("geom_1",),
    "get_literature": ("lit_1",),
    "get_literature_records": ("lit_1",),
    "get_network_solve": ("nsolve_1",),
    "get_reaction_full": ("rxe_1",),
    "get_reaction_kinetics": ("rxe_1",),
    "get_species_thermo": ("spe_1",),
    "get_transition_state": ("ts_1",),
    "get_transition_state_entry": ("tse_1",),
}

_ENVELOPE = {
    "request": {"filter": {}, "sort": ""},
    "review_summary": {"total": 0},
    "records": [],
    "record": {},
    "results": [],
    "points": [],
    "pagination": {"offset": 0, "limit": 50, "returned": 0, "total": 0},
}


def _handler_recording(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ENVELOPE)

    return handler


def _query(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


def _invoke(client: TCKDBClient, name: str, **kwargs):
    args = _REQUIRED_ARGUMENTS.get(name, ())
    result = getattr(client, name)(*args, **kwargs)
    # The NDJSON exports return lazy iterators; the request has already been
    # issued by then, but draining keeps the generator from being collected
    # mid-assertion.
    if inspect.isgenerator(result):
        list(result)
    return result


# ---------------------------------------------------------------------------
# The knob reaches the wire
# ---------------------------------------------------------------------------


def test_the_ledger_and_the_golden_agree_on_who_must_forward_profile():
    """A drifting list here would make the parametrization pass vacuously."""

    assert len(PROFILE_METHODS) >= 43, PROFILE_METHODS
    assert "search_thermo" in PROFILE_METHODS
    assert "search_kinetics_analytics" in PROFILE_METHODS
    # Not on the scientific surface, so it never gained the knob.
    assert "health" not in PROFILE_METHODS
    assert "upload" not in PROFILE_METHODS


@pytest.mark.parametrize("method_name", PROFILE_METHODS)
def test_every_typed_method_accepts_profile(method_name):
    parameters = inspect.signature(getattr(TCKDBClient, method_name)).parameters
    assert "profile" in parameters, method_name
    assert parameters["profile"].default is None


@pytest.mark.parametrize("method_name", PROFILE_METHODS)
def test_profile_is_serialized_onto_the_request(method_name):
    seen: list[httpx.Request] = []
    client, _ = make_client(_handler_recording(seen))

    _invoke(client, method_name, profile="curated")

    assert seen, method_name
    assert _query(seen[-1]).get("profile") == ["curated"], method_name


@pytest.mark.parametrize("method_name", PROFILE_METHODS)
def test_exploratory_is_forwarded_verbatim_rather_than_normalised_away(
    method_name,
):
    seen: list[httpx.Request] = []
    client, _ = make_client(_handler_recording(seen))

    _invoke(client, method_name, profile="exploratory")

    assert _query(seen[-1]).get("profile") == ["exploratory"], method_name


@pytest.mark.parametrize("method_name", PROFILE_METHODS)
def test_omitting_profile_sends_no_profile_parameter(method_name):
    """The default request must stay byte-identical to the pre-Stage-3 one."""

    seen: list[httpx.Request] = []
    client, _ = make_client(_handler_recording(seen))

    _invoke(client, method_name)

    assert "profile" not in _query(seen[-1]), method_name


# ---------------------------------------------------------------------------
# POST search forms: query string, never the body
# ---------------------------------------------------------------------------

_POST_SEARCHES = (
    ("search_reactions", "method"),
    ("search_thermo", "method"),
    ("search_kinetics", "method"),
    ("search_species_calculations", "method_http"),
    ("search_species_structures", "method_http"),
    ("search_conformers", "method_http"),
    ("search_transition_states", "method_http"),
    ("search_networks", "method_http"),
    ("search_network_kinetics", "method_http"),
    ("search_network_solves", "method_http"),
    ("search_statmech", "method_http"),
    ("search_transport", "method_http"),
    ("search_artifacts", "method_http"),
    ("search_energy_correction_schemes", "method_http"),
    ("search_calculations", "method_http"),
)


@pytest.mark.parametrize("method_name,verb_kwarg", _POST_SEARCHES)
def test_post_searches_put_profile_in_the_query_not_the_body(
    method_name, verb_kwarg
):
    """The backend resolves ``profile`` from the query on POST too.

    Its router-level dependency reads the query string, and the POST search
    routes reject unknown *query* keys while allowing exactly ``profile`` and
    ``release`` through. A profile smuggled into the JSON body would be
    ignored — silently answering under the wrong contract.
    """

    seen: list[httpx.Request] = []
    client, _ = make_client(_handler_recording(seen))

    getattr(client, method_name)(**{verb_kwarg: "POST"}, profile="curated")

    request = seen[-1]
    assert request.method == "POST", method_name
    assert _query(request).get("profile") == ["curated"], method_name
    body = json.loads(request.content or b"{}")
    assert "profile" not in body, method_name


@pytest.mark.parametrize("method_name,verb_kwarg", _POST_SEARCHES)
def test_get_form_of_the_same_search_also_forwards_profile(
    method_name, verb_kwarg
):
    seen: list[httpx.Request] = []
    client, _ = make_client(_handler_recording(seen))

    getattr(client, method_name)(**{verb_kwarg: "GET"}, profile="curated")

    assert seen[-1].method == "GET", method_name
    assert _query(seen[-1]).get("profile") == ["curated"], method_name


# ---------------------------------------------------------------------------
# The echo comes back
# ---------------------------------------------------------------------------


def test_the_resolved_profile_echo_is_handed_back_untouched():
    """A consumer reads what answered, not what it asked for."""

    echo = {
        "filter": {},
        "sort": "",
        "profile": "curated",
        "profile_recommendation": "approved_floor_only",
        "profile_release_ref": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_ENVELOPE, "request": echo})

    client, _ = make_client(handler)

    response = client.search_thermo(smiles="C", profile="curated")

    assert response["request"]["profile"] == "curated"
    assert response["request"]["profile_recommendation"] == "approved_floor_only"
    assert response["request"]["profile_release_ref"] is None


def test_an_exploratory_response_says_tckdb_recommends_nothing():
    echo = {
        "filter": {},
        "sort": "",
        "profile": "exploratory",
        "profile_recommendation": "none",
        "profile_release_ref": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_ENVELOPE, "request": echo})

    client, _ = make_client(handler)

    response = client.search_kinetics(reactants=["C"], profile="exploratory")

    assert response["request"]["profile"] == "exploratory"
    assert response["request"]["profile_recommendation"] == "none"


def test_a_curated_request_can_still_be_answered_exploratorily():
    """The echo is the authority; the client must not overwrite it."""

    echo = {
        "filter": {},
        "sort": "",
        "profile": "exploratory",
        "profile_recommendation": "none",
        "profile_release_ref": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_ENVELOPE, "request": echo})

    client, _ = make_client(handler)

    response = client.search_species(smiles="C", profile="curated")

    assert response["request"]["profile"] == "exploratory"


# ---------------------------------------------------------------------------
# ``?release=`` is deliberately absent
# ---------------------------------------------------------------------------


def test_the_golden_publishes_release_on_the_same_operations():
    """Proof that its absence from the client is a decision, not an oversight.

    The OpenAPI document declares ``release`` alongside ``profile`` on every
    scientific operation, because both come from one router-level dependency.
    Declared is not accepted: that dependency raises 422
    ``release_scoping_not_implemented`` whenever the parameter is supplied.
    """

    assert _operations_publishing("release") == _operations_publishing("profile")


def test_no_typed_method_accepts_a_release_parameter():
    """Adding one would produce a request the backend answers with 422.

    Release-scoped reads are served by
    ``GET /scientific/releases/{tag}/selections`` and the release artifacts,
    which take the release through the **path**. Those endpoints are
    ``raw_only`` in the ledger, so there is no typed method to add it to
    either.
    """

    for entry in COVERAGE:
        if entry.status != "typed" or not entry.client_method:
            continue
        parameters = inspect.signature(
            getattr(TCKDBClient, entry.client_method)
        ).parameters
        assert "release" not in parameters, entry.client_method


def test_the_release_endpoints_are_reachable_but_not_typed():
    release_operations = {
        entry.operation: entry
        for entry in COVERAGE
        if entry.path.startswith("/api/v1/scientific/releases")
    }
    assert release_operations, "the golden must still publish the release reads"
    for entry in release_operations.values():
        assert entry.status == "raw_only", entry.operation
        assert entry.client_method is None


def test_the_remedy_for_release_scoping_is_documented_in_the_readme():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    assert "profile=" in readme
    assert "release" in readme
