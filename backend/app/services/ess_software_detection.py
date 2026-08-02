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

SoftwareName = Literal["gaussian", "orca", "molpro", "psi4"]

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
    r"|\bORCA\b"  # any header line naming ORCA (version, citation, footer)
    # ORCA's version line carries a ``-  RELEASE  -`` suffix. That pairing is an
    # ORCA convention and is kept, unlike the bare ``Program Version X.Y.Z`` it
    # replaced -- a phrase many programs print, which made ORCA (the last
    # branch) a catch-all for every unrecognised log.
    r"|Program\s+Version\s+[\d.]+\s*-+\s*RELEASE",
    re.IGNORECASE,
)
_MOLPRO_MARKERS = re.compile(r"PROGRAM SYSTEM MOLPRO", re.IGNORECASE)
# Both alternatives are anchored on the literal program name. The banner
# title is Psi4's own tagline and the start line is the run header it prints
# immediately below it; no other program emits either string. Deliberately
# *not* anchored on a version line: Psi4 writes ``Psi4 1.9.1 release`` for a
# tagged build but only ``Psi4 1.4a1.dev75`` for a development build, so a
# ``release``-bearing pattern would silently miss dev builds (see
# ``tests/fixtures/psi4/io_error_truncated.out``).
_PSI4_MARKERS = re.compile(
    r"Psi4:\s*An Open-Source Ab Initio Electronic Structure Package"
    r"|Psi4 started on:",
    re.IGNORECASE,
)


def detect_software_from_text(text: str) -> SoftwareName | None:
    """Best-effort sniff for ``"gaussian"``, ``"orca"``, ``"molpro"`` or ``"psi4"``.

    Returns ``None`` when no recognised marker is found. Molpro's banner
    (``***  PROGRAM SYSTEM MOLPRO  ***``) and Psi4's are unambiguous and
    checked before the ORCA fallback so they can never be mistaken for
    another program: ORCA is matched partly by a generic
    ``Program Version X.Y.Z`` line, so anything with a distinctive banner
    must be ruled out first.

    Identifying a program here does **not** imply every extraction path
    supports it. Psi4 is wired for charge/multiplicity only; the
    single-point-energy, Hessian and parameter-extraction paths each decline
    it explicitly rather than falling through to another program's parser.
    """
    head = text[:8000]
    if _GAUSSIAN_MARKERS.search(head):
        return "gaussian"
    if _MOLPRO_MARKERS.search(head):
        return "molpro"
    if _PSI4_MARKERS.search(head):
        return "psi4"
    if _ORCA_MARKERS.search(head):
        return "orca"
    return None
