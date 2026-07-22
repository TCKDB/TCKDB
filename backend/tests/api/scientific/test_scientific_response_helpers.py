from __future__ import annotations

from typing import Any, cast

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.routes.scientific._response import (
    omit_trust_unless_requested,
    prepare_assessment_response,
)


class _Request(BaseModel):
    include: list[str]


class _Payload(BaseModel):
    request: _Request


class _DetailVisibility(BaseModel):
    request: dict[str, Any]
    record: dict[str, Any]


class _SearchVisibility(BaseModel):
    request: dict[str, Any]
    records: list[dict[str, Any]]


class _AssessmentVisibility(BaseModel):
    request: _Request
    records: list[dict[str, Any]]


def test_omit_trust_unless_requested_drops_detail_trust():
    payload = _Payload(request=_Request(include=[]))
    visibility = _DetailVisibility(
        request={"include": []},
        record={"record_ref": "calc_1", "trust": {"review_status": "not_reviewed"}},
    )

    response = omit_trust_unless_requested(visibility, payload)

    assert isinstance(response, JSONResponse)
    assert b'"trust"' not in response.body


def test_omit_trust_unless_requested_preserves_explicit_trust():
    payload = _Payload(request=_Request(include=["trust"]))
    visibility = _DetailVisibility(
        request={"include": ["trust"]},
        record={"record_ref": "calc_1", "trust": {"review_status": "not_reviewed"}},
    )

    response = omit_trust_unless_requested(visibility, payload)

    assert response is visibility


def test_omit_trust_unless_requested_drops_search_record_trust_from_json_response():
    payload = _Payload(request=_Request(include=[]))
    visibility = JSONResponse(
        {
            "request": {"include": []},
            "records": [
                {
                    "record_ref": "kin_1",
                    "trust": {"review_status": "not_reviewed"},
                }
            ],
        }
    )

    response = omit_trust_unless_requested(visibility, payload, scope="search")

    assert isinstance(response, JSONResponse)
    assert b'"trust"' not in response.body


def test_prepare_assessment_response_attaches_only_when_requested():
    payload = _AssessmentVisibility(
        request=_Request(include=["assessments"]),
        records=[{"record_ref": "kin_1"}],
    )
    attached: list[object] = []

    def attach(session: Session, target: _AssessmentVisibility) -> None:
        attached.append(session)
        target.records[0]["assessments"] = {"state": "unassessed"}

    session = cast(Session, object())
    response = prepare_assessment_response(
        session,
        payload,
        attach_assessments=attach,
    )

    assert attached == [session]
    assert isinstance(response, JSONResponse)
    assert b'"assessments"' in response.body


def test_prepare_assessment_response_omits_default_assessment_fields():
    payload = _AssessmentVisibility(
        request=_Request(include=[]),
        records=[
            {
                "record_ref": "kin_1",
                "assessments": {"state": "unassessed"},
            }
        ],
    )
    attached = False

    def attach(_session: Session, _target: _AssessmentVisibility) -> None:
        nonlocal attached
        attached = True

    response = prepare_assessment_response(
        cast(Session, object()),
        payload,
        attach_assessments=attach,
    )

    assert not attached
    assert isinstance(response, JSONResponse)
    assert b'"assessments"' not in response.body
