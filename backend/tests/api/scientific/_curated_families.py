"""The route families the curated ref guard was rolled out by (PR-2a, PR-2b, PR-2c).

The guard itself has no allowlist any more (PR-5): it applies to every scientific route that is not exempt. These
groups survive only so the per-family tests keep selecting the routes they were written for.
"""

from __future__ import annotations

_SCIENTIFIC_BASE = "/api/v1/scientific"

#: The species-side route families. Matched on whole path segments, so ``/species`` does not cover
#: ``/species-entries`` (which is listed on its own).
SPECIES_SIDE_PREFIXES: frozenset[str] = frozenset(
    f"{_SCIENTIFIC_BASE}/{segment}"
    for segment in (
        "statmech",
        "thermo",
        "species-entries",
        "species",
        "transport",
        "conformer-groups",
        "conformer-observations",
        "conformers",
        "calculations",
        "species-calculations",
        "artifacts",
        "geometries",
    )
)

#: The transition-state and reaction route families.
TS_REACTION_PREFIXES: frozenset[str] = frozenset(
    f"{_SCIENTIFIC_BASE}/{segment}"
    for segment in (
        "transition-states",
        "transition-state-entries",
        "reaction-entries",
        "reactions",
        "kinetics",
    )
)

#: The network family, literature, analytics and the provenance vocabulary reads.
NETWORK_PROVENANCE_PREFIXES: frozenset[str] = frozenset(
    f"{_SCIENTIFIC_BASE}/{segment}"
    for segment in (
        "networks",
        "network-solves",
        "network-kinetics",
        "literature",
        "analytics",
        "level-of-theories",
        "frequency-scale-factors",
        "energy-correction-schemes",
        "composite-schemes",
    )
)
