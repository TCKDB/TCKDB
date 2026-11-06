"""A server that fails its own response model is a logged 500, not the caller's fault.

``ResponseValidationError`` used to have no handler: the client got a
``text/plain`` "Internal Server Error". The validation errors can carry ORM
object reprs (``<app.db.models.geometry.Geometry object at 0x...>``), so the
body is fixed text and the detail goes to the log. A malformed *request* is
still the caller's fault and still 422 with its detail.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.errors import register_exception_handlers


class _Payload(BaseModel):
    x: int


class _Out(BaseModel):
    geometry: _Payload


class _InternalObject:
    """Stands in for an ORM row the response model cannot read."""


class _In(BaseModel):
    n: int


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/broken", response_model=_Out)
    def _broken():
        return {"geometry": _InternalObject()}

    @app.post("/echo")
    def _echo(body: _In):
        return {"n": body.n}

    return TestClient(app, raise_server_exceptions=False)


def test_response_model_failure_is_a_coded_500_without_internals(caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="app.api.errors"):
        resp = _client().get("/broken")

    assert resp.status_code == 500, resp.text
    assert resp.headers["content-type"].startswith("application/json"), resp.text
    body = resp.json()
    assert body["code"] == "data_integrity_error", body
    assert body["context"] == {}
    rendered = repr(body)
    for leak in ("_InternalObject", "object at 0x", "geometry", "Input should", "type=", "loc"):
        assert leak not in rendered, f"{leak!r} reached the client: {body}"

    records = [r for r in caplog.records if "response model validation failed" in r.getMessage()]
    assert len(records) == 1, caplog.text
    message = records[0].getMessage()
    assert records[0].levelno == logging.ERROR
    assert "GET /broken" in message
    assert "geometry" in message and "Field required" in message


def test_request_validation_failure_is_still_a_422_with_its_detail() -> None:
    resp = _client().post("/echo", json={"n": "not-a-number"})

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"][0]["loc"] == ["body", "n"], body


def test_a_valid_response_is_untouched() -> None:
    resp = _client().post("/echo", json={"n": 3})
    assert resp.status_code == 200
    assert resp.json() == {"n": 3}


def test_the_handler_is_what_makes_it_json(monkeypatch) -> None:
    """Premise pin: without the handler the same failure is text/plain."""
    app = FastAPI()  # deliberately not registering handlers

    @app.get("/broken", response_model=_Out)
    def _broken():
        return {"geometry": _InternalObject()}

    resp = TestClient(app, raise_server_exceptions=False).get("/broken")
    assert resp.status_code == 500
    assert not resp.headers["content-type"].startswith("application/json")
