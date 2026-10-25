"""POST /api/v1/scientific/species-entries/{species_entry_ref}/thermo/select (+ ``/manifest``).

Selection of one species entry's thermo record for the formation enthalpy at
298.15 K: which records are eligible and, among those, the display order of
``docs/specs/browse_order.md``. TCKDB ranks no method above another. Read-only: a POST because the request is a structured body, not because anything
is written. The ordinary ``GET .../thermo`` browse order is untouched.

The path takes a public ``spe_...`` ref only; an integer id is refused. The request body is a
:class:`ThermoSelectionRequest` and carries no database id and no candidate cap (the cap is
fixed server-side at 500 visible records; above it the answer is the 422
``thermo_selection_population_too_large``).

The response is built from public refs only and is returned directly, not through the
internal-id stripper that other scientific routes use. A test asserts instead that no database id appears anywhere in either document.

The session is a read-only REPEATABLE READ snapshot opened **before** the route body runs (``get_snapshot_db``), so
resolving the entry and conformer-group refs and the selection itself read one consistent state.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.schemas.reads.scientific_thermo_selection import (
    ThermoSelectionManifest,
    ThermoSelectionRequest,
    ThermoSelectionResponse,
)
from app.services.scientific_read.profile import stamp_read_profile
from app.services.scientific_read.ref_guard import attach_review_statuses
from app.services.thermo_selection.public import (
    build_response,
    profile_has_floor,
    redact_manifest,
    resolve_species_entry_ref,
    run_selection,
)

router = APIRouter(prefix="/species-entries")

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
    "/{species_entry_ref}/thermo/select",
    response_model=ThermoSelectionResponse,
    summary="Select thermo for formation enthalpy at 298.15 K",
)
def select_species_thermo(
    request: Request,
    body: ThermoSelectionRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> ThermoSelectionResponse:
    """Assess every visible thermo record of the entry for H298 and say which, if any, to use.

    ``outcome`` is the selection basis: ``sole_eligible_candidate`` (one record is eligible; it is the
    only one, not the best), ``incomparable_alternatives`` (several are eligible and TCKDB ranks none
    above another) or ``no_applicable_candidate``. More than 500 visible
    candidates is refused with 422 ``thermo_selection_population_too_large``: nothing is assessed,
    because an answer picked from part of the population would say nothing about the rest. Only records
    visible under the caller's read profile are counted.

    ``result_mode=first`` may name the first record in display order among the alternatives; the
    ``selection`` then says ``administrative_first`` and that this is not a method claim.
    """
    _refuse_unknown_query_keys(request)
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    selection, _ = run_selection(session, species_entry_id=entry_id, body=body)
    manifest = redact_manifest(selection.manifest, withhold_excluded=profile_has_floor())
    payload = build_response(selection, body=body, species_entry_ref=species_entry_ref, manifest=manifest)
    stamp_read_profile(payload)
    attach_review_statuses(payload)
    return payload


@router.post(
    "/{species_entry_ref}/thermo/select/manifest",
    response_model=ThermoSelectionManifest,
    summary="Download the replayable decision manifest for a thermo selection",
)
def select_species_thermo_manifest(
    request: Request,
    body: ThermoSelectionRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request as ``/thermo/select``, answered with the decision manifest as a download.

    The manifest holds the normalised request, the effective review floor, every assessed
    candidate with its normalised inputs and the
    administrative order, as public refs only. It is enough to recompute the decision with no
    database. The same visibility rules apply as for ``/thermo/select``.
    """
    _refuse_unknown_query_keys(request)
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    selection, _ = run_selection(session, species_entry_id=entry_id, body=body)
    manifest = redact_manifest(selection.manifest, withhold_excluded=profile_has_floor())
    return JSONResponse(
        manifest,
        headers={"Content-Disposition": f'attachment; filename="thermo_selection_{species_entry_ref}.json"'},
    )
