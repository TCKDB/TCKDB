"""Basins as connected components of structures.

:mod:`app.chemistry.basin_matching` answers one question about two structures: do they share a basin? This module
answers the question about a *set*: which structures belong together, which one stands for them, and how far the
group stretches.

Why connected components
------------------------
Comparing a new structure with one stored reference makes the partition depend on the order of deposition: three
structures 18 degrees apart (0, 18, 36) with a 20 degree tolerance give three different partitions from six orders,
because 0 and 36 do not match each other but both match 18. A basin here is a connected component of the graph whose
edges are "the matcher says these two share a basin". The component of a set is a function of the set, so no order
of deposition can change it, and no reference has to be chosen to define it.

Only *defining* members take part (the caller decides which; see :mod:`app.services.conformer_basin_store`). A
component can chain: every member linked to the next while the ends are further apart than the tolerance. That is a
*wide basin* (:attr:`BasinShape.is_clique` is false). It is reported, never split, because any automatic cut would
reintroduce an order dependence or an arbitrary threshold.

The representative is the *medoid*: the member with the smallest summed best-mapping distance to the others (ties:
lowest key). It is derived from the set, so it is not identity and can change as members arrive. Energy appears
nowhere here.

Cost: one comparison per pair, 0.15 to 0.66 ms each, cached in :class:`PairTable` for the lifetime of the table.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass

from app.chemistry.basin_matching import (
    BasinComparison,
    BasinMatchParams,
    compare_basins_symmetric,
    comparison_distance,
)

Coords = Sequence[tuple[float, float, float]]


@dataclass(frozen=True)
class BasinShape:
    """What a component looks like, derived from its members alone."""

    #: Key of the member that minimises the summed best-mapping distance to the others (ties: the lowest key).
    medoid_key: int
    #: Whether every pair of members matches. ``False`` is a wide basin.
    is_clique: bool
    #: The largest per-rotor torsion difference between any two members under their best mapping, in degrees.
    span_deg: float


class PairTable:
    """Symmetric, cached pairwise comparisons of the structures of one species.

    ``coords`` maps a member key (an observation id) to its mapped coordinates. Each pair is evaluated once, with
    the lower key first, so the result does not depend on the order the pair is asked for in.
    """

    def __init__(self, smiles: str, params: BasinMatchParams, coords: dict[int, Coords]) -> None:
        self.smiles = smiles
        self.params = params
        self._coords = coords
        self._cache: dict[tuple[int, int], BasinComparison] = {}

    def keys(self) -> list[int]:
        return sorted(self._coords)

    def compare(self, a: int, b: int) -> BasinComparison:
        lo, hi = (a, b) if a < b else (b, a)
        cached = self._cache.get((lo, hi))
        if cached is None:
            cached = compare_basins_symmetric(
                self.smiles, list(self._coords[lo]), list(self._coords[hi]), self.params
            )
            self._cache[(lo, hi)] = cached
        return cached

    def linked(self, a: int, b: int) -> bool:
        return a == b or self.compare(a, b).same_basin


def connected_components(table: PairTable, keys: Sequence[int]) -> list[list[int]]:
    """The connected components of ``keys`` under the table's links, each sorted, ordered by their lowest key."""
    ordered = sorted(set(keys))
    parent: dict[int, int] = {k: k for k in ordered}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in itertools.combinations(ordered, 2):
        if find(a) != find(b) and table.linked(a, b):
            # the lower root wins, so the representative of a component is its lowest key whatever the order
            ra, rb = find(a), find(b)
            parent[max(ra, rb)] = min(ra, rb)
    grouped: dict[int, list[int]] = {}
    for k in ordered:
        grouped.setdefault(find(k), []).append(k)
    return [grouped[root] for root in sorted(grouped)]


def basin_shape(table: PairTable, keys: Sequence[int]) -> BasinShape:
    """The medoid, clique flag and span of one component."""
    members = sorted(set(keys))
    if not members:
        raise ValueError("a basin has at least one member")
    totals: dict[int, float] = dict.fromkeys(members, 0.0)
    is_clique = True
    span = 0.0
    for a, b in itertools.combinations(members, 2):
        comparison = table.compare(a, b)
        distance = comparison_distance(comparison)
        totals[a] += distance
        totals[b] += distance
        is_clique = is_clique and comparison.same_basin
        if comparison.torsion_deltas:
            span = max(span, max(comparison.torsion_deltas))
    medoid = min(members, key=lambda k: (round(totals[k], 9), k))
    return BasinShape(medoid_key=medoid, is_clique=is_clique, span_deg=round(span, 4))
