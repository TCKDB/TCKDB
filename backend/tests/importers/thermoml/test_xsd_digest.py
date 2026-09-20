"""The committed ``ThermoML.xsd`` must match the pinned digest.

Mutation check: flip one byte of the committed XSD and this test goes
red (verified manually; see the PR body's mutation table).
"""

from __future__ import annotations

import hashlib

from app.importers.thermoml import XSD_SHA256
from app.importers.thermoml.validate import XSD_PATH


def test_committed_xsd_matches_pinned_digest():
    data = XSD_PATH.read_bytes()
    assert hashlib.sha256(data).hexdigest() == XSD_SHA256


def test_sha256sums_file_matches_pinned_digest():
    sums_path = XSD_PATH.parent / "SHA256SUMS"
    text = sums_path.read_text(encoding="utf-8")
    assert XSD_SHA256 in text
    assert "ThermoML.xsd" in text
