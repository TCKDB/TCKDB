"""Read schemas for ``/api/v1/scientific/releases/*``.

The public face of the citable-release layer: what releases exist, what a
release's manifest says, which selections stand behind it, and whether the
release still verifies against the live database.

Like every other scientific response these carry a ``request`` echo derived
from :class:`app.schemas.reads.scientific_common.ProfiledRequestEcho`, so the
resolved read profile is reported here too — a release endpoint that dropped
the profile echo would be the first place a consumer learned the echo is not
actually universal.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    DatasetReleaseStatus,
    ReadProfile,
    ReleaseArtifactKind,
    ReleaseSelectionAction,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ProfiledRequestEcho,
    SupersessionNotice,
)


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed query."""

    filter: dict[str, object] = Field(default_factory=dict)
    include: list[str] = Field(default_factory=list)


class CurationPolicySummary(BaseModel):
    """The named, versioned rubric a release or selection cites."""

    curation_policy_ref: str
    name: str
    version: str
    description: str | None = None
    criteria: dict[str, Any] | None = None


class CuratorSummary(BaseModel):
    """Who made a curation decision. Attribution, never a database row id."""

    username: str
    full_name: str | None = None
    orcid: str | None = None
    affiliation: str | None = None


class ReleaseArtifactSummary(BaseModel):
    """One checksummed file in a release, and where to fetch it."""

    path: str
    kind: ReleaseArtifactKind
    media_type: str
    sha256: str
    byte_count: int
    record_count: int
    download_url: str


class ReleaseVersionBinding(BaseModel):
    """What the release's numbers were produced by."""

    alembic_revision: str
    backend_version: str
    schemas_package_version: str
    review_policy_version: str
    recovery_archive_schema: str


class DatasetReleaseSummary(BaseModel):
    """A citable release, without its manifest body."""

    release_ref: str
    tag: str
    title: str
    description: str | None = None
    status: DatasetReleaseStatus
    published_at: str | None = None
    withdrawn_at: str | None = None
    withdrawn_reason: str | None = None
    doi: str | None = None
    data_license: str
    code_license: str
    citation_text: str
    contact: str
    changelog_entry: str | None = None
    curation_policy: CurationPolicySummary
    has_manifest: bool = False


class ReleaseManifestSummary(BaseModel):
    """The frozen manifest: the document a citation resolves to."""

    manifest_ref: str
    manifest_schema: str
    profile: ReadProfile
    content_sha256: str
    generated_at: str | None = None
    selected_record_count: int
    candidate_record_count: int
    versions: ReleaseVersionBinding
    artifacts: list[ReleaseArtifactSummary] = Field(default_factory=list)
    document: dict[str, Any]


class ReleaseSelectionRecord(BaseModel):
    """One attributed selection row, standing or superseded.

    Two unrelated supersessions meet on this row and must not be confused;
    ``backend/docs/specs/dataset_release_and_profiles.md`` §"two supersessions"
    holds them apart:

    ``supersedes_selection_ref``  a *curator's opinion* was revised. Both
                                  records remain equally valid science.
    ``record_supersession``       the *science* was corrected. The selected
                                  record has been replaced by a better
                                  measurement of the same thing.

    A standing selection whose selected record has since been superseded is the
    single most misleading state this API can serve: it is what a citable,
    DOI-bearing release points a reader outside the project at. It is reported
    here rather than folded into ``live_divergence``, which is a per-file byte
    digest — "the database has moved", advisory and routinely true — and cannot
    name a record.
    """

    selection_ref: str
    action: ReleaseSelectionAction
    stands: bool
    record_type: str
    record_ref: str | None = None
    subject_type: str
    subject_ref: str | None = None
    supersedes_selection_ref: str | None = None
    #: Set only when the *selected scientific record* has since been replaced.
    #: Computed live from the supersession ledger, never frozen into the
    #: release: a release published before the correction existed cannot have
    #: recorded it, and rewriting the frozen artifacts to add it would break
    #: their published digests.
    record_supersession: SupersessionNotice | None = None
    rationale: str
    created_at: str | None = None
    curator: CuratorSummary | None = None
    curation_policy: CurationPolicySummary | None = None


class ReleaseVerificationSummary(BaseModel):
    """Integrity of the *frozen* release — stored bytes vs recorded digests.

    Independent of the live corpus. ``verified: false`` means the frozen data
    was tampered with, not that new science has been uploaded since; for that
    see :class:`ReleaseDivergenceSummary`.
    """

    verified: bool
    content_sha256_recorded: str
    content_sha256_recomputed: str
    artifacts_checked: int
    problems: list[str] = Field(default_factory=list)


class ReleaseDivergenceSummary(BaseModel):
    """How far the live database has moved since the release was published.

    Advisory. ``diverged: true`` is the normal steady state of a live instance
    — uploads continue and review advances, and the release deliberately does
    not move with them. It never prevents a citation from resolving.
    """

    diverged: bool
    differences: list[str] = Field(default_factory=list)
    note: str


class ScientificReleaseListResponse(BaseModel):
    """Response envelope for ``GET /scientific/releases``."""

    request: RequestEcho
    records: list[DatasetReleaseSummary]
    pagination: Pagination


class ScientificReleaseDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/releases/{ref}``."""

    request: RequestEcho
    record: DatasetReleaseSummary
    manifest: ReleaseManifestSummary | None = None


class ScientificReleaseManifestResponse(BaseModel):
    """Response envelope for ``GET /scientific/releases/{ref}/manifest``."""

    request: RequestEcho
    release_ref: str
    tag: str
    manifest: ReleaseManifestSummary
    verification: ReleaseVerificationSummary
    live_divergence: ReleaseDivergenceSummary


class ScientificReleaseSelectionsResponse(BaseModel):
    """Response envelope for ``GET /scientific/releases/{ref}/selections``."""

    request: RequestEcho
    release_ref: str
    tag: str
    records: list[ReleaseSelectionRecord]
    pagination: Pagination


__all__ = [
    "CurationPolicySummary",
    "CuratorSummary",
    "DatasetReleaseSummary",
    "ReleaseArtifactSummary",
    "ReleaseDivergenceSummary",
    "ReleaseManifestSummary",
    "ReleaseSelectionRecord",
    "ReleaseVerificationSummary",
    "ReleaseVersionBinding",
    "RequestEcho",
    "ScientificReleaseDetailResponse",
    "ScientificReleaseListResponse",
    "ScientificReleaseManifestResponse",
    "ScientificReleaseSelectionsResponse",
]
