"""Symmetry-aware conformer-basin matching.

Two geometries of one species belong to the same torsional basin when **some
relabelling of the molecule that its own graph allows** puts every rotor
within that rotor's tolerance. This module is that test.

Why it exists
-------------
The earlier rule canonicalised each geometry before comparing: of the atom
mappings the molecular graph allowed, it kept the one whose list of
quantised torsion bins sorted lowest. That choice is discontinuous. A planar
aryl-nitro, aryl-aldehyde or aryl-ether group twisted +0.5 degrees and -0.5
degrees is one conformer, but its two ring-side terminals trade places at the
0/360 wrap, so the lexicographic minimum picked different reference atoms and
the two were assigned to different basins. Sampling the same minimum with a
tiny perturbation therefore forked it, and ESS output routinely prints
179.99 and -179.99 for the same planar geometry.

Here nothing is canonicalised. The comparison asks an existence question over
the molecule's symmetries, so a tiny perturbation cannot change the answer:
the symmetry that matched before still matches.

Definition
----------
Let ``sigma`` range over the automorphisms of the molecular graph (element
and bonded neighbours; bond orders are ignored on purpose, so the two oxygens
of a nitro group or a carboxylate are interchangeable, exactly as they are
when the deposited geometry is mapped onto the species graph). For each rotor
slot ``i`` let ``delta_i(sigma)`` be the circular difference, after the slot's
symmetry fold, between the torsion of geometry ``A`` on the slot's atoms and
the torsion of geometry ``B`` on the slot's image under ``sigma``. Then::

    same basin  <=>  there exists sigma with  delta_i(sigma) <= tolerance_i  for every slot i

This is an existence test, not "minimise the largest difference": the
tolerances differ per slot, so "the largest difference" has no single unit.

Hydrogens
---------
Hydrogens are not enumerated as part of ``sigma``; that is what makes the
search bounded.

* Methyl hydrogens never need a permutation: a methyl rotor is measured
  modulo 120 degrees, which already identifies its three hydrogens.
* The two hydrogens of an ``-NH2`` or a radical ``-CH2`` top are compared **as a pair**. Both hydrogens'
  torsions are measured, and the top matches when SOME assignment of the two hydrogens of one structure to the
  two of the other puts BOTH torsions within tolerance. The assignment is the one choice a top has, so it is
  enumerated (two of them) and applied to both hydrogens at once. Letting either hydrogen stand for the slot on
  its own (what version 2 did) merged trans and gauche ethylamine, which share one N-H position and differ in
  the other. A planar top (amide, aniline, a radical -CH2) still matches its swapped form, because the swapped
  assignment pairs each hydrogen with its mirror partner; no special fold is needed.
* A one-hydrogen top (O-H, S-H, O-O-H, N-O-H) has nothing to permute.

The cap
-------
The number of heavy-atom automorphisms can blow up (a molecule carrying
several CF3 or tert-butyl tops has thousands). Enumeration stops at
``max_symmetry_mappings``, always keeping the identity first and the rest in
a fixed depth-first order over the graph, so the truncated set is the same
for every caller. A truncated search can only say "different basin" for a pair
that needed a mapping beyond the cap, never "same basin" wrongly, so the
failure mode is a split, not a merge. ``cap_hit`` is reported so it can be
recorded.

Above the cap the answer can depend on the order of atoms in a deposited
file: which mappings fall inside the first N depends on how the deposited
atoms were mapped onto the species graph, and that choice is arbitrary among
equivalent atoms. Below the cap nothing depends on it. A molecule that hits the
cap says so on every observation (``symmetry_cap_hit`` in the stored
fingerprint).

Rigid molecules (no rotors) are compared by Kabsch RMSD, and take the minimum
over the same mappings and the same cap, with every ordering of the hydrogens
of each atom, for the same reason: the atom order of the coordinates is arbitrary
among equivalent atoms (ethane's two ends, a methyl's hydrogens).
"""

from __future__ import annotations

import functools
import itertools
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from rdkit import Chem

from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.torsion_fingerprint import RotorSlot, identify_rotor_slots, kabsch_rmsd

#: The only matching rule this module implements. A scheme row names it in its parameters so that a
#: future rule is a new value here and a new scheme version, not a silent change of meaning.
MATCHING_RULE_SYMMETRY_EXISTENCE = "symmetry_existence"

_EPS = 1e-9


@dataclass(frozen=True)
class BasinMatchParams:
    """The numbers and switches of one scheme version, as basin matching reads them."""

    tolerance_heavy_deg: float
    tolerance_terminal_h_deg: float
    exclude_methyl: bool
    exclude_terminal_noisy: bool
    methyl_symmetry_fold: int
    max_symmetry_mappings: int
    rigid_use_rmsd: bool
    rmsd_threshold_angstrom: float | None

    @classmethod
    def from_scheme_params(cls, params: dict[str, Any]) -> BasinMatchParams:
        rule = params.get("matching_rule", MATCHING_RULE_SYMMETRY_EXISTENCE)
        if rule != MATCHING_RULE_SYMMETRY_EXISTENCE:
            raise ValueError(f"Unsupported conformer matching_rule: {rule!r}")
        use_rmsd = bool(params.get("rigid_fallback_use_rmsd", True))
        return cls(
            tolerance_heavy_deg=float(params["torsion_tolerance_heavy_degrees"]),
            tolerance_terminal_h_deg=float(params["torsion_tolerance_terminal_h_degrees"]),
            exclude_methyl=bool(params.get("exclude_methyl_rotors", False)),
            exclude_terminal_noisy=bool(params.get("exclude_terminal_noisy_rotors", False)),
            methyl_symmetry_fold=int(params.get("methyl_symmetry_fold", 3)),
            max_symmetry_mappings=int(params["max_symmetry_mappings"]),
            rigid_use_rmsd=use_rmsd,
            rmsd_threshold_angstrom=float(params["rmsd_threshold_angstrom"]) if use_rmsd else None,
        )

    def tolerance_for(self, slot: RotorSlot) -> float:
        return self.tolerance_terminal_h_deg if slot.is_terminal_h else self.tolerance_heavy_deg


@dataclass(frozen=True)
class SymmetryModel:
    """Everything about one species that basin matching needs and that does not depend on a geometry."""

    smiles: str
    slots: tuple[RotorSlot, ...]
    #: ``(S, 4)`` atom indices of each slot's own torsion.
    quartets: np.ndarray
    #: ``(S, 4)``: the same torsion measured through the other hydrogen of a two-hydrogen top (equal to
    #: ``quartets`` for every other slot).
    quartets_alt: np.ndarray
    #: ``(M, S, 2, 4)``: the image of each slot's torsion under each automorphism, with the two
    #: hydrogen choices of a two-hydrogen top as the third axis (equal for every other slot).
    images: np.ndarray
    periods: np.ndarray
    tolerances: np.ndarray
    n_mappings: int
    cap_hit: bool
    #: Heavy atoms in index order, the image of each under each automorphism (``(M, H)`` atom indices) and
    #: the hydrogens bonded to each heavy atom. Used by the rigid-molecule comparison, which has no rotors to
    #: measure and compares coordinates instead.
    heavy_atoms: tuple[int, ...] = ()
    heavy_images: np.ndarray | None = None
    hydrogens: tuple[tuple[int, ...], ...] = ()

    @property
    def rotor_count(self) -> int:
        return len(self.slots)


def _heavy_automorphisms(
    adjacency: dict[int, frozenset[int]],
    colour: dict[int, int],
    *,
    limit: int,
) -> tuple[list[dict[int, int]], bool]:
    """All automorphisms of the heavy-atom graph, identity first, up to ``limit``.

    Returns ``(automorphisms, truncated)``. ``truncated`` is true only when at least one more
    automorphism exists than were returned.
    """
    nodes = sorted(adjacency)
    identity = {v: v for v in nodes}
    found: list[dict[int, int]] = [identity]
    truncated = False

    # Order the search so each atom after the first of its component is next to a mapped one: that lets
    # the adjacency check prune early.
    order: list[int] = []
    seen: set[int] = set()
    for root in nodes:
        if root in seen:
            continue
        queue = [root]
        seen.add(root)
        while queue:
            v = queue.pop(0)
            order.append(v)
            for w in sorted(adjacency[v]):
                if w not in seen:
                    seen.add(w)
                    queue.append(w)

    by_colour: dict[int, list[int]] = defaultdict(list)
    for v in nodes:
        by_colour[colour[v]].append(v)

    mapping: dict[int, int] = {}
    used: set[int] = set()

    def consistent(v: int, w: int) -> bool:
        mapped_neighbours = [u for u in adjacency[v] if u in mapping]
        image_neighbours = [x for x in adjacency[w] if x in used]
        if len(mapped_neighbours) != len(image_neighbours):
            return False
        return all(mapping[u] in adjacency[w] for u in mapped_neighbours)

    def search(k: int) -> bool:
        """Depth-first search. Returns False once the limit is exceeded, to unwind."""
        nonlocal truncated
        if k == len(order):
            candidate = dict(mapping)
            if candidate != identity:
                if len(found) >= limit:
                    truncated = True
                    return False
                found.append(candidate)
            return True
        v = order[k]
        for w in by_colour[colour[v]]:
            if w in used or not consistent(v, w):
                continue
            mapping[v] = w
            used.add(w)
            ok = search(k + 1)
            del mapping[v]
            used.discard(w)
            if not ok:
                return False
        return True

    search(0)
    return found, truncated


def _colour_classes(adjacency: dict[int, frozenset[int]], label: dict[int, tuple]) -> dict[int, int]:
    """Colour refinement: atoms with different surroundings never share a colour."""
    colour = dict.fromkeys(adjacency, 0)
    table: dict[tuple, int] = {}
    for v in sorted(adjacency):
        colour[v] = table.setdefault(label[v], len(table))
    while True:
        signatures = {v: (colour[v], tuple(sorted(colour[w] for w in adjacency[v]))) for v in adjacency}
        table = {}
        refined = {}
        for v in sorted(adjacency):
            refined[v] = table.setdefault(signatures[v], len(table))
        if len(set(refined.values())) == len(set(colour.values())):
            return refined
        colour = refined


@functools.lru_cache(maxsize=64)
def build_symmetry_model(
    smiles: str,
    exclude_methyl: bool,
    exclude_terminal_noisy: bool,
    methyl_symmetry_fold: int,
    tolerance_heavy_deg: float,
    tolerance_terminal_h_deg: float,
    max_symmetry_mappings: int,
) -> SymmetryModel:
    """Build (and cache) the rotor slots and bounded symmetry set of one species graph.

    :raises ValueError: if ``smiles`` cannot be parsed.
    """
    parsed = Chem.MolFromSmiles(smiles)
    if parsed is None:
        raise ValueError(f"Cannot parse SMILES for basin matching: {smiles!r}")
    mol = Chem.AddHs(parsed)
    slots = tuple(
        identify_rotor_slots(
            mol,
            exclude_methyl=exclude_methyl,
            exclude_terminal_noisy=exclude_terminal_noisy,
            methyl_symmetry_fold=methyl_symmetry_fold,
        )
    )

    heavy = [a.GetIdx() for a in mol.GetAtoms() if resolve_element_symbol(a.GetSymbol()) != "H"]
    heavy_set = set(heavy)
    hydrogens: dict[int, list[int]] = {x: [] for x in heavy}
    adjacency: dict[int, frozenset[int]] = {}
    for x in heavy:
        atom = mol.GetAtomWithIdx(x)
        hydrogens[x] = sorted(n.GetIdx() for n in atom.GetNeighbors() if n.GetIdx() not in heavy_set)
        adjacency[x] = frozenset(n.GetIdx() for n in atom.GetNeighbors() if n.GetIdx() in heavy_set)
    # Element and hydrogen count stand in for the explicit hydrogens; bond orders and charges are left out
    # so resonance-equivalent atoms (nitro oxygens, carboxylate oxygens) are interchangeable.
    label = {x: (resolve_element_symbol(mol.GetAtomWithIdx(x).GetSymbol()), len(hydrogens[x])) for x in heavy}
    colour = _colour_classes(adjacency, label)

    automorphisms, truncated = _heavy_automorphisms(adjacency, colour, limit=max(1, max_symmetry_mappings))

    parent_of_h = {h: x for x, hs in hydrogens.items() for h in hs}

    def image_options(atom: int, sigma: dict[int, int], slot: RotorSlot) -> tuple[int, int]:
        if atom in heavy_set:
            image = sigma[atom]
            return image, image
        hs = hydrogens[sigma[parent_of_h[atom]]]
        # Methyl hydrogens fold away, a single hydrogen is unique; only a two-hydrogen top has a real choice.
        return (hs[0], hs[1]) if slot.top_kind == "two_h" else (hs[0], hs[0])

    n_slots = len(slots)
    quartets = np.zeros((n_slots, 4), dtype=np.int32)
    quartets_alt = np.zeros((n_slots, 4), dtype=np.int32)
    images = np.zeros((len(automorphisms), n_slots, 2, 4), dtype=np.int32)
    for s, slot in enumerate(slots):
        quartet = (slot.terminal_a, slot.bond_begin, slot.bond_end, slot.terminal_d)
        quartets[s] = quartet
        # A two-hydrogen top's quartet holds exactly one of its hydrogens; the other is its alternative.
        alternative = slot.alt_terminal_h
        quartets_alt[s] = tuple(
            alternative if alternative is not None and slot.top_kind == "two_h" and atom not in heavy_set else atom
            for atom in quartet
        )
        for m, sigma in enumerate(automorphisms):
            first = [0, 0, 0, 0]
            second = [0, 0, 0, 0]
            for position, atom in enumerate(quartet):
                a, b = image_options(atom, sigma, slot)
                first[position], second[position] = a, b
            images[m, s, 0] = first
            images[m, s, 1] = second

    periods = np.array([360.0 / slot.symmetry_fold if slot.symmetry_fold > 1 else 360.0 for slot in slots])
    tolerances = np.array(
        [tolerance_terminal_h_deg if slot.is_terminal_h else tolerance_heavy_deg for slot in slots]
    )
    return SymmetryModel(
        smiles=smiles,
        slots=slots,
        quartets=quartets,
        quartets_alt=quartets_alt,
        images=images,
        periods=periods,
        tolerances=tolerances,
        n_mappings=len(automorphisms),
        cap_hit=truncated,
        heavy_atoms=tuple(heavy),
        heavy_images=np.array([[sigma[x] for x in heavy] for sigma in automorphisms], dtype=np.int32),
        hydrogens=tuple(tuple(hydrogens[x]) for x in heavy),
    )


def symmetry_model_for(smiles: str, params: BasinMatchParams) -> SymmetryModel:
    """The cached :class:`SymmetryModel` of ``smiles`` under ``params``."""
    return build_symmetry_model(
        smiles,
        params.exclude_methyl,
        params.exclude_terminal_noisy,
        params.methyl_symmetry_fold,
        params.tolerance_heavy_deg,
        params.tolerance_terminal_h_deg,
        params.max_symmetry_mappings,
    )


def _dihedrals_deg(coords: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Dihedral angles in ``[0, 360)`` for atom-index quartets of any leading shape ``(..., 4)``."""
    p0, p1, p2, p3 = (coords[indices[..., k]] for k in range(4))
    b0 = p1 - p0
    b1 = p2 - p1
    b2 = p3 - p2
    norm = np.linalg.norm(b1, axis=-1, keepdims=True)
    unit = np.divide(b1, norm, out=np.zeros_like(b1), where=norm > 0)
    v = b0 - np.sum(b0 * unit, axis=-1, keepdims=True) * unit
    w = b2 - np.sum(b2 * unit, axis=-1, keepdims=True) * unit
    x = np.sum(v * w, axis=-1)
    y = np.sum(np.cross(unit, v) * w, axis=-1)
    return np.degrees(np.arctan2(y, x)) % 360.0


#: A heavy atom's hydrogens are permuted exhaustively when there are at most this many orderings per mapping
#: (3 hydrogens on each of two atoms is 36); beyond it the hydrogens keep their index order, deterministically.
_MAX_HYDROGEN_ORDERINGS = 720


def _rigid_min_rmsd(model: SymmetryModel, a: np.ndarray, b: np.ndarray, threshold: float | None) -> float:
    """Smallest Kabsch RMSD between ``a`` and ``b`` over the molecule's symmetries.

    A rigid molecule has no rotors to measure, so it is compared by coordinates. The coordinates are in the
    atom order the atom mapping happened to choose, and that choice is arbitrary for equivalent atoms (the
    hydrogens of a methyl, the two ends of ethane), so the comparison takes the minimum over the same symmetry
    mappings the rotor comparison uses, with every ordering of the hydrogens of each atom, under the same cap.
    Stops at the first mapping within ``threshold`` (the order is fixed, so this is deterministic).
    """
    assert model.heavy_images is not None
    heavy_position = {atom: i for i, atom in enumerate(model.heavy_atoms)}
    n_atoms = len(a)
    best = math.inf
    for image in model.heavy_images:
        base = np.arange(n_atoms)
        orderings: list[list[tuple[tuple[int, ...], tuple[int, ...]]]] = []
        for i, atom in enumerate(model.heavy_atoms):
            base[atom] = image[i]
            own = model.hydrogens[i]
            target = model.hydrogens[heavy_position[int(image[i])]]
            if len(own) > 1:
                orderings.append([(own, order) for order in itertools.permutations(target)])
            elif own:
                base[own[0]] = target[0]
        count = 1
        for options in orderings:
            count *= len(options)
        if count > _MAX_HYDROGEN_ORDERINGS:
            orderings = [options[:1] for options in orderings]
        for combo in itertools.product(*orderings):
            permutation = base.copy()
            for own, order in combo:
                for hydrogen, image_hydrogen in zip(own, order, strict=True):
                    permutation[hydrogen] = image_hydrogen
            rmsd = kabsch_rmsd(a.tolist(), b[permutation].tolist())
            best = min(best, rmsd)
            if threshold is not None and best <= threshold:
                return best
    return best


@dataclass
class BasinComparison:
    """Outcome of comparing two geometries of one species for basin identity.

    :param same_basin: Whether a symmetry mapping puts every rotor within its tolerance (or, for a
        rigid molecule, whether the RMSD is within threshold).
    :param torsion_deltas: Per-rotor circular differences under the closest valid mapping, or under
        the mapping with the smallest total difference when none is valid (diagnostic).
    :param kabsch_rmsd: Same-order Kabsch RMSD when coordinates were given.
    :param is_rigid: Whether the molecule has no rotors.
    :param symmetry_mappings: How many automorphisms were searched.
    :param symmetry_cap_hit: Whether the search was cut short by the cap.
    """

    same_basin: bool
    torsion_deltas: list[float]
    kabsch_rmsd: float | None
    is_rigid: bool
    symmetry_mappings: int
    symmetry_cap_hit: bool

    @property
    def total_delta(self) -> float:
        return float(sum(self.torsion_deltas))


def compare_basins(
    smiles: str,
    coords_a: list[tuple[float, float, float]],
    coords_b: list[tuple[float, float, float]] | None,
    params: BasinMatchParams,
) -> BasinComparison:
    """Compare two geometries of one species, both in species-canonical atom order.

    :param smiles: The species graph, which fixes the atom order the coordinates are in.
    :param coords_a: Coordinates of the first geometry (``mapped_coords`` of an atom mapping).
    :param coords_b: Coordinates of the second geometry, or ``None``.
    :param params: Tolerances and switches of the scheme version.
    """
    model = symmetry_model_for(smiles, params)
    rmsd: float | None = None
    if coords_a is not None and coords_b is not None and len(coords_a) == len(coords_b):
        rmsd = kabsch_rmsd(coords_a, coords_b)

    if model.rotor_count == 0:
        if rmsd is not None and params.rmsd_threshold_angstrom is not None:
            minimum = _rigid_min_rmsd(
                model,
                np.asarray(coords_a, dtype=np.float64),
                np.asarray(coords_b, dtype=np.float64),
                params.rmsd_threshold_angstrom,
            )
            return BasinComparison(
                minimum <= params.rmsd_threshold_angstrom, [], minimum, True, model.n_mappings, model.cap_hit
            )
        # No RMSD data: nothing distinguishes the two, so they share a basin.
        return BasinComparison(True, [], rmsd, True, model.n_mappings, model.cap_hit)

    if coords_b is None or coords_a is None or len(coords_a) != len(coords_b):
        return BasinComparison(False, [], rmsd, False, model.n_mappings, model.cap_hit)

    a = np.asarray(coords_a, dtype=np.float64)
    b = np.asarray(coords_b, dtype=np.float64)
    period = model.periods[None, :, None]
    first = _dihedrals_deg(a, model.quartets) % model.periods  # (S,)
    second = _dihedrals_deg(a, model.quartets_alt) % model.periods  # (S,): the other hydrogen of a two-hydrogen top
    image = _dihedrals_deg(b, model.images) % period  # (M, S, 2): the image's two hydrogens (equal outside a pair)

    def circular(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        difference = np.abs(x - y)
        return np.minimum(difference, period - difference)

    # A two-hydrogen top is compared as a pair under one assignment of its hydrogens: both must be within
    # tolerance. For every other slot both hydrogens are the same atom, so the two assignments coincide and
    # this is the plain circular difference.
    straight = np.maximum(circular(first[None, :, None], image[..., 0:1]), circular(second[None, :, None], image[..., 1:2]))
    swapped = np.maximum(circular(first[None, :, None], image[..., 1:2]), circular(second[None, :, None], image[..., 0:1]))
    delta = np.minimum(straight, swapped)[..., 0]  # (M, S)
    valid = np.all(delta <= model.tolerances[None, :] + _EPS, axis=1)
    totals = delta.sum(axis=1)
    if valid.any():
        best = int(np.argmin(np.where(valid, totals, math.inf)))
    else:
        best = int(np.argmin(totals))
    return BasinComparison(
        bool(valid.any()),
        [float(d) for d in delta[best]],
        rmsd,
        False,
        model.n_mappings,
        model.cap_hit,
    )


def comparison_distance(comparison: BasinComparison) -> float:
    """The best-mapping distance of a comparison: summed torsion differences, or the RMSD of a rigid molecule."""
    # Rounded, so two distances that differ only by floating-point noise tie, and a tie goes to the older group.
    if comparison.is_rigid:
        return round(float(comparison.kabsch_rmsd), 6) if comparison.kabsch_rmsd is not None else 0.0
    return round(comparison.total_delta, 6)


def compare_basins_symmetric(
    smiles: str,
    coords_a: list[tuple[float, float, float]],
    coords_b: list[tuple[float, float, float]] | None,
    params: BasinMatchParams,
) -> BasinComparison:
    """``compare_basins`` evaluated both ways round, so linkage between two structures is symmetric.

    The symmetries of a molecule form a group, so below the cap ``a`` matches ``b`` exactly when ``b`` matches
    ``a`` (the inverse mapping carries one to the other) and the two answers coincide. The cap keeps the first
    ``max_symmetry_mappings`` mappings in a fixed order, and a truncated set is not closed under inverses, so
    there the two directions can differ. Basin linkage must not depend on which structure was deposited first,
    so the pair is linked when EITHER direction matches, and its distance is the smaller of the two.
    """
    forward = compare_basins(smiles, coords_a, coords_b, params)
    if not forward.symmetry_cap_hit or coords_b is None:
        return forward
    backward = compare_basins(smiles, coords_b, coords_a, params)
    if forward.same_basin != backward.same_basin:
        return forward if forward.same_basin else backward
    return forward if comparison_distance(forward) <= comparison_distance(backward) else backward
