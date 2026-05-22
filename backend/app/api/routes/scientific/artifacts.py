"""GET + POST /api/v1/scientific/artifacts/search.

Standalone artifact-metadata search. **Metadata only** — never inlines
artifact bytes, never resolves a presigned download URL, never exposes
geometry/coordinate payloads. The persisted ``uri`` is the storage URI
verbatim, matching the existing ``include=artifacts`` projection on the
calculation detail endpoint.

Artifact body download is explicitly out of scope for the public
scientific read surface; see ``backend/docs/specs/scientific_artifact_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.common import (
    ArtifactKind,
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
)
from app.schemas.reads.scientific_artifact_search import (
    ScientificArtifactSearchRequest,
    ScientificArtifactSearchResponse,
)
from app.services.scientific_read.artifacts_search import search_artifacts
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)

router = APIRouter(prefix="/artifacts")

# Query-string keys allowed alongside POST. None in v0 — POST search
# requires every filter/include/pagination knob to live in the JSON body
# (same convention as the other scientific search endpoints).
_POST_ALLOWED_QS_KEYS: set[str] = set()


@router.get("/search", response_model=ScientificArtifactSearchResponse)
def artifacts_search_get(
    session: Session = Depends(get_db),
    # artifact filters
    artifact_kind: ArtifactKind | None = Query(None),
    filename: str | None = Query(None),
    filename_contains: str | None = Query(None),
    sha256: str | None = Query(None),
    has_sha256: bool | None = Query(None),
    has_bytes: bool | None = Query(None),
    bytes_min: int | None = Query(None, ge=0),
    bytes_max: int | None = Query(None, ge=0),
    # calculation filters
    calculation_ref: str | None = Query(None),
    calculation_type: CalculationType | None = Query(None),
    quality: CalculationQuality | None = Query(None),
    method: str | None = Query(None),
    basis: str | None = Query(None),
    software: str | None = Query(None),
    software_version: str | None = Query(None),
    workflow_tool: str | None = Query(None),
    workflow_tool_version: str | None = Query(None),
    # owner filters
    species_entry_ref: str | None = Query(None),
    transition_state_entry_ref: str | None = Query(None),
    conformer_observation_ref: str | None = Query(None),
    # time filters
    created_after: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
    # review/trust filters
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    # sort / include / pagination
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificArtifactSearchResponse:
    """Standalone artifact-metadata search.

    At least one meaningful filter is required (422 ``missing_filter``
    otherwise). Filters AND-combine. Sort vocabulary is v0-frozen —
    supplying ``sort=`` yields 422 ``client_sort_not_supported``.

    Returns artifact metadata only; never bodies, never download URLs.
    """
    request = ScientificArtifactSearchRequest(
        artifact_kind=artifact_kind,
        filename=filename,
        filename_contains=filename_contains,
        sha256=sha256,
        has_sha256=has_sha256,
        has_bytes=has_bytes,
        bytes_min=bytes_min,
        bytes_max=bytes_max,
        calculation_ref=calculation_ref,
        calculation_type=calculation_type,
        quality=quality,
        method=method,
        basis=basis,
        software=software,
        software_version=software_version,
        workflow_tool=workflow_tool,
        workflow_tool_version=workflow_tool_version,
        species_entry_ref=species_entry_ref,
        transition_state_entry_ref=transition_state_entry_ref,
        conformer_observation_ref=conformer_observation_ref,
        created_after=created_after,
        created_before=created_before,
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(search_artifacts(session, request))


@router.post("/search", response_model=ScientificArtifactSearchResponse)
def artifacts_search_post(
    request: Request,
    body: ScientificArtifactSearchRequest,
    session: Session = Depends(get_db),
) -> ScientificArtifactSearchResponse:
    """JSON-body variant for structured artifact search.

    All filters / include / pagination knobs live in the body. Any
    query-string keys are rejected with 422
    ``post_search_fields_must_be_in_body`` (same convention as the
    other scientific search endpoints). ``sort`` in the body is rejected
    by the service layer.
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
    return apply_internal_ids_visibility(search_artifacts(session, body))
