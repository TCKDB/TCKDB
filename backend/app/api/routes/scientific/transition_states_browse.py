"""GET /api/v1/scientific/transition-states/browse.

A public, unauthenticated, identifier-free catalogue read over the
transition-state-entry corpus. See
:func:`app.services.scientific_read.transition_states_search.browse_transition_states`
for why this is a separate function/route rather than an opt-in
relaxation of ``/transition-states/search``'s ``missing_filter`` refusal.

**Do not relax ``missing_filter`` on ``/transition-states/search`` to
serve this need.** That guard exists to prevent an unbounded accidental
scan of a public, unauthenticated table, ``/transition-states/search`` has
other callers who rely on it staying an exact lookup, and changing a
shipped contract to serve a new use case is the wrong trade. This route is
a sibling with its own request model
(:class:`~app.schemas.reads.scientific_transition_state_search.TransitionStatesBrowseRequest`),
which structurally has no ``reaction_ref`` / ``reaction_entry_ref`` /
``transition_state_ref`` / ``transition_state_entry_ref`` field to accept
-- a caller who has one of those wants ``/transition-states/search``; this
route is for the caller who does not.

Registered as its **own** router sharing the ``/transition-states``
prefix, not by decorating ``transition_states.ts_router`` in place --
that router already carries the
``/{transition_state_ref_or_id}`` detail catch-all by the time any other
module can import it, so a route added to it afterwards would be
appended after the catch-all and shadowed (``"browse"`` would resolve as
``transition_state_ref_or_id="browse"`` and 404/422 rather than reach this
handler). Route resolution order follows the order routers are
``include_router()``-ed onto the app, not which module "owns" a path
prefix, so registering this module's router *before*
``transition_states.ts_router`` in
``app/api/routes/scientific/__init__.py`` is what keeps
``/transition-states/browse`` reachable. Same mechanism ``/search``
already relies on (see its own module docstring: "registered before the
catch-all detail handler so /search routes don't get swallowed by
/{handle}") -- this route just needs the ordering enforced one level up,
in the router-assembly module, because it lives in a different file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import (
    ANYWHERE_SCOPE,
    TRANSITION_STATE_RECORD_SECTIONS,
    omit_trust_unless_requested,
    omit_unrequested_sections,
)
from app.db.models.common import (
    RecordReviewStatus,
    TransitionStateEntryStatus,
)
from app.schemas.reads.scientific_transition_state_search import (
    ScientificTransitionStatesBrowseResponse,
    TransitionStatesBrowseRequest,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.transition_states_search import (
    browse_transition_states,
)

router = APIRouter(prefix="/transition-states")


@router.get(
    "/browse", response_model=ScientificTransitionStatesBrowseResponse
)
def scientific_transition_states_browse(
    session: Session = Depends(get_db),
    status: TransitionStateEntryStatus | None = Query(None),
    charge: int | None = Query(None),
    multiplicity: int | None = Query(None, ge=1),
    has_calculations: bool | None = Query(None),
    has_opt: bool | None = Query(None),
    has_freq: bool | None = Query(None),
    has_sp: bool | None = Query(None),
    has_irc: bool | None = Query(None),
    has_path_search: bool | None = Query(None),
    has_geometry_validation: bool | None = Query(None),
    has_scf_stability: bool | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software: str | None = Query(None),
    software_version: str | None = Query(None),
    workflow_tool: str | None = Query(None),
    workflow_tool_version: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificTransitionStatesBrowseResponse:
    """List transition-state entries with no filter required, for a catalogue page.

    Every filter ``/transition-states/search`` accepts *except* the four
    owner/parent ref filters (``reaction_ref``, ``reaction_entry_ref``,
    ``transition_state_ref``, ``transition_state_entry_ref``) is accepted
    here too, so a listing can be narrowed after it is opened. The four
    ref filters are excluded because they are handles, not narrowing
    filters: a caller who already has one wants an exact lookup on
    ``/transition-states/search``, and there is no field on
    ``TransitionStatesBrowseRequest`` to carry one. Unlike
    ``/species/browse``, there are no composition filters here either --
    a transition state has no formula of its own; it is identified by the
    reaction it connects, not by a molecular graph. ``limit`` is capped at
    200 (default 50, same as ``/transition-states/search``); ``offset`` is
    capped by the hosted ``public_max_offset`` setting via the shared
    pagination validator. Ordering is
    ``review_rank ASC, created_at DESC, id DESC`` -- the same default as
    ``/transition-states/search`` -- which is what keeps pagination stable
    across requests. Client-supplied ``sort=`` is rejected with 422
    (``client_sort_not_supported``), matching the sibling endpoint.

    Records are at the transition-state-entry grain, same shape as
    ``/transition-states/search`` and the TS-entry detail endpoint
    (``ScientificTransitionStateEntryRecord``): each carries its own
    ``transition_state_entry`` core block, its parent ``transition_state``
    core block, and a ``reaction`` context block (refs plus a rendered
    equation string) -- enough to identify the record and say what
    reaction it belongs to without a second request.
    """
    request = TransitionStatesBrowseRequest(
        status=status,
        charge=charge,
        multiplicity=multiplicity,
        has_calculations=has_calculations,
        has_opt=has_opt,
        has_freq=has_freq,
        has_sp=has_sp,
        has_irc=has_irc,
        has_path_search=has_path_search,
        has_geometry_validation=has_geometry_validation,
        has_scf_stability=has_scf_stability,
        method=method,
        basis=basis,
        software=software,
        software_version=software_version,
        workflow_tool=workflow_tool,
        workflow_tool_version=workflow_tool_version,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = browse_transition_states(session, request)
    visibility = apply_internal_ids_visibility(payload)
    # Same reasoning as /transition-states/search: ``trust`` sits at the
    # record root here, but ``include=entries`` nests entry records that
    # carry the same field, so ANYWHERE_SCOPE is required to reach both
    # depths.
    visibility = omit_trust_unless_requested(
        visibility, payload, scope=ANYWHERE_SCOPE
    )
    # Same argument as ``trust`` above: every gated section on this record
    # is materialised again on each nested ``entries[*]`` sibling, so a
    # record-shaped scope would leave the nested copies nulled at depth.
    return omit_unrequested_sections(
        visibility,
        payload,
        table=TRANSITION_STATE_RECORD_SECTIONS,
        scope=ANYWHERE_SCOPE,
    )
