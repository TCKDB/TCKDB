"""Scientific artifact metadata search and approved-byte download.

Search remains metadata-only. A separate content-addressed download route
serves bytes only when an owning calculation is explicitly approved and
re-verifies the persisted digest and size before returning content.
"""

from __future__ import annotations

import logging
from datetime import datetime
from mimetypes import guess_type
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_curator_or_admin
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._profile import PROFILE_QUERY_KEYS
from app.db.models.app_user import AppUser
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
    ArtifactKind,
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
)
from app.schemas.reads.scientific_artifact_integrity import (
    ScientificArtifactIntegrityHistoryResponse,
    ScientificArtifactIntegrityResponse,
)
from app.schemas.reads.scientific_artifact_search import (
    ScientificArtifactSearchRequest,
    ScientificArtifactSearchResponse,
)
from app.services.artifact_integrity import (
    record_from_error,
    record_integrity_observation,
)
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    load_artifact_bytes,
)
from app.services.scientific_read.artifact_download import (
    resolve_approved_artifact_by_sha256,
)
from app.services.scientific_read.artifact_integrity_reads import (
    artifact_integrity_history,
    search_artifact_integrity,
)
from app.services.scientific_read.artifacts_search import search_artifacts
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.profile import current_read_profile

router = APIRouter(prefix="/artifacts")
logger = logging.getLogger(__name__)

# Query-string keys allowed alongside POST. None in v0 — POST search
# requires every filter/include/pagination knob to live in the JSON body
# (same convention as the other scientific search endpoints).
# The router-level ``?profile=`` dependency puts these two keys on every
# scientific operation, POSTs included, so they must be allowed through the
# "search fields belong in the body" guard. Everything else is still
# rejected rather than silently ignored.
_POST_ALLOWED_QS_KEYS: set[str] = set(PROFILE_QUERY_KEYS)


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


@router.get("/integrity", response_model=ScientificArtifactIntegrityResponse)
def artifact_integrity_search(
    _user: AppUser = Depends(require_curator_or_admin),
    session: Session = Depends(get_db),
    sha256: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
    calculation_ref: str | None = Query(None),
    only_currently_broken: bool = Query(False),
    sort: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificArtifactIntegrityResponse:
    """What TCKDB has observed about custody of its own stored objects.

    ``hard_fail_reason`` already tells a client that a calculation's
    evidence can no longer be produced. This is where a curator finds out
    *what happened* -- expected against observed digest and size, the
    store's own metadata at the moment of detection, which read found it,
    when it started and whether it has recurred.

    One record per digest, not one per observation: repeat detections are
    appended forever by design (persistence is evidence), so a raw listing
    would bury one incident under its own retries. The full sequence is at
    ``/{sha256}/integrity``.

    Curator or admin only. The rows carry verifier prose and the object
    store's own ``ETag`` and paths, which is operational detail about a
    deployment rather than science about a record -- the same reasoning
    that gates raw artifact bytes (ADR 0004).
    """
    try:
        payload = search_artifact_integrity(
            session,
            sha256=sha256,
            calculation_ref=calculation_ref,
            only_currently_broken=only_currently_broken,
            sort=sort,
            offset=offset,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return apply_internal_ids_visibility(payload)


@router.get(
    "/{sha256}/integrity",
    response_model=ScientificArtifactIntegrityHistoryResponse,
)
def artifact_integrity_detail(
    sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
    _user: AppUser = Depends(require_curator_or_admin),
    session: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> ScientificArtifactIntegrityHistoryResponse:
    """Every observation recorded for one object, oldest first.

    Not flattened to current state. A break followed by a ``verified``
    observation followed by another break is a claim about the storage
    layer that no summary can express, and it is the claim an operator
    deciding whether to trust a bucket needs. The summary block carries
    the current verdict so a caller that only wants that does not have to
    reconstruct it.

    An unknown or never-observed digest returns an empty history rather
    than a 404: "nothing has ever been recorded about this object" is a
    true and useful answer, and it is emphatically *not* a verification
    claim -- an artifact nobody has read has been checked by nothing.
    """
    return apply_internal_ids_visibility(
        artifact_integrity_history(
            session, sha256=sha256, offset=offset, limit=limit
        )
    )


@router.get(
    "/{sha256}/download",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}},
        401: {"description": "Authentication required."},
        404: {"description": "No approved artifact has this digest."},
        502: {
            "description": (
                "Stored bytes failed integrity verification. Recorded "
                "durably; retrying will not clear it."
            )
        },
        503: {"description": "Artifact storage is unavailable."},
    },
)
def download_approved_artifact(
    sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
    user: AppUser = Depends(get_current_user),
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

    When that verification fails, this route does more than answer: it
    writes an ``artifact_integrity_event``, which hard-fails the owning
    calculation at read time for every subsequent reader. A 502 here is
    therefore not a private incident between one caller and the store —
    it is a recorded break in TCKDB's custody of the evidence
    (``docs/adr/0014-custody-of-stored-evidence-is-recorded-not-logged.md``).
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
    # These two are converted to HTTPException, which means they bypass the
    # ArtifactStorageUnavailable handler in app.api.errors and its logging.
    # Without these lines the download path reproduces the exact problem that
    # made the 2026-08-05 storage outage undiagnosable: a bare access-log 5xx
    # naming a subsystem, with no record of why that subsystem failed.
    except ArtifactIntegrityError as exc:
        logger.error(
            "Artifact integrity verification failed on download: %s",
            exc,
            exc_info=exc,
        )
        # A log is not a record. This reader gets a 502 either way; the
        # row is what makes the break visible to the *next* reader, and
        # to the trust evaluator, without anyone having to grep. Written
        # in its own transaction and best-effort, so it cannot turn a
        # 502 into a 500. See ADR 0014.
        record_from_error(
            exc,
            detected_during=ArtifactIntegrityDetectionContext.download,
            artifact=artifact,
            detected_by=user.id,
        )
        raise HTTPException(
            status_code=502,
            detail="Stored artifact failed integrity verification.",
        ) from exc
    except ArtifactStorageUnavailable as exc:
        logger.warning(
            "Artifact storage unavailable on download: %s", exc, exc_info=exc
        )
        # An unreachable store says nothing about the object and must not
        # be recorded as a custody break — but a store that answered and
        # said the object is *not there*, for a digest a row still
        # references, has told us something durable.
        if getattr(exc, "missing", False):
            record_integrity_observation(
                sha256=sha256,
                finding=ArtifactIntegrityFinding.object_missing,
                detected_during=ArtifactIntegrityDetectionContext.download,
                expected_bytes=artifact.bytes,
                artifact_id=artifact.id,
                artifact_recorded_at=artifact.created_at,
                detected_by=user.id,
                detail=str(exc),
            )
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
            # Raw bytes carry no JSON envelope, so the resolved read
            # profile is echoed as a header rather than being silently
            # dropped on the one scientific endpoint that returns binary.
            "X-TCKDB-Read-Profile": current_read_profile().profile.value,
        },
    )
