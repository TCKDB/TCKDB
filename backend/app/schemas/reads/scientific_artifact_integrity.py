"""Read schemas for the artifact custody record (ADR 0014).

``artifact_integrity_event`` answers "has TCKDB ever failed to produce
the bytes behind this digest, and what did it see when it looked?". Until
now that question was answerable only with a psql prompt on the
deployment, while the *consequence* -- ``hard_fail_reason`` --  reached
clients. A curator could be told a record was untrustworthy and had no
way to find out what happened to it.

Two shapes, and the split is the substantive decision.

**The list aggregates; it does not paginate raw observations.** Every
read of a corrupt object appends another row, deliberately: persistence
is evidence, and a break that recurs across months is a different story
from one that happened once. But that also means a frequently-read
corrupt artifact produces arbitrarily many near-identical rows, and a
list surface that paged through them would bury one incident under its
own retries. So the list is one record per digest -- first seen, last
seen, how many, and the latest verdict -- which is the shape of the
question a curator is actually asking.

**Nothing is collapsed at rest.** The aggregation happens at read time.
The table stays append-only, a repair stays a later ``verified``
observation rather than an edit, and the full sequence is reachable per
digest, because the sequence is the point: "broken, then verified, then
broken again" is a claim about the storage layer that no current-state
summary can express.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
)
from app.schemas.reads.scientific_common import Pagination, ProfiledRequestEcho


class ArtifactIntegrityObservation(BaseModel):
    """One recorded observation about custody of one stored object.

    Carries expected *and* observed, plus the store's own metadata as it
    was at the moment of detection. That pairing is what separates the
    three causes of a digest mismatch -- the object was modified after we
    wrote it, we never stored what we said we did, or the store returned
    wrong bytes on this read -- and an operator who cannot tell them apart
    will investigate the wrong one.
    """

    #: The handle this observation is cited by. The reproducibility
    #: rubric copies this record's verdict for artifacts it does not read
    #: itself and names the observation it copied; without a ref on this
    #: surface that citation named a row nobody could look up, and a
    #: curator holding it was reduced to matching on timestamps.
    #:
    #: A ref and not the row id, which was the citation before. A row id
    #: is an implementation detail of one database instance -- it does
    #: not survive a restore and means nothing on another deployment --
    #: and ``apply_internal_ids_visibility`` strips every ``*_id`` key
    #: from this response by policy, so exposing it here could not have
    #: worked: the hosted startup check refuses to boot with
    #: ``ALLOW_PUBLIC_INTERNAL_IDS`` true, and these routes accept no
    #: ``include`` token to opt in with.
    integrity_event_ref: str = Field(max_length=40)
    finding: ArtifactIntegrityFinding
    detected_during: ArtifactIntegrityDetectionContext
    detected_at: datetime
    is_break: bool
    #: What the object should hash to; this is the content-addressed key.
    expected_sha256: str = Field(max_length=64)
    #: What the retrieved bytes actually hashed to. Null only when nothing
    #: was retrieved (``object_missing``).
    observed_sha256: str | None = Field(default=None, max_length=64)
    expected_bytes: int | None = None
    observed_bytes: int | None = None
    #: The store's own answers at detection time.
    object_last_modified_at: datetime | None = None
    object_etag: str | None = None
    object_content_length: int | None = None
    #: A copy of the artifact row's ``created_at``, taken at detection.
    #: Compared against ``object_last_modified_at`` it separates "modified
    #: after write" from "never stored correctly".
    artifact_recorded_at: datetime | None = None
    detail: str | None = None


class ArtifactIntegrityDigestSummary(BaseModel):
    """The custody history of one content-addressed object, aggregated.

    ``currently_broken`` reads the *latest* observation, not "any break
    ever". The log is append-only and a repair is recorded as a later
    ``verified`` row, so "any" would leave a restored object condemned
    forever -- which would make this a trap rather than a report.
    """

    sha256: str = Field(max_length=64)
    currently_broken: bool
    latest: ArtifactIntegrityObservation
    first_observed_at: datetime
    last_observed_at: datetime
    observation_count: int
    break_count: int
    #: Which reads have looked at this object. Coverage is uneven by
    #: construction, so an absence of findings means very different things
    #: depending on which contexts have actually run over it.
    detected_during: list[ArtifactIntegrityDetectionContext]
    #: Every calculation whose artifact rows point at this object. Plural,
    #: and that is the point: the store is content-addressed, so one
    #: corrupt object affects every row that shares it.
    calculation_refs: list[str] = Field(default_factory=list)
    filenames: list[str] = Field(default_factory=list)


class IntegrityRequestEcho(ProfiledRequestEcho):
    """Echo of the parsed request, surfaced in the response envelope."""

    filter: dict[str, object]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificArtifactIntegrityResponse(BaseModel):
    """Response envelope for ``/scientific/artifacts/integrity``."""

    request: IntegrityRequestEcho
    records: list[ArtifactIntegrityDigestSummary]
    pagination: Pagination


class ScientificArtifactIntegrityHistoryResponse(BaseModel):
    """Response envelope for ``/scientific/artifacts/{sha256}/integrity``.

    The summary and the sequence together: a caller that only needs the
    current verdict does not have to reconstruct it from the page, and a
    caller reconstructing an incident gets the observations in the order
    they were recorded.
    """

    request: IntegrityRequestEcho
    sha256: str = Field(max_length=64)
    summary: ArtifactIntegrityDigestSummary | None = None
    observations: list[ArtifactIntegrityObservation] = Field(default_factory=list)
    pagination: Pagination


__all__ = [
    "ArtifactIntegrityDigestSummary",
    "ArtifactIntegrityObservation",
    "IntegrityRequestEcho",
    "ScientificArtifactIntegrityHistoryResponse",
    "ScientificArtifactIntegrityResponse",
]
