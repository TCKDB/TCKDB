"""GET /api/v1/scientific/calculations/{calculation_ref_or_id}.

Default-shape detail endpoint plus ``include=results`` heavy section.
Other heavy include sections (dependencies, parameters, constraints,
artifacts, geometries, geometry_validation, scf_stability, scan, irc,
path_search) are recognized as legal include tokens but not yet
expanded — non-empty unimplemented heavy includes are rejected with 422
``include_not_implemented_yet``. See
``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import omit_trust_unless_requested
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
)
from app.schemas.reads.scientific_calculation import (
    CalculationDetailRequest,
    ScientificCalculationDetailResponse,
)
from app.schemas.reads.scientific_calculation_search import (
    CalculationOwnerKind,
    CalculationsSearchRequest,
    ScientificCalculationsSearchResponse,
)
from app.schemas.reads.scientific_common import (
    GeometryValidationStatus,
    SCFStabilityStatusValue,
)
from app.services.scientific_read.calculations import get_calculation
from app.services.scientific_read.calculations_search import (
    search_calculations,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)

router = APIRouter(prefix="/calculations")


_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get(
    "/search", response_model=ScientificCalculationsSearchResponse
)
def scientific_calculations_search_get(
    session: Session = Depends(get_db),
    species_entry_ref: str | None = Query(None),
    transition_state_entry_ref: str | None = Query(None),
    species_ref: str | None = Query(None),
    transition_state_ref: str | None = Query(None),
    owner_kind: CalculationOwnerKind | None = Query(None),
    calculation_type: CalculationType | None = Query(None),
    quality: CalculationQuality | None = Query(None),
    has_result: bool | None = Query(None),
    has_artifacts: bool | None = Query(None),
    has_input_geometry: bool | None = Query(None),
    has_output_geometry: bool | None = Query(None),
    artifact_kind: ArtifactKind | None = Query(None),
    created_before: datetime | None = Query(None),
    created_after: datetime | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    lot_ref: str | None = Query(None),
    lot_hash: str | None = Query(None),
    software: str | None = Query(None),
    software_version: str | None = Query(None),
    workflow_tool: str | None = Query(None),
    workflow_tool_version: str | None = Query(None),
    geometry_validation_status: GeometryValidationStatus | None = Query(None),
    scf_stability_status: SCFStabilityStatusValue | None = Query(None),
    dependency_role: CalculationDependencyRole | None = Query(None),
    parent_calculation_ref: str | None = Query(None),
    child_calculation_ref: str | None = Query(None),
    parameter_key: str | None = Query(None),
    parameter_value: str | None = Query(None),
    canonical_parameter_key: str | None = Query(None),
    canonical_parameter_value: str | None = Query(None),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    include_rejected_quality: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """MVP scientific calculation search.

    AND-combines the supplied filters and returns calculation records
    in the same shape as the detail endpoint. At least one meaningful
    filter is required; pure pagination/include/review knobs are not
    enough — see ``backend/docs/specs/scientific_calculation_reads.md``.
    """
    request_obj = CalculationsSearchRequest(
        species_entry_ref=species_entry_ref,
        transition_state_entry_ref=transition_state_entry_ref,
        species_ref=species_ref,
        transition_state_ref=transition_state_ref,
        owner_kind=owner_kind,
        calculation_type=calculation_type,
        quality=quality,
        has_result=has_result,
        has_artifacts=has_artifacts,
        has_input_geometry=has_input_geometry,
        has_output_geometry=has_output_geometry,
        artifact_kind=artifact_kind,
        created_before=created_before,
        created_after=created_after,
        method=method,
        basis=basis,
        lot_ref=lot_ref,
        lot_hash=lot_hash,
        software=software,
        software_version=software_version,
        workflow_tool=workflow_tool,
        workflow_tool_version=workflow_tool_version,
        geometry_validation_status=geometry_validation_status,
        scf_stability_status=scf_stability_status,
        dependency_role=dependency_role,
        parent_calculation_ref=parent_calculation_ref,
        child_calculation_ref=child_calculation_ref,
        parameter_key=parameter_key,
        parameter_value=parameter_value,
        canonical_parameter_key=canonical_parameter_key,
        canonical_parameter_value=canonical_parameter_value,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        include_rejected_quality=include_rejected_quality,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    payload = search_calculations(session, request_obj)
    visibility = apply_internal_ids_visibility(payload)
    visibility = _omit_unrequested_heavy_sections(
        visibility, payload, scope="search"
    )
    return omit_trust_unless_requested(visibility, payload, scope="search")


@router.post(
    "/search", response_model=ScientificCalculationsSearchResponse
)
def scientific_calculations_search_post(
    request: Request,
    body: CalculationsSearchRequest,
    session: Session = Depends(get_db),
):
    """JSON-body variant of /scientific/calculations/search.

    All filters live in the body. Query-string parameters are rejected
    with 422 ``post_search_fields_must_be_in_body``.
    """
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
    payload = search_calculations(session, body)
    visibility = apply_internal_ids_visibility(payload)
    visibility = _omit_unrequested_heavy_sections(
        visibility, payload, scope="search"
    )
    return omit_trust_unless_requested(visibility, payload, scope="search")


@router.get(
    "/{calculation_ref_or_id}",
    response_model=ScientificCalculationDetailResponse,
)
def scientific_calculation_detail(
    calculation_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one calculation as a scientific/provenance record.

    Path handle accepts an integer ``calculation.id`` or a public ref
    of the form ``calc_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs and unknown ids return 404.
    Default response identifies the row by ``calculation_ref`` only —
    integer ids surface only when ``include=internal_ids`` is supplied
    *and* the deployment permits it.

    Heavy include sections (currently only ``include=results``) appear
    in ``record`` only when the caller opts in, so the default-shape
    response stays compact.

    See ``docs/specs/public_identifier_policy.md`` and
    ``docs/specs/internal_ids_visibility_policy.md``.
    """
    request = CalculationDetailRequest(include=parse_include(include))
    payload = get_calculation(
        session,
        calculation_handle=calculation_ref_or_id,
        request=request,
    )
    visibility = apply_internal_ids_visibility(payload)
    visibility = _omit_unrequested_heavy_sections(visibility, payload)
    return omit_trust_unless_requested(visibility, payload)


# ---------------------------------------------------------------------------
# Conditional-include omission
# ---------------------------------------------------------------------------


# Heavy include tokens whose corresponding ``record`` field is omitted
# when the caller did not opt in. The mapping is (token → record key) so
# each new heavy include only needs one entry.
_OMITTABLE_RECORD_KEYS: dict[str, str] = {
    "results": "results",
    "dependencies": "dependencies",
    "artifacts": "artifacts",
    "input_geometries": "input_geometries",
    "output_geometries": "output_geometries",
    "geometry_validation": "geometry_validation",
    "scf_stability": "scf_stability",
    "wavefunction_diagnostic": "wavefunction_diagnostic",
    "parameters": "parameters",
    "constraints": "constraints",
    "review": "review_history",
    "scan": "scan",
    "irc": "irc",
    "path_search": "path_search",
}


def _omit_unrequested_heavy_sections(visibility, payload, *, scope: str = "detail"):
    """Drop ``record.<key>`` fields for heavy includes the caller didn't request.

    The Phase D :func:`apply_internal_ids_visibility` returns either the
    Pydantic model unchanged (when the deployment allows internal ids
    *and* the caller opted in) or a :class:`JSONResponse` carrying a
    pre-stripped dict. In the JSONResponse branch we mutate the
    serialized dict; in the Pydantic-model branch we re-serialize via
    ``model_dump`` so we can also drop keys. The OpenAPI / response_model
    contract is preserved in both branches because the dropped keys are
    declared ``... | None = None`` on the schema.

    Distinguishing "did not ask" (key absent) from "asked, no row"
    (key present, value ``null``) lets clients tell the two cases apart
    without having to re-read ``request.include``.

    ``scope='detail'`` operates on the singular ``record`` field;
    ``scope='search'`` operates on every entry of the ``records`` list.
    """
    requested = set(payload.request.include)
    to_drop = {
        record_key
        for token, record_key in _OMITTABLE_RECORD_KEYS.items()
        if token not in requested
    }
    if not to_drop:
        return visibility

    if isinstance(visibility, JSONResponse):
        import json

        data = json.loads(visibility.body)
    else:
        data = visibility.model_dump(mode="json")

    if scope == "detail":
        record = data.get("record")
        if isinstance(record, dict):
            for key in to_drop:
                record.pop(key, None)
    else:  # scope == "search"
        for record in data.get("records", []) or []:
            if isinstance(record, dict):
                for key in to_drop:
                    record.pop(key, None)

    return JSONResponse(data)
