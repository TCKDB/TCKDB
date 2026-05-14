"""Tests for the idempotency-key helpers."""

from __future__ import annotations

import pytest

from tckdb_client.idempotency import (
    make_idempotency_key,
    validate_idempotency_key,
)


@pytest.mark.parametrize(
    "key",
    [
        "abcdefghij1234567890",
        "tool:job-1:thermo:eth",
        "A" * 16,
        "A" * 200,
        "valid.key_with-dots:colons",
    ],
)
def test_validate_accepts_valid_keys(key: str) -> None:
    assert validate_idempotency_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "short",                  # too short
        "A" * 15,                 # one below minimum
        "A" * 201,                # one above max
        "has space inside the_key",
        "has/slash/inside/value12",
        "unicodé_chars_here_xx",
    ],
)
def test_validate_rejects_invalid_keys(key: str) -> None:
    with pytest.raises(ValueError):
        validate_idempotency_key(key)


def test_validate_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        validate_idempotency_key(12345)  # type: ignore[arg-type]


def test_make_key_joins_with_colons() -> None:
    key = make_idempotency_key("mytool", "job-123", "thermo", "ethanol-pure")
    assert key == "mytool:job-123:thermo:ethanol-pure"
    assert validate_idempotency_key(key) == key


def test_make_key_replaces_illegal_chars() -> None:
    key = make_idempotency_key("my tool", "job/123", "thermo!", "n-butane (s)")
    # spaces, slashes, exclamations, parentheses become '-'
    assert " " not in key
    assert "/" not in key
    assert "!" not in key
    assert "(" not in key
    assert ")" not in key
    validate_idempotency_key(key)


def test_make_key_pads_short_input_to_min_length() -> None:
    key = make_idempotency_key("a", "b")
    assert len(key) >= 16
    validate_idempotency_key(key)


def test_make_key_rejects_empty_part() -> None:
    with pytest.raises(ValueError):
        make_idempotency_key("ok-part", "")


def test_make_key_rejects_no_parts() -> None:
    with pytest.raises(ValueError):
        make_idempotency_key()


def test_make_key_rejects_too_long_result() -> None:
    with pytest.raises(ValueError):
        make_idempotency_key("a" * 250)
