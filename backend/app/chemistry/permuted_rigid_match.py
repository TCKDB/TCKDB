"""Find the relabelling that lays one geometry onto another (#679).

Two geometries can be one structure moved rigidly *and* listed with their atoms
in a different order. :func:`find_matching_permutation` looks for a
permutation of the second geometry's atoms that makes it a rigid copy of the
first, using nothing but the geometries: no species, no SMILES, no bond graph.

Why geometry-only: the caller (the no-optimisation duplicate energy rule in
``calculation_levels``) holds stored geometries and calculations, not the
species they belong to, and ``torsion_fingerprint.resolve_atom_mapping`` (which
does take a species) does not answer this question anyway: it maps atoms onto a
species graph, picks one arbitrary member of each symmetry class (the three H
of a CH3 land wherever the isomorphism enumerated them first), and so two
permuted copies of one geometry do not superpose after it.

**Soundness.** A match is returned only after the permuted coordinates pass a
Kabsch RMSD test (proper rotations only, so a mirror image is never matched)
against the caller's tolerance, and only between atoms of the same nuclide.
Everything before that final test is a way of *finding* a candidate
permutation, never of accepting one. A wrong guess costs time, not correctness.

**Search.** Three anchor atoms of the first geometry fix a rigid motion; each
is tried against every atom of the second that could be it. "Could be it" is a
rotation-invariant test: same nuclide and the same sorted list of distances to
every other atom, to within what rounding allows. For an asymmetric molecule
every atom has exactly one candidate and one fit settles it; for a symmetric
one the candidates are the symmetry-equivalent atoms. With the motion fixed,
every atom is assigned to its nearest same-nuclide neighbour and the result is
verified.

**Cost is bounded, and a bound is a refusal to match.** The search is capped on
the number of atoms, on the number of rigid fits tried for one pair, on the
candidate anchor sets examined for one pair (50,000), and (through
:class:`SearchBudget`) on the fits and work units spent across a whole record.
A cheap O(n^2) gate on the sorted list of all pairwise distances sends different
shapes away before anything O(n^3) is built. Past any cap the answer is ``"capped"`` and the
caller treats the pair as *different*, which is the behaviour before this
search existed: a duplicate energy that slips past a cap is accepted, never a
distinct one refused.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

import numpy as np

from app.chemistry.torsion_fingerprint import kabsch_rmsd

Coordinates = Sequence[tuple[float, float, float]]

#: Larger geometries are not searched (reported as capped). The distance-signature
#: table is O(n^3); stored thermo/statmech species are tens of atoms.
MAX_ATOMS = 200

#: Rigid fits tried for one pair of geometries before giving up.
MAX_FITS_PER_PAIR = 256

#: Candidate anchor placements examined (cheap distance checks, no fit) for one pair.
MAX_EXAMINED_PER_PAIR = 50_000

#: Fits a whole record may spend over all of its geometry pairs.
MAX_FITS_PER_RECORD = 4_096

#: How far an atom may sit from its image after the anchors' motion (Angstrom) for the
#: assignment to be tried at all. Only a way to build a candidate; the verdict is the
#: caller's tolerance on the whole geometry.
_ASSIGNMENT_RADIUS = 0.05

#: Float noise allowance added to the pair-distance tolerances. It widens only the
#: candidate filter, never the verdict.
_FLOAT_SLACK = 1e-12

#: ``sin`` of the angle at the first anchor below which a third anchor is not
#: credited with fixing the rotation about the line of the first two.
_MIN_ANCHOR_SINE = 1e-3
_PREFERRED_ANCHOR_SINE = 0.1


#: Work units a whole record may spend. One unit is roughly one array element touched: a pair's
#: distance-list gate costs n(n-1)/2, the per-atom candidate table n^3, a rigid fit 4 n^2. At
#: n = 200 that is 2e4, 8e6 and 1.6e5 units, so the budget pays for a handful of full searches on
#: the largest geometries searched at all and for thousands of gate comparisons.
MAX_WORK_PER_RECORD = 50_000_000


@dataclass
class SearchBudget:
    """What one record may spend on relabelling searches, shared by every pair it compares.

    Two limits: rigid ``fits`` and ``work`` units (the gate and the candidate
    table, which are not fits, are charged here too). Past either, a pair is
    ``"capped"``.
    """

    fits_left: int = field(default_factory=lambda: MAX_FITS_PER_RECORD)
    work_left: int = field(default_factory=lambda: MAX_WORK_PER_RECORD)

    def charge(self, units: int) -> bool:
        """Spend ``units``; ``False`` (and nothing left) when they are not there."""
        if units > self.work_left:
            self.work_left = 0
            return False
        self.work_left -= units
        return True


class PreparedGeometry:
    """One geometry's atoms with the rotation-invariant tables a search reads, built once.

    A record compares every same-size pair of its geometries; the distance
    matrix and the sorted list of all pairwise distances of one geometry are
    the same for every partner, so they are computed on first use and kept.
    """

    def __init__(self, coordinates: Coordinates, nuclides: Sequence[Hashable]) -> None:
        self.n = len(coordinates)
        self.nuclides = list(nuclides)
        self.nuclide_counts: Counter[Hashable] = Counter(self.nuclides)
        self._coordinates = coordinates

    @cached_property
    def array(self) -> np.ndarray:
        return np.asarray(self._coordinates, dtype=np.float64)

    @cached_property
    def distances(self) -> np.ndarray:
        return np.linalg.norm(self.array[:, None, :] - self.array[None, :, :], axis=2)

    @cached_property
    def pair_distances(self) -> np.ndarray:
        """Every pairwise distance, sorted: the cheap shape signature the gate compares."""
        return np.sort(self.distances[np.triu_indices(self.n, 1)])


@dataclass(frozen=True)
class PermutationSearch:
    """Outcome of :func:`find_matching_permutation`.

    ``outcome`` is ``"matched"`` (``permutation[i]`` is the atom of the second
    geometry that is atom ``i`` of the first), ``"different"`` (the search was
    exhaustive and none exists) or ``"capped"`` (a bound was hit first; nothing
    is known and the caller must not treat the pair as the same).
    """

    outcome: Literal["matched", "different", "capped"]
    permutation: tuple[int, ...] | None = None
    fits: int = 0


def _points(array: np.ndarray) -> list[tuple[float, float, float]]:
    return [(float(row[0]), float(row[1]), float(row[2])) for row in array]


def _proper_rotation(moving: np.ndarray, fixed: np.ndarray) -> np.ndarray:
    """The proper rotation ``R`` (determinant +1) minimising ``|moving @ R.T - fixed|``.

    Both inputs are centred. The reflection correction keeps ``R`` a rotation,
    which is what keeps an enantiomer from being fitted onto its mirror image.
    """
    u, _s, vt = np.linalg.svd(moving.T @ fixed)
    sign = 1.0 if np.linalg.det(vt.T @ u.T) >= 0.0 else -1.0
    return vt.T @ np.diag([1.0, 1.0, sign]) @ u.T


def _pick_anchors(coords: np.ndarray, candidate_counts: np.ndarray) -> list[int]:
    """Two or three atoms of the first geometry to anchor the search on.

    Fewest candidates first, since each anchor multiplies the placements to
    try. A third anchor is used when one off the line of the first two exists;
    for a linear molecule two anchors fix everything there is to fix.
    """
    order = [int(i) for i in np.argsort(candidate_counts, kind="stable")]
    first, second = order[0], order[1]
    d1 = coords[second] - coords[first]
    n1 = float(np.linalg.norm(d1))
    best: tuple[float, int] | None = None
    for index in order[2:]:
        d2 = coords[index] - coords[first]
        n2 = float(np.linalg.norm(d2))
        if n1 == 0.0 or n2 == 0.0:
            continue
        sine = float(np.linalg.norm(np.cross(d1, d2))) / (n1 * n2)
        if sine >= _PREFERRED_ANCHOR_SINE:
            return [first, second, index]
        if best is None or sine > best[0]:
            best = (sine, index)
    if best is not None and best[0] >= _MIN_ANCHOR_SINE:
        return [first, second, best[1]]
    return [first, second]


def find_matching_permutation(
    coords_a: Coordinates,
    nuclides_a: Sequence[Hashable],
    coords_b: Coordinates,
    nuclides_b: Sequence[Hashable],
    *,
    tolerance: float,
    budget: SearchBudget | None = None,
) -> PermutationSearch:
    """Find a relabelling of ``b`` that makes it a rigid copy of ``a``.

    :param coords_a: Atom positions of the first geometry, in its order.
    :param nuclides_a: One hashable per atom, element plus isotope mass number:
        atoms are only ever matched to an atom with an equal entry, so a ``2H``
        is never taken for an ``1H``.
    :param coords_b: Atom positions of the second geometry, in its order.
    :param nuclides_b: As ``nuclides_a``.
    :param tolerance: Largest Kabsch RMSD (Angstrom) at which the relabelled
        ``b`` still counts as the same structure as ``a``; the caller's rounding
        bound. Proper rotations only: a mirror image is not a match.
    :param budget: The record's remaining work, decremented as it is spent.
    :returns: A :class:`PermutationSearch`; only ``"matched"`` means "same".
    """
    return match_prepared(
        PreparedGeometry(coords_a, nuclides_a), PreparedGeometry(coords_b, nuclides_b), tolerance=tolerance, budget=budget
    )


def match_prepared(
    first: PreparedGeometry,
    second: PreparedGeometry,
    *,
    tolerance: float,
    budget: SearchBudget | None = None,
) -> PermutationSearch:
    """:func:`find_matching_permutation` on geometries whose tables are already built (or shared)."""
    n = first.n
    if n != second.n:
        return PermutationSearch("different")
    if first.nuclide_counts != second.nuclide_counts:
        return PermutationSearch("different")
    if n < 2:
        # One atom has no order to differ in; the caller's own comparison covers it.
        return PermutationSearch("different")
    if n > MAX_ATOMS:
        return PermutationSearch("capped")

    # If the Kabsch RMSD of the relabelled copy is within `tolerance`, every atom is within
    # sqrt(n) * tolerance of its image under the best motion, so every pairwise distance is within
    # 2 sqrt(n) * tolerance. That bound, and not a per-atom one, is what the RMSD verdict allows, so
    # the filters below never refuse a pair the verdict would accept.
    pair_slack = 2.0 * math.sqrt(n) * tolerance + _FLOAT_SLACK

    # Gate, O(n^2): the sorted list of all pairwise distances is the same for a rigid copy in any
    # atom order (sorting is 1-Lipschitz in the sup norm). Different shapes stop here, before the
    # O(n^3) candidate table below, which is what a record of many large distinct geometries would
    # otherwise pay for every pair.
    if budget is not None and not budget.charge(n * (n - 1) // 2):
        return PermutationSearch("capped")
    if float(np.abs(first.pair_distances - second.pair_distances).max()) > pair_slack:
        return PermutationSearch("different")

    if budget is not None and not budget.charge(n**3):
        return PermutationSearch("capped")
    a, b = first.array, second.array
    dist_a, dist_b = first.distances, second.distances
    sorted_a = np.sort(dist_a, axis=1)
    sorted_b = np.sort(dist_b, axis=1)
    codes: dict[Hashable, int] = {}
    code_a = np.array([codes.setdefault(x, len(codes)) for x in first.nuclides])
    code_b = np.array([codes.setdefault(x, len(codes)) for x in second.nuclides])
    compatible = np.zeros((n, n), dtype=bool)
    for i in range(n):
        same_nuclide = code_b == code_a[i]
        close = np.abs(sorted_b - sorted_a[i]).max(axis=1) <= pair_slack
        compatible[i] = same_nuclide & close
    counts = compatible.sum(axis=1)
    if (counts == 0).any():
        return PermutationSearch("different")

    anchors = _pick_anchors(a, counts)
    candidates = [np.flatnonzero(compatible[i]).tolist() for i in anchors]

    fits = 0
    examined = 0
    rows = np.arange(n)
    a_list = _points(a)
    for b0 in candidates[0]:
        for b1 in candidates[1]:
            examined += 1
            if examined > MAX_EXAMINED_PER_PAIR:
                return PermutationSearch("capped", fits=fits)
            if b1 == b0 or abs(dist_b[b0, b1] - dist_a[anchors[0], anchors[1]]) > pair_slack:
                continue
            thirds = candidates[2] if len(anchors) == 3 else [None]
            for b2 in thirds:
                examined += 1
                if examined > MAX_EXAMINED_PER_PAIR:
                    return PermutationSearch("capped", fits=fits)
                picked = [b0, b1]
                if b2 is not None:
                    if b2 in (b0, b1):
                        continue
                    if (
                        abs(dist_b[b0, b2] - dist_a[anchors[0], anchors[2]]) > pair_slack
                        or abs(dist_b[b1, b2] - dist_a[anchors[1], anchors[2]]) > pair_slack
                    ):
                        continue
                    picked.append(b2)
                if fits >= MAX_FITS_PER_PAIR or (budget is not None and budget.fits_left <= 0):
                    return PermutationSearch("capped", fits=fits)
                if budget is not None and not budget.charge(4 * n * n):
                    return PermutationSearch("capped", fits=fits)
                fits += 1
                if budget is not None:
                    budget.fits_left -= 1

                fixed_pts = a[anchors]
                moving_pts = b[picked]
                centre_a = fixed_pts.mean(axis=0)
                centre_b = moving_pts.mean(axis=0)
                rotation = _proper_rotation(moving_pts - centre_b, fixed_pts - centre_a)
                moved = (b - centre_b) @ rotation.T + centre_a
                gaps = np.linalg.norm(a[:, None, :] - moved[None, :, :], axis=2)
                gaps = np.where(compatible, gaps, np.inf)
                nearest = gaps.argmin(axis=1)
                if (gaps[rows, nearest] > _ASSIGNMENT_RADIUS).any() or np.unique(nearest).size != n:
                    continue
                permuted = _points(b[nearest])
                if kabsch_rmsd(a_list, permuted) <= tolerance:
                    return PermutationSearch("matched", tuple(int(j) for j in nearest), fits)
    return PermutationSearch("different", fits=fits)
