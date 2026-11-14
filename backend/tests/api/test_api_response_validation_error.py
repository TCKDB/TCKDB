"""A server that fails its own response model is a logged 500, not the caller's fault.

Two sources, one answer (500 ``server_response_invalid``, fixed text):

* ``ResponseValidationError`` -- what a route returned does not fit its
  ``response_model``. Before: a ``text/plain`` "Internal Server Error".
* a pydantic ``ValidationError`` escaping an endpoint -- a read model built from
  a row that does not fit it, whether by ``Model.model_validate(row)`` or by
  ``Model(field=row.field, ...)``. It is a ``ValueError``, so it would be a
  422 that blames the caller and echoes an ORM object repr.
  ``ServerFaultRoute`` (on every router) turns it into a 500.

Endpoint code that validates *caller* input itself goes through
``caller_input`` and keeps its 422.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, create_model

from app.api.app import create_app
from app.api.errors import register_exception_handlers
from app.api.server_faults import ServerFaultRoute, caller_input
from app.schemas.entities.software import SoftwareRead
from tests.api.test_api_network_reads import _upload_pdep_network
from tests.api.test_api_reads import _hydrogen_conformer_payload
from tests.api.test_api_upload_computed_species import _hydrogen_bundle_payload

_LEAKS = ("object at 0x", "Input should", "type=", "input_type", "loc", "nope")


class _Payload(BaseModel):
    x: int


class _Out(BaseModel):
    geometry: _Payload


class _InternalObject:
    """Stands in for an ORM row the response model cannot read."""


class _In(BaseModel):
    n: int


def _mini() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    router = APIRouter(route_class=ServerFaultRoute)

    @router.get("/broken", response_model=_Out)
    def _broken():
        return {"geometry": _InternalObject()}

    @router.get("/row")
    def _row():
        return _Out.model_validate({"geometry": _InternalObject()})

    @router.get("/built")
    def _built():
        return _Out(geometry=_InternalObject())  # type: ignore[arg-type]

    @router.get("/caller")
    def _caller():
        return caller_input(_In, n="not-a-number")

    @router.post("/echo")
    def _echo(body: _In):
        return {"n": body.n}

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _assert_server_fault(resp) -> None:
    assert resp.status_code == 500, resp.text
    assert resp.headers["content-type"].startswith("application/json"), resp.text
    body = resp.json()
    assert body["code"] == "server_response_invalid", body
    assert body["context"] == {}
    rendered = repr(body)
    for leak in _LEAKS:
        assert leak not in rendered, f"{leak!r} reached the client: {body}"


def _logged(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


@pytest.mark.parametrize("path", ["/broken", "/row", "/built"])
def test_a_server_side_validation_failure_is_a_coded_500(caplog, path) -> None:
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        resp = _mini().get(path)

    _assert_server_fault(resp)
    messages = _logged(caplog)
    assert any(f"GET {path}" in m and "geometry" in m for m in messages), caplog.text
    # The offending value is not logged: a secret in a response must not reach the log.
    assert not any("_InternalObject" in m for m in messages), messages


def test_the_log_is_capped_and_reports_the_total(caplog) -> None:
    class _Many(BaseModel):
        rows: list[_Payload]

    app = FastAPI()
    register_exception_handlers(app)
    router = APIRouter(route_class=ServerFaultRoute)

    @router.get("/many", response_model=_Many)
    def _many():
        return _Many.model_validate({"rows": [{"x": "bad"}] * 100})

    app.include_router(router)
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        resp = TestClient(app, raise_server_exceptions=False).get("/many")
    assert resp.status_code == 500
    (message,) = [m for m in _logged(caplog) if "response model validation failed" in m]
    assert "100 error(s), first 20" in message
    assert message.count("'loc'") == 20


def test_a_validation_failure_on_caller_input_is_still_a_422_with_its_detail() -> None:
    resp = _mini().get("/caller")
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] != "server_response_invalid"
    assert "n" in str(body["detail"])


def test_request_validation_failure_is_still_a_422_with_its_detail() -> None:
    resp = _mini().post("/echo", json={"n": "not-a-number"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["loc"] == ["body", "n"]


def test_a_valid_response_is_untouched() -> None:
    resp = _mini().post("/echo", json={"n": 3})
    assert resp.status_code == 200
    assert resp.json() == {"n": 3}


def test_without_the_route_class_a_row_validation_is_the_422_this_replaces() -> None:
    """Premise pin: the pattern read as the caller's fault before ``ServerFaultRoute``."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/row")
    def _row():
        return _Out.model_validate({"geometry": _InternalObject()})

    assert TestClient(app, raise_server_exceptions=False).get("/row").status_code == 422


# ---------------------------------------------------------------------------
# The real app
# ---------------------------------------------------------------------------


def test_every_route_uses_the_server_fault_route_class() -> None:
    """A router added without it would put a server-side ValidationError back on the 422 path."""
    #: ``path -> reason`` for a route that must not use it. Empty on purpose.
    allowed: dict[str, str] = {}
    routes = [r for r in create_app().routes if isinstance(r, APIRoute)]
    assert len(routes) > 100, len(routes)  # a guard over an empty set proves nothing
    assert any(r.path.startswith("/api/") for r in routes)
    offenders = [r.path for r in routes if not isinstance(r, ServerFaultRoute) and r.path not in allowed]
    assert not offenders, f"use route_class=ServerFaultRoute (or a subclass): {offenders}"


def test_a_real_route_whose_row_fails_its_read_model_is_a_coded_500(client, monkeypatch, caplog) -> None:
    """The original shape: ``SoftwareRead.model_validate(row)`` in the route."""

    # Same name as the real model: the route class classifies by the failing model's name.
    StrictSoftwareRead = create_model("SoftwareRead", __base__=SoftwareRead, nope=(int, ...))

    client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
    software_id = client.get("/api/v1/software").json()["items"][0]["id"]
    monkeypatch.setattr("app.api.routes.software.SoftwareRead", StrictSoftwareRead)

    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        resp = client.get(f"/api/v1/software/{software_id}")

    _assert_server_fault(resp)
    assert str(software_id) not in resp.text
    assert any(
        "GET /api/v1/software/{software_id}" in m and "(SoftwareRead)" in m and "nope" in m
        for m in _logged(caplog)
    ), caplog.text


def test_a_real_route_building_a_read_model_by_constructor_is_a_coded_500(client, monkeypatch, caplog) -> None:
    """``NetworkStateRead(field=row.field, ...)``: the constructor path, not ``model_validate``."""
    from app.api.routes.networks import NetworkStateRead

    StrictNetworkStateRead = create_model("NetworkStateRead", __base__=NetworkStateRead, nope=(int, ...))

    network_id, _ = _upload_pdep_network(client)
    monkeypatch.setattr("app.api.routes.networks.NetworkStateRead", StrictNetworkStateRead)

    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        resp = client.get(f"/api/v1/networks/{network_id}")

    _assert_server_fault(resp)
    assert any(
        "GET /api/v1/networks/{network_id}" in m and "(NetworkStateRead)" in m for m in _logged(caplog)
    ), caplog.text


def test_a_non_response_model_failing_inside_a_route_stays_a_422() -> None:
    """The rule: only a response or read model is a server fault; anything else keeps its 422."""
    app = FastAPI()
    register_exception_handlers(app)
    router = APIRouter(route_class=ServerFaultRoute)

    class _CallerOnly(BaseModel):  # in no response model, not a ``*Read``
        n: int

    @router.get("/inside")
    def _inside():
        return _CallerOnly.model_validate({"n": "x"})

    app.include_router(router)
    resp = TestClient(app, raise_server_exceptions=False).get("/inside")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] != "server_response_invalid"


def test_a_name_that_is_also_a_response_model_stays_a_422_through_caller_input() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    router = APIRouter(route_class=ServerFaultRoute)

    class Both(BaseModel):
        n: int

    @router.get("/both", response_model=Both)
    def _both():
        return caller_input(Both.model_validate, {"n": "x"})

    app.include_router(router)
    assert TestClient(app, raise_server_exceptions=False).get("/both").status_code == 422


# ---------------------------------------------------------------------------
# Caller input that must stay a 422
# ---------------------------------------------------------------------------

_LOOKUP_CASES = [
    ("/api/v1/lookup/species", {"smiles": "", "charge": 0, "multiplicity": 1}),
    ("/api/v1/lookup/species", {"smiles": "   ", "charge": 0, "multiplicity": 1}),
    ("/api/v1/lookup/species", {"smiles": "[e-]", "charge": -1, "multiplicity": 2}),
    (
        "/api/v1/lookup/species-calculation",
        {"smiles": "", "charge": 0, "multiplicity": 1, "type": "opt", "method": "wb97xd"},
    ),
    (
        "/api/v1/lookup/species-calculation",
        {"smiles": "[e-]", "charge": -1, "multiplicity": 2, "type": "opt", "method": "wb97xd"},
    ),
    ("/api/v1/lookup/reaction", {"reactants": "", "products": "C"}),
    ("/api/v1/lookup/reaction", {"reactants": "C", "products": "C", "product_multiplicities": 0}),
    ("/api/v1/lookup/reaction", {"reactants": "[e-]", "products": "C"}),
]


@pytest.mark.parametrize(("path", "params"), _LOOKUP_CASES)
def test_lookup_caller_input_is_not_a_server_fault(client, path, params) -> None:
    resp = client.get(path, params=params)
    # The status and code origin/main gives for these inputs.
    assert resp.status_code == 422, (resp.status_code, resp.text)
    assert resp.json()["code"] == "validation_error", resp.text
    _record(f"lookup:{path}:{sorted(params.items())}", resp)


_ORIGINS = [
    {},
    {"origin_kind": "nope"},
    {"origin_kind": "reused_result"},
]


@pytest.mark.parametrize("origin", _ORIGINS, ids=["empty", "unknown_kind", "reused_without_source"])
def test_upload_workflow_revalidation_of_caller_input_is_not_a_server_fault(client, origin) -> None:
    payload = _hydrogen_bundle_payload()
    payload["conformers"][0]["primary_calculation"]["parameters_json"] = {"tckdb_origin": origin}
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 422, (resp.status_code, resp.text)
    assert "server_response_invalid" not in resp.text
    _record(f"computed-species:{origin}", resp)


def test_a_real_route_that_validates_caller_input_keeps_its_422(client) -> None:
    """A request model built from query parameters is the caller's: coded 422, not a 500."""
    resp = client.get("/api/v1/scientific/species/search", params={"formula": "C" * 5000})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] != "server_response_invalid"
    _record("species-search-formula", resp)


def _record(key: str, resp) -> None:
    """Dump status and body when asked, to compare a run on this tree with one on origin/main."""
    target = os.environ.get("TCKDB_DUMP_422_BODIES")
    if not target:
        return
    path = Path(target)
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = {"status": resp.status_code, "body": resp.text}
    path.write_text(json.dumps(data, sort_keys=True, indent=1))


# ---------------------------------------------------------------------------
# Dry run renders a raw ValidationError the way the route class classifies it
# ---------------------------------------------------------------------------


def _raw_validation_error(model_name: str):
    from pydantic import ValidationError

    strict = create_model(model_name, n=(int, ...))
    try:
        strict.model_validate({"n": "x"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


@pytest.mark.parametrize(
    ("model_name", "status"),
    [("SoftwareRead", 500), ("CalculationWithResultsPayload", 422)],
)
def test_dry_run_renders_a_validation_error_as_the_route_class_classifies_it(model_name, status) -> None:
    """``render_handled_exception`` is what ``/bundles/dry-run`` reports submit's refusal with."""
    from starlette.requests import Request

    from app.api.errors import render_handled_exception

    app = create_app()
    request = Request({"type": "http", "method": "POST", "path": "/bundles/dry-run", "headers": [], "app": app})
    rendered = render_handled_exception(request, _raw_validation_error(model_name))
    assert rendered is not None
    assert rendered[0] == status, rendered
    assert (rendered[1]["code"] == "server_response_invalid") == (status == 500)
