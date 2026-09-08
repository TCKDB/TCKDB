"""GET /api/v1/scientific/networks/browse.

A public, unauthenticated, identifier-free catalogue read over the
pressure-dependent-network corpus. See
:func:`app.services.scientific_read.networks_search.browse_networks`
for why this is a separate function/route rather than an opt-in
relaxation of ``/networks/search``'s ``missing_filter`` refusal -- the
same reasoning
:func:`app.services.scientific_read.reactions.browse_reactions`'s
docstring gives for its own relationship to ``/reactions/search``.

**Do not relax the missing-filter guard on ``/networks/search`` to serve
this need.** That guard exists to prevent an unbounded accidental scan
of a public, unauthenticated table; ``/networks/search`` has other
callers who rely on it staying a narrowed lookup; and changing a
shipped contract to serve a new use case is the wrong trade. This route
is a sibling with its own request model
(:class:`~app.schemas.reads.scientific_network_search.NetworkBrowseRequest`),
which -- unlike the reaction/transition-state/species browse siblings --
keeps every filter the search request has, ``network_ref`` included: a
network has no owner/parent ref to exclude, and narrowing an open
catalogue listing down to one exact ref is a legitimate page
interaction rather than a lookup that belongs on ``/search`` instead.

Registered as its **own** router sharing the ``/networks`` prefix, not
by adding a route to ``networks.router`` in place. That router already
carries the ``/{network_ref_or_id}`` detail catch-all, so a route
appended to it afterwards would be shadowed (``"browse"`` would resolve
as ``network_ref_or_id="browse"`` and 404/422 rather than reach this
handler). Route resolution order follows the order routers are
``include_router()``-ed onto the app, not which module "owns" a path
prefix, so registering this module's router *before* ``networks.router``
in ``app/api/routes/scientific/__init__.py`` is what keeps
``/networks/browse`` reachable -- same mechanism
``transition_states_browse.py`` documents for its own relationship to
``transition_states.ts_router``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import (
    NETWORK_RECORD_SECTIONS,
    SEARCH_SCOPE,
    omit_unrequested_sections,
)
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_network_search import (
    NetworkBrowseRequest,
    ScientificNetworkSearchResponse,
)
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.scientific_read.networks_search import browse_networks

router = APIRouter(prefix="/networks")


@router.get("/browse", response_model=ScientificNetworkSearchResponse)
def scientific_networks_browse(
    session: Session = Depends(get_db),
    network_ref: str | None = Query(None),
    species_ref: str | None = Query(None),
    species_entry_ref: str | None = Query(None),
    reaction_ref: str | None = Query(None),
    reaction_entry_ref: str | None = Query(None),
    has_species: bool | None = Query(None),
    has_reactions: bool | None = Query(None),
    has_states: bool | None = Query(None),
    has_channels: bool | None = Query(None),
    has_solves: bool | None = Query(None),
    has_kinetics: bool | None = Query(None),
    has_chebyshev: bool | None = Query(None),
    has_plog: bool | None = Query(None),
    has_point_kinetics: bool | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software: str | None = Query(None),
    software_version: str | None = Query(None),
    workflow_tool: str | None = Query(None),
    workflow_tool_version: str | None = Query(None),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    pressure_min: float | None = Query(None),
    pressure_max: float | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List networks with no filter required, for a catalogue page.

    Same record shape as ``GET/POST /scientific/networks/search``
    (:class:`~app.schemas.reads.scientific_network.ScientificNetworkRecord`)
    and the same include tokens -- unlike search, no filter is required:
    with none supplied, every visible network in the corpus is a
    candidate (a ``Network`` row is not usage-derived the way a
    ``level_of_theory`` row is, so an empty network with no attached
    species/reactions still appears).

    ``limit`` is capped at 200 (default 50, matching
    ``/networks/search``); ``offset`` is capped by the hosted
    ``public_max_offset`` setting via the shared pagination validator.
    Default sort is ``review_rank ASC, created_at DESC, id DESC`` -- the
    same order ``/networks/search`` uses. Client-supplied ``sort=`` is
    rejected with 422 (``client_sort_not_supported``), matching the
    sibling endpoint.
    """
    request = NetworkBrowseRequest(
        network_ref=network_ref,
        species_ref=species_ref,
        species_entry_ref=species_entry_ref,
        reaction_ref=reaction_ref,
        reaction_entry_ref=reaction_entry_ref,
        has_species=has_species,
        has_reactions=has_reactions,
        has_states=has_states,
        has_channels=has_channels,
        has_solves=has_solves,
        has_kinetics=has_kinetics,
        has_chebyshev=has_chebyshev,
        has_plog=has_plog,
        has_point_kinetics=has_point_kinetics,
        method=method,
        basis=basis,
        software=software,
        software_version=software_version,
        workflow_tool=workflow_tool,
        workflow_tool_version=workflow_tool_version,
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        pressure_min=pressure_min,
        pressure_max=pressure_max,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = browse_networks(session, request)
    visibility = apply_internal_ids_visibility(payload)
    return omit_unrequested_sections(
        visibility,
        payload,
        table=NETWORK_RECORD_SECTIONS,
        scope=SEARCH_SCOPE,
    )


__all__ = ["router"]
