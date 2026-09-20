"""Shared fixture-corpus helpers for the QCSchema adapter tests."""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def discover_corpus_cases() -> list[str]:
    """Every ``tests/fixtures/<case>/`` directory carrying both files.

    Returns an empty list (rather than raising) when the corpus directory
    is missing entirely, so the emptiness itself is asserted as a test
    failure in ``test_corpus.py`` -- not swallowed here as collection
    error noise.
    """
    if not FIXTURES.exists():
        return []
    return sorted(
        d.name
        for d in FIXTURES.iterdir()
        if d.is_dir() and (d / "document.json").exists() and (d / "meta.json").exists()
    )


def load_case(name: str) -> tuple[bytes, dict]:
    case_dir = FIXTURES / name
    raw = (case_dir / "document.json").read_bytes()
    meta = json.loads((case_dir / "meta.json").read_text())
    return raw, meta


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
