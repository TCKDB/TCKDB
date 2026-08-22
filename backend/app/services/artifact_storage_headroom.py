"""How much room is left, asked *before* a write is refused.

The gap this closes
-------------------
TCKDB learns its object store is full by **being refused**, and that
observation is durable (:mod:`app.services.artifact_storage_capacity`).
Nothing warned while the store was *getting* full, so the first notice an
operator got was a depositor's failed upload. This module is the
before-the-fact half: a number, its source, and its staleness, computed on
each ``/status`` poll.

What it reports, and what it deliberately does not
--------------------------------------------------
**It reports headroom in bytes, not a percentage.** "80 % full" invites an
operator to read a store as safe when the next 50 MB write will fail. The
predicate here is instead::

    headroom < MAX_ARTIFACT_BYTES

— *the store no longer has room for the largest artifact TCKDB will
accept*. That is a statement about the next real upload rather than about
a ratio, and it is the same 50 MB ceiling the upload path enforces, so the
warning and the refusal are talking about the same object.

Two arms, either of which may have no opinion
---------------------------------------------
``headroom = min(arms)`` over whichever of these answered:

``bucket_quota``
    ``quota - ledger_usage``. The quota comes from MinIO's admin API
    (:func:`app.services.artifact_storage_admin.report_bucket_quota_bytes`);
    the usage comes from **TCKDB's own ledger**, for reasons below.
``free_space``
    :func:`app.services.artifact_storage_admin.report_free_bytes`, the
    drive free space MinIO reports. Blind to a bucket quota — measured,
    418 MiB free while a 2 MiB write was refused for quota — which is
    exactly why it is one arm of a ``min()`` and not the whole answer.

If neither arm has an opinion (AWS S3, a non-MinIO store, a credential
without admin rights, a timeout) this returns ``None`` and ``/status``
says nothing. Never raises: a probe that could turn a healthy deployment
red would be a worse bug than the silence it was added to fix.

Why usage comes from the ledger and not from the store
------------------------------------------------------
MinIO *will* report per-bucket usage, from two places, and **both are
traps**, both measured against ``RELEASE.2025-09-07T16-13-09Z``:

* ``get-bucket-quota`` returns a ``size`` field. It read **0** the whole
  time, including while the bucket held 60 MiB. It is not usage.
* ``GET /minio/admin/v3/datausageinfo`` does report real per-bucket usage,
  but from a background scanner that ticks **once every 60.0 s, dead
  steady**, with the number frozen in between and the bucket **absent
  entirely** until its first scan. With a 20 MiB hard quota, 60 × 1 MiB
  written back-to-back were **all 60 accepted — 3× the quota, zero
  refusals** — because the writes outran the scanner. And at the instant
  of a real refusal, ``usage + incoming > quota`` evaluated to
  ``19,922,944 + 1,048,576 = 20,971,520 > 20,971,520`` → **false**. A
  gauge built on it says "proceed" in the same millisecond MinIO answers
  400.

TCKDB's ledger has neither problem: it is written in the same transaction
as the upload, so it is never behind.

The subtlety that silently inflates the number
-----------------------------------------------
``calculation_artifact`` is append-only and, per its own docstring,
"duplicate uploads of the same content (same sha256) produce two rows
pointing at the same content-addressed object". The store is
content-addressed and holds **one object per distinct sha256**. So a plain
``SUM(bytes)`` overcounts, by exactly the re-upload rate — which on a
bulk-ingest producer is not small. :func:`deposited_bytes` therefore sums
over **distinct sha256**, and
``test_the_same_content_deposited_twice_is_counted_once`` fails against
the naive sum.

What the ledger cannot see, stated rather than papered over
------------------------------------------------------------
The ledger is a **lower bound** on what the bucket holds, so this arm
errs towards reporting *more* headroom than exists. Three things occupy
the bucket without a ``calculation_artifact`` row behind them:

1. ``reclaimed/<digest>`` holds. The orphan sweep in
   ``backend/scripts/ops/verify_artifact_integrity.py --reclaim-orphans``
   *copies* an unreferenced object aside and never deletes it from the
   hold, precisely so a reclaim cannot destroy bytes. Those copies are in
   the same bucket and count against the same quota.
2. Write-then-rollback orphans. ``store_artifact`` writes the object
   before the row commits, so a failed upload can leave bytes with no row
   (see :mod:`app.services.artifact_persistence`).
3. Anything an operator put in the bucket by hand.

The free-space arm sees all three; the quota arm does not. Since the two
are combined with ``min()``, the warning is still raised by whichever arm
is closer to the truth — but on a quota-limited store with a large hold,
the quota arm can be optimistic, and that is a known shortfall rather
than an oversight.

Cost, and where it is paid
--------------------------
There is no scheduler in this backend, so this runs inline in the
``/status`` request thread. The ledger half is a cheap indexed aggregate.
The quota half is a network round trip that answers the same way for
weeks, so it is cached for :data:`_QUOTA_TTL_SECONDS` (``300`` by default,
overridable with ``TCKDB_STORAGE_QUOTA_TTL_SECONDS``). Free space is
**not** cached: it is the number that actually moves.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import artifact_storage, artifact_storage_admin

logger = logging.getLogger(__name__)


#: Default seconds a bucket quota is believed without re-asking. A quota is
#: set by an operator and then sits unchanged for months, so re-fetching it
#: on a poll that happens every five minutes buys nothing and costs a
#: network round trip on the ``/status`` thread. Long enough to be free,
#: short enough that raising a quota shows up within one poll cycle of the
#: TTL rather than requiring a restart.
_QUOTA_TTL_SECONDS = 300.0


def _ttl_seconds() -> float:
    """Read the TTL at call time so a test can set it without a reimport."""
    raw = os.environ.get("TCKDB_STORAGE_QUOTA_TTL_SECONDS", "")
    if not raw:
        return _QUOTA_TTL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "TCKDB_STORAGE_QUOTA_TTL_SECONDS=%r is not a number; using %ss",
            raw,
            _QUOTA_TTL_SECONDS,
        )
        return _QUOTA_TTL_SECONDS
    # Zero is meaningful — "never cache" — so only a negative is rejected.
    return value if value >= 0 else _QUOTA_TTL_SECONDS

#: ``(endpoint, bucket) -> (fetched_at_monotonic, quota_bytes_or_None)``.
#: ``None`` is cached too: a store that has no quota, or will not say, must
#: not be re-asked every poll either.
_quota_cache: dict[tuple[str, str], tuple[float, Optional[int]]] = {}
_quota_cache_lock = threading.Lock()

#: Sums ``bytes`` over **distinct** ``sha256``. ``MIN(bytes)`` rather than
#: ``MAX`` or ``ANY`` so that two rows disagreeing about the size of one
#: digest — which the schema permits and integrity checking exists to
#: catch — cannot inflate the total. A digest is one object; picking the
#: smaller claim keeps this a lower bound on purpose.
_LEDGER_SQL = text(
    "SELECT COALESCE(SUM(b), 0) FROM ("
    "  SELECT sha256, MIN(bytes) AS b FROM calculation_artifact GROUP BY sha256"
    ") AS distinct_objects"
)


@dataclass(frozen=True)
class Headroom:
    """Bytes of room left, where the number came from, and how stale it is.

    ``source`` names the arm that produced the minimum, so an operator
    reading the warning knows whether to free disk or to raise a quota —
    two different actions, and the whole reason a single "full" boolean
    was not enough.
    """

    #: The minimum across the arms that had an opinion. May be negative:
    #: a bucket already over its quota is worth reporting as such rather
    #: than clamping to zero, which would read as "exactly no room" and
    #: hide how far past the line it is.
    bytes: int
    #: ``"bucket_quota"`` or ``"free_space"``.
    source: str
    #: When the arms were combined, UTC.
    measured_at: datetime
    #: Per-arm detail. ``None`` means that arm had no opinion.
    quota_bytes: Optional[int]
    ledger_bytes: Optional[int]
    free_bytes: Optional[int]
    #: Age of the cached quota answer in seconds; ``0.0`` when it was
    #: fetched on this call, ``None`` when there is no quota arm. This is
    #: the only stale number in the report — the ledger and the free-space
    #: figure are both read fresh — so it is reported rather than averaged
    #: into a single "as of".
    quota_age_seconds: Optional[float]

    @property
    def is_low(self) -> bool:
        """No room left for the largest artifact TCKDB will accept."""
        return self.bytes < artifact_storage.MAX_ARTIFACT_BYTES


def _default_session_factory() -> Callable[[], Session]:
    from app.api.deps import SessionLocal  # type: ignore

    return SessionLocal


def deposited_bytes(
    session_factory: Optional[Callable[[], Session]] = None,
) -> Optional[int]:
    """Bytes TCKDB believes it has deposited, deduplicated by digest.

    ``None`` on any database failure, which means "no opinion" and takes
    the quota arm out of the ``min()`` entirely. Reporting ``0`` there
    would be the dangerous direction: it would claim the whole quota is
    free on a store where the database is simply unreachable.
    """
    factory = session_factory or _default_session_factory()
    try:
        with factory() as session:
            return int(session.execute(_LEDGER_SQL).scalar_one() or 0)
    except Exception as exc:
        logger.debug("artifact ledger sum failed (%s); no usage opinion", type(exc).__name__)
        return None


def _cached_quota(
    *,
    endpoint_url: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    monotonic: Callable[[], float],
) -> tuple[Optional[int], Optional[float]]:
    """``(quota_bytes_or_None, age_seconds)``; fetches only past the TTL."""
    key = (endpoint_url, bucket)
    ttl = _ttl_seconds()
    now = monotonic()
    with _quota_cache_lock:
        cached = _quota_cache.get(key)
        if cached is not None:
            fetched_at, value = cached
            age = now - fetched_at
            if age < ttl:
                return value, age

    # Fetched outside the lock: a slow admin API must not hold every other
    # /status thread behind it. Two concurrent misses cost two round trips,
    # which is cheaper than serialising the endpoint.
    value = artifact_storage_admin.report_bucket_quota_bytes(
        endpoint_url=endpoint_url,
        bucket=bucket,
        access_key=access_key,
        secret_key=secret_key,
    )
    with _quota_cache_lock:
        _quota_cache[key] = (monotonic(), value)
    return value, 0.0


def clear_quota_cache() -> None:
    """Forget every cached quota. For tests and for an operator REPL."""
    with _quota_cache_lock:
        _quota_cache.clear()


def current_headroom(
    *,
    endpoint_url: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    session_factory: Optional[Callable[[], Session]] = None,
    monotonic: Optional[Callable[[], float]] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> Optional[Headroom]:
    """The smallest headroom any arm can vouch for, or ``None``.

    ``None`` means *no opinion*: no arm answered. It is not "there is
    room" and must never be rendered as one.

    Never raises. Every failure inside is a missing arm, and a missing arm
    is silence rather than a guess.
    """
    monotonic = monotonic or time.monotonic
    now = now or (lambda: datetime.now(timezone.utc))

    quota_bytes: Optional[int] = None
    quota_age: Optional[float] = None
    ledger_bytes: Optional[int] = None
    free_bytes: Optional[int] = None
    try:
        quota_bytes, quota_age = _cached_quota(
            endpoint_url=endpoint_url,
            bucket=bucket,
            access_key=access_key,
            secret_key=secret_key,
            monotonic=monotonic,
        )
        if quota_bytes is not None:
            ledger_bytes = deposited_bytes(session_factory)
        free_bytes = artifact_storage_admin.report_free_bytes(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
        )
    except Exception as exc:  # pragma: no cover - defensive
        # Both callees promise never to raise. If one ever breaks that
        # promise it must not take /status with it.
        logger.warning("headroom probe failed (%r); no headroom opinion", exc)
        return None

    arms: list[tuple[str, int]] = []
    if quota_bytes is not None and ledger_bytes is not None:
        arms.append(("bucket_quota", quota_bytes - ledger_bytes))
    if free_bytes is not None:
        arms.append(("free_space", free_bytes))
    if not arms:
        return None

    # ``min`` on the value, ties resolved by the order above, which puts the
    # quota first: on a quota-limited store the quota is the constraint an
    # operator has to act on, and naming free space there would send them to
    # the wrong runbook page.
    source, value = min(arms, key=lambda arm: arm[1])
    if quota_bytes is None:
        quota_age = None
    return Headroom(
        bytes=value,
        source=source,
        measured_at=now(),
        quota_bytes=quota_bytes,
        ledger_bytes=ledger_bytes,
        free_bytes=free_bytes,
        quota_age_seconds=quota_age,
    )


__all__ = [
    "Headroom",
    "clear_quota_cache",
    "current_headroom",
    "deposited_bytes",
]
