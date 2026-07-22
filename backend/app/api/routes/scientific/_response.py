"""Response-boundary helpers for scientific read routes."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.services.scientific_read.internal_ids import apply_internal_ids_visibility

AssessmentAttacher = Callable[[Session, Any], Any]


def prepare_assessment_response(
    session: Session,
    payload: Any,
    *,
    attach_assessments: AssessmentAttacher,
) -> Any:
    """Attach requested assessments, then apply public-field visibility."""

    if "assessments" in set(payload.request.include):
        attach_assessments(session, payload)
    visibility = apply_internal_ids_visibility(payload)
    return omit_assessments_unless_requested(visibility, payload)


def omit_trust_unless_requested(
    visibility: Any,
    payload: Any,
    *,
    scope: str = "detail",
):
    """Drop ``record.trust`` unless the caller explicitly requested it.

    ``scope`` selects which embedded shape to clean:

    - ``"detail"`` — single ``record.trust`` on the top-level object.
    - ``"search"`` — ``records[*].trust`` on a list response.
    - ``"full"`` — composite ``/reaction-entries/{id}/full`` shape;
      strips ``trust`` from each embedded kinetics record, each
      embedded calculation summary, and each embedded
      transition-state-entry record so the default ``/full`` payload
      stays byte-identical to its pre-trust-propagation shape.
    """
    if "trust" in set(payload.request.include):
        return visibility

    if isinstance(visibility, JSONResponse):
        data = json.loads(visibility.body)
    else:
        data = visibility.model_dump(mode="json")

    if scope == "detail":
        record = data.get("record")
        if isinstance(record, dict):
            record.pop("trust", None)
    elif scope == "full":
        for section in ("kinetics", "calculations", "transition_states"):
            for record in data.get(section, []) or []:
                if isinstance(record, dict):
                    record.pop("trust", None)
    else:
        for record in data.get("records", []) or []:
            if isinstance(record, dict):
                record.pop("trust", None)

    return JSONResponse(data)


def omit_assessments_unless_requested(visibility: Any, payload: Any):
    """Remove opt-in assessment summaries from every nested record by default."""
    if "assessments" in set(payload.request.include):
        return visibility

    if isinstance(visibility, JSONResponse):
        data = json.loads(visibility.body)
    else:
        data = visibility.model_dump(mode="json")

    def strip(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("assessments", None)
            for value in node.values():
                strip(value)
        elif isinstance(node, list):
            for value in node:
                strip(value)

    strip(data)
    return JSONResponse(data)
