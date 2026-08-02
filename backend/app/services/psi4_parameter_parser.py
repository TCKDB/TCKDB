"""Psi4 output-log parsing.

Deliberately narrow: only the charge and spin multiplicity a Psi4 log
declares are read here. That is what
:mod:`app.services.charge_multiplicity_reconciliation` needs in order to
cross-check an uploader's declared values against the log the run actually
produced.

**Single-point energy is not extracted, on purpose.** A Psi4 log prints many
``Total Energy`` lines — one per SCF, one per post-HF correction, several more
for a composite or MRCC-driven job — and which one is "the" energy depends on
the method that was run. Guessing wrong would silently record a wrong number
into ``calc_sp_result``, which is worse than recording nothing, so the
single-point path declines Psi4 rather than picking a line. Frequencies and
geometries are likewise not wired.

DB-free — pure text in, values out.
"""

from __future__ import annotations

import re

PARSER_VERSION = "psi4-0.1.0"

# ---------------------------------------------------------------------------
# Charge / multiplicity
# ---------------------------------------------------------------------------

# Psi4 states the pair twice per geometry, in two different shapes.
#
# The geometry header, printed once per molecule Psi4 activates:
#
#     Geometry (in Angstrom), charge = 0, multiplicity = 3:
#
# and the SCF module's own summary block a few lines further down:
#
#       Charge       = 0
#       Multiplicity = 3
#
# Leading indentation varies between builds (the 1.4 dev build in the
# fixtures writes the geometry header flush-left, 1.9 indents it four
# spaces), so neither pattern anchors on column position.
_GEOMETRY_HEADER_RE = re.compile(
    r"Geometry\s*\([^)]*\)\s*,\s*charge\s*=\s*(-?\d+)\s*,\s*multiplicity\s*=\s*(-?\d+)",
    re.IGNORECASE,
)

# Anchored to line starts so the two values must be adjacent lines of the
# same block. Without ``^``/``$`` the ``\s+`` between them would happily
# span unrelated intervening output and pair a charge with a multiplicity
# from somewhere else entirely.
_SCF_BLOCK_RE = re.compile(
    r"^[ \t]*Charge[ \t]*=[ \t]*(-?\d+)[ \t]*\r?$\n"
    r"^[ \t]*Multiplicity[ \t]*=[ \t]*(-?\d+)[ \t]*\r?$",
    re.MULTILINE,
)


def parse_all_charge_multiplicity(text: str) -> list[tuple[int, int]]:
    """Return *every* charge/multiplicity pair the log declares.

    Mirrors the Gaussian and ORCA functions of the same name: it reports
    what the log says, without reducing or choosing. A Psi4 log states the
    pair at least twice for a single ordinary job (geometry header plus SCF
    block) and repeats both on every step of an optimisation, so a long run
    yields dozens of entries. Repetition is expected and harmless — the
    entries agree.

    They do not always agree, which is the point of returning all of them.
    A counterpoise or SAPT job activates each fragment as its own molecule
    and prints a header per fragment, so the first pair describes a
    *fragment* rather than the whole system. Callers cross-checking a
    declared value against the log therefore require unanimity before
    trusting anything — see
    :mod:`app.services.charge_multiplicity_reconciliation`, whose
    ``_unanimous`` helper yields *unknown* on disagreement rather than
    fabricating a mismatch against whichever pair happened to come first.

    Values are reported exactly as written, including an unphysical
    multiplicity below 1 (2S+1 >= 1). Discarding it is the reconciler's job,
    not the parser's: it drops the offending *multiplicity* while keeping
    the charge from the same line, which this function could not express by
    dropping the pair. Parsers report what the log said; the reconciliation
    decides what may be compared. Same division as Gaussian and ORCA.

    Returns an empty list for a log that is truncated before the header, is
    not Psi4 at all, or is otherwise unreadable. Absence is not a
    contradiction.
    """
    found: list[tuple[int, int]] = []
    for match in _GEOMETRY_HEADER_RE.finditer(text):
        found.append((int(match.group(1)), int(match.group(2))))
    for match in _SCF_BLOCK_RE.finditer(text):
        found.append((int(match.group(1)), int(match.group(2))))
    return found
