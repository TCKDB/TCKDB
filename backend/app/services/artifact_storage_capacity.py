"""Is the object store refusing writes for want of room, and has that lifted?

This module owns one question and the rule that answers it. The question
is asked by ``/status``; the observations that answer it are appended by
the artifact write path, by the ``/status`` capacity probe, and by an
operator.

The clearing rule, and why the obvious one is wrong
---------------------------------------------------
The obvious rule is "clear the flag when a write succeeds". It is
incorrect, and the measurement that shows it is the same one that
motivated recording a size at all: on a MinIO filled to its free-space
threshold, **an 8 MiB write was refused and a 1-byte write succeeded in
the same second.** MinIO sizes its threshold check against the incoming
object, so small writes keep landing on a store with no room for any real
artifact. Clearing on any success would restore a green ``/status`` while
every genuine ESS log upload still failed — a false negative in a health
signal, which is worse than no signal because it is confidently wrong.

So the rule implemented here is:

    **The store is full when its most recent refusal has not been
    answered.** A refusal of *N* bytes is answered by a later observation
    that is an operator clear, a successful write of at least *N* bytes,
    or a capacity report of at least *N* free bytes.

Deliberately **not** a time-based expiry. A timer guesses; a
size-qualified success measures. A stale "full" that an operator can
clear is safer than a flag that goes quiet on its own while the disk is
still full and nobody has acted — which is the exact silence this exists
to break.

Two refusals that a capacity report must not answer
---------------------------------------------------
A free-space number cannot see a **bucket quota**. Measured against MinIO
with ``mc quota set --size 20MiB``: a 2 MiB write was refused with
``XMinioAdminBucketQuotaExceeded`` while the admin API reported
**437,858,304 bytes (418 MiB) free**. Letting a capacity report answer
that refusal would clear the flag on a store that refuses every upload —
reintroducing the false negative through a different door. So
:data:`_QUOTA_CODES` refusals are answerable only by a real write or by
an operator.

A refusal whose size was never known is answerable only by an operator,
for the same reason: no size comparison can be made, so no write can
prove the condition lifted.

Why every write is its own transaction
--------------------------------------
A refusal is discovered on the way to raising an error, and the request
that discovered it is about to roll back. Attaching the record to that
transaction would mean the one thing that must survive is the one thing
that gets discarded. This mirrors
:func:`app.services.artifact_integrity.record_integrity_observation` and
:func:`app.services.upload_submission.record_failed_upload`, down to the
injectable ``session_factory``.

Recording is best effort and never masks the caller's failure. A
depositor must still get their 507; if the database is also unhappy, that
is logged and the storage error is re-raised as itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.artifact_storage_capacity import ArtifactStorageCapacityEvent
from app.db.models.common import ArtifactStorageCapacityObservation

logger = logging.getLogger(__name__)

#: Refusals a free-space report cannot speak to, because they are not
#: about free space. **Measured**, not assumed: MinIO answered
#: ``XMinioAdminBucketQuotaExceeded`` for a 2 MiB write while its own
#: admin API reported 418 MiB free on the drive. ``QuotaExceeded`` is the
#: Ceph RGW spelling of the same condition and is carried for the reason
#: the write-path code set carries it — an unrecognised spelling
#: reproduces the defect exactly.
_QUOTA_CODES = frozenset({"XMinioAdminBucketQuotaExceeded", "QuotaExceeded"})


@dataclass(frozen=True)
class StorageFullState:
    """An outstanding refusal: the store said no, and nothing has said yes.

    Returned by :func:`current_full_state`. ``None`` from that function
    means "no refusal is outstanding", which is **not** the same as "there
    is room" — nothing available to this process can establish that. The
    distinction is why ``/status`` names its field after the observation
    rather than after the capacity.
    """

    #: When the refusal was recorded.
    observed_at: datetime

    #: The store's own error code, e.g. ``XMinioStorageFull``.
    s3_code: Optional[str]

    #: The size the store refused. ``None`` when the refusing call never
    #: knew it, in which case only an operator can clear this.
    attempted_bytes: Optional[int]

    @property
    def clearable_by_capacity_report(self) -> bool:
        """Whether a free-space number could answer this refusal.

        False for a bucket quota, which no amount of free disk explains.
        """
        if self.attempted_bytes is None:
            return False
        return (self.s3_code or "") not in _QUOTA_CODES


def _session_factory(
    session_factory: Optional[Callable[[], Session]],
) -> Callable[[], Session]:
    if session_factory is not None:
        return session_factory
    # Lazy: this service must not pull the app layer in at import time.
    from app.api.deps import SessionLocal  # type: ignore

    return SessionLocal


# ---------------------------------------------------------------------------
# Reading the log
# ---------------------------------------------------------------------------


def current_full_state(session: Session) -> Optional[StorageFullState]:
    """The head-of-log answer to "is the store refusing writes for room?".

    Two indexed queries, not a walk: find the newest ``refused`` row, then
    ask whether anything after it answers it. There is no stored summary
    to keep in step with the log, which is the point — see the model
    docstring and ADR 0007.

    Returns the outstanding refusal, or ``None`` when none is outstanding.
    """
    refusal = session.scalars(
        select(ArtifactStorageCapacityEvent)
        .where(
            ArtifactStorageCapacityEvent.observation
            == ArtifactStorageCapacityObservation.refused
        )
        .order_by(ArtifactStorageCapacityEvent.id.desc())
        .limit(1)
    ).first()
    if refusal is None:
        return None

    state = StorageFullState(
        observed_at=refusal.created_at,
        s3_code=refusal.s3_code,
        attempted_bytes=refusal.observed_bytes,
    )

    # What would answer this refusal? An operator always. A sized
    # observation only if it is at least as large as the refusal, and only
    # if it is a kind that can speak to this refusal's cause.
    answering = [ArtifactStorageCapacityObservation.operator_clear]
    if state.attempted_bytes is not None:
        answering.append(ArtifactStorageCapacityObservation.accepted)
        if state.clearable_by_capacity_report:
            answering.append(ArtifactStorageCapacityObservation.capacity_report)

    condition = ArtifactStorageCapacityEvent.observation.in_(answering)
    if state.attempted_bytes is not None:
        # An operator clear carries no size; every other answering kind
        # must meet or beat the refused size. This is the comparison the
        # whole design exists for: a 1-byte success does not answer an
        # 8 MiB refusal.
        condition = condition & (
            (
                ArtifactStorageCapacityEvent.observation
                == ArtifactStorageCapacityObservation.operator_clear
            )
            | (ArtifactStorageCapacityEvent.observed_bytes >= state.attempted_bytes)
        )

    answered = session.scalars(
        select(ArtifactStorageCapacityEvent.id)
        .where(ArtifactStorageCapacityEvent.id > refusal.id)
        .where(condition)
        .limit(1)
    ).first()
    return None if answered is not None else state


# ---------------------------------------------------------------------------
# Appending to the log
# ---------------------------------------------------------------------------


def append_observation(
    session: Session,
    *,
    observation: ArtifactStorageCapacityObservation,
    observed_bytes: Optional[int] = None,
    s3_code: Optional[str] = None,
    detail: Optional[str] = None,
    created_by: Optional[int] = None,
) -> ArtifactStorageCapacityEvent:
    """Append one observation on the caller's session and transaction.

    The explicit-session core. Used directly by the operator-clear route,
    which has a request transaction that should own its own write, and by
    the own-transaction wrappers below.
    """
    event = ArtifactStorageCapacityEvent(
        observation=observation,
        observed_bytes=observed_bytes,
        s3_code=s3_code,
        detail=detail,
        created_by=created_by,
    )
    session.add(event)
    session.flush()
    return event


def _append_in_own_transaction(
    *,
    observation: ArtifactStorageCapacityObservation,
    observed_bytes: Optional[int],
    s3_code: Optional[str],
    detail: Optional[str],
    session_factory: Optional[Callable[[], Session]],
) -> bool:
    """Append in a transaction of this function's own. Never raises."""
    try:
        factory = _session_factory(session_factory)
        with factory() as session:
            with session.begin():
                append_observation(
                    session,
                    observation=observation,
                    observed_bytes=observed_bytes,
                    s3_code=s3_code,
                    detail=detail,
                )
        return True
    except Exception:  # pragma: no cover - must never mask the real failure
        logger.exception(
            "FAILED to record an artifact storage capacity observation "
            "durably (observation=%s bytes=%s code=%s); the condition is real "
            "and is now only in this log",
            observation.value,
            observed_bytes,
            s3_code,
        )
        return False


def record_refusal(
    *,
    s3_code: str,
    attempted_bytes: Optional[int],
    detail: str,
    session_factory: Optional[Callable[[], Session]] = None,
) -> None:
    """Record that the store refused a write for want of room.

    ``detail`` is what the store said and goes to the row and the log, not
    to ``/status``: that endpoint is public and the message carries a
    content digest.
    """
    _append_in_own_transaction(
        observation=ArtifactStorageCapacityObservation.refused,
        observed_bytes=attempted_bytes,
        s3_code=s3_code,
        detail=detail,
        session_factory=session_factory,
    )
    logger.error(
        "artifact storage refused a %s-byte write for want of room (S3 code "
        "%s); /status will report degraded until a write of at least that "
        "size succeeds, the store reports that much free space, or an "
        "operator clears it: %s",
        attempted_bytes,
        s3_code,
        detail,
    )


def note_successful_write(
    *,
    accepted_bytes: int,
    session_factory: Optional[Callable[[], Session]] = None,
) -> None:
    """Record a successful write, but only while a refusal is outstanding.

    Two properties, both deliberate.

    **It is silent on a healthy store.** Writing a row per upload would
    turn an incident log into an upload log and make the head-of-log query
    proportional to traffic. This is the discipline
    ``artifact_integrity_event`` applies to its ``verified`` rows.

    **It records the write even when the write does not clear anything.**
    A 1-byte success during an 8 MiB refusal is not noise — it is the
    evidence that the store is in the partial state where small uploads
    land and real ones do not, and an operator reading the log should see
    it. It simply does not answer the refusal.
    """
    try:
        factory = _session_factory(session_factory)
        with factory() as session:
            with session.begin():
                state = current_full_state(session)
                if state is None:
                    return
                append_observation(
                    session,
                    observation=ArtifactStorageCapacityObservation.accepted,
                    observed_bytes=accepted_bytes,
                )
                clears = (
                    state.attempted_bytes is not None
                    and accepted_bytes >= state.attempted_bytes
                )
        if clears:
            logger.info(
                "artifact storage accepted a %s-byte write, at or above the "
                "%s bytes refused at %s; the storage-full observation is "
                "answered",
                accepted_bytes,
                state.attempted_bytes,
                state.observed_at.isoformat(),
            )
        else:
            logger.warning(
                "artifact storage accepted a %s-byte write while a %s-byte "
                "write is still refused (since %s); the store is NOT clear "
                "and /status stays degraded",
                accepted_bytes,
                state.attempted_bytes,
                state.observed_at.isoformat(),
            )
    except Exception:  # pragma: no cover - must never fail an upload
        logger.exception(
            "FAILED to record a successful %s-byte artifact write against the "
            "storage capacity log",
            accepted_bytes,
        )


def note_capacity_report(
    *,
    free_bytes: int,
    session_factory: Optional[Callable[[], Session]] = None,
) -> None:
    """Record free space the store reported about itself.

    Only meaningful while a refusal is outstanding, and only appended
    then, for the same reason :func:`note_successful_write` is.
    """
    _append_in_own_transaction(
        observation=ArtifactStorageCapacityObservation.capacity_report,
        observed_bytes=free_bytes,
        s3_code=None,
        detail=None,
        session_factory=session_factory,
    )


__all__ = [
    "ArtifactStorageCapacityObservation",
    "StorageFullState",
    "append_observation",
    "current_full_state",
    "note_capacity_report",
    "note_successful_write",
    "record_refusal",
]
