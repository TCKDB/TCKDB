"""xtb version banner -- the only thing TCKDB reads from an xtb output file.

Recognition only. No energy, frequency, Hessian, charge or multiplicity is parsed from an xtb log; those values
keep coming from the upload payload. The banner is read so that DR-0008 can compare the release a depositor
declared with the release the log states.
"""

from __future__ import annotations

import re

#: ``* xtb version 6.7.1 (edcfbbe) compiled by 'conda@...' on 2024-11-25``. The commit hash in parentheses is not
#: recorded as ``build``: it names a source revision, not a build a depositor declares, and treating it as one
#: would turn a version-only declaration into a build contradiction.
_VERSION_RE = re.compile(r"\*\s*xtb version\s+(\d+(?:\.\d+){1,2})\b")


def parse_software_version(text: str) -> dict | None:
    """Extract the xtb version from its banner, or ``None`` when the banner is absent."""
    m = _VERSION_RE.search(text[:8000])
    if m is None:
        return None
    return {"name": "xtb", "version": m.group(1), "build": None, "release_date_raw": None}
