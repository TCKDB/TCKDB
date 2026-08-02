"""Unit tests for keyset traversal and the snapshot watermark.

These test the mechanism in isolation — cursor round-tripping, the
mixed-direction predicate expansion, and the guards that stop a cursor being
reused against a different query. Endpoint-level traversal is covered by the
analytics API tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, select

from app.services.scientific_read.keyset import (
    CURSOR_VERSION,
    Cursor,
    InvalidCursorError,
    Watermark,
    decode_cursor,
    encode_cursor,
    keyset_predicate,
    query_signature,
    watermark_predicate,
)

_METADATA = MetaData()
_ROWS = Table(
    "keyset_probe",
    _METADATA,
    Column("id", Integer, primary_key=True),
    Column("rank", Integer),
    Column("name", String),
)


def _watermark(max_id: int = 500) -> Watermark:
    return Watermark(max_id=max_id, taken_at=datetime(2026, 8, 1, 12, 0, 0))


# ---------------------------------------------------------------------------
# Cursor encoding
# ---------------------------------------------------------------------------


def test_cursor_round_trips_through_encoding():
    cursor = Cursor(
        watermark=_watermark(),
        last_values=[2, datetime(2026, 7, 1, 9, 30), 4321],
        query_signature="sig-a",
    )
    decoded = decode_cursor(encode_cursor(cursor), expected_signature="sig-a")

    assert decoded.last_values == [2, datetime(2026, 7, 1, 9, 30), 4321]
    assert decoded.watermark.max_id == 500
    assert decoded.watermark.taken_at == datetime(2026, 8, 1, 12, 0, 0)


def test_cursor_token_is_url_safe_and_unpadded():
    token = encode_cursor(
        Cursor(watermark=_watermark(), last_values=[1], query_signature="sig-a")
    )
    assert "=" not in token
    assert "+" not in token and "/" not in token


def test_release_ref_survives_the_round_trip():
    cursor = Cursor(
        watermark=Watermark(
            max_id=9, taken_at=datetime(2026, 8, 1), release_ref="rel_2026_07"
        ),
        last_values=[1],
        query_signature="sig-a",
    )
    decoded = decode_cursor(encode_cursor(cursor), expected_signature="sig-a")
    assert decoded.watermark.release_ref == "rel_2026_07"


# ---------------------------------------------------------------------------
# Cursor rejection — each of these would otherwise silently return a wrong page
# ---------------------------------------------------------------------------


def test_cursor_from_a_different_query_is_rejected():
    """A cursor is a position in ONE result order; reusing it elsewhere is a bug."""
    token = encode_cursor(
        Cursor(watermark=_watermark(), last_values=[1], query_signature="sig-a")
    )
    with pytest.raises(InvalidCursorError, match="cursor_query_mismatch"):
        decode_cursor(token, expected_signature="sig-b")


@pytest.mark.parametrize(
    "token",
    ["", "not-base64!!", "YWJj", "e30", "!!!!"],
)
def test_malformed_cursors_are_rejected(token):
    with pytest.raises(InvalidCursorError):
        decode_cursor(token, expected_signature="sig-a")


def test_cursor_from_a_future_version_is_rejected():
    import base64
    import json

    payload = json.dumps(
        {
            "v": CURSOR_VERSION + 1,
            "watermark": _watermark().as_dict(),
            "last_values": [1],
            "query_signature": "sig-a",
        }
    )
    token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    with pytest.raises(InvalidCursorError, match="cursor version"):
        decode_cursor(token, expected_signature="sig-a")


# ---------------------------------------------------------------------------
# Query signature
# ---------------------------------------------------------------------------


def test_signature_is_stable_across_filter_ordering():
    assert query_signature("/x", {"a": 1, "b": 2}) == query_signature(
        "/x", {"b": 2, "a": 1}
    )


def test_signature_changes_with_endpoint_or_filters():
    base = query_signature("/x", {"a": 1})
    assert query_signature("/y", {"a": 1}) != base
    assert query_signature("/x", {"a": 2}) != base


# ---------------------------------------------------------------------------
# The predicate expansion
# ---------------------------------------------------------------------------


def _sql(expression) -> str:
    return str(
        expression.compile(compile_kwargs={"literal_binds": True})
    ).replace("\n", " ")


def test_single_ascending_key_is_a_plain_comparison():
    sql = _sql(keyset_predicate([(_ROWS.c.id, "asc")], [10]))
    assert "keyset_probe.id > 10" in sql


def test_single_descending_key_flips_the_comparison():
    sql = _sql(keyset_predicate([(_ROWS.c.id, "desc")], [10]))
    assert "keyset_probe.id < 10" in sql


def test_mixed_direction_keys_expand_lexicographically():
    """ORDER BY rank ASC, id DESC must not become a naive row comparison."""
    sql = _sql(
        keyset_predicate([(_ROWS.c.rank, "asc"), (_ROWS.c.id, "desc")], [3, 77])
    )
    # Branch 1: strictly greater on the leading key.
    assert "keyset_probe.rank > 3" in sql
    # Branch 2: tie on the leading key, then strictly *less* on the trailing
    # DESC key.
    assert "keyset_probe.rank = 3" in sql
    assert "keyset_probe.id < 77" in sql
    assert " OR " in sql


def test_three_keys_produce_three_branches():
    predicate = keyset_predicate(
        [(_ROWS.c.rank, "asc"), (_ROWS.c.name, "asc"), (_ROWS.c.id, "desc")],
        [1, "b", 5],
    )
    assert len(predicate.clauses) == 3


def test_mismatched_key_and_value_counts_are_rejected():
    with pytest.raises(ValueError, match="same length"):
        keyset_predicate([(_ROWS.c.id, "asc")], [1, 2])


def test_empty_keys_are_rejected():
    with pytest.raises(ValueError, match="at least one sort key"):
        keyset_predicate([], [])


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------


def test_watermark_bounds_the_id_column():
    sql = _sql(watermark_predicate(_ROWS.c.id, _watermark(max_id=250)))
    assert "keyset_probe.id <= 250" in sql


def test_watermark_excludes_rows_inserted_after_traversal_began():
    """The property the watermark exists to provide, stated as a query."""
    stmt = select(_ROWS.c.id).where(watermark_predicate(_ROWS.c.id, _watermark(100)))
    sql = _sql(stmt)
    assert "keyset_probe.id <= 100" in sql


# ---------------------------------------------------------------------------
# The combination is what makes traversal stable
# ---------------------------------------------------------------------------


def test_keyset_and_watermark_compose_into_one_page_query():
    watermark = _watermark(max_id=1000)
    stmt = (
        select(_ROWS.c.id)
        .where(watermark_predicate(_ROWS.c.id, watermark))
        .where(
            keyset_predicate(
                [(_ROWS.c.rank, "asc"), (_ROWS.c.id, "desc")], [2, 640]
            )
        )
        .order_by(_ROWS.c.rank.asc(), _ROWS.c.id.desc())
        .limit(50)
    )
    sql = _sql(stmt)
    assert "keyset_probe.id <= 1000" in sql
    assert "keyset_probe.rank > 2" in sql
    assert "ORDER BY keyset_probe.rank ASC, keyset_probe.id DESC" in sql
    assert "LIMIT 50" in sql


def test_watermark_taken_at_is_preserved_exactly():
    """The echoed snapshot time must not drift through serialization."""
    taken = datetime(2026, 8, 1, 12, 0, 0) + timedelta(microseconds=123456)
    cursor = Cursor(
        watermark=Watermark(max_id=1, taken_at=taken),
        last_values=[1],
        query_signature="sig-a",
    )
    decoded = decode_cursor(encode_cursor(cursor), expected_signature="sig-a")
    assert decoded.watermark.taken_at == taken
