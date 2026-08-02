"""Safe lazy pagination for scientific search envelopes.

Two traversals live here. :func:`iter_paginated_records` walks offsets and
is what the transactional searches use. :func:`iter_keyset_records` follows
the analytics surface's ``next_cursor`` instead, because offset paging over
a corpus that is still being written to can skip or duplicate rows — a
dataset built that way is not reproducible, and nothing in the response
would tell the caller it happened.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any, TypeVar, cast

from tckdb_client.errors import TCKDBPaginationError
from tckdb_client.scientific_types import ScientificSearchResponse

RecordT = TypeVar("RecordT")
_DEFAULT_LIMIT = 50


def _integer_field(pagination: Mapping[str, object], name: str) -> int:
    value = pagination.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TCKDBPaginationError(
            f"Malformed pagination: {name!r} must be an integer."
        )
    return value


def _initial_value(value: object, *, name: str, default: int, minimum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TCKDBPaginationError(
            f"Invalid iterator {name}: expected an integer >= {minimum}."
        )
    return value


def iter_paginated_records(
    fetch_page: Callable[..., ScientificSearchResponse[RecordT]],
    parameters: Mapping[str, Any],
) -> Iterator[RecordT]:
    """Yield records while following the server's pagination metadata.

    The supplied filter/include arguments are copied unchanged. Only
    ``offset`` and ``limit`` advance. Malformed, inconsistent, or
    non-advancing pages raise :class:`TCKDBPaginationError` instead of
    silently dropping records or looping forever.
    """

    stable_parameters = dict(parameters)
    collapse_first = stable_parameters.get("collapse") == "first"
    requested_offset = _initial_value(
        stable_parameters.pop("offset", None),
        name="offset",
        default=0,
        minimum=0,
    )
    requested_limit = _initial_value(
        stable_parameters.pop("limit", None),
        name="limit",
        default=_DEFAULT_LIMIT,
        minimum=1,
    )
    expected_total: int | None = None

    while True:
        page = fetch_page(
            **stable_parameters,
            offset=requested_offset,
            limit=requested_limit,
        )
        if not isinstance(page, Mapping):
            raise TCKDBPaginationError("Malformed page: response must be an object.")

        records = page.get("records")
        pagination = page.get("pagination")
        if not isinstance(records, list):
            raise TCKDBPaginationError("Malformed page: 'records' must be a list.")
        if not isinstance(pagination, Mapping):
            raise TCKDBPaginationError(
                "Malformed page: 'pagination' must be an object."
            )

        page_offset = _integer_field(pagination, "offset")
        page_limit = _integer_field(pagination, "limit")
        returned = _integer_field(pagination, "returned")
        total = _integer_field(pagination, "total")
        has_post_collapse_total = "post_collapse_total" in pagination
        post_collapse_total = (
            _integer_field(pagination, "post_collapse_total")
            if has_post_collapse_total
            else total
        )
        if page_offset < 0 or page_limit < 1 or returned < 0 or total < 0:
            raise TCKDBPaginationError(
                "Malformed pagination: offset/returned/total must be non-negative "
                "and limit must be positive."
            )
        if post_collapse_total < 0 or post_collapse_total > total:
            raise TCKDBPaginationError(
                "Malformed pagination: post_collapse_total must be between zero "
                "and total."
            )
        if has_post_collapse_total:
            expected_post_total = min(total, 1) if collapse_first else total
            if post_collapse_total != expected_post_total:
                raise TCKDBPaginationError(
                    "Malformed pagination: post_collapse_total does not match "
                    "the requested collapse mode."
                )
        if page_offset != requested_offset:
            raise TCKDBPaginationError(
                "Malformed pagination: server offset does not match the requested offset."
            )
        if returned != len(records):
            raise TCKDBPaginationError(
                "Malformed pagination: returned does not match len(records)."
            )
        if returned > page_limit:
            raise TCKDBPaginationError(
                "Malformed pagination: returned exceeds the server page limit."
            )
        if collapse_first and not has_post_collapse_total:
            if returned > 1 or (page_offset >= 1 and returned > 0):
                raise TCKDBPaginationError(
                    "Malformed pagination: legacy collapsed pages may return only "
                    "one record at offset zero."
                )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise TCKDBPaginationError(
                "Pagination total changed while iterating; restart the query."
            )
        if returned == 0 and page_offset >= post_collapse_total:
            return
        if page_offset + returned > post_collapse_total:
            raise TCKDBPaginationError(
                "Malformed pagination: page extends beyond the reported "
                "post-collapse total."
            )

        yield from cast(list[RecordT], records)

        next_offset = page_offset + returned
        if next_offset >= post_collapse_total:
            return
        # Compatibility fallback for servers predating post_collapse_total.
        # Their collapsed result set has at most one row even though ``total``
        # reports the larger pre-collapse candidate count.
        if collapse_first and not has_post_collapse_total:
            if returned == 1 or page_offset >= 1:
                return
        if returned == 0 or next_offset <= requested_offset:
            raise TCKDBPaginationError(
                "Pagination did not advance before reaching the reported total."
            )
        requested_offset = next_offset


def _keyset_page_records(page: object) -> list[Any]:
    """Validate one keyset page and return its records."""

    if not isinstance(page, Mapping):
        raise TCKDBPaginationError("Malformed page: response must be an object.")
    records = page.get("records")
    pagination = page.get("pagination")
    if not isinstance(records, list):
        raise TCKDBPaginationError("Malformed page: 'records' must be a list.")
    if not isinstance(pagination, Mapping):
        raise TCKDBPaginationError("Malformed page: 'pagination' must be an object.")
    page_limit = _integer_field(pagination, "limit")
    returned = _integer_field(pagination, "returned")
    if page_limit < 1 or returned < 0:
        raise TCKDBPaginationError(
            "Malformed pagination: returned must be non-negative and limit "
            "must be positive."
        )
    if returned != len(records):
        raise TCKDBPaginationError(
            "Malformed pagination: returned does not match len(records)."
        )
    if returned > page_limit:
        raise TCKDBPaginationError(
            "Malformed pagination: returned exceeds the server page limit."
        )
    return records


def iter_keyset_records(
    fetch_page: Callable[..., Mapping[str, Any]],
    parameters: Mapping[str, Any],
) -> Iterator[RecordT]:
    """Yield records by following the server's ``next_cursor``.

    Two situations fall back to :func:`iter_paginated_records`, because a
    cursor cannot express either of them:

    * the caller pinned a starting ``offset`` — keyset traversal has no
      notion of "start at row 100";
    * the server does not publish ``next_cursor`` at all, i.e. it predates
      the keyset contract. Truncating to one page there would silently hand
      back a fraction of the result set.

    Everything else is a protocol violation and raises
    :class:`TCKDBPaginationError` rather than looping forever or returning
    a set the caller cannot tell is incomplete.
    """

    stable_parameters = dict(parameters)
    cursor = stable_parameters.pop("cursor", None)
    if cursor is not None and not isinstance(cursor, str):
        raise TCKDBPaginationError("Invalid iterator cursor: expected a string.")

    offset = stable_parameters.get("offset")
    if offset is not None and offset != 0:
        yield from iter_paginated_records(fetch_page, stable_parameters)
        return
    stable_parameters.pop("offset", None)

    seen_cursors: set[str] = set()
    first_page = True
    while True:
        page = fetch_page(**stable_parameters, cursor=cursor)
        records = _keyset_page_records(page)

        if "next_cursor" not in page:
            if first_page:
                # A pre-keyset server. Restart from offset zero rather than
                # yield this probe page twice.
                yield from iter_paginated_records(fetch_page, stable_parameters)
                return
            raise TCKDBPaginationError(
                "Malformed page: a keyset traversal received a page with no "
                "'next_cursor'; the result set may be truncated."
            )
        first_page = False

        next_cursor = page.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise TCKDBPaginationError(
                "Malformed page: 'next_cursor' must be a string or null."
            )
        if next_cursor is not None and not records:
            raise TCKDBPaginationError(
                "Pagination did not advance: the server returned an empty page "
                "with a next_cursor."
            )

        yield from cast(list[RecordT], records)

        if next_cursor is None:
            return
        if next_cursor in seen_cursors:
            raise TCKDBPaginationError(
                "Pagination looped: the server repeated a cursor it had "
                "already issued."
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor


__all__ = ["iter_keyset_records", "iter_paginated_records"]
