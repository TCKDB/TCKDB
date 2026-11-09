"""xtb version banner; no other value is read from an xtb log."""

from __future__ import annotations

import re

#: ``* xtb version 6.7.1 (edcfbbe) compiled by ...``; the commit hash is not recorded as ``build``.
_VERSION_RE = re.compile(r"\*\s*xtb version\s+(\d+(?:\.\d+){1,2})(?=\s|\()")


def parse_software_version(text: str) -> dict | None:
    """Extract the xtb version from its banner, or ``None`` when the banner is absent."""
    m = _VERSION_RE.search(text[:8000])
    if m is None:
        return None
    return {"name": "xtb", "version": m.group(1), "build": None, "release_date_raw": None}
