"""Generic helpers for the ``Idempotency-Key`` HTTP header.

The server constraint is intentionally narrow: 16-200 characters from
``[A-Za-z0-9._:-]``. This module mirrors that constraint exactly so an
invalid key is caught client-side before hitting the wire.

Producer-specific keys (``arc:job-12345:thermo:ethanol``) belong in
adapters, not in this generic transport layer.
"""

from __future__ import annotations

import re

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{16,200}$")
_PART_INVALID = re.compile(r"[^A-Za-z0-9._:\-]+")
_KEY_MIN = 16
_KEY_MAX = 200


def validate_idempotency_key(key: str) -> str:
    """Validate the idempotency key shape and return it unchanged.

    Raises ``ValueError`` for any value that the server would reject.
    Callers should validate locally before sending so a bad key turns
    into a clear client-side error rather than a ``400`` round-trip.
    """
    if not isinstance(key, str):
        raise ValueError("Idempotency key must be a string.")
    if not _KEY_PATTERN.match(key):
        raise ValueError(
            "Idempotency key must be 16-200 characters from [A-Za-z0-9._:-]."
        )
    return key


def make_idempotency_key(*parts: str) -> str:
    """Build an opaque idempotency key from caller-supplied string parts.

    Parts are joined with ``:`` so producers can carry their own logical
    identity (tool name, job id, output kind, label) into the key.
    Illegal characters in any part are replaced with ``-`` so callers
    don't need to pre-sanitize. The result is validated against the
    server constraint before being returned.

    .. warning::
       Sanitization is **lossy**. Two parts that differ only in
       disallowed characters (``"foo bar"`` vs ``"foo-bar"``) collapse
       to the same key. Producer adapters that need stronger
       uniqueness should pre-normalize their parts or append a stable
       payload-hash suffix, e.g. ``make_idempotency_key("arc", job_id,
       kind, payload_hash[:12])``.

    The function is deliberately not chemistry-aware — adapters decide
    what makes a stable, unique-per-request key for their domain.
    """
    if not parts:
        raise ValueError("make_idempotency_key requires at least one part.")
    cleaned: list[str] = []
    for part in parts:
        if not isinstance(part, str):
            raise ValueError("Idempotency key parts must be strings.")
        if not part:
            raise ValueError("Idempotency key parts must be non-empty.")
        cleaned.append(_PART_INVALID.sub("-", part))
    key = ":".join(cleaned)
    if len(key) > _KEY_MAX:
        raise ValueError(
            f"Idempotency key exceeds {_KEY_MAX} characters: got {len(key)}."
        )
    if len(key) < _KEY_MIN:
        # Pad with a stable suffix derived from the input — keeps the key
        # deterministic so retries still match. The padding is appended,
        # not random, because random would break the retry contract.
        key = (key + ":" + "0" * _KEY_MIN)[:max(_KEY_MIN, len(key))]
    return validate_idempotency_key(key)


__all__ = ["validate_idempotency_key", "make_idempotency_key"]
