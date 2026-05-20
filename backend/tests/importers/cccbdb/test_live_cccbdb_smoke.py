"""Optional live smoke tests for the CCCBDB experimental-species parser.

These tests are **skipped by default**. They run only when
``TCKDB_CCCBDB_LIVE_TESTS=1`` is set in the environment. They are not
part of CI. Their purpose is local parser-drift checking against the
real CCCBDB site; they are intentionally tolerant and assert only
broad invariants.

Constraints (also documented in
``backend/docs/specs/cccbdb_importer.md``):

* explicit allowlist of 1-3 URLs
* clear User-Agent identifying TCKDB + a contact mailto
* short request timeout
* at most one conservative retry
* no automatic writing of downloaded HTML back into the repo
"""

from __future__ import annotations

import os
import time

import pytest

from app.importers.cccbdb.parsers import parse_experimental_species_page

_LIVE_ENABLED = os.environ.get("TCKDB_CCCBDB_LIVE_TESTS") == "1"

pytestmark = pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="Set TCKDB_CCCBDB_LIVE_TESTS=1 to enable live CCCBDB smoke tests.",
)

_USER_AGENT = (
    "tckdb-cccbdb-importer/0.1 "
    "(+https://github.com/TCKDB/TCKDB; "
    "mailto:calvin.p@campus.technion.ac.il) "
    "phase=1 mode=smoke"
)
_TIMEOUT_SECONDS = 15.0

_ALLOWLIST = {
    "H2": "https://cccbdb.nist.gov/exp1x.asp?casno=1333740",
    "H2O": "https://cccbdb.nist.gov/exp1x.asp?casno=7732185",
    "benzene": "https://cccbdb.nist.gov/exp1x.asp?casno=71432",
}


def _fetch(url: str) -> str:
    """Polite GET with one conservative retry."""

    import requests

    headers = {"User-Agent": _USER_AGENT}
    last_exc: Exception | None = None
    for attempt in (0, 1):
        try:
            response = requests.get(
                url, headers=headers, timeout=_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == 0:
                time.sleep(2.0)
                continue
            raise
    assert last_exc is not None  # unreachable; keeps type checkers quiet
    raise last_exc


@pytest.mark.parametrize("species", list(_ALLOWLIST.keys()))
def test_live_parser_finds_identity_or_thermo(species):
    """Broad invariant: parsing the page yields *some* parseable signal.

    We accept either an identity field (formula/name) or at least one
    thermochemistry value, since the live page layout may not match
    the fixture sections exactly. A page that yields neither indicates
    parser drift.
    """

    url = _ALLOWLIST[species]
    html = _fetch(url)
    record = parse_experimental_species_page(html, source_url=url)

    signal = (
        record.identity.formula is not None
        or record.identity.name is not None
        or record.thermo.values
    )
    assert signal, (
        f"Live parser produced no identity or thermo signal for "
        f"{species} ({url}). Warnings: {record.warnings[:5]}"
    )


def test_live_provenance_is_attached():
    url = _ALLOWLIST["H2"]
    html = _fetch(url)
    record = parse_experimental_species_page(html, source_url=url)
    assert record.source_metadata.source == "CCCBDB"
    assert record.source_metadata.source_release == "22"
    assert record.source_metadata.source_database_doi == "10.18434/T47C7Z"
    assert record.source_metadata.content_sha256
