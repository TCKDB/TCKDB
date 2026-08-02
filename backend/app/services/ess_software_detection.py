"""Detect which electronic-structure program produced a log.

Content-based (not extension-based): the program is identified by its
banner in the output text, so a log named ``.out``, ``.log``, ``.txt`` or
anything else is classified identically. Shared by the parameter-extraction
and single-point-energy paths so the two never disagree on the same bytes.

DB-free — pure text in, program name out.
"""

from __future__ import annotations

import re
from typing import Literal

SoftwareName = Literal["gaussian", "orca", "molpro"]

_GAUSSIAN_MARKERS = re.compile(
    r"Gaussian\s+\d+:|Entering Gaussian System", re.IGNORECASE
)
# Every alternative must name ORCA. A bare ``Program Version X.Y.Z`` was
# previously accepted here, which is not an ORCA fingerprint at all -- it is a
# phrase many programs print. Because ORCA is the last branch, any non-Gaussian,
# non-Molpro log containing that phrase anywhere in its first 8 kB was dispatched
# to the ORCA parser, which would then read charge, multiplicity and energies out
# of a foreign format. Misattribution is worse than silence: returning ``None``
# means the caller records nothing, while a wrong program yields a confident
# wrong answer. Reject, don't guess.
_ORCA_MARKERS = re.compile(
    r"\*\s*O\s+R\s+C\s+A\s*\*"  # the ASCII-art banner, asterisk-anchored
    r"|\bORCA\b",  # any header line naming ORCA (version, citation, footer)
    re.IGNORECASE,
)
_MOLPRO_MARKERS = re.compile(r"PROGRAM SYSTEM MOLPRO", re.IGNORECASE)


def detect_software_from_text(text: str) -> SoftwareName | None:
    """Best-effort sniff for ``"gaussian"``, ``"orca"`` or ``"molpro"``.

    Returns ``None`` when no recognised marker is found. Molpro's banner
    (``***  PROGRAM SYSTEM MOLPRO  ***``) is unambiguous and checked before
    the ORCA fallback so it can never be mistaken for another program.
    """
    head = text[:8000]
    if _GAUSSIAN_MARKERS.search(head):
        return "gaussian"
    if _MOLPRO_MARKERS.search(head):
        return "molpro"
    if _ORCA_MARKERS.search(head):
        return "orca"
    return None
