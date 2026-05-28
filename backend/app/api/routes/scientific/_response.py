"""Response-boundary helpers for scientific read routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi.responses import JSONResponse


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
      strips ``trust`` from each embedded kinetics record and each
      embedded calculation summary so the default ``/full`` payload
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
        for section in ("kinetics", "calculations"):
            for record in data.get(section, []) or []:
                if isinstance(record, dict):
                    record.pop("trust", None)
    else:
        for record in data.get("records", []) or []:
            if isinstance(record, dict):
                record.pop("trust", None)

    return JSONResponse(data)
