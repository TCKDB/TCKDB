"""A 422 request-validation body does not echo the caller's input back.

pydantic puts the offending value under each error's ``input``. For a large invalid body that made the response as large
as the request (a 55 MB body came back as 55 MB). The handler drops ``input``; ``loc``, ``msg`` and ``type`` say what
is wrong and where.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.errors import register_exception_handlers


class _Payload(BaseModel):
    n: int
    note: str


def _client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    def _echo(payload: _Payload) -> dict:
        return {"ok": True}

    return TestClient(app)


def test_a_422_body_has_no_input() -> None:
    response = _client().post("/echo", json={"n": "not an int", "note": "x"})
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert errors, "the error list must still be there"
    for error in errors:
        assert "input" not in error
        assert error["loc"] and error["msg"] and error["type"]
    assert "not an int" not in response.text


def test_a_large_invalid_body_gets_a_small_422() -> None:
    big = "z" * 5_000_000
    # `note` is the right type; the extra-large value sits in a field that is wrong (a list where str is expected).
    response = _client().post("/echo", json={"n": 1, "note": [big]})
    assert response.status_code == 422
    assert len(response.content) < 2_000, len(response.content)
    assert big[:100] not in response.text


def test_a_caller_input_error_message_carries_no_input_value() -> None:
    from app.api.server_faults import caller_input

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/caller")
    def _caller() -> dict:
        caller_input(_Payload.model_validate, {"n": "SECRET-VALUE-" + "q" * 40, "note": "x"})
        return {}

    response = TestClient(app).get("/caller")
    assert response.status_code == 422
    assert "SECRET-VALUE" not in response.text
    assert "input_value" not in response.text
    assert "n\n" in response.json()["detail"]  # still says where


def test_a_valid_body_is_still_accepted() -> None:
    response = _client().post("/echo", json={"n": 1, "note": "x"})
    assert response.status_code == 200
