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
    """Drop ``record.trust`` unless the caller explicitly requested it."""
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
    else:
        for record in data.get("records", []) or []:
            if isinstance(record, dict):
                record.pop("trust", None)

    return JSONResponse(data)
