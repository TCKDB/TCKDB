"""Curator-gated write API for curation policies and dataset releases.

    POST  /api/v1/releases/policies
    POST  /api/v1/releases
    POST  /api/v1/releases/{release_handle}/selections
    POST  /api/v1/releases/{release_handle}/selections/{selection_ref}/supersede
    POST  /api/v1/releases/{release_handle}/selections/{selection_ref}/withdraw
    POST  /api/v1/releases/{release_handle}/publish
    POST  /api/v1/releases/{release_handle}/withdraw
    POST  /api/v1/releases/{release_handle}/doi

Every request body addresses records by **public ref**, never by database id —
the same rule upload payloads follow. The *subject* of a selection (the species
entry whose thermo was chosen, the reaction entry whose kinetics was chosen) is
derived from the record rather than supplied, so a selection cannot be recorded
against the wrong thing.

There is no ``PATCH`` and no ``DELETE`` on selections, on purpose: changing a
curated decision appends a supersession row. The public read side lives at
``/api/v1/scientific/releases/*`` and is unauthenticated, because a citation
must resolve for anyone.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_write_db, require_curator_or_admin
from app.db.models.app_user import AppUser
from app.db.models.dataset_release import DatasetRelease, ReleaseSelection
from app.schemas.reads.scientific_release import (
    DatasetReleaseSummary,
    ReleaseSelectionRecord,
)
from app.services.release.curation import (
    DEFAULT_CODE_LICENSE,
    DEFAULT_DATA_LICENSE,
    ReleaseCurationError,
    ReleaseRecordNotFound,
    ReleaseStateConflict,
    add_selection,
    create_release,
    publish_release,
    record_doi,
    resolve_curation_policy,
    resolve_selectable_record,
    supersede_selection,
    withdraw_release,
    withdraw_selection,
)
from app.services.release.manifest import (
    ReleaseManifestStateConflict,
    ReleaseStateError,
    freeze_manifest,
)
from app.services.scientific_read.releases import (
    UnknownReleaseError,
    get_release,
    get_release_selection,
    resolve_release,
)

router = APIRouter()


def _refusal_status(exc: ReleaseCurationError | ReleaseStateError) -> int:
    """The status a curation refusal arrives at, read off the exception.

    Three answers, and a client retries differently for each:

    * **409** — the body is fine and the world is not how it assumed. No
      corrected payload helps: the release has to be published, a new one
      cut, or a different row superseded first.
    * **404** — the request named a record that is not there.
    * **422** — the body itself is wrong, and a corrected one is accepted.

    Deriving the status from the exception class rather than from the route
    is the point. ``release_tag_taken`` and
    ``curation_policy_version_conflict`` were 409 because two ``except``
    clauses said so, which is why :mod:`app.api.code_catalogue` had to carry
    a note explaining that reading the raise site gave the wrong answer.
    Now the raise site *is* the answer, and a new refusal gets its status by
    choosing a class instead of by remembering which handler catches it.
    """
    if isinstance(exc, (ReleaseStateConflict, ReleaseManifestStateConflict)):
        return 409
    if isinstance(exc, ReleaseRecordNotFound):
        return 404
    return 422


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CurationPolicyRequest(BaseModel):
    """Register (or re-resolve) a named, versioned curation policy."""

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    criteria: dict = Field(default_factory=dict)


class CreateReleaseRequest(BaseModel):
    """Open a draft release.

    No ``doi`` field: a DOI is recorded after a deposit, never asserted up
    front.

    ``data_license`` and ``code_license`` are optional and default to the
    house pair — ``CC-BY-4.0`` for the corpus, ``MIT`` for the code. A curator
    cutting an ordinary release does not have to restate the licensing of the
    project on every request, and one who is publishing under different terms
    passes them explicitly and gets exactly what they passed. An empty string
    is still refused: not naming a license and declaring there is none are
    different claims.
    """

    tag: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1)
    curation_policy_name: str = Field(min_length=1)
    curation_policy_version: str = Field(min_length=1)
    data_license: str = Field(
        default=DEFAULT_DATA_LICENSE, min_length=1, max_length=64
    )
    code_license: str = Field(
        default=DEFAULT_CODE_LICENSE, min_length=1, max_length=64
    )
    citation_text: str = Field(min_length=1)
    contact: str = Field(min_length=1)
    description: str | None = None
    changelog_entry: str | None = None


class SelectionRequest(BaseModel):
    """Select one candidate record, by public ref, with a stated reason."""

    record_ref: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=1)


class SupersedeRequest(BaseModel):
    """Replace a standing selection with a different candidate."""

    record_ref: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=1)


class ReasonRequest(BaseModel):
    """A reason is mandatory for anything that retracts a decision."""

    reason: str = Field(min_length=1)


class DoiRequest(BaseModel):
    """Record a DOI minted out-of-band for a published release."""

    doi: str = Field(min_length=1, max_length=128)


class CurationPolicyResponse(BaseModel):
    curation_policy_ref: str
    name: str
    version: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/policies", response_model=CurationPolicyResponse, status_code=201)
def create_curation_policy(
    body: CurationPolicyRequest,
    session: Session = Depends(get_write_db),
    user: AppUser = Depends(require_curator_or_admin),
) -> CurationPolicyResponse:
    """Register a curation policy version, or return the identical existing one.

    :raises HTTPException: 409 when the version exists with different content —
        a policy version that a released dataset cites must never change.
    """
    try:
        policy = resolve_curation_policy(
            session,
            name=body.name,
            version=body.version,
            description=body.description,
            criteria=body.criteria,
            created_by=user.id,
        )
    except ReleaseCurationError as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return CurationPolicyResponse(
        curation_policy_ref=policy.public_ref, name=policy.name, version=policy.version
    )


@router.post("", response_model=DatasetReleaseSummary, status_code=201)
def create_dataset_release(
    body: CreateReleaseRequest,
    session: Session = Depends(get_write_db),
    user: AppUser = Depends(require_curator_or_admin),
) -> DatasetReleaseSummary:
    """Open a draft dataset release bound to a curation policy version.

    :raises HTTPException: 404 when the policy version is unknown; 409 when the
        tag is taken.
    """
    from app.db.models.dataset_release import CurationPolicy

    policy = session.scalars(
        select(CurationPolicy).where(
            CurationPolicy.name == body.curation_policy_name,
            CurationPolicy.version == body.curation_policy_version,
        )
    ).first()
    if policy is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "unknown_curation_policy: register the policy version before "
                "opening a release that cites it."
            ),
        )
    try:
        release = create_release(
            session,
            tag=body.tag,
            title=body.title,
            curation_policy=policy,
            data_license=body.data_license,
            code_license=body.code_license,
            citation_text=body.citation_text,
            contact=body.contact,
            description=body.description,
            changelog_entry=body.changelog_entry,
            created_by=user.id,
        )
    except ReleaseCurationError as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return get_release(session, release.tag).record


@router.post(
    "/{release_handle}/selections",
    response_model=ReleaseSelectionRecord,
    status_code=201,
)
def append_selection(
    body: SelectionRequest,
    release_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_write_db),
    user: AppUser = Depends(require_curator_or_admin),
) -> ReleaseSelectionRecord:
    """Append an attributed selection to a draft release.

    :raises HTTPException: 404 unknown release, or a ``record_ref`` naming no
        record; 409 when the release is no longer a draft or the subject
        already has a standing selection (supersede it instead); 422 for a
        body that can be corrected and resent — an unselectable ref, a
        blank rationale, a record below the approval floor.
    """
    release = _release(session, release_handle)
    try:
        candidate = resolve_selectable_record(session, body.record_ref)
        selection = add_selection(
            session,
            release=release,
            record_type=candidate.record_type,
            record_id=candidate.record_id,
            subject_type=candidate.subject_type,
            subject_id=candidate.subject_id,
            rationale=body.rationale,
            selected_by=user.id,
        )
    except ReleaseCurationError as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return _selection_record(session, release_handle, selection.public_ref)


@router.post(
    "/{release_handle}/selections/{selection_ref}/supersede",
    response_model=ReleaseSelectionRecord,
    status_code=201,
)
def supersede(
    body: SupersedeRequest,
    release_handle: str = Path(..., min_length=1, max_length=64),
    selection_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_write_db),
    user: AppUser = Depends(require_curator_or_admin),
) -> ReleaseSelectionRecord:
    """Append a replacement selection. The superseded row is left untouched.

    201, not 200: this creates a new row. Nothing is edited.

    :raises HTTPException: 404 unknown release, unknown selection, or a
        ``record_ref`` naming no record; 409 when the row has already been
        superseded or the release is no longer a draft; 422 when the
        replacement is incoherent.
    """
    release = _release(session, release_handle)
    superseded = _selection(session, selection_ref, release=release)
    try:
        candidate = resolve_selectable_record(session, body.record_ref)
        selection = supersede_selection(
            session,
            superseded=superseded,
            record_id=candidate.record_id,
            rationale=body.rationale,
            selected_by=user.id,
        )
    except ReleaseCurationError as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return _selection_record(session, release_handle, selection.public_ref)


@router.post(
    "/{release_handle}/selections/{selection_ref}/withdraw",
    response_model=ReleaseSelectionRecord,
    status_code=201,
)
def withdraw_a_selection(
    body: ReasonRequest,
    release_handle: str = Path(..., min_length=1, max_length=64),
    selection_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_write_db),
    user: AppUser = Depends(require_curator_or_admin),
) -> ReleaseSelectionRecord:
    """Append a withdrawal: the release now recommends nothing for this subject.

    :raises HTTPException: 404 unknown release or selection; 409 when the row
        has already been superseded or the release is no longer a draft; 422
        when no reason was given.
    """
    release = _release(session, release_handle)
    superseded = _selection(session, selection_ref, release=release)
    try:
        selection = withdraw_selection(
            session,
            superseded=superseded,
            rationale=body.reason,
            selected_by=user.id,
        )
    except ReleaseCurationError as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return _selection_record(session, release_handle, selection.public_ref)


@router.post("/{release_handle}/publish", response_model=DatasetReleaseSummary)
def publish(
    release_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_write_db),
    user: AppUser = Depends(require_curator_or_admin),
) -> DatasetReleaseSummary:
    """Publish a draft release and freeze its immutable, checksummed manifest.

    Publishing and freezing are one operation because a published release
    without a manifest would be citable but unverifiable — the worst of both.

    :raises HTTPException: 404 unknown release; 409 when the release is not a
        draft or a manifest already exists; 422 when the release selects
        nothing or a selected record holds a non-finite value.
    """
    release = _release(session, release_handle)
    try:
        publish_release(session, release)
        freeze_manifest(session, release, created_by=user.id)
    except (ReleaseCurationError, ReleaseStateError) as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return get_release(session, release.tag).record


@router.post("/{release_handle}/withdraw", response_model=DatasetReleaseSummary)
def withdraw(
    body: ReasonRequest,
    release_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_write_db),
    _user: AppUser = Depends(require_curator_or_admin),
) -> DatasetReleaseSummary:
    """Retract a published release, keeping its row and manifest readable.

    :raises HTTPException: 404 unknown release; 409 when it is not published;
        422 when no reason was given.
    """
    release = _release(session, release_handle)
    try:
        withdraw_release(session, release, reason=body.reason)
    except ReleaseCurationError as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return get_release(session, release.tag).record


@router.post("/{release_handle}/doi", response_model=DatasetReleaseSummary)
def attach_doi(
    body: DoiRequest,
    release_handle: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_write_db),
    _user: AppUser = Depends(require_curator_or_admin),
) -> DatasetReleaseSummary:
    """Record a DOI that was minted out-of-band for a published release.

    :raises HTTPException: 404 unknown release; 409 when the release is not
        published or already cites a different DOI.
    """
    release = _release(session, release_handle)
    try:
        record_doi(session, release, doi=body.doi)
    except ReleaseCurationError as exc:
        raise HTTPException(_refusal_status(exc), detail=str(exc)) from exc
    return get_release(session, release.tag).record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _release(session: Session, handle: str):
    try:
        return resolve_release(session, handle)
    except UnknownReleaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _selection(
    session: Session, selection_ref: str, *, release: DatasetRelease
) -> ReleaseSelection:
    """Resolve a selection *within* the release named in the URL.

    Scoping is not decoration. Without it, superseding via the wrong release
    tag resolved the selection globally and appended the replacement to the
    *other* release — silently editing a release the caller never named, and
    then 500ing when the response could not be found under the requested one.
    A mismatch is a 404: from this release's point of view, that selection does
    not exist.
    """
    row = session.scalars(
        select(ReleaseSelection).where(ReleaseSelection.public_ref == selection_ref)
    ).first()
    if row is None or row.dataset_release_id != release.id:
        raise HTTPException(
            status_code=404,
            detail=(
                f"unknown_selection: no selection {selection_ref!r} belongs to "
                f"release {release.tag!r}."
            ),
        )
    return row


def _selection_record(
    session: Session, release_handle: str, selection_ref: str
) -> ReleaseSelectionRecord:
    """Read back exactly the row just written.

    This used to page the whole ledger at ``limit=200`` and scan for the ref,
    which capped a release at 200 selections: the 201st POST fell off the end
    of the first page, raised 500, and rolled the new row back. A curator
    simply could not build a larger release — while the read service's own
    comments assume "a mature release has thousands of selections".
    """
    record = get_release_selection(session, release_handle, selection_ref)
    if record is None:  # pragma: no cover - defensive; just-written row
        raise HTTPException(
            status_code=500, detail="selection_not_readable_after_write"
        )
    return record


__all__ = ["router"]
