"""POST /api/v1/scientific/reaction-entries/{reaction_entry_ref}/kinetics/select (+ ``/manifest``).

Method-aware selection among one reaction entry's stored rate coefficients, for one stated gas-phase kinetic
question. Read-only: a POST because the request is a structured body, not because anything is written. The
ordinary ``GET .../kinetics`` browse order is untouched.

The path takes a public ``rxe_...`` ref only; an integer id is refused. The request body is a
:class:`KineticsSelectionRequest` and carries no database id and no candidate cap (the cap is fixed server-side at
500 visible records; above it the answer is the 422 ``kinetics_selection_population_too_large``).

The session is a read-only REPEATABLE READ snapshot opened **before** the route body runs (``get_snapshot_db``), so
resolving the reaction entry, counting the population and loading every record are one consistent state, and the
manifest says so. ``get_db`` cannot be used here: its session may already have run a statement, and an isolation
level cannot be changed after one.

The response is built from public refs only and is returned directly, not through the internal-id stripper that
other scientific routes use: that stripper drops every key ending in ``_id``, which would delete the rule registry
key (``rule_id``) from the decision. A test asserts instead that no database id appears anywhere in either document.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.schemas.reads.scientific_kinetics_selection import (
    KineticsSelectionManifest,
    KineticsSelectionRequest,
    KineticsSelectionResponse,
)
from app.services.kinetics_selection.public import (
    build_response,
    check_request_refs,
    profile_has_floor,
    redact_manifest,
    resolve_reaction_entry_ref,
    run_selection,
)
from app.services.scientific_read.profile import stamp_read_profile

router = APIRouter(prefix="/reaction-entries")

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
    "/{reaction_entry_ref}/kinetics/select",
    response_model=KineticsSelectionResponse,
    summary="Select kinetics for a stated gas-phase rate-coefficient question",
)
def select_reaction_kinetics(
    request: Request,
    body: KineticsSelectionRequest,
    reaction_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> KineticsSelectionResponse:
    """Assess every visible kinetics record of the entry against the question and say which, if any, to use.

    A record answers the question only if its stored claims say so (direction, target, coefficient basis, phase,
    temperature and pressure coverage, collider); a missing fact makes it ``unresolved`` and a form this selector
    does not evaluate makes it ``unsupported``, never applicable by default. ``outcome`` is the selection basis:
    ``policy_preferred`` (a registered, active rule leaves one determination with nothing preferred over it),
    ``incomparable_alternatives`` (the rules do not rank the leading determinations), ``sole_eligible_candidate``,
    ``no_applicable_candidate`` or ``policy_conflict`` (opposing rules or a cycle: nothing is selected). Every
    rule shipped in this release is inactive, so ``method_preferred`` ranks nothing today and says so.

    More than 500 visible candidates is refused with 422 ``kinetics_selection_population_too_large``: nothing is
    assessed, because a winner picked from part of the population would say nothing about the rest. Only records
    visible under the caller's read profile are counted.

    ``mode=first`` may name a determination among unranked first-front alternatives; the ``selection`` then says
    ``administrative_first`` and that this is not a method claim. A determination is returned with every eligible
    fitted representation of it.
    """
    _refuse_unknown_query_keys(request)
    entry_id = resolve_reaction_entry_ref(session, reaction_entry_ref)
    check_request_refs(session, body)
    selection = run_selection(session, reaction_entry_id=entry_id, body=body)
    manifest = redact_manifest(selection.manifest, withhold_excluded=profile_has_floor())
    payload = build_response(selection, body=body, reaction_entry_ref=reaction_entry_ref, manifest=manifest)
    stamp_read_profile(payload)
    return payload


@router.post(
    "/{reaction_entry_ref}/kinetics/select/manifest",
    response_model=KineticsSelectionManifest,
    summary="Download the replayable decision manifest for a kinetics selection",
)
def select_reaction_kinetics_manifest(
    request: Request,
    body: KineticsSelectionRequest,
    reaction_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request as ``/kinetics/select``, answered with the decision manifest as a download.

    The manifest holds the normalised request, the snapshot isolation it was read under, every assessed candidate
    with its normalised inputs, the determinations, the rules consulted, each comparison and the administrative
    order, as public refs only. It is enough to recompute the decision with no database. It is a new snapshot of
    the current data, not a retrieval of an earlier selection. The same visibility rules apply as for
    ``/kinetics/select``.
    """
    _refuse_unknown_query_keys(request)
    entry_id = resolve_reaction_entry_ref(session, reaction_entry_ref)
    check_request_refs(session, body)
    selection = run_selection(session, reaction_entry_id=entry_id, body=body)
    manifest = redact_manifest(selection.manifest, withhold_excluded=profile_has_floor())
    return JSONResponse(
        manifest,
        headers={"Content-Disposition": f'attachment; filename="kinetics_selection_{reaction_entry_ref}.json"'},
    )
