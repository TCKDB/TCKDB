"""Statmech selection routes (+ ``/manifest`` download).

``POST /api/v1/scientific/statmech/select``
``POST /api/v1/scientific/statmech/select/manifest``

Which supplied statistical model supports a declared use, and which, if any, audited rules prefer. One typed target
union (a species entry or a transition-state entry, with its scope) rather than a handler per owner kind. Read-only: a
POST because the request is a structured body, not because anything is written. The ordinary browse orders are
untouched, and nothing here stores a decision or endorses a record. No computed thermodynamic value is returned.

The body carries public refs only (a typed ref of the wrong kind is refused by the schema), no database id, no sort
expression, no pagination, no caller-authored rule and no bound: the engineering bounds are fixed server-side, and above
them the answer is the coded 422 ``statmech_selection_bound_exceeded``, never a prefix winner. A valid negative
scientific outcome (nothing qualifies, a conflict, an unresolved comparability) is HTTP 200.

The session is a read-only REPEATABLE READ snapshot opened **before** the route body runs (``get_snapshot_db``), so
resolving the target, counting the population and loading every record are one consistent state, and the manifest says
so. The response is built from public refs only and is returned directly, not through the internal-id stripper (which
would delete the rule registry key ``rule_id`` from the decision); a test asserts instead that no database id appears
in either document.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.schemas.reads.scientific_statmech_selection import (
    StatmechSelectionManifest,
    StatmechSelectionRequest,
    StatmechSelectionResponse,
)
from app.services.scientific_read.profile import stamp_read_profile
from app.services.statmech_selection.public import build_response, run_selection

router = APIRouter(prefix="/statmech")

_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


def _refuse_unknown_query_keys(request: Request) -> None:
    forbidden = set(request.query_params.keys()) - _ALLOWED_QS_KEYS
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                "post_search_fields_must_be_in_body: query-string keys "
                f"{sorted(forbidden)!r} are not accepted on POST; supply all fields in the JSON body."
            ),
        )


@router.post(
    "/select",
    response_model=StatmechSelectionResponse,
    summary="Qualify supplied statistical models for a stated use, and prefer one only where audited rules say so",
)
def select_statmech_models(
    request: Request,
    body: StatmechSelectionRequest,
    session: Session = Depends(get_snapshot_db),
) -> StatmechSelectionResponse:
    """Which of one species or transition-state entry's statmech models support the stated claim, or the preferred one.

    ``intent=qualify`` returns every candidate's assessment and selects nothing. ``intent=protocol_preferred`` also
    compares whole applicable models, but only where an audited rule says one precedes another for exactly this task,
    objective and scope; **this release ships no active rule**, so the answer is the qualified models, a sole eligible
    model, or incomparable alternatives, never an invented ranking. Alternatives are not equally accurate, only
    unranked. Unknown is never unity: a recipe fact a model does not state is reported as missing.

    A valid negative scientific outcome is 200. Refusals are 422: a contradictory request, a deferred bound exceeded
    (``statmech_selection_bound_exceeded``: more than 500 visible candidates, 25,000 nested rows, a dependency traversal
    past depth 8 or a manifest over 10 MiB; nothing is chosen from a prefix), or a population this release does not
    evaluate (``statmech_selection_unsupported``). An unknown, hidden or foreign target or candidate ref is one 404
    (``unknown_statmech_candidate_ref`` for a candidate).
    """
    _refuse_unknown_query_keys(request)
    selection = run_selection(session, body=body)
    payload = build_response(selection)
    stamp_read_profile(payload)
    return payload


@router.post(
    "/select/manifest",
    response_model=StatmechSelectionManifest,
    summary="Download the replayable decision manifest for a statmech selection",
)
def select_statmech_models_manifest(
    request: Request,
    body: StatmechSelectionRequest,
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request, answered with the decision manifest as a download (a new snapshot, not a saved decision)."""
    _refuse_unknown_query_keys(request)
    selection = run_selection(session, body=body)
    target_ref = selection.manifest["subject"]["entry_ref"]
    return JSONResponse(
        selection.manifest,
        headers={"Content-Disposition": f'attachment; filename="statmech_selection_{target_ref}.json"'},
    )
