"""Durable recording of what TCKDB observes about custody of stored artifacts.

TCKDB refuses a reaction whose atoms do not balance and a saddle point
made of the wrong elements. It should not shrug when its own object
store hands back bytes that are not what it stored. This module is where
that position stops being a log line: a detected digest mismatch becomes
an ``artifact_integrity_event`` row, and the trust evaluator reads those
rows at read time (ADR 0014).

Two properties are load-bearing and neither is negotiable:

**The record is written in its own transaction.** The download route
runs on a read session that never commits, and the discovery happens on
the way to returning an error. Attaching the record to the request's
transaction would mean the one thing that must survive is the one thing
that gets rolled back. This mirrors
:func:`app.services.upload_submission.record_failed_upload`, which
solved the same problem for failed uploads, down to the injectable
``session_factory``.

**Recording is best effort and never masks the original failure.** The
reader must still get their 502. If the database is also unhappy, the
integrity failure is logged and re-raised as itself, not replaced by a
database error — a custody break reported as an ORM traceback is a
custody break nobody will find.

What is deliberately *not* here: any attempt to repair, quarantine or
delete. A corrupt object is evidence about an incident and deleting it
destroys the only copy of what actually happened. Remediation is an
operator decision made against the record, not an automatic response to
detecting it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import ArtifactIntegrityEvent, CalculationArtifact
from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
)
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    head_artifact_object,
)

logger = logging.getLogger(__name__)


def _naive(value: object) -> Optional[datetime]:
    """Coerce a store-reported timestamp to the naive UTC the schema uses.

    ``calculation_artifact.created_at`` is ``TIMESTAMP WITHOUT TIME
    ZONE`` filled by ``now()`` on the database server, while boto3 hands
    back a timezone-aware ``LastModified``. Storing the aware value
    unchanged would make the one comparison this column exists for — is
    the object newer than the row? — silently wrong by the server's UTC
    offset, which is the difference between "modified after write" and
    "never stored correctly".
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value
    from datetime import timezone

    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _probe_object(sha256: str, *, client=None, bucket: str | None = None) -> dict:
    """Capture the store's own metadata for an object, best effort."""
    head = head_artifact_object(sha256, client=client, bucket=bucket)
    if not head:
        return {}
    etag = head.get("ETag")
    return {
        "object_last_modified_at": _naive(head.get("LastModified")),
        "object_etag": str(etag).strip('"') if etag is not None else None,
        "object_content_length": head.get("ContentLength"),
    }


def record_integrity_observation(
    *,
    sha256: str,
    finding: ArtifactIntegrityFinding,
    detected_during: ArtifactIntegrityDetectionContext,
    observed_sha256: str | None = None,
    expected_bytes: int | None = None,
    observed_bytes: int | None = None,
    artifact_id: int | None = None,
    artifact_recorded_at: datetime | None = None,
    detected_by: int | None = None,
    detail: str | None = None,
    session_factory: Optional[Callable[[], Session]] = None,
    storage_client=None,
    bucket: str | None = None,
) -> Optional[int]:
    """Write one ``artifact_integrity_event`` in its own transaction.

    Records both breaks and the ``verified`` observation that clears
    them; the table is an append-only log of observations, not of
    failures, which is what lets a repaired object be recorded without
    editing or deleting the break.

    Returns the event id, or ``None`` if recording itself failed — in
    which case the failure is logged and swallowed, because the caller's
    job is to report the integrity break to its own caller and this must
    not get in the way of that.

    ``artifact_id`` and ``artifact_recorded_at`` are optional: the
    ``store_dedup_verification`` context detects a break on an object
    whose row does not exist yet, and when they are absent this function
    fills them from any *committed* artifact row already sharing the
    digest. That backfill is the honest thing to do in a content-
    addressed store — if another record already points at the corrupt
    object, the record must say so.
    """
    if session_factory is None:
        # Lazy: this service must not pull the app layer in at import time.
        from app.api.deps import SessionLocal as session_factory  # type: ignore

    probe = _probe_object(sha256, client=storage_client, bucket=bucket)

    try:
        with session_factory() as session:
            with session.begin():
                if artifact_id is None or artifact_recorded_at is None:
                    row = session.scalars(
                        select(CalculationArtifact)
                        .where(CalculationArtifact.sha256 == sha256)
                        .order_by(CalculationArtifact.id)
                        .limit(1)
                    ).first()
                    if row is not None:
                        artifact_id = artifact_id or row.id
                        artifact_recorded_at = artifact_recorded_at or row.created_at
                        expected_bytes = (
                            expected_bytes if expected_bytes is not None else row.bytes
                        )

                event = ArtifactIntegrityEvent(
                    sha256=sha256,
                    artifact_id=artifact_id,
                    finding=finding,
                    detected_during=detected_during,
                    expected_bytes=expected_bytes,
                    observed_sha256=observed_sha256,
                    observed_bytes=observed_bytes,
                    artifact_recorded_at=artifact_recorded_at,
                    detail=detail,
                    created_by=detected_by,
                    **probe,
                )
                session.add(event)
                session.flush()
                event_id = event.id
            log = logger.error if finding.is_break else logger.warning
            log(
                "artifact integrity observation recorded: sha=%s finding=%s "
                "context=%s observed_sha=%s event_id=%s",
                sha256,
                finding.value,
                detected_during.value,
                observed_sha256,
                event_id,
            )
            return event_id
    except Exception:  # pragma: no cover - must never mask the real failure
        logger.exception(
            "FAILED to record an artifact integrity observation durably "
            "(sha=%s finding=%s context=%s); the break is real and is now "
            "only in this log",
            sha256,
            finding.value,
            detected_during.value,
        )
        return None


def record_from_error(
    error: ArtifactIntegrityError,
    *,
    detected_during: ArtifactIntegrityDetectionContext,
    artifact: CalculationArtifact | None = None,
    detected_by: int | None = None,
    session_factory: Optional[Callable[[], Session]] = None,
    storage_client=None,
    bucket: str | None = None,
) -> Optional[int]:
    """Record the break described by an :class:`ArtifactIntegrityError`.

    The convenience form for the common case: a caller that already has
    the structured exception and, usually, the artifact row that led it
    there.
    """
    return record_integrity_observation(
        sha256=error.sha256,
        finding=error.finding,
        detected_during=detected_during,
        observed_sha256=error.observed_sha256,
        expected_bytes=(
            error.expected_bytes
            if error.expected_bytes is not None
            else (artifact.bytes if artifact is not None else None)
        ),
        observed_bytes=error.observed_bytes,
        artifact_id=artifact.id if artifact is not None else None,
        artifact_recorded_at=artifact.created_at if artifact is not None else None,
        detected_by=detected_by,
        detail=str(error),
        session_factory=session_factory,
        storage_client=storage_client,
        bucket=bucket,
    )


def record_integrity_verified(
    *,
    sha256: str,
    detected_during: ArtifactIntegrityDetectionContext,
    observed_bytes: int | None = None,
    artifact_id: int | None = None,
    artifact_recorded_at: datetime | None = None,
    detected_by: int | None = None,
    detail: str | None = None,
    session_factory: Optional[Callable[[], Session]] = None,
    storage_client=None,
    bucket: str | None = None,
) -> Optional[int]:
    """Record that a previously-broken object now reads back correctly.

    This is how a hard fail is cleared, and it is cleared by evidence:
    the row carries ``observed_sha256 == sha256``, which a check
    constraint enforces, so an operator cannot assert a repair that did
    not happen. Nothing is updated or deleted — the break stays in the
    log as the account of what occurred, and the later observation
    supersedes it.

    Callers should only invoke this for a digest that *has* a recorded
    break (see :func:`digests_with_recorded_breaks`). Writing a row per
    successful read would turn an incident log into a download log and
    make the trust query proportional to traffic.
    """
    return record_integrity_observation(
        sha256=sha256,
        finding=ArtifactIntegrityFinding.verified,
        detected_during=detected_during,
        observed_sha256=sha256,
        observed_bytes=observed_bytes,
        artifact_id=artifact_id,
        artifact_recorded_at=artifact_recorded_at,
        detected_by=detected_by,
        detail=detail,
        session_factory=session_factory,
        storage_client=storage_client,
        bucket=bucket,
    )


def digests_with_recorded_breaks(
    session: Session, sha256s: list[str]
) -> frozenset[str]:
    """Return the subset of ``sha256s`` whose *latest* observation is a break.

    One query for a batch of digests, so read paths that grade many
    calculations at once do not fan out. Kept here rather than in the
    trust package because it is a question about storage custody, not
    about the science.

    "Latest", not "any": the table is append-only and a repaired object
    is recorded as a later ``verified`` observation, so a digest with a
    break followed by a verification is *not* currently broken.
    """
    if not sha256s:
        return frozenset()
    rows = session.execute(
        select(ArtifactIntegrityEvent.sha256, ArtifactIntegrityEvent.finding)
        .where(ArtifactIntegrityEvent.sha256.in_(sorted(set(sha256s))))
        .order_by(ArtifactIntegrityEvent.sha256, ArtifactIntegrityEvent.id)
    ).all()
    latest: dict[str, ArtifactIntegrityFinding] = {}
    for sha, finding in rows:
        latest[sha] = finding
    return frozenset(sha for sha, finding in latest.items() if finding.is_break)


__all__ = [
    "digests_with_recorded_breaks",
    "record_from_error",
    "record_integrity_observation",
    "record_integrity_verified",
]
