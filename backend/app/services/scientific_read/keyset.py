"""Keyset traversal with a snapshot watermark, for reproducible paging.

The problem offset pagination has
---------------------------------
``LIMIT/OFFSET`` is deterministic for a *static* corpus and wrong for a live
one. Between page 1 and page 2 an upload can insert a record that sorts above
the reader's position — every subsequent page then shifts down by one and the
reader silently **skips** a record. A deprecation removes a record from the
visible set and the reader silently sees one **twice**. Neither is detectable
from the responses. For a database whose whole point is that someone can build
a dataset from it and cite what they built, "your traversal may have quietly
omitted rows" is a correctness problem, not a performance one.

The two mechanisms here
-----------------------
**Keyset** — instead of "skip 200 rows", a cursor carries the sort key of the
last row delivered and the next page asks for rows strictly after it in the
sort order. Rows inserted above the reader's position no longer shift it.

**Watermark** — a cursor also carries a snapshot bound: the highest record id
that existed when traversal began. Every page re-applies ``id <= watermark``,
so a traversal sees the corpus as it was at page 1 no matter how much is
uploaded while it runs. When traversal is scoped to a Stage 3 dataset release,
the watermark is pinned to that release instead, which is what makes
"re-run the query that produced my dataset" answerable.

What the watermark does NOT freeze — stated plainly
---------------------------------------------------
The watermark bounds **insertions**, because record ids are monotonic. It does
not freeze **curation**: ``record_review`` rows are mutable, so a record
approved or deprecated mid-traversal can still enter or leave the visible set
between pages. Pinning review state as well would require reading review
history as-of a timestamp, which the schema does not currently support for
this path.

Callers that need a genuinely immutable, citable set should not use this at
all — they should read a published release's frozen ``selected_records``
artifact, which Stage 3 already guarantees is byte-stable
(``backend/docs/specs/dataset_release_and_profiles.md``). This module makes
*live* traversal far more reproducible than offset paging; it does not make it
immutable, and it must not be described as if it did.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import and_, or_

#: Bumped when the cursor payload's shape changes, so an old cursor is
#: rejected with a clear error instead of being silently misread.
CURSOR_VERSION = 1

SortDirection = Literal["asc", "desc"]


class InvalidCursorError(ValueError):
    """Raised when a supplied cursor is malformed, or from another query."""


@dataclass(frozen=True)
class Watermark:
    """The snapshot bound a traversal is pinned to."""

    #: Highest primary-key id included in the traversal. Record ids are
    #: monotonic, so this bounds insertions without needing a clock.
    max_id: int
    #: When the traversal started; informational, echoed to the caller.
    taken_at: datetime
    #: Set when traversal is pinned to a published dataset release.
    release_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_id": self.max_id,
            "taken_at": self.taken_at.isoformat(),
            "release_ref": self.release_ref,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Watermark:
        return cls(
            max_id=int(payload["max_id"]),
            taken_at=datetime.fromisoformat(payload["taken_at"]),
            release_ref=payload.get("release_ref"),
        )


@dataclass(frozen=True)
class Cursor:
    """An opaque position in a specific query's result order."""

    watermark: Watermark
    #: The sort-key values of the last row delivered, in sort order.
    last_values: list[Any]
    #: Identifies the query shape this cursor belongs to. A cursor handed to a
    #: different endpoint, or to the same endpoint with different filters,
    #: would silently produce a wrong page; binding the shape turns that into
    #: an explicit error.
    query_signature: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "v": CURSOR_VERSION,
            "watermark": self.watermark.as_dict(),
            "last_values": [_encode_value(v) for v in self.last_values],
            "query_signature": self.query_signature,
        }


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}
    if hasattr(value, "value"):  # enum
        return value.value
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and "__dt__" in value:
        return datetime.fromisoformat(value["__dt__"])
    return value


def encode_cursor(cursor: Cursor) -> str:
    """Serialize a cursor to a URL-safe opaque token."""
    raw = json.dumps(cursor.as_dict(), separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(token: str, *, expected_signature: str) -> Cursor:
    """Parse a cursor token, rejecting anything not from this exact query.

    :raises InvalidCursorError: malformed, wrong version, or issued for a
        different query shape.
    """
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding).decode("utf-8")
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError(
            "invalid_cursor: the cursor is not a token this API issued."
        ) from exc

    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise InvalidCursorError(
            "invalid_cursor: cursor version is not supported by this server; "
            "restart the traversal without a cursor."
        )
    signature = payload.get("query_signature")
    if signature != expected_signature:
        raise InvalidCursorError(
            "cursor_query_mismatch: this cursor was issued for a different "
            "query. A cursor is only valid for the exact filter set that "
            "produced it; restart the traversal without a cursor."
        )
    try:
        return Cursor(
            watermark=Watermark.from_dict(payload["watermark"]),
            last_values=[_decode_value(v) for v in payload["last_values"]],
            query_signature=signature,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCursorError(
            "invalid_cursor: the cursor payload is malformed."
        ) from exc


def query_signature(endpoint: str, filters: dict[str, Any]) -> str:
    """A stable fingerprint of the endpoint plus its filter set."""
    import hashlib

    canonical = json.dumps(
        {"endpoint": endpoint, "filters": filters},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def keyset_predicate(
    keys: list[tuple[Any, SortDirection]],
    last_values: list[Any],
):
    """Build the "strictly after this row" predicate for a mixed-direction sort.

    For ``ORDER BY a ASC, b DESC, id DESC`` and a last row ``(a0, b0, id0)``
    the predicate is the lexicographic expansion::

        a > a0
        OR (a = a0 AND b < b0)
        OR (a = a0 AND b = b0 AND id < id0)

    A row-value comparison (``(a, b) > (a0, b0)``) would be simpler and could
    use a composite index directly, but SQL row comparison requires every
    column to sort in the *same* direction. These sorts mix ASC and DESC, so
    the expansion is written out.

    The final key must be unique (normally the primary key), otherwise ties on
    every key would make the predicate skip rows.

    :param keys: ``(column, direction)`` pairs, in sort order.
    :param last_values: the previous page's final row, aligned with ``keys``.
    """
    if len(keys) != len(last_values):
        raise ValueError(
            "The sort keys and the previous page's final row must be the "
            f"same length; got {len(keys)} keys and {len(last_values)} values."
        )
    if not keys:
        raise ValueError(
            "Keyset pagination needs at least one sort key to page from."
        )

    branches = []
    for index, (column, direction) in enumerate(keys):
        equalities = [
            keys[prior][0] == last_values[prior] for prior in range(index)
        ]
        strict = (
            column > last_values[index]
            if direction == "asc"
            else column < last_values[index]
        )
        branches.append(and_(*equalities, strict) if equalities else strict)
    return or_(*branches)


def watermark_predicate(id_column, watermark: Watermark):
    """Restrict a query to rows that existed when traversal began."""
    return id_column <= watermark.max_id


__all__ = [
    "CURSOR_VERSION",
    "Cursor",
    "InvalidCursorError",
    "SortDirection",
    "Watermark",
    "decode_cursor",
    "encode_cursor",
    "keyset_predicate",
    "query_signature",
    "watermark_predicate",
]
