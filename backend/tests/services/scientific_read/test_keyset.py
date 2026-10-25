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
    seal,
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
    token = base64.urlsafe_b64encode(seal(payload.encode())).decode().rstrip("=")
    with pytest.raises(InvalidCursorError, match="cursor version"):
        decode_cursor(token, expected_signature="sig-a")


def test_a_version_one_cursor_is_refused_as_an_old_cursor_version():
    """Kills: leaving CURSOR_VERSION unchanged after the payload changed, so an old cursor resumes the new order."""
    import base64
    import json

    assert CURSOR_VERSION == 4
    payload = json.dumps(
        {"v": 1, "watermark": _watermark().as_dict(), "last_values": [1], "query_signature": "sig-a"}
    )
    token = base64.urlsafe_b64encode(seal(payload.encode())).decode().rstrip("=")
    with pytest.raises(InvalidCursorError, match="cursor version"):
        decode_cursor(token, expected_signature="sig-a")


@pytest.mark.parametrize("raw", [f"{CURSOR_VERSION}.0", f'"{CURSOR_VERSION}"'])
def test_a_cursor_whose_version_is_a_float_or_a_string_is_rejected(raw):
    """Kills: ``payload.get("v") != CURSOR_VERSION`` accepting the float ``2.0`` (``2.0 == 2``).

    A bool is not tested here: ``true`` is ``1``, which the current version is not, so it would be refused for the
    wrong reason. The value must equal the current version when compared as a number and still be refused."""
    import base64
    import json

    good = json.dumps(
        {
            "v": CURSOR_VERSION,
            "watermark": _watermark().as_dict(),
            "last_values": [1],
            "query_signature": "sig-a",
        }
    )
    payload = good.replace(f'"v": {CURSOR_VERSION}', f'"v": {raw}')
    assert payload != good
    token = base64.urlsafe_b64encode(seal(payload.encode())).decode().rstrip("=")
    with pytest.raises(InvalidCursorError, match="cursor version"):
        decode_cursor(token, expected_signature="sig-a")


# ---------------------------------------------------------------------------
# Sealing: the token is opaque, and a forged one is a cursor error
# ---------------------------------------------------------------------------


def test_a_cursor_is_opaque_without_the_key():
    """Kills: a token that is plain base64 JSON (it would hand out record ids and the snapshot bound)."""
    import base64
    import json

    from app.services.scientific_read.keyset import Cursor, encode_cursor

    token = encode_cursor(Cursor(watermark=_watermark(), last_values=[1234567, 7654321], query_signature="sig-a"))
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    with pytest.raises(ValueError):
        json.loads(raw)
    assert b"1234567" not in raw and b"7654321" not in raw
    assert b"max_id" not in raw and b"last_values" not in raw


def test_flipping_any_single_byte_of_a_token_is_refused():
    """Kills: a seal that authenticates only part of the token (nonce, body or tag)."""
    import base64

    from app.services.scientific_read.keyset import Cursor, encode_cursor

    token = encode_cursor(Cursor(watermark=_watermark(), last_values=[1, "x"], query_signature="sig-a"))
    raw = bytearray(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    assert len(raw) > 60
    for index in range(len(raw)):
        forged = bytearray(raw)
        forged[index] ^= 0x01
        bad = base64.urlsafe_b64encode(bytes(forged)).decode().rstrip("=")
        with pytest.raises(InvalidCursorError, match="invalid_cursor"):
            decode_cursor(bad, expected_signature="sig-a")
    # Control: the untouched token still opens.
    assert decode_cursor(token, expected_signature="sig-a").last_values == [1, "x"]


def test_a_token_is_sealed_with_aes_gcm_and_the_version_as_associated_data():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app.services.scientific_read import keyset

    blob = keyset.seal(b"hello")
    assert blob[: keyset._NONCE_BYTES] != keyset.seal(b"hello")[: keyset._NONCE_BYTES]  # a fresh nonce each time
    assert len(blob) == keyset._NONCE_BYTES + len(b"hello") + 16
    assert keyset.unseal(blob) == b"hello"
    # The same bytes sealed as another version do not open as this one.
    nonce, body = blob[: keyset._NONCE_BYTES], blob[keyset._NONCE_BYTES :]
    other = AESGCM(_key_of(keyset)).encrypt(nonce, b"hello", b"tckdb-cursor-v999")
    assert other != body
    with pytest.raises(ValueError):
        keyset.unseal(nonce + other)


def _key_of(keyset) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from app.api.config import settings

    secret = settings.paging_cursor_key or keyset._LOCAL_DEVELOPMENT_KEY
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=keyset._KEY_INFO).derive(secret.encode())


def test_the_same_cursor_is_sealed_differently_each_time():
    from app.services.scientific_read.keyset import Cursor, encode_cursor

    cursor = Cursor(watermark=_watermark(), last_values=[1], query_signature="sig-a")
    assert encode_cursor(cursor) != encode_cursor(cursor)


def test_a_cursor_sealed_under_another_key_is_refused(monkeypatch):
    """A key rotation invalidates cursors in flight: a 422 and a restart, nothing worse."""
    from app.api.config import settings
    from app.services.scientific_read.keyset import Cursor, encode_cursor

    monkeypatch.setattr(settings, "paging_cursor_key", "a" * 40)
    token = encode_cursor(Cursor(watermark=_watermark(), last_values=[1], query_signature="sig-a"))
    assert decode_cursor(token, expected_signature="sig-a").last_values == [1]
    monkeypatch.setattr(settings, "paging_cursor_key", "b" * 40)
    with pytest.raises(InvalidCursorError, match="invalid_cursor"):
        decode_cursor(token, expected_signature="sig-a")


def test_a_hosted_deployment_without_a_key_issues_no_cursor(monkeypatch):
    from app.api.config import settings
    from app.services.scientific_read.keyset import Cursor, encode_cursor

    monkeypatch.setattr(settings, "paging_cursor_key", None)
    monkeypatch.setattr(settings, "deployment_mode", "hosted_public")
    with pytest.raises(RuntimeError, match="PAGING_CURSOR_KEY"):
        encode_cursor(Cursor(watermark=_watermark(), last_values=[1], query_signature="sig-a"))


def _sealed(payload: dict) -> str:
    import base64
    import json

    return base64.urlsafe_b64encode(seal(json.dumps(payload).encode())).decode().rstrip("=")


def _good_payload() -> dict:
    return {
        "v": CURSOR_VERSION,
        "watermark": _watermark().as_dict(),
        "last_values": [1],
        "query_signature": "sig-a",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"last_values": "x"},
        {"last_values": []},
        {"last_values": [{"a": 1}]},
        {"last_values": [{"__dt__": 5}]},
        {"last_values": [{"__dt__": "not a time"}]},
        {"last_values": [float("nan")]},
        {"watermark": "x"},
        {"watermark": {"max_id": "9", "taken_at": "2026-01-01T00:00:00+00:00"}},
        {"watermark": {"max_id": True, "taken_at": "2026-01-01T00:00:00+00:00"}},
        {"watermark": {"max_id": -1, "taken_at": "2026-01-01T00:00:00+00:00"}},
        {"watermark": {"max_id": 1, "taken_at": 5}},
        {"watermark": {"max_id": 1, "taken_at": "2026-01-01T00:00:00+00:00", "groups": {"a": "b"}}},
        {"watermark": {"max_id": 1, "taken_at": "2026-01-01T00:00:00+00:00", "release_ref": 5}},
    ],
    ids=repr,
)
def test_a_sealed_payload_of_the_wrong_shape_is_a_cursor_error(change):
    """Kills: a bad value reaching the comparison or the database (a 500 where a 422 belongs)."""
    with pytest.raises(InvalidCursorError, match="invalid_cursor"):
        decode_cursor(_sealed({**_good_payload(), **change}), expected_signature="sig-a")


def test_a_key_of_the_wrong_kind_is_refused_before_it_is_compared():
    from app.services.scientific_read.keyset import check_key_values

    check_key_values([1, 2.5, "a"], [float, float, str])  # numbers are numbers
    for bad in ([1, 2.5, 3], ["x", 2.5, "a"], [True, 2.5, "a"], [1, 2.5], [1, 2.5, "a", 4]):
        with pytest.raises(InvalidCursorError):
            check_key_values(bad, [float, float, str])


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
