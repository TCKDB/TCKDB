"""GET /api/v1/scientific/reactions/browse.

A public, unauthenticated, identifier-free catalogue read over the
reaction-entry corpus. See
:func:`app.services.scientific_read.reactions.browse_reactions` for why
this is a separate function/route rather than an opt-in relaxation of
``/reactions/search``'s ``missing_reaction_search_filter`` refusal.

**Do not relax the missing-filter guard on ``/reactions/search`` to serve
this need.** That guard exists to prevent an unbounded accidental scan of
a public, unauthenticated table; ``/reactions/search`` has other callers
who rely on it staying an exact/participant lookup; and changing a
shipped contract to serve a new use case is the wrong trade. This route
is a sibling with its own request model
(:class:`~app.schemas.reads.scientific_reactions.ReactionsBrowseRequest`),
which structurally has no ``reaction_ref`` / ``reaction_entry_ref`` field
to accept -- a caller who has one of those wants
``/scientific/reactions/search``; this route is for the caller who does
not.

Registered as its own router sharing the ``/reactions`` prefix
(mirroring ``transition_states_browse.py``'s relationship to
``transition_states.ts_router``), not by adding a route to
``reactions.router`` in place. ``reactions.router`` currently carries only
``/search`` (GET + POST) with no catch-all handle route, so there is no
route-ordering hazard today -- but the browse route is still kept as a
separate module for the same reason species/TS browse are: a distinct
static path, registered next to its search sibling, needs no coupling to
that sibling's file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import OFFSET_DESCRIPTION, CursorQuery
from app.api.server_faults import ServerFaultRoute, caller_input
from app.db.models.common import RecordReviewStatus
from app.schemas.reads._field_bounds import (
    MAX_PARTICIPANTS_PER_REACTION as _MAX_PARTICIPANTS_PER_REACTION,
)
from app.schemas.reads.scientific_reactions import (
    ReactionDirectionQuery,
    ReactionsBrowseRequest,
    ScientificReactionSearchResponse,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.reactions import browse_reactions

router = APIRouter(route_class=ServerFaultRoute, prefix="/reactions")


def _drop_blank_smiles(values: list[str] | None) -> list[str]:
    """Filter out blank/whitespace-only entries so an empty value stays a no-op.

    ``?reactant_smiles=`` parses as ``[""]``, which would hit the
    unresolvable-SMILES guard in
    :func:`app.services.scientific_read.reactions.browse_reactions` and
    hard-empty the response; dropping blank entries keeps an empty value
    unconstrained.
    """
    if not values:
        return []
    return [v for v in values if v.strip()]


@router.get("/browse", response_model=ScientificReactionSearchResponse)
def scientific_reactions_browse(
    session: Session = Depends(get_db),
    family: str | None = Query(
        None,
        description=(
            "Exact match against the seeded reaction_family vocabulary "
            "(see /meta/reaction-families for the discoverable set of "
            "values). Unknown values return an empty result, not a 422."
        ),
    ),
    reactant_smiles: list[str] | None = Query(
        None,
        description=(
            "One or more exact-match SMILES filters; repeat the "
            "parameter for more than one "
            "(?reactant_smiles=C&reactant_smiles=[OH]), up to "
            f"{_MAX_PARTICIPANTS_PER_REACTION} per call; an empty value "
            "(?reactant_smiles=) means unfiltered, the same as omitting "
            "it. With one value, the filter matches that species; "
            "``request.filter.reactant_smiles`` echoes a one-item list. "
            "With more than one value, "
            "all of them are matched together as a single group against "
            "one stored side in one orientation, not independently -- "
            "which side(s) that group is tried against is governed by "
            "``direction`` (below): with the default ``forward`` this "
            "group is matched only against the stored reactants, "
            "leaving the product side unconstrained unless "
            "``product_smiles`` is also given. If any supplied SMILES "
            "does not resolve to a species TCKDB has, the result is "
            "empty rather than a partial match on the ones that did."
        ),
    ),
    product_smiles: list[str] | None = Query(
        None,
        description=(
            "One or more exact-match SMILES filters; repeat the "
            "parameter for more than one "
            "(?product_smiles=N&product_smiles=[NH2]), up to "
            f"{_MAX_PARTICIPANTS_PER_REACTION} per call; an empty value "
            "(?product_smiles=) means unfiltered, the same as omitting "
            "it. With one value, the filter matches that species; "
            "``request.filter.product_smiles`` echoes a one-item list. "
            "With more than one value, "
            "all of them are matched together as a single group against "
            "one stored side in one orientation, not independently -- "
            "which side(s) that group is tried against is governed by "
            "``direction`` (below): with the default ``forward`` this "
            "group is matched only against the stored products, "
            "leaving the reactant side unconstrained unless "
            "``reactant_smiles`` is also given. If any supplied SMILES "
            "does not resolve to a species TCKDB has, the result is "
            "empty rather than a partial match on the ones that did."
        ),
    ),
    direction: ReactionDirectionQuery = Query(
        ReactionDirectionQuery.forward,
        description=(
            "Which stored orientation(s) ``reactant_smiles`` / "
            "``product_smiles`` are matched against. The default is "
            "``forward``: ``reactant_smiles`` matches only the stored "
            "reactant side and ``product_smiles`` only the stored product "
            "side. ``direction=either`` tries both orientations and "
            "returns a record that matches in either; every "
            "returned record carries ``matched_direction`` saying which "
            "orientation actually matched. ``direction=reverse`` swaps "
            "the single orientation tried (``reactant_smiles`` against "
            "stored products, ``product_smiles`` against stored "
            "reactants). Mirrors ``/scientific/reactions/search``'s "
            "``direction`` parameter and vocabulary "
            "(:class:`~app.schemas.reads.scientific_reactions.ReactionDirectionQuery`); "
            "``direction=exact`` is not a legal value here either."
        ),
    ),
    has_kinetics: bool | None = Query(None),
    has_transition_state: bool | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    offset: int = Query(0, ge=0, description=OFFSET_DESCRIPTION),
    cursor: CursorQuery = None,
    limit: int = Query(50, ge=1, le=200),
) -> ScientificReactionSearchResponse:
    """List reaction entries with no filter required, for a catalogue page.

    Same record shape as ``GET/POST /scientific/reactions/search``
    (:class:`~app.schemas.reads.scientific_reactions.ReactionScientificRecord`)
    -- equation, participants (with ``formula``/``stoichiometry``),
    review, availability -- so a browse row and a search row render
    identically. Unlike search, no filter is required: with none
    supplied, every reaction entry in the corpus is a candidate.

    ``limit`` is capped at 200 (default 50, matching
    ``/reactions/search``); ``offset`` is capped by the hosted
    ``public_max_offset`` setting via the shared pagination validator.
    Default sort is ``review_rank ASC, has_kinetics DESC,
    has_transition_state DESC, created_at DESC, id DESC`` -- the same
    order ``/reactions/search`` uses, in the species-browse style of
    "review rank, then an availability flag, then recency, then id".
    There is no client-supplied ``sort=`` parameter to reject; this
    operation has none to accept.

    ``reactant_smiles`` / ``product_smiles`` are each matched as a group
    against the stored side(s) selected by ``direction``, not just
    species-by-species -- every SMILES within one parameter's group must
    land on the same stored side together; see the parameter
    descriptions and
    :func:`app.services.scientific_read.reactions.browse_reactions`.

    ``direction`` defaults to ``forward``; ``direction=either`` tries
    both stored orientations and returns a record that matches in
    either. ``matched_direction`` on each record reports which
    orientation matched.
    """
    request = caller_input(
        ReactionsBrowseRequest,
        family=family,
        reactant_smiles=_drop_blank_smiles(reactant_smiles),
        product_smiles=_drop_blank_smiles(product_smiles),
        direction=direction,
        has_kinetics=has_kinetics,
        has_transition_state=has_transition_state,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        offset=offset,
        cursor=cursor,
        limit=limit,
    )
    return apply_internal_ids_visibility(browse_reactions(session, request))
