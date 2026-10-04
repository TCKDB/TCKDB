"""POST /api/v1/scientific/networks/{network_ref}/kinetics/select (+ ``/manifest``).

Method-aware selection among one network's stored pressure-dependent solves, for one stated gas-phase
rate-coefficient question. Read-only: a POST because the request is a structured body, not because anything is
written. The ordinary network browse and evaluation endpoints are untouched.

The path takes a public ``net_...`` ref only; an integer id is refused. ``channel_key`` and every other part of the
question are body fields, never path segments. The request carries no database id and no bound (the bounds are
fixed server-side, and exceeding one is a coded 422, nothing assessed).

The session is a read-only REPEATABLE READ snapshot opened **before** the route body runs (``get_snapshot_db``), so
resolving the network, counting the population and loading every solve are one consistent state, and the manifest
says so. ``get_db`` cannot be used here: its session may already have run a statement.

The response is returned directly, not through the internal-id stripper other scientific routes use: that stripper
drops every key ending in ``_id``, which would delete the rule registry key (``rule_id``) from the decision. A test
asserts instead that no database id appears anywhere in either document.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.schemas.reads.scientific_network_selection import (
    NetworkSelectionManifest,
    NetworkSelectionRequest,
    NetworkSelectionResponse,
)
from app.services.network_selection.public import (
    build_response,
    check_request_refs,
    profile_has_floor,
    redact_manifest,
    resolve_network_ref,
    run_selection,
)
from app.services.scientific_read.profile import stamp_read_profile

router = APIRouter(prefix="/networks")

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
    "/{network_ref}/kinetics/select",
    response_model=NetworkSelectionResponse,
    summary="Select network kinetics for a stated gas-phase rate-coefficient question",
)
def select_network_kinetics(
    request: Request,
    body: NetworkSelectionRequest,
    network_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> NetworkSelectionResponse:
    """Assess every visible solve of the network against the question and say which node, if any, to use.

    The competition unit is one determination (single channel) or one declared product set of one solve (bundle,
    full network). A solve answers only if its stored declarations say so (state partition, boundaries, regime,
    validity, bath scope, output catalog, observable, coefficient basis, units, temperature and pressure support); a
    missing fact makes it ``unresolved`` and a form this selector does not evaluate makes it ``unsupported``, never
    applicable by default. Nothing is derived, re-anchored, evaluated, spliced across solves or averaged.

    ``outcome`` is the selection basis: ``policy_preferred``, ``incomparable_alternatives``,
    ``sole_eligible_candidate``, ``no_applicable_candidate`` or ``policy_conflict``. Every rule shipped in this
    release is inactive, so ``method_preferred`` ranks nothing today and says so. A population over a server bound
    (solves, kinetics parents, channel or bundle nodes, states, channels, required outputs, evidence entries, numeric
    cells, snapshot size) is refused with 422 ``network_selection_population_too_large`` or
    ``network_selection_snapshot_too_large``: nothing is assessed, because a winner picked from part of the
    population would say nothing about the rest.

    ``mode=first`` may name a node among unranked first-front alternatives; the ``selection`` then says
    ``administrative_first`` and that this is not a method claim.
    """
    _refuse_unknown_query_keys(request)
    resolve_network_ref(session, network_ref)
    check_request_refs(session, body)
    selection = run_selection(session, network_ref=network_ref, body=body)
    manifest = redact_manifest(selection.manifest, withhold_excluded=profile_has_floor())
    payload = build_response(selection, body=body, manifest=manifest)
    stamp_read_profile(payload)
    return payload


@router.post(
    "/{network_ref}/kinetics/select/manifest",
    response_model=NetworkSelectionManifest,
    summary="Download the replayable decision manifest for a network selection",
)
def select_network_kinetics_manifest(
    request: Request,
    body: NetworkSelectionRequest,
    network_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request as ``/kinetics/select``, answered with the decision manifest as a download.

    The manifest holds the normalised request, the snapshot isolation it was read under, every captured solve with
    its normalised facts, every assessment, the rules consulted, each comparison and the administrative order, as
    public refs only. It is enough to recompute the decision, and check the recorded assessments and outcome, with no
    database. It is a new snapshot of the current data, not a retrieval of an earlier selection.
    """
    _refuse_unknown_query_keys(request)
    resolve_network_ref(session, network_ref)
    check_request_refs(session, body)
    selection = run_selection(session, network_ref=network_ref, body=body)
    manifest = redact_manifest(selection.manifest, withhold_excluded=profile_has_floor())
    return JSONResponse(
        manifest,
        headers={"Content-Disposition": f'attachment; filename="network_selection_{network_ref}.json"'},
    )
