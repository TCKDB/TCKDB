"""Safe lazy pagination for scientific search envelopes."""

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
        if page_offset < 0 or page_limit < 1 or returned < 0 or total < 0:
            raise TCKDBPaginationError(
                "Malformed pagination: offset/returned/total must be non-negative "
                "and limit must be positive."
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
        if page_offset + returned > total:
            raise TCKDBPaginationError(
                "Malformed pagination: page extends beyond the reported total."
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise TCKDBPaginationError(
                "Pagination total changed while iterating; restart the query."
            )

        yield from cast(list[RecordT], records)

        next_offset = page_offset + returned
        if next_offset >= total:
            return
        if returned == 0 or next_offset <= requested_offset:
            raise TCKDBPaginationError(
                "Pagination did not advance before reaching the reported total."
            )
        # Hosted responses intentionally report the pre-collapse candidate
        # count. A collapsed query is therefore complete after its one
        # returned record even when pagination.total is larger than one.
        if collapse_first:
            return
        requested_offset = next_offset


__all__ = ["iter_paginated_records"]
