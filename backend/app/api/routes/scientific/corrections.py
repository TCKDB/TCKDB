"""Scientific correction-reference endpoints — frequency scale factors and
energy correction schemes.

Six public endpoints across two prefixes:

- ``GET  /scientific/frequency-scale-factors/{frequency_scale_factor_ref_or_id}``
- ``GET  /scientific/frequency-scale-factors/search``
- ``POST /scientific/frequency-scale-factors/search``
- ``GET  /scientific/energy-correction-schemes/{energy_correction_scheme_ref_or_id}``
- ``GET  /scientific/energy-correction-schemes/search``
- ``POST /scientific/energy-correction-schemes/search``

Each router uses a single prefix; the ``/search`` route is registered
before ``/{handle}`` so FastAPI doesn't route the search path through
the catch-all detail handler.

Both record types are reference/curation data. They are non-reviewable
(no entry in ``SubmissionRecordType``); the response envelope still
carries an empty ``review_summary`` for shape parity with the rest of
the scientific surface.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import EnergyCorrectionSchemeKind, FrequencyScaleKind
from app.schemas.reads.scientific_energy_correction_scheme import (
    ScientificEnergyCorrectionSchemeDetailResponse,
)
from app.schemas.reads.scientific_energy_correction_scheme_search import (
    EnergyCorrectionSchemeSearchRequest,
    ScientificEnergyCorrectionSchemeSearchResponse,
)
from app.schemas.reads.scientific_frequency_scale_factor import (
    ScientificFrequencyScaleFactorDetailResponse,
)
from app.schemas.reads.scientific_frequency_scale_factor_search import (
    FrequencyScaleFactorSearchRequest,
    ScientificFrequencyScaleFactorSearchResponse,
)
from app.services.scientific_read.energy_correction_schemes import (
    get_energy_correction_scheme,
)
from app.services.scientific_read.energy_correction_schemes_search import (
    search_energy_correction_schemes,
)
from app.services.scientific_read.frequency_scale_factors import (
    get_frequency_scale_factor,
)
from app.services.scientific_read.frequency_scale_factors_search import (
    search_frequency_scale_factors,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)


fsf_router = APIRouter(prefix="/frequency-scale-factors")
ecs_router = APIRouter(prefix="/energy-correction-schemes")


_POST_ALLOWED_QS_KEYS: set[str] = set()


# ===========================================================================
# Frequency scale factors
# ===========================================================================


@fsf_router.get(
    "/search",
    response_model=ScientificFrequencyScaleFactorSearchResponse,
)
def scientific_frequency_scale_factor_search_get(
    session: Session = Depends(get_db),
    frequency_scale_factor_ref: str | None = Query(None),
    value: float | None = Query(None),
    value_min: float | None = Query(None),
    value_max: float | None = Query(None),
    scale_kind: FrequencyScaleKind | None = Query(None),
    model_kind: str | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software: str | None = Query(None),
    software_version: str | None = Query(None),
    literature_ref: str | None = Query(None),
    used_by_statmech: bool | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    min_review_status: str | None = Query(None),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Frequency-scale-factor search.

    AND-combines the supplied filters; at least one meaningful filter
    is required. Explicit ``False`` bool filter values count as
    meaningful — see
    ``backend/docs/specs/scientific_correction_reads.md``.
    """
    request_obj = FrequencyScaleFactorSearchRequest(
        frequency_scale_factor_ref=frequency_scale_factor_ref,
        value=value,
        value_min=value_min,
        value_max=value_max,
        scale_kind=scale_kind,
        model_kind=model_kind,
        method=method,
        basis=basis,
        software=software,
        software_version=software_version,
        literature_ref=literature_ref,
        used_by_statmech=used_by_statmech,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        min_review_status=min_review_status,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(
        search_frequency_scale_factors(session, request_obj)
    )


@fsf_router.post(
    "/search",
    response_model=ScientificFrequencyScaleFactorSearchResponse,
)
def scientific_frequency_scale_factor_search_post(
    request: Request,
    body: FrequencyScaleFactorSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/frequency-scale-factors/search."""
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
        search_frequency_scale_factors(session, body)
    )


@fsf_router.get(
    "/{frequency_scale_factor_ref_or_id}",
    response_model=ScientificFrequencyScaleFactorDetailResponse,
)
def scientific_frequency_scale_factor_detail(
    frequency_scale_factor_ref_or_id: str = Path(
        ..., min_length=1, max_length=64
    ),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one FSF row as a scientific record.

    Path handle accepts an integer ``frequency_scale_factor.id`` or a
    public ref of the form ``fsf_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    return apply_internal_ids_visibility(
        get_frequency_scale_factor(
            session,
            frequency_scale_factor_handle=frequency_scale_factor_ref_or_id,
            include=parse_include(include),
        )
    )


# ===========================================================================
# Energy correction schemes
# ===========================================================================


@ecs_router.get(
    "/search",
    response_model=ScientificEnergyCorrectionSchemeSearchResponse,
)
def scientific_energy_correction_scheme_search_get(
    session: Session = Depends(get_db),
    energy_correction_scheme_ref: str | None = Query(None),
    name: str | None = Query(None),
    version: str | None = Query(None),
    scheme_kind: EnergyCorrectionSchemeKind | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software: str | None = Query(None),
    software_version: str | None = Query(None),
    literature_ref: str | None = Query(None),
    has_corrections: bool | None = Query(None),
    used_by_thermo: bool | None = Query(None),
    used_by_calculation: bool | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    min_review_status: str | None = Query(None),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Energy-correction-scheme search.

    AND-combines the supplied filters; at least one meaningful filter
    is required. ``software`` / ``software_version`` / ``used_by_thermo``
    are accepted but currently deferred (no backing column on this row
    or no available relationship) — see the spec for details.
    """
    request_obj = EnergyCorrectionSchemeSearchRequest(
        energy_correction_scheme_ref=energy_correction_scheme_ref,
        name=name,
        version=version,
        scheme_kind=scheme_kind,
        method=method,
        basis=basis,
        software=software,
        software_version=software_version,
        literature_ref=literature_ref,
        has_corrections=has_corrections,
        used_by_thermo=used_by_thermo,
        used_by_calculation=used_by_calculation,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        min_review_status=min_review_status,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(
        search_energy_correction_schemes(session, request_obj)
    )


@ecs_router.post(
    "/search",
    response_model=ScientificEnergyCorrectionSchemeSearchResponse,
)
def scientific_energy_correction_scheme_search_post(
    request: Request,
    body: EnergyCorrectionSchemeSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/energy-correction-schemes/search."""
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
        search_energy_correction_schemes(session, body)
    )


@ecs_router.get(
    "/{energy_correction_scheme_ref_or_id}",
    response_model=ScientificEnergyCorrectionSchemeDetailResponse,
)
def scientific_energy_correction_scheme_detail(
    energy_correction_scheme_ref_or_id: str = Path(
        ..., min_length=1, max_length=64
    ),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one ECS row as a scientific record.

    Path handle accepts an integer ``energy_correction_scheme.id`` or
    a public ref of the form ``ecs_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    return apply_internal_ids_visibility(
        get_energy_correction_scheme(
            session,
            energy_correction_scheme_handle=energy_correction_scheme_ref_or_id,
            include=parse_include(include),
        )
    )


__all__ = ["fsf_router", "ecs_router"]
