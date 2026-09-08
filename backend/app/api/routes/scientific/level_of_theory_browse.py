"""GET /api/v1/scientific/level-of-theories/browse.

A public, unauthenticated, identifier-free catalogue read over the
level-of-theory reference library. See
:func:`app.services.scientific_read.level_of_theory_search.browse_levels_of_theory`
for why this is a separate function/route rather than an opt-in
relaxation of ``/level-of-theories/search``'s ``missing_filter`` refusal
-- the same reasoning
:func:`app.services.scientific_read.reactions.browse_reactions`'s
docstring gives for its own relationship to ``/reactions/search``.

**Do not relax the missing-filter guard on ``/level-of-theories/search``
to serve this need.** That guard exists to prevent an unbounded
accidental scan of a public, unauthenticated table; ``/level-of-theories
/search`` has other callers who rely on it staying a narrowed lookup;
and changing a shipped contract to serve a new use case is the wrong
trade. This route is a sibling with its own request model
(:class:`~app.schemas.reads.scientific_level_of_theory_search.LevelOfTheoryBrowseRequest`),
which -- unlike the reaction/transition-state/species browse siblings --
keeps every filter the search request has, ``level_of_theory_ref``
included: a level of theory has no owner/parent ref to exclude, and
narrowing an open catalogue listing down to one exact ref is a
legitimate page interaction rather than a lookup that belongs on
``/search`` instead.

Registered as its **own** router sharing the ``/level-of-theories``
prefix, not by adding a route to ``level_of_theory.router`` in place.
That router already carries the ``/{level_of_theory_ref_or_id}`` detail
catch-all, so a route appended to it afterwards would be shadowed
(``"browse"`` would resolve as ``level_of_theory_ref_or_id="browse"``
and 404/422 rather than reach this handler). Route resolution order
follows the order routers are ``include_router()``-ed onto the app, not
which module "owns" a path prefix, so registering this module's router
*before* ``level_of_theory.router`` in
``app/api/routes/scientific/__init__.py`` is what keeps
``/level-of-theories/browse`` reachable -- same mechanism
``transition_states_browse.py`` documents for its own relationship to
``transition_states.ts_router``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import (
    LEVEL_OF_THEORY_RECORD_SECTIONS,
    SEARCH_SCOPE,
    omit_unrequested_sections,
)
from app.db.models.common import SpinTreatment
from app.schemas.reads.scientific_level_of_theory_search import (
    LevelOfTheoryBrowseRequest,
    ScientificLevelOfTheorySearchResponse,
)
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.scientific_read.level_of_theory_search import (
    browse_levels_of_theory,
)

router = APIRouter(prefix="/level-of-theories")


@router.get("/browse", response_model=ScientificLevelOfTheorySearchResponse)
def scientific_level_of_theory_browse(
    session: Session = Depends(get_db),
    level_of_theory_ref: str | None = Query(None),
    lot_hash: str | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    dispersion: str | None = Query(None),
    solvent: str | None = Query(None),
    spin_treatment: SpinTreatment | None = Query(None),
    has_correction_schemes: bool | None = Query(None),
    has_frequency_scale_factors: bool | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    min_review_status: str | None = Query(None),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List levels of theory with no filter required, for a catalogue page.

    Same record shape as ``GET/POST /scientific/level-of-theories/search``
    (:class:`~app.schemas.reads.scientific_level_of_theory.ScientificLevelOfTheoryRecord`)
    and the same include tokens (``correction_schemes``,
    ``frequency_scale_factors``, ``used_by``, ``software``) -- unlike
    search, no filter is required: with none supplied, every level of
    theory with at least one attributing calculation is a candidate
    (usage-derived, same as search -- see
    ``app/services/scientific_read/level_of_theory_search.py``).

    ``limit`` is capped at 200 (default 50, matching
    ``/level-of-theories/search``); ``offset`` is capped by the hosted
    ``public_max_offset`` setting via the shared pagination validator.
    Default sort is ``method ASC, basis ASC, id ASC`` -- the same order
    ``/level-of-theories/search`` uses. Client-supplied ``sort=`` is
    rejected with 422 (``client_sort_not_supported``), matching the
    sibling endpoint.
    """
    request = LevelOfTheoryBrowseRequest(
        level_of_theory_ref=level_of_theory_ref,
        lot_hash=lot_hash,
        method=method,
        basis=basis,
        dispersion=dispersion,
        solvent=solvent,
        spin_treatment=spin_treatment,
        has_correction_schemes=has_correction_schemes,
        has_frequency_scale_factors=has_frequency_scale_factors,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        min_review_status=min_review_status,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = browse_levels_of_theory(session, request)
    visibility = apply_internal_ids_visibility(payload)
    return omit_unrequested_sections(
        visibility,
        payload,
        table=LEVEL_OF_THEORY_RECORD_SECTIONS,
        scope=SEARCH_SCOPE,
    )


__all__ = ["router"]
