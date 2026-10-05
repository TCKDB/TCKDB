"""Structure selection routes (+ ``/manifest`` downloads).

``POST /api/v1/scientific/species-entries/{species_entry_ref}/calculations/select``
``POST /api/v1/scientific/species-entries/{species_entry_ref}/conformers/select``
``POST /api/v1/scientific/transition-state-entries/{transition_state_entry_ref}/evidence/select``

Which stored energies, conformer basins or saddles of ONE entry answer a stated question, and which, if any, the
evidence orders. Read-only: a POST because the request is a structured body, not because anything is written. The
ordinary browse orders are untouched.

The transition-state route takes an *entry* (``tse_...``), not the transition-state concept (``ts_...``): the unit of
evidence is an entry, and comparing several entries of one concept needs a validated same-path declaration that is
not taken yet, so sibling entries are assessed one at a time.

Paths take public refs only; an integer id is refused. The body carries no database id, no sort expression, no
pagination, no caller-authored rule and no bound (the engineering bounds are fixed server-side; above them the answer
is a coded 422 and never a prefix winner).

The session is a read-only REPEATABLE READ snapshot opened **before** the route body runs (``get_snapshot_db``), so
resolving the entry, counting the population and loading every record are one consistent state, and the manifest says
so. The response is built from public refs only and is returned directly, not through the internal-id stripper
(which would delete the rule registry key ``rule_id`` from the decision); a test asserts instead that no database id
appears in either document.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_snapshot_db
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.schemas.reads.scientific_structure_selection import (
    CalculationSelectionRequest,
    ConformerSelectionRequest,
    StructureSelectionManifest,
    StructureSelectionResponse,
    TransitionStateEvidenceSelectionRequest,
)
from app.services.scientific_read.profile import stamp_read_profile
from app.services.structure_selection.public import (
    SelectionBody,
    build_response,
    check_request_refs,
    resolve_species_entry_ref,
    resolve_transition_state_entry_ref,
    run_selection,
)

species_router = APIRouter(prefix="/species-entries")
tse_router = APIRouter(prefix="/transition-state-entries")

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


def _select(session: Session, request: Request, body: SelectionBody, entry_id: int, entry_ref: str) -> StructureSelectionResponse:
    _refuse_unknown_query_keys(request)
    check_request_refs(session, body)
    selection = run_selection(session, entry_id=entry_id, body=body)
    payload = build_response(selection, entry_ref=entry_ref)
    stamp_read_profile(payload)
    return payload


def _manifest(session: Session, request: Request, body: SelectionBody, entry_id: int, entry_ref: str, name: str) -> JSONResponse:
    _refuse_unknown_query_keys(request)
    check_request_refs(session, body)
    selection = run_selection(session, entry_id=entry_id, body=body)
    return JSONResponse(
        selection.manifest,
        headers={"Content-Disposition": f'attachment; filename="structure_selection_{name}_{entry_ref}.json"'},
    )


@species_router.post(
    "/{species_entry_ref}/calculations/select",
    response_model=StructureSelectionResponse,
    summary="Select among one species entry's calculations for a stated energy question",
)
def select_species_calculations(
    request: Request,
    body: CalculationSelectionRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> StructureSelectionResponse:
    """The lowest comparable recorded energy among the entry's calculations, or the audited-rule protocol preference.

    Values are ordered only inside one cohort (the same established recipe, quantity and energy convention); several
    cohorts end as ``incomparable_alternatives`` and a lower absolute energy never names a method winner. A calculation
    keeps its own record: observations are not collapsed. The answer is conditional on the authorized population the
    caller can see and is never a global-search certificate. More than 500 visible candidates, 5,000 nested rows, a
    dependency traversal past depth 8 or a manifest over 10 MiB is refused with a coded 422: nothing is chosen from a
    prefix. A deferred quantity (enthalpy, Gibbs energy, barrier, rate, ...) is 422 ``structure_selection_unsupported``.
    """
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    return _select(session, request, body, entry_id, species_entry_ref)


@species_router.post(
    "/{species_entry_ref}/calculations/select/manifest",
    response_model=StructureSelectionManifest,
    summary="Download the replayable decision manifest for a calculation selection",
)
def select_species_calculations_manifest(
    request: Request,
    body: CalculationSelectionRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request, answered with the decision manifest as a download (a new snapshot, not a saved decision)."""
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    return _manifest(session, request, body, entry_id, species_entry_ref, "calculations")


@species_router.post(
    "/{species_entry_ref}/conformers/select",
    response_model=StructureSelectionResponse,
    summary="Select among one species entry's validated conformer basins",
)
def select_species_conformers(
    request: Request,
    body: ConformerSelectionRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> StructureSelectionResponse:
    """The lowest validated basin of one cohort, the basins that support a claim, or the protocol preference.

    A basin needs supported curvature evidence on its own geometry: imaginary modes are judged by magnitude against
    the stored tau (ADR 0012), never counted. Repeated determinations of one basin are alternates, not independent
    confirmation, and a basin with a passing and a refuted determination is contested. A complete profile claim
    (``all_requested_members``) needs every requested basin eligible and in one cohort; no mixed-method profile is
    ever built from each basin's available method.
    """
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    return _select(session, request, body, entry_id, species_entry_ref)


@species_router.post(
    "/{species_entry_ref}/conformers/select/manifest",
    response_model=StructureSelectionManifest,
    summary="Download the replayable decision manifest for a conformer selection",
)
def select_species_conformers_manifest(
    request: Request,
    body: ConformerSelectionRequest,
    species_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request, answered with the decision manifest as a download."""
    entry_id = resolve_species_entry_ref(session, species_entry_ref)
    return _manifest(session, request, body, entry_id, species_entry_ref, "conformers")


@tse_router.post(
    "/{transition_state_entry_ref}/evidence/select",
    response_model=StructureSelectionResponse,
    summary="Select among one transition state entry's saddle determinations",
)
def select_transition_state_evidence(
    request: Request,
    body: TransitionStateEvidenceSelectionRequest,
    transition_state_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> StructureSelectionResponse:
    """Which saddle determinations support the claim (default: a conventional first-order saddle), or the lowest one.

    A first-order claim does not depend on counting imaginary modes: extra modes follow the stored tau and their
    declared dispositions. A higher-order characterization, when supported, never implies first-order TST
    suitability. Reactive connectivity is an additional claim that needs path evidence bound to the saddle geometry.
    """
    entry_id = resolve_transition_state_entry_ref(session, transition_state_entry_ref)
    return _select(session, request, body, entry_id, transition_state_entry_ref)


@tse_router.post(
    "/{transition_state_entry_ref}/evidence/select/manifest",
    response_model=StructureSelectionManifest,
    summary="Download the replayable decision manifest for a transition-state evidence selection",
)
def select_transition_state_evidence_manifest(
    request: Request,
    body: TransitionStateEvidenceSelectionRequest,
    transition_state_entry_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_snapshot_db),
) -> JSONResponse:
    """The same request, answered with the decision manifest as a download."""
    entry_id = resolve_transition_state_entry_ref(session, transition_state_entry_ref)
    return _manifest(session, request, body, entry_id, transition_state_entry_ref, "evidence")
