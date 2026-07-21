"""Scientific artifact metadata search and approved-byte download.

Search remains metadata-only. A separate content-addressed download route
serves bytes only when an owning calculation is explicitly approved and
re-verifies the persisted digest and size before returning content.
"""

from __future__ import annotations

from datetime import datetime
from mimetypes import guess_type
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.routes.scientific._common import parse_include
from app.db.models.app_user import AppUser
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
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    load_artifact_bytes,
)
from app.services.scientific_read.artifact_download import (
    resolve_approved_artifact_by_sha256,
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


@router.get(
    "/{sha256}/download",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}},
        401: {"description": "Authentication required."},
        404: {"description": "No approved artifact has this digest."},
        502: {"description": "Stored bytes failed integrity verification."},
        503: {"description": "Artifact storage is unavailable."},
    },
)
def download_approved_artifact(
    sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
    _user: AppUser = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Response:
    """Download curator-approved bytes by their content-addressed digest.

    Unlike the metadata search endpoints, which are part of the public
    read surface, raw-artifact bytes are served only to authenticated
    callers (any valid API key or session): unredacted logs may embed
    producer-side scratch paths, usernames, and cluster hostnames that
    must never reach anonymous clients. This gate is unconditional (no
    opt-out flag) so no deployment can accidentally re-expose the bytes;
    see ``docs/adr/0004-store-artifacts-verbatim-gate-raw-log-access.md``.
    The digest is still re-verified against the stored bytes below.
    """

    artifact = resolve_approved_artifact_by_sha256(session, sha256)
    if artifact is None:
        # Deliberately indistinguishable from an unknown digest: callers cannot
        # probe whether non-approved/private content exists.
        raise HTTPException(status_code=404, detail="Approved artifact not found.")

    try:
        content = load_artifact_bytes(
            sha256,
            expected_bytes=artifact.bytes,
        )
    except ArtifactIntegrityError as exc:
        raise HTTPException(
            status_code=502,
            detail="Stored artifact failed integrity verification.",
        ) from exc
    except ArtifactStorageUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Artifact storage is unavailable.",
        ) from exc

    media_type = guess_type(artifact.filename)[0] or "application/octet-stream"
    encoded_filename = quote(artifact.filename, safe="")
    return Response(
        content=content,
        media_type=media_type,
        headers={
            # Authenticated, potentially PII-bearing bytes: forbid shared
            # caches from retaining them. ``public`` would let a CDN serve
            # one user's raw log to a later anonymous request for the same
            # URL, defeating the auth gate (ADR 0004).
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{encoded_filename}"
            ),
            "ETag": f'"{sha256}"',
            "X-Content-SHA256": sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )
