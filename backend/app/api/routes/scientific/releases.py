"""Public read endpoints for citable dataset releases.

    GET /scientific/releases
    GET /scientific/releases/{release_handle}
    GET /scientific/releases/{release_handle}/manifest
    GET /scientific/releases/{release_handle}/selections
    GET /scientific/releases/{release_handle}/artifacts/{artifact_path}

``release_handle`` accepts either the public ref (``rel_...``) or the citable
tag (``2026.07.0``), because that is what a paper will quote.

These are unauthenticated on purpose: a release exists to be cited, and a
citation that only a logged-in curator can resolve is not a citation. The
curator-gated *write* paths live in ``app/api/routes/releases_admin.py``.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.common import DatasetReleaseStatus
from app.schemas.reads.scientific_release import (
    ScientificReleaseDetailResponse,
    ScientificReleaseListResponse,
    ScientificReleaseManifestResponse,
    ScientificReleaseSelectionsResponse,
)
from app.services.release.manifest import load_manifest
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.scientific_read.profile import (
    current_read_profile,
    release_backed_profile,
    set_current_read_profile,
)
from app.services.scientific_read.releases import (
    ManifestNotFrozenError,
    UnknownReleaseError,
    get_release,
    get_release_manifest,
    get_release_selections,
    list_releases,
    resolve_release,
)

router = APIRouter(prefix="/releases")


def _claim_release_backing(release) -> None:
    """Let this response claim ``tckdb_curated_release`` — the only place that may.

    Everything served from a *published* release resource was resolved through
    an attributed ``release_selection``, so the endorsement token is true here
    and nowhere else on the read surface. A draft is not an endorsement, so it
    keeps whatever profile the caller asked for.
    """
    if release.status is DatasetReleaseStatus.published:
        set_current_read_profile(release_backed_profile(release))


@router.get("", response_model=ScientificReleaseListResponse)
def scientific_releases(
    session: Session = Depends(get_db),
    status: DatasetReleaseStatus | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List dataset releases, newest first.

    Withdrawn releases are listed rather than hidden: a reader holding an old
    citation needs to be able to discover that it was retracted.
    """
    payload = list_releases(session, status=status, offset=offset, limit=limit)
    return apply_internal_ids_visibility(payload)


@router.get("/{release_handle}", response_model=ScientificReleaseDetailResponse)
def scientific_release_detail(
    release_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
):
    """Release detail: citation metadata, licenses, policy, and manifest.

    :raises HTTPException: 404 when no release matches the handle.
    """
    release = _release(session, release_handle)
    _claim_release_backing(release)
    payload = get_release(session, release_handle)
    return apply_internal_ids_visibility(payload)


@router.get(
    "/{release_handle}/manifest", response_model=ScientificReleaseManifestResponse
)
def scientific_release_manifest(
    release_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
):
    """The frozen manifest, plus a live re-verification of every checksum.

    :raises HTTPException: 404 when the release is unknown or has no frozen
        manifest (a draft release is not citable).
    """
    release = _release(session, release_handle)
    _claim_release_backing(release)
    try:
        payload = get_release_manifest(session, release_handle)
    except ManifestNotFrozenError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return apply_internal_ids_visibility(payload)


@router.get(
    "/{release_handle}/selections", response_model=ScientificReleaseSelectionsResponse
)
def scientific_release_selections(
    release_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include_superseded: bool = Query(True),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """The attributed, append-only selection ledger behind a release.

    :raises HTTPException: 404 when no release matches the handle.
    """
    release = _release(session, release_handle)
    _claim_release_backing(release)
    payload = get_release_selections(
        session,
        release_handle,
        include_superseded=include_superseded,
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(payload)


@router.get("/{release_handle}/artifacts/{artifact_path}")
def scientific_release_artifact(
    release_handle: str = Path(..., min_length=1, max_length=64),
    artifact_path: str = Path(..., min_length=1, max_length=255),
    session: Session = Depends(get_db),
) -> Response:
    """Serve one frozen release artifact, byte-for-byte as published.

    The bytes come from ``release_artifact.content``, written once at
    publication. Nothing that happened to the corpus afterwards — new uploads,
    review advancing, a DOI being attached — can change what this returns.

    It deliberately does **not** re-render and compare. That is what it used to
    do, returning 409 on any mismatch, which meant a single ordinary upload for
    a released species made the citation stop resolving: the candidate set had
    legitimately grown, the file legitimately differed, and the operator was
    told to go looking for tampering. Whether the live database still agrees is
    reported, non-fatally, by ``GET .../manifest`` under ``live_divergence``.

    The response carries ``ETag`` and ``X-TCKDB-Content-SHA256`` so a client can
    verify the download against the manifest independently, plus
    ``X-TCKDB-Read-Profile`` — raw bytes have no JSON envelope, and the profile
    echo has to hold here too.

    :raises HTTPException: 404 unknown release, unfrozen manifest, or unknown
        artifact path; 500 only if the stored bytes fail their own digest,
        which means the row was tampered with.
    """
    release = _release(session, release_handle)
    manifest = load_manifest(session, release)
    if manifest is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "manifest_not_frozen: this release has no immutable manifest, "
                "so it ships no citable artifacts."
            ),
        )
    recorded = {row.path: row for row in manifest.artifacts}
    row = recorded.get(artifact_path)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown_release_artifact: {artifact_path!r} is not in this manifest.",
        )

    # Cheap self-check on the stored bytes. This can only fire if the frozen
    # row itself was corrupted, which is a real integrity failure rather than
    # the corpus having moved on.
    if hashlib.sha256(row.content).hexdigest() != row.sha256:
        raise HTTPException(
            status_code=500,
            detail=(
                "release_artifact_corrupt: the stored bytes for this artifact "
                "no longer match their recorded digest. This is a storage "
                "integrity failure, not ordinary corpus growth; restore from "
                "backup before citing this release."
            ),
        )

    return Response(
        content=row.content,
        media_type=row.media_type,
        headers={
            "ETag": f'"{row.sha256}"',
            "X-TCKDB-Content-SHA256": row.sha256,
            "X-TCKDB-Read-Profile": current_read_profile().profile.value,
            "Content-Disposition": f"attachment; filename={artifact_path}",
        },
    )


def _release(session: Session, handle: str):
    try:
        return resolve_release(session, handle)
    except UnknownReleaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
