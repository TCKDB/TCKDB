"""CHEMKIN export must not silently publish transcribed rates as its own.

ADR 0010 gave ``network_solve`` a ``kind`` token so a consumer can tell rates
this database derived (``computed``) from rates read out of a publication
(``reported``). Every JSON read surface carries it, and PR #92 closed the last
one that did not.

A mechanism file cannot carry it the same way. CHEMKIN has no field for
provenance — only ``!`` comments — so the moment network kinetics are added to
this export, a reported solve's PLOG or Chebyshev block would enter a mechanism
file indistinguishable from one TCKDB derived itself, and would then propagate
into every simulation built on that file.

**There is no live gap today**: ``chemkin_serialize`` touches only the classic
``kinetics`` table. This test exists so that when someone adds network kinetics
to the export, the decision is *forced* rather than skipped. It is a tripwire,
not a check on behaviour that exists.
"""

from __future__ import annotations

import re
from pathlib import Path

_SERIALIZER = (
    Path(__file__).parents[3] / "app" / "services" / "scientific_read" / "chemkin_serialize.py"
)

# Any of these appearing in the serializer means network kinetics have started
# reaching mechanism output.
_NETWORK_KINETICS_MARKERS = (
    "NetworkKinetics",
    "network_kinetics",
    "NetworkSolve",
    "network_solve",
)

# If they have, at least one of these must appear too: the origin token has to
# be read before it can be disclosed.
#
# Deliberately NOT a bare ``kind``. The serializer already uses ``model_kind``
# nine times and ``kind`` four times for classic kinetics, so a loose marker
# matches trivially and the guard passes while the hole is wide open -- which
# is exactly what the first version of this test did.
_ORIGIN_MARKERS = (
    "NetworkSolveKind",
    "network_solve_kind",
    "solve.kind",
    "solve_kind",
)


def test_chemkin_export_discloses_transcribed_rates_if_it_emits_them() -> None:
    """Adding network kinetics to CHEMKIN export requires deciding on disclosure.

    The three defensible answers, none of which this test picks:

    1. Annotate — emit the origin as a ``!`` comment beside the rate. The file
       already carries ``! SMILES=… ref=…`` comments per species, so the idiom
       exists and a reader who keeps the comments keeps the provenance.
    2. Exclude by default — omit ``reported`` solves from mechanism export
       unless explicitly requested, on the grounds that a mechanism file is
       consumed by simulators that strip comments.
    3. Gate behind an explicit include token, so asking for transcribed rates
       in a mechanism is a deliberate act with a name.

    What is *not* defensible is emitting them with no disclosure at all, which
    is what happens by default if nobody thinks about it.
    """
    source = _SERIALIZER.read_text()

    # Comments and docstrings mention these words legitimately; only look at code.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    code = re.sub(r'""".*?"""', "", code, flags=re.DOTALL)

    emits_network_kinetics = any(marker in code for marker in _NETWORK_KINETICS_MARKERS)
    if not emits_network_kinetics:
        return  # No live gap: the export still covers only the classic table.

    assert any(marker in code for marker in _ORIGIN_MARKERS), (
        "chemkin_serialize.py now reads network kinetics but never reads the "
        "solve's origin kind, so a `reported` solve's rates would enter a "
        "mechanism file indistinguishable from rates this database derived "
        "(ADR 0010). Decide explicitly: annotate with a `!` comment beside the "
        "rate (the file already emits `! SMILES=... ref=...` per species), "
        "exclude reported solves unless requested, or gate them behind an "
        "include token. Then update this test to assert whichever you chose."
    )
