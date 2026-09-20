"""``fetch_archive`` refuses every URL but the single pinned one.

Mirrors ``backend/tests/importers/cccbdb/test_url_guard.py``.
"""

from __future__ import annotations

import pytest

from app.importers.thermoml import ARCHIVE_URL
from app.importers.thermoml.archive import UnverifiedUrlError, fetch_archive


def test_fetch_archive_refuses_unpinned_url(tmp_path):
    with pytest.raises(UnverifiedUrlError):
        fetch_archive("https://example.invalid/ThermoML.tgz", tmp_path / "out.tgz")


def test_fetch_archive_refuses_trc_nist_gov():
    """The archive must never be fetched from ``trc.nist.gov/ThermoML/``
    -- that host serves a client-side JS application with no stable
    per-file download endpoint (see module docstrings)."""

    with pytest.raises(UnverifiedUrlError):
        fetch_archive(
            "https://trc.nist.gov/ThermoML/ThermoML.v2020-09-30.tgz",
            "/tmp/unused.tgz",  # never reached
        )


def test_pinned_url_is_the_data_nist_gov_download_endpoint():
    """Sanity: the pinned URL is the PDR download endpoint, not the
    JS-app host."""

    assert ARCHIVE_URL.startswith("https://data.nist.gov/od/ds/mds2-2422/")
    assert "trc.nist.gov" not in ARCHIVE_URL
