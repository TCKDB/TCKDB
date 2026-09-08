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
from app.db.models.common import RecordReviewStatus
from app.schemas.reads._field_bounds import (
    MAX_PARTICIPANTS_PER_REACTION as _MAX_PARTICIPANTS_PER_REACTION,
)
from app.schemas.reads.scientific_reactions import (
    ReactionsBrowseRequest,
    ScientificReactionSearchResponse,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.reactions import browse_reactions

router = APIRouter(prefix="/reactions")


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
            f"{_MAX_PARTICIPANTS_PER_REACTION} per call. A single value "
            "behaves exactly as a bare SMILES filter always has. Every "
            "supplied species must appear among a reaction's "
            "participants; the product side is unconstrained unless "
            "``product_smiles`` is also given. Despite the name, this "
            "does not restrict matches to the stored reactant role: "
            "browse matches with direction=either, so a species listed "
            "here also matches reaction entries where it is stored as a "
            "PRODUCT and reached in reverse -- those records come back "
            "with matched_direction=\"reverse\". If any supplied SMILES "
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
            f"{_MAX_PARTICIPANTS_PER_REACTION} per call. A single value "
            "behaves exactly as a bare SMILES filter always has. Every "
            "supplied species must appear among a reaction's "
            "participants; the reactant side is unconstrained unless "
            "``reactant_smiles`` is also given. Despite the name, this "
            "does not restrict matches to the stored product role: "
            "browse matches with direction=either, so a species listed "
            "here also matches reaction entries where it is stored as a "
            "REACTANT and reached in reverse -- those records come back "
            "with matched_direction=\"reverse\". If any supplied SMILES "
            "does not resolve to a species TCKDB has, the result is "
            "empty rather than a partial match on the ones that did."
        ),
    ),
    has_kinetics: bool | None = Query(None),
    has_transition_state: bool | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    offset: int = Query(0, ge=0),
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

    ``reactant_smiles`` / ``product_smiles`` match **either** stored
    side of the reaction, not just the one the parameter names -- see
    the parameter descriptions and
    :func:`app.services.scientific_read.reactions.browse_reactions`.
    """
    request = ReactionsBrowseRequest(
        family=family,
        reactant_smiles=reactant_smiles or [],
        product_smiles=product_smiles or [],
        has_kinetics=has_kinetics,
        has_transition_state=has_transition_state,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(browse_reactions(session, request))
