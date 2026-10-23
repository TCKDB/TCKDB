"""``collapse`` and ``selection_policy`` are refused on EVERY scientific operation, not only where they once worked.

A parameter an operation does not declare is silently ignored by FastAPI, so before this guard ``/reactions/browse``
answered ``?collapse=first`` with 200 and ignored it while ``/species/browse`` refused it. Registered as a router-level
dependency on ``scientific_router`` (beside the ``?profile=`` dependency), it checks the query string of every request
and the JSON body of every request that has one, so no list read, search or export can drift from the others.

The refusal names the select endpoint for the record kind the path is about, when there is one
(:mod:`app.schemas.reads.removed_parameters`).
"""

from __future__ import annotations

import json
import re

from fastapi import Request

from app.schemas.reads import removed_parameters as removed

#: Path pattern -> what to say. First match wins; the last entry is the default (no select endpoint to point at).
_BY_PATH: tuple[tuple[re.Pattern[str], removed.RemovedParameters], ...] = (
    (re.compile(r"/species-entries/[^/]+/thermo|/thermo/"), removed.THERMO),
    (re.compile(r"/reaction-entries/[^/]+/kinetics|/kinetics/search"), removed.KINETICS),
    (re.compile(r"/statmech"), removed.STATMECH),
    (re.compile(r"/transport"), removed.TRANSPORT),
    (re.compile(r"/species-calculations|/calculations/search"), removed.SPECIES_CALCULATIONS),
)
_DEFAULT = removed.RemovedParameters(select_endpoint=None)


def _spec_for(path: str) -> removed.RemovedParameters:
    for pattern, spec in _BY_PATH:
        if pattern.search(path):
            return spec
    return _DEFAULT


async def refuse_picking_parameters(request: Request) -> None:
    """Raise 422 ``browse_parameter_removed`` when the request names ``collapse`` or ``selection_policy`` anywhere."""
    names = set(request.query_params.keys())
    if request.method in ("POST", "PUT", "PATCH") and "json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None  # a body that is not JSON is the route's own 422 to give
        if isinstance(body, dict):
            names |= set(body)
    picking = _spec_for(request.url.path)
    # Only the picking parameters: renames are route-specific and stay with the routes that have them.
    picking_only = removed.RemovedParameters(
        select_endpoint=picking.select_endpoint, renamed={}, picking=True, pointer=picking.pointer
    )
    picking_only.check(names)
