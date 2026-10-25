"""Keyset traversal with a snapshot watermark, for reproducible paging.

The problem offset pagination has
---------------------------------
``LIMIT/OFFSET`` is deterministic for a *static* corpus and wrong for a live
one. Between page 1 and page 2 an upload can insert a record that sorts above
the reader's position — every subsequent page then shifts down by one and the
reader silently sees one record **twice**. A deprecation removes a record from
the visible set above the reader's position — every subsequent page shifts up
and the reader silently **skips** one. Neither is detectable from the
responses. For a database whose whole point is that someone can build
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

One engine for every list
-------------------------
Every browse and list read pages through :class:`Paging`. It opens the request's
cursor (or notes its absence), bounds the candidate set to the snapshot, and
returns one :class:`Page` that says where the next one starts. Two flavours share
the cursor, the signature and the watermark:

* :meth:`Paging.window` for a list a service has already materialised in Python
  (most do: they classify records, then order them);
* :meth:`Paging.sql_window` for a ranked SQL subquery, where the "strictly
  after" predicate is pushed into the statement.

A cursor never carries a hidden record. It holds the sort key of the last row
*delivered* (so a row the profile shows) and a watermark taken over the rows
the request is allowed to see, never over the table. A record the read profile
withholds therefore contributes neither a key, an id nor a count to any cursor.

``offset`` stays for random access ("page N of M") and is deterministic only for
a static corpus. A cursor and a non-zero offset cannot be combined.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

from app.api.error_contract import CodedValueError
from app.schemas.reads.scientific_common import WatermarkEcho
from app.versioning import same_value

T = TypeVar("T")

#: Bumped when the cursor payload's shape changes, so an old cursor is
#: rejected with a clear error instead of being silently misread. Compared with :func:`same_value` (exact type and
#: value), never ``==``, so a payload that spells the version ``true`` or ``3.0`` is refused. Version 3: every list
#: endpoint pages through this module, and the payload carries an integrity digest.
CURSOR_VERSION = 3

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
    #: For a list that mixes several tables (ids of different tables are not comparable): the highest id per table
    #: when the traversal began. A table absent here had no row then, so every row of it is newer than the snapshot.
    groups: dict[str, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_id": self.max_id,
            "taken_at": self.taken_at.isoformat(),
            "release_ref": self.release_ref,
            "groups": self.groups,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Watermark:
        return cls(
            max_id=int(payload["max_id"]),
            taken_at=datetime.fromisoformat(payload["taken_at"]),
            release_ref=payload.get("release_ref"),
            groups=(
                {str(k): int(v) for k, v in payload["groups"].items()}
                if payload.get("groups") is not None
                else None
            ),
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
    if isinstance(value, (tuple, list)):
        return [_encode_value(v) for v in value]
    if hasattr(value, "value"):  # enum
        return value.value
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and "__dt__" in value:
        return datetime.fromisoformat(value["__dt__"])
    if isinstance(value, list):
        return tuple(_decode_value(v) for v in value)
    return value


def _digest(payload: Mapping[str, Any]) -> str:
    """A short integrity digest over every field of the payload but the digest itself.

    It is not a secret and not authentication: it detects an edited or truncated token, so one that was altered is
    refused rather than read as a different position. (A token cannot gain access by being edited: every page applies
    the request's own visibility rules again.)
    """
    body = {k: v for k, v in payload.items() if k != "k"}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def encode_cursor(cursor: Cursor) -> str:
    """Serialize a cursor to a URL-safe opaque token."""
    payload = cursor.as_dict()
    payload["k"] = _digest(payload)
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
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

    if not isinstance(payload, dict) or not same_value(payload.get("v"), CURSOR_VERSION):
        raise InvalidCursorError(
            "invalid_cursor: cursor version is not supported by this server; "
            "restart the traversal without a cursor."
        )
    recorded_digest = payload.get("k")
    if not isinstance(recorded_digest, str) or not hmac.compare_digest(recorded_digest, _digest(payload)):
        raise InvalidCursorError("invalid_cursor: the cursor was altered or is not a token this API issued.")
    signature = payload.get("query_signature")
    if signature != expected_signature:
        raise InvalidCursorError(
            "cursor_query_mismatch: this cursor was issued for a different "
            "query. A cursor is only valid for the exact filter set that "
            "produced it; restart the traversal without a cursor."
        )
    try:
        last_values = payload["last_values"]
        if not isinstance(last_values, list) or not last_values:
            raise ValueError("last_values")
        return Cursor(
            watermark=Watermark.from_dict(payload["watermark"]),
            last_values=[_decode_value(v) for v in last_values],
            query_signature=signature,
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
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


# ---------------------------------------------------------------------------
# The engine every list read pages through
# ---------------------------------------------------------------------------

#: Request fields that choose a page or a response shape, not a result set. Everything else a request model carries is a
#: filter, and a cursor is bound to it.
_NOT_A_FILTER: frozenset[str] = frozenset({"offset", "limit", "cursor", "include", "sort", "check_inputs"})


def request_scope(request: BaseModel | Mapping[str, Any] | None) -> dict[str, Any]:
    """The filters of a request, as the value a cursor is bound to.

    Paging knobs (offset, limit, cursor) and response-shape knobs (include) are left out, so a client may change the page
    size between pages and may ask for more detail on one of them. A filter that is unset is left out too, so an
    omitted default and an explicit ``None`` bind the same cursor.
    """
    if request is None:
        return {}
    if isinstance(request, BaseModel):
        return request.model_dump(mode="json", exclude=set(_NOT_A_FILTER), exclude_none=True)
    return {k: v for k, v in request.items() if k not in _NOT_A_FILTER and v is not None}


def signature_for(endpoint: str, scope: Mapping[str, Any]) -> str:
    """Bind a cursor to this endpoint, these filters, and the read profile in force.

    The profile is part of the signature because it changes the visible set: a cursor issued mid-traversal under
    ``exploratory`` and replayed under ``curated`` would silently walk a different corpus. Refusing it is the honest
    outcome.
    """
    from app.services.scientific_read.profile import current_read_profile

    resolved = current_read_profile()
    return query_signature(endpoint, {**scope, "__profile__": resolved.profile.value})


@dataclass(frozen=True)
class Page(Generic[T]):
    """One page, and where the next one starts."""

    items: list[T]
    #: Size of the set this traversal walks (the snapshot, after the request's filters and visibility rules).
    total: int
    mode: Literal["offset", "cursor"]
    #: ``None`` on the last page.
    next_cursor: str | None
    watermark: Watermark

    def envelope(self) -> dict[str, Any]:
        """The response fields that carry the cursor contract: ``next_cursor`` and ``watermark``.

        ``watermark`` (when the traversal's snapshot was taken) is echoed on cursor pages only. A page served by offset
        names no snapshot, so the same request still returns the same body: nothing in it is the wall clock. The
        snapshot's time is inside ``next_cursor``, and every later page echoes it.
        """
        return {
            "next_cursor": self.next_cursor,
            "watermark": (
                WatermarkEcho(taken_at=self.watermark.taken_at, release_ref=self.watermark.release_ref)
                if self.mode == "cursor"
                else None
            ),
        }


def _as_key(values: Sequence[Any]) -> tuple:
    return tuple(_as_key(v) if isinstance(v, (list, tuple)) else v for v in values)


class Paging:
    """One request's position in a list: opens its cursor, bounds the set to the snapshot, slices a page.

    Build one right after the request's offset and limit are validated, before any early return, so that a cursor
    that does not belong to this request is refused even when the request matches nothing::

        paging = Paging("thermo/search", request, offset=offset, limit=limit)
        visible = paging.snapshot(visible, id_of=lambda r: r.id)      # then summarise ``visible``
        page = paging.window(visible, key_of=lambda r: (rank(r), -ts(r), -r.id))

    ``key_of`` must return a tuple whose ascending order is the display order, ending in a unique value (the record's
    id, negated for a newest-first order) so the order is total. The cursor holds that tuple for the last record
    delivered and the next page asks for records strictly after it. Because the page is cut from the key, not from a
    row count, a record that sorts above the reader's position cannot shift it.
    """

    def __init__(
        self,
        endpoint: str,
        request: BaseModel | Mapping[str, Any] | None,
        *,
        offset: int,
        limit: int,
        scope_extra: Mapping[str, Any] | None = None,
    ) -> None:
        if isinstance(request, BaseModel):
            token = request.model_dump(include={"cursor"}).get("cursor")
        elif request is not None:
            token = request.get("cursor")
        else:
            token = None
        if token is not None and offset:
            raise CodedValueError(
                "cursor_offset_conflict",
                "cursor= and a non-zero offset= cannot be combined; a cursor already encodes the traversal position. "
                "Re-issue with the cursor alone, or drop the cursor to page by offset.",
                context={"offset": offset},
            )
        scope = request_scope(request)
        if scope_extra:
            scope.update(scope_extra)
        self.endpoint = endpoint
        self.offset = offset
        self.limit = limit
        self.signature = signature_for(endpoint, scope)
        self.resume: Cursor | None = (
            decode_cursor(token, expected_signature=self.signature) if token is not None else None
        )
        self._watermark: Watermark | None = self.resume.watermark if self.resume is not None else None
        self._snapshotted = False

    @property
    def mode(self) -> Literal["offset", "cursor"]:
        return "cursor" if self.resume is not None else "offset"

    # -- the snapshot -----------------------------------------------------------------------------------------------

    def snapshot(
        self,
        items: list[T],
        *,
        id_of: Callable[[T], int],
        group_of: Callable[[T], str] | None = None,
    ) -> list[T]:
        """Bound ``items`` (the records this request may see) to the traversal's snapshot.

        Under a cursor, drops what was inserted after the traversal began. Without one, starts the traversal: the bound
        is the highest id among ``items``, and ``items`` must already be what the read profile shows, so that the bound
        never encodes the id of a record the profile withholds.
        """
        self._snapshotted = True
        if group_of is not None:
            # Ids of different tables are not comparable: bound each table by its own highest id.
            if self.resume is not None:
                bounds = self.resume.watermark.groups or {}
                return [item for item in items if id_of(item) <= bounds.get(group_of(item), 0)]
            bounds = {}
            for item in items:
                group = group_of(item)
                bounds[group] = max(bounds.get(group, 0), id_of(item))
            self._watermark = Watermark(max_id=0, taken_at=datetime.now(timezone.utc), groups=bounds)
            return items
        if self.resume is not None:
            bound = self.resume.watermark.max_id
            return [item for item in items if id_of(item) <= bound]
        self._watermark = Watermark(
            max_id=max((id_of(item) for item in items), default=0),
            taken_at=datetime.now(timezone.utc),
        )
        return items

    def window(
        self,
        items: list[T],
        *,
        key_of: Callable[[T], tuple],
        id_of: Callable[[T], int] | None = None,
    ) -> Page[T]:
        """Order ``items`` by ``key_of`` and cut the page: after the cursor's position, or at ``offset``.

        Pass ``id_of`` unless :meth:`snapshot` was already called on this list.
        """
        if not self._snapshotted:
            if id_of is None:
                raise ValueError("Paging.window needs id_of when snapshot() was not called.")
            items = self.snapshot(items, id_of=id_of)
        watermark = self._watermark
        assert watermark is not None
        keyed = sorted(((_as_key(key_of(item)), item) for item in items), key=lambda pair: pair[0])
        total = len(keyed)
        if self.resume is not None:
            last = _as_key(self.resume.last_values)
            if keyed and len(last) != len(keyed[0][0]):
                raise InvalidCursorError("invalid_cursor: the cursor payload is malformed.")
            keyed = [pair for pair in keyed if pair[0] > last]
            start = 0
        else:
            start = self.offset
        chunk = keyed[start : start + self.limit]
        more = len(keyed) > start + self.limit
        next_cursor = None
        if more and chunk:
            next_cursor = encode_cursor(
                Cursor(watermark=watermark, last_values=list(chunk[-1][0]), query_signature=self.signature)
            )
        return Page(
            items=[item for _key, item in chunk],
            total=total,
            mode=self.mode,
            next_cursor=next_cursor,
            watermark=watermark,
        )

    # -- SQL ----------------------------------------------------------------------------------------------------------

    def begin(self, max_id: int | None) -> None:
        """Start a traversal whose rows a caller selects itself (raw SQL): ``max_id`` is the highest id the request may
        see. Under a cursor the traversal's own snapshot stands and this does nothing."""
        if self.resume is None:
            self._watermark = Watermark(max_id=int(max_id or 0), taken_at=datetime.now(timezone.utc))

    def finish(self, rows: list[T], *, more: bool, last_values: Sequence[Any] | None, total: int) -> Page[T]:
        """The :class:`Page` for rows a caller selected itself. ``last_values`` is the sort key of ``rows[-1]``."""
        watermark = self._watermark
        assert watermark is not None, "Paging.begin() was not called"
        next_cursor = None
        if more and rows and last_values is not None:
            next_cursor = encode_cursor(
                Cursor(watermark=watermark, last_values=list(last_values), query_signature=self.signature)
            )
        return Page(items=rows, total=total, mode=self.mode, next_cursor=next_cursor, watermark=watermark)

    def restrict(self, stmt, id_column):
        """Under a cursor, limit ``stmt`` to rows that existed when the traversal began (so counts and summaries agree
        with the page). Without one, ``stmt`` is returned unchanged."""
        if self.resume is None:
            return stmt
        return stmt.where(id_column <= self.resume.watermark.max_id)

    def sql_window(
        self,
        session,
        ranked,
        *,
        columns: Sequence[Any],
        keys: Sequence[tuple[Any, SortDirection]],
        total: int,
    ) -> Page[Any]:
        """One page of a ranked subquery, with the "strictly after the cursor" predicate pushed into the statement.

        ``ranked`` exposes an ``id`` column; ``columns`` is what to select (it must include every key column);
        ``keys`` is the sort, most significant first, ending in the unique ``ranked.c.id``. ``total`` is the size of the
        set the caller counted (after :meth:`restrict`). The page's ``items`` are the selected rows.
        """
        id_column = ranked.c.id
        if self._watermark is None:
            max_id = session.scalar(select(func.max(id_column)))
            self.begin(max_id)
        stmt = select(*columns).where(id_column <= self._watermark.max_id)  # type: ignore[union-attr]
        return self.sql_page(session, stmt, keys=keys, total=total)

    def sql_page(
        self,
        session,
        stmt,
        *,
        keys: Sequence[tuple[Any, SortDirection]],
        total: int,
    ) -> Page[Any]:
        """Order ``stmt`` by ``keys`` and cut the page after the cursor's position (or at ``offset``).

        For a statement the caller built itself, already bounded to the snapshot (:meth:`restrict`); in offset mode the
        caller must have called :meth:`begin`. ``keys`` must end in a column that is unique per row.
        """
        watermark = self._watermark
        assert watermark is not None, "Paging.begin() was not called"
        stmt = stmt.order_by(*[col.asc() if direction == "asc" else col.desc() for col, direction in keys])
        if self.resume is not None:
            if len(self.resume.last_values) != len(keys):
                raise InvalidCursorError("invalid_cursor: the cursor payload is malformed.")
            stmt = stmt.where(keyset_predicate(list(keys), self.resume.last_values))
        else:
            stmt = stmt.offset(self.offset)
        rows = list(session.execute(stmt.limit(self.limit + 1)).all())
        more = len(rows) > self.limit
        rows = rows[: self.limit]
        last_values = [_plain(_key_value(rows[-1], col)) for col, _direction in keys] if rows else None
        return self.finish(rows, more=more, last_values=last_values, total=total)


def _key_value(row, column) -> Any:
    """The value of sort column ``column`` on ``row``: by the column itself, else off the row's entity (an ORM column
    selected as part of a whole entity is not a key of the row's mapping)."""
    try:
        return row._mapping[column]
    except KeyError:
        pass
    try:
        return getattr(row, column.key)
    except AttributeError:
        return getattr(row[0], column.key)


def _plain(value: Any) -> Any:
    """A database value as a JSON-able cursor value (a ``Decimal`` rank is still a number)."""
    from decimal import Decimal

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


__all__ = [
    "CURSOR_VERSION",
    "Cursor",
    "InvalidCursorError",
    "Page",
    "Paging",
    "SortDirection",
    "Watermark",
    "decode_cursor",
    "encode_cursor",
    "keyset_predicate",
    "query_signature",
    "request_scope",
    "signature_for",
    "watermark_predicate",
]
