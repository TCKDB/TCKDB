"""The snapshot compare script's deposit warning hash follows the server's formula."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.services.deposit_warnings import content_hash

SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "scripts" / "paper_snapshot"


@pytest.fixture(scope="module")
def compare():
    sys.path.insert(0, str(SNAPSHOT_DIR))
    try:
        import compare as module

        yield module
    finally:
        sys.path.remove(str(SNAPSHOT_DIR))


CASES = [
    ("code_a", "field[0].x", "A message with no ref.", {"n": 1, "xs": ["a", "b"], "flag": True}),
    ("code_b", "f", "Another message.", None),
]


@pytest.mark.parametrize(("code", "field", "message", "context"), CASES)
def test_warning_hash_equals_the_server_hash(compare, code, field, message, context):
    assert compare.warning_hash(code, field, message, context) == content_hash(code, field, message, context)


@pytest.mark.parametrize(("code", "field", "message", "context"), CASES)
def test_masked_hash_of_a_ref_free_warning_equals_the_server_hash(compare, code, field, message, context):
    values = {"code": code, "field": field, "message": message, "context": context, "content_hash": "x"}
    assert compare.masked_warning_hash(values) == content_hash(code, field, message, context)


def test_masked_hash_ignores_the_public_refs_in_a_warning(compare):
    def values(ref: str) -> dict:
        return {
            "code": "c",
            "field": "f",
            "message": f"calc {ref} differs",
            "context": {"refs": [ref]},
            "content_hash": "x",
        }

    one = compare.masked_warning_hash(values("calc_" + "a" * 26))
    two = compare.masked_warning_hash(values("calc_" + "b" * 26))
    assert one == two
