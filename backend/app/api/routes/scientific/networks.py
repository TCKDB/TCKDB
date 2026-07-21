"""Scientific Network / PDep endpoints — detail surface + search."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import NetworkKineticsModelKind, RecordReviewStatus
from app.schemas.reads.scientific_network import (
    ScientificNetworkDetailResponse,
    ScientificNetworkSolveDetailResponse,
)
from app.schemas.reads.scientific_network_kinetics import (
    ScientificNetworkKineticsDetailResponse,
)
from app.schemas.reads.scientific_network_kinetics_search import (
    NetworkKineticsSearchRequest,
    ScientificNetworkKineticsSearchResponse,
)
from app.schemas.reads.scientific_network_search import (
    NetworkSearchRequest,
    ScientificNetworkSearchResponse,
)
from app.schemas.reads.scientific_network_solve_search import (
    NetworkSolveSearchRequest,
    ScientificNetworkSolveSearchResponse,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.network_kinetics import (
    get_network_kinetics,
)
from app.services.scientific_read.network_kinetics_search import (
    search_network_kinetics,
)
from app.services.scientific_read.network_solves_search import (
    search_network_solves,
)
from app.services.scientific_read.networks import get_network, get_network_solve
from app.services.scientific_read.networks_search import search_networks

router = APIRouter(prefix="/networks")
solve_router = APIRouter(prefix="/network-solves")
kinetics_router = APIRouter(prefix="/network-kinetics")


_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get(
    "/search", response_model=ScientificNetworkSearchResponse
)
def scientific_networks_search_get(
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
    """MVP scientific network search.

    AND-combines the supplied filters; at least one meaningful filter
    required. Explicit ``False`` bool filter values count as
    meaningful.
    """
    request_obj = NetworkSearchRequest(
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
    return apply_internal_ids_visibility(
        search_networks(session, request_obj)
    )


@router.post(
    "/search", response_model=ScientificNetworkSearchResponse
)
def scientific_networks_search_post(
    request: Request,
    body: NetworkSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/networks/search."""
    forbidden = set(request.query_params.keys()) - _POST_ALLOWED_QS_KEYS
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                "post_search_fields_must_be_in_body: query-string keys "
                f"{sorted(forbidden)!r} are not accepted on POST; supply "
                "all search fields in the JSON body."
            ),
        )
    return apply_internal_ids_visibility(search_networks(session, body))


@router.get(
    "/{network_ref_or_id}",
    response_model=ScientificNetworkDetailResponse,
)
def scientific_network_detail(
    network_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one network as a scientific record.

    Path handle accepts an integer ``network.id`` or a public ref of
    the form ``net_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    return apply_internal_ids_visibility(
        get_network(
            session,
            network_handle=network_ref_or_id,
            include=parse_include(include),
        )
    )


@solve_router.get(
    "/search", response_model=ScientificNetworkSolveSearchResponse
)
def scientific_network_solves_search_get(
    session: Session = Depends(get_db),
    network_solve_ref: str | None = Query(None),
    network_ref: str | None = Query(None),
    solve_method: str | None = Query(None),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    pressure_min: float | None = Query(None),
    pressure_max: float | None = Query(None),
    has_bath_gas: bool | None = Query(None),
    has_energy_transfer: bool | None = Query(None),
    has_source_calculations: bool | None = Query(None),
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
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """MVP scientific network-solve search.

    AND-combines the supplied filters; at least one meaningful
    filter required. Explicit ``False`` bool filter values count as
    meaningful. ``solve_method`` filters ``NetworkSolve.me_method``
    (the master-equation algorithm); ``method`` / ``basis`` filter
    through the source-calculation level-of-theory join.
    """
    request_obj = NetworkSolveSearchRequest(
        network_solve_ref=network_solve_ref,
        network_ref=network_ref,
        solve_method=solve_method,
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        pressure_min=pressure_min,
        pressure_max=pressure_max,
        has_bath_gas=has_bath_gas,
        has_energy_transfer=has_energy_transfer,
        has_source_calculations=has_source_calculations,
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
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(
        search_network_solves(session, request_obj)
    )


@solve_router.post(
    "/search", response_model=ScientificNetworkSolveSearchResponse
)
def scientific_network_solves_search_post(
    request: Request,
    body: NetworkSolveSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/network-solves/search."""
    forbidden = set(request.query_params.keys()) - _POST_ALLOWED_QS_KEYS
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                "post_search_fields_must_be_in_body: query-string keys "
                f"{sorted(forbidden)!r} are not accepted on POST; supply "
                "all search fields in the JSON body."
            ),
        )
    return apply_internal_ids_visibility(
        search_network_solves(session, body)
    )


@solve_router.get(
    "/{network_solve_ref_or_id}",
    response_model=ScientificNetworkSolveDetailResponse,
)
def scientific_network_solve_detail(
    network_solve_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one network-solve as a scientific record.

    Path handle accepts an integer ``network_solve.id`` or a public
    ref of the form ``nsolve_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    return apply_internal_ids_visibility(
        get_network_solve(
            session,
            network_solve_handle=network_solve_ref_or_id,
            include=parse_include(include),
        )
    )


@kinetics_router.get(
    "/search", response_model=ScientificNetworkKineticsSearchResponse
)
def scientific_network_kinetics_search_get(
    session: Session = Depends(get_db),
    network_kinetics_ref: str | None = Query(None),
    network_ref: str | None = Query(None),
    network_solve_ref: str | None = Query(None),
    source_species_entry_refs: list[str] | None = Query(None),
    sink_species_entry_refs: list[str] | None = Query(None),
    source_smiles: list[str] | None = Query(None),
    sink_smiles: list[str] | None = Query(None),
    model_kind: NetworkKineticsModelKind | None = Query(None),
    temperature_min: float | None = Query(None),
    temperature_max: float | None = Query(None),
    pressure_min: float | None = Query(None),
    pressure_max: float | None = Query(None),
    has_chebyshev: bool | None = Query(None),
    has_plog: bool | None = Query(None),
    has_points: bool | None = Query(None),
    has_source_calculations: bool | None = Query(None),
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
):
    """MVP scientific network-kinetics search.

    AND-combines the supplied filters; at least one meaningful filter
    required. Explicit ``False`` bool filter values count as
    meaningful. ``model_kind`` narrows on
    ``NetworkKinetics.model_kind`` (Chebyshev / PLOG / tabulated);
    ``method`` / ``basis`` / ``software`` / ``workflow_tool`` filters
    route through the parent solve's source-calc graph. Review state
    is inherited from the parent solve.
    """
    request_obj = NetworkKineticsSearchRequest(
        network_kinetics_ref=network_kinetics_ref,
        network_ref=network_ref,
        network_solve_ref=network_solve_ref,
        source_species_entry_refs=source_species_entry_refs or [],
        sink_species_entry_refs=sink_species_entry_refs or [],
        source_smiles=source_smiles or [],
        sink_smiles=sink_smiles or [],
        model_kind=model_kind,
        temperature_min=temperature_min,
        temperature_max=temperature_max,
        pressure_min=pressure_min,
        pressure_max=pressure_max,
        has_chebyshev=has_chebyshev,
        has_plog=has_plog,
        has_points=has_points,
        has_source_calculations=has_source_calculations,
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
    return apply_internal_ids_visibility(
        search_network_kinetics(session, request_obj)
    )


@kinetics_router.post(
    "/search", response_model=ScientificNetworkKineticsSearchResponse
)
def scientific_network_kinetics_search_post(
    request: Request,
    body: NetworkKineticsSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/network-kinetics/search."""
    forbidden = set(request.query_params.keys()) - _POST_ALLOWED_QS_KEYS
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                "post_search_fields_must_be_in_body: query-string keys "
                f"{sorted(forbidden)!r} are not accepted on POST; supply "
                "all search fields in the JSON body."
            ),
        )
    return apply_internal_ids_visibility(
        search_network_kinetics(session, body)
    )


@kinetics_router.get(
    "/{network_kinetics_ref_or_id}",
    response_model=ScientificNetworkKineticsDetailResponse,
)
def scientific_network_kinetics_detail(
    network_kinetics_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one network-kinetics record as a scientific projection.

    Path handle accepts an integer ``network_kinetics.id`` or a public
    ref of the form ``nkin_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.

    Default response carries the kinetics core block + parent network,
    solve, and channel context + bounded evidence and
    available_sections summaries. The model-specific payloads
    (Chebyshev coefficient matrix, PLOG rows, point-tabulated triples)
    are deferred behind explicit include tokens. ``include=points`` is
    capped at ``settings.public_max_limit`` rows; the response carries
    ``points_truncated`` + ``point_count_total`` so callers can detect
    the cap and refine their request.
    """
    return apply_internal_ids_visibility(
        get_network_kinetics(
            session,
            network_kinetics_handle=network_kinetics_ref_or_id,
            include=parse_include(include),
        )
    )
