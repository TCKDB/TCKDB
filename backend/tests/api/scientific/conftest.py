"""Shared fixtures for scientific API tests.

Phase D: scientific read responses hide internal integer IDs by default.
Tests that need the legacy id-bearing shape can opt in via the
``allow_internal_ids`` fixture, which both flips the
``ALLOW_PUBLIC_INTERNAL_IDS`` setting **and** signals callers to add
``"internal_ids"`` to their ``include=`` lists.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def allow_internal_ids(monkeypatch):
    """Allow ``include=internal_ids`` to restore integer IDs in responses.

    Flips ``settings.allow_public_internal_ids`` to ``True`` for the
    duration of the test so any request that supplies
    ``include=internal_ids`` (or its functional equivalent in a POST
    body) gets the legacy id-bearing response shape back. The setting
    is restored at end-of-test by pytest's monkeypatch.

    Use this fixture in tests that specifically verify ``*_id``
    fields, the Phase B/C id+ref coexistence contract, or any
    compatibility shape that relied on integer ids being present in
    the default response.
    """
    from app.api.config import settings

    monkeypatch.setattr(settings, "allow_public_internal_ids", True)
    yield
