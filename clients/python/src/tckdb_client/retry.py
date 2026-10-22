"""Opt-in retry/backoff policy for :class:`tckdb_client.TCKDBClient`.

Retrying an HTTP request is only safe when replaying it cannot create a
second server-side record. This module encodes that rule explicitly
rather than leaving it to caller discipline:

- ``GET`` / ``HEAD`` are read-only and always replayable.
- ``POST`` is replayable **only** when the request carries an
  ``Idempotency-Key``; the backend then collapses the replay onto the
  stored response instead of persisting a duplicate upload.
- Every other method (``PUT``, ``PATCH``, ``DELETE``, ...) is never
  retried. The TCKDB API uses them for curation state transitions where
  a silent replay would be indistinguishable from a second human action.

A ``POST`` without an idempotency key is never retried — not on a
connection reset, not on a 503, not on a timeout. A timeout in
particular is the dangerous case: the server may have committed the
upload and lost the response, so a blind replay would duplicate a
scientific record.

The status line is not always enough
------------------------------------
Those rules ask whether replaying is *safe*. A second question is
whether it is *useful*, and the status alone cannot answer it. TCKDB
reports two different artifact-storage failures at two statuses that are
both in :data:`DEFAULT_RETRY_STATUS_CODES`: a 503 when the object store
did not answer, where waiting is exactly right, and a 502 when the store
answered and the bytes a published record points at are gone, where
waiting cannot help — only an operator restoring the object can, and that
is a different request by a different principal. Retrying the second is a
backoff schedule with no exit.

So the response *body* is consulted too. Its ``code`` field is checked
against :data:`NON_RETRYABLE_CODES`, which the server generates from its
own catalogue of error codes, and a match stops after one attempt
whatever the status says. This is possible only because the decision
point in ``TCKDBClient._send`` holds the whole response: ``httpx`` has
already read the body by then, so the code is in scope where the retry is
decided rather than one layer up where the error is raised.

It is a **deny list, and unrecognised codes are retried.** A client that
abandons a genuinely transient failure because it did not recognise the
code is a worse bug than the wasted attempts this rule exists to prevent,
and a pinned client talking to a newer server will meet codes it has
never heard of routinely.

Retries are **off by default**. Construct a :class:`RetryPolicy` and pass
it to ``TCKDBClient(..., retry=policy)`` to opt in.
"""

from __future__ import annotations

import random as _random
import time as _time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Callable, Mapping

from .rejection_codes import NON_RETRYABLE_CODES

#: Transient status codes worth replaying. 429 is the only 4xx here:
#: it is an explicit "come back later", not a malformed request. Every
#: other 4xx describes a client-side problem that a replay cannot fix.
DEFAULT_RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})

#: Codes that override the status above. Generated server-side from
#: ``app.api.code_catalogue``, re-exported here so a caller configuring a
#: policy does not have to know which module the set came from. See the
#: module docstring for why the body is consulted at all.
DEFAULT_NON_RETRYABLE_CODES: frozenset[str] = NON_RETRYABLE_CODES

#: Methods that are safe to replay unconditionally.
SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD"})

#: Methods that are safe to replay *only* with an idempotency key.
IDEMPOTENT_WITH_KEY_METHODS: frozenset[str] = frozenset({"POST"})

_RETRY_AFTER_HEADER = "Retry-After"


def _monotonic_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff with jitter for transient failures.

    Parameters
    ----------
    max_attempts:
        Total attempts including the first. ``1`` disables retrying.
    backoff_base:
        Seconds for the first retry delay. Attempt *n* waits
        ``backoff_base * 2 ** (n - 1)`` before jitter.
    max_backoff:
        Ceiling applied to the computed exponential delay (before
        ``Retry-After`` is taken into account).
    jitter:
        Fraction of the computed delay that is randomised, in ``[0, 1]``.
        ``0`` gives deterministic backoff; the default ``0.5`` spreads
        each delay uniformly over ``[0.5 * d, d]`` so a fleet of clients
        does not resynchronise on a recovering server.
    retry_status_codes:
        Response codes worth replaying. Defaults to
        :data:`DEFAULT_RETRY_STATUS_CODES`.
    non_retryable_codes:
        Error-body ``code`` values that veto a retry the status would
        otherwise allow. Defaults to
        :data:`DEFAULT_NON_RETRYABLE_CODES`. Pass ``frozenset()`` to
        restore the pre-#234 behaviour of deciding on the status alone —
        which means replaying a lost artifact forever, so there is no
        good reason to.
    respect_retry_after:
        Honour the ``Retry-After`` response header. The client never
        sleeps *less* than the server asked for.
    max_retry_after:
        If the server asks for a longer wait than this, give up
        immediately and surface the error instead of blocking the caller.
    sleep:
        Injected for tests; called with the delay in seconds.
    rand:
        Injected for tests; must return a float in ``[0, 1)``.
    now:
        Injected for tests; returns the current UTC time, used to convert
        an HTTP-date ``Retry-After`` into a delay.
    """

    max_attempts: int = 3
    backoff_base: float = 0.5
    max_backoff: float = 30.0
    jitter: float = 0.5
    retry_status_codes: frozenset[int] = DEFAULT_RETRY_STATUS_CODES
    non_retryable_codes: frozenset[str] = DEFAULT_NON_RETRYABLE_CODES
    respect_retry_after: bool = True
    max_retry_after: float = 120.0
    sleep: Callable[[float], None] = field(default=_time.sleep, repr=False, compare=False)
    rand: Callable[[], float] = field(default=_random.random, repr=False, compare=False)
    now: Callable[[], datetime] = field(default=_monotonic_now, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise ValueError("max_attempts must be an integer.")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1.")
        if self.backoff_base < 0:
            raise ValueError("backoff_base must be >= 0.")
        if self.max_backoff < 0:
            raise ValueError("max_backoff must be >= 0.")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError("jitter must be between 0 and 1 inclusive.")
        if self.max_retry_after < 0:
            raise ValueError("max_retry_after must be >= 0.")

    # ------------------------------------------------------------------
    # Eligibility
    # ------------------------------------------------------------------

    def method_is_retryable(self, method: str, *, has_idempotency_key: bool) -> bool:
        """Return ``True`` when replaying ``method`` cannot duplicate work.

        This is the load-bearing safety rule: a ``POST`` is only ever
        replayed when the caller attached an ``Idempotency-Key`` so the
        server can collapse the replay.
        """
        normalized = method.upper()
        if normalized in SAFE_METHODS:
            return True
        if normalized in IDEMPOTENT_WITH_KEY_METHODS:
            return bool(has_idempotency_key)
        return False

    def status_is_retryable(self, status_code: int) -> bool:
        """Return ``True`` for transient response codes only."""
        return status_code in self.retry_status_codes

    def code_is_retryable(self, code: object) -> bool:
        """Return ``False`` only for a code known to be deterministic.

        The veto over :meth:`status_is_retryable`, and deliberately the
        weaker of the two: it can stop a retry the status permitted, never
        start one the status refused. So a 422 does not become retryable
        by omission from :attr:`non_retryable_codes`.

        Everything unrecognised answers ``True`` — a code from a newer
        server, a body with no ``code`` field, a body that was not JSON at
        all, ``None``. Read the default as "keep retrying unless TCKDB has
        said in writing that it is pointless", not as a whitelist. The
        failure this shape avoids: a client pinned at an older version
        meets a transient code added since, does not recognise it, and
        gives up on an outage that would have cleared in four seconds.
        """
        if not isinstance(code, str):
            return True
        return code not in self.non_retryable_codes

    # ------------------------------------------------------------------
    # Delay computation
    # ------------------------------------------------------------------

    def backoff_delay(self, attempt: int) -> float:
        """Jittered exponential delay before retry number ``attempt``.

        ``attempt`` is 1-based: ``1`` is the wait after the first
        failure.
        """
        if attempt < 1:
            raise ValueError("attempt must be >= 1.")
        raw = self.backoff_base * (2 ** (attempt - 1))
        capped = min(raw, self.max_backoff)
        if self.jitter <= 0:
            return capped
        low = capped * (1.0 - self.jitter)
        return low + (capped - low) * self.rand()

    def parse_retry_after(self, headers: Mapping[str, str]) -> float | None:
        """Return the ``Retry-After`` delay in seconds, if the server sent one.

        Handles both wire forms: delta-seconds (``Retry-After: 12``) and
        an HTTP-date (``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT``).
        A date already in the past yields ``0.0``. Unparseable values are
        ignored (``None``) rather than guessed at.
        """
        if not self.respect_retry_after:
            return None
        raw: str | None = None
        target = _RETRY_AFTER_HEADER.lower()
        for name, value in headers.items():
            if name.lower() == target:
                raw = value
                break
        if raw is None:
            return None
        candidate = raw.strip()
        if not candidate:
            return None
        try:
            seconds = float(int(candidate))
        except ValueError:
            pass
        else:
            return max(0.0, seconds)
        try:
            when = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - self.now()).total_seconds())

    def delay_for(self, attempt: int, headers: Mapping[str, str] | None = None) -> float:
        """Delay to wait before retry ``attempt``, honouring ``Retry-After``.

        The result is never smaller than a ``Retry-After`` the server
        supplied: jitter may lengthen a wait, never shorten one below
        what was asked for.
        """
        delay = self.backoff_delay(attempt)
        if headers is None:
            return delay
        retry_after = self.parse_retry_after(headers)
        if retry_after is None:
            return delay
        return max(delay, retry_after)

    def retry_after_exceeds_budget(self, headers: Mapping[str, str] | None) -> bool:
        """``True`` when the server asked to wait longer than we will."""
        if headers is None:
            return False
        retry_after = self.parse_retry_after(headers)
        if retry_after is None:
            return False
        return retry_after > self.max_retry_after


__all__ = [
    "DEFAULT_NON_RETRYABLE_CODES",
    "DEFAULT_RETRY_STATUS_CODES",
    "IDEMPOTENT_WITH_KEY_METHODS",
    "RetryPolicy",
    "SAFE_METHODS",
]
