"""Entry-level stereo descriptors (DR-0038).

A *species* is a constitution: the stereo-blind canonical SMILES plus charge and
multiplicity. Stereochemistry is resolved only on the *species entry*, as a
canonical **stereo descriptor** string: one token per potential stereo element,
``R``/``S`` for a tetrahedral centre, ``E``/``Z`` for a stereogenic double bond,
``Ra``/``Sa`` for an allene axis, ``?`` for an element nothing pinned down. Examples: ``E``, ``R,S``, ``E,?``, ``Ra``.

Vocabulary
----------
``R S`` / ``E Z``
    CIP labels (RDKit's new CIP labeller, ``rdCIPLabeler``). Never read from
    cis/trans or the bond-stereo enum: those are relative to whichever reference
    atoms RDKit picked and flip with atom order outside legacy perception.
``r s``
    CIP's lower-case labels for a *pseudo-asymmetric* centre (C3 of
    pentane-2,3,4-triol in the ``R,S`` diastereomer).
``e z``
    The same for a *pseudo-asymmetric double bond*: the two ends differ only by
    the chirality of their substituents (``CC(O)C(=CC)C(C)O`` has four isomers,
    ``E`` and ``Z`` for the two achiral-substituent pairs and ``e``/``z`` for the
    other two).
``-``
    The element is a *potential* stereo element of the constitution but is not
    stereogenic in this configuration (C3 of pentane-2,3,4-triol when C2 and C4
    are both ``R``). The descriptor keeps one token per potential element so the
    positions stay fixed; ``-`` is a statement, not an unknown.
``?``
    Nothing pinned the element down.

Why a descriptor and not a SMILES
---------------------------------
A SMILES cannot encode the E/Z configuration of a sigma-vinyl radical such as
1-propenyl (``[CH]=CC``) unless every hydrogen is written, yet the two isomers
are different molecules. The descriptor is computed from a molecule object, never
from SMILES text, so geometry-perceived configurations are first-class. A
canonical isomeric SMILES is stored beside it as derived search text wherever
one can be written (:func:`stereo_smiles_for`); it is never the key.

For a sigma-vinyl radical CIP ranks the unpaired electron lowest. In the ``Z``
isomer the hydrogen on the radical carbon (which outranks the radical) is on the
same side as the substituent on the other carbon, and the radical orbital is
trans to it. A name taken from the precursor halide can carry the opposite letter.

One string per configuration (injective)
----------------------------------------
Elements are ordered by canonical atom rank of the stereo-*stripped*
constitution (``CanonicalRankAtoms`` without tie-breaking), so the order is the
same whether the input carried stereo marks or not. Two configurations are the
same entry exactly when a graph automorphism of the constitution maps one onto the
other, so the descriptor is the **lexicographically smallest token string over
every automorphism** (a mirror image is not an automorphism: enantiomers stay
distinct). Sorting inside a symmetry class would not do: it loses the correlation
*between* classes and puts different diastereomers of galactitol and allitol on
one string. Enumerating the automorphisms is capped (:data:`_AUTOMORPHISM_CAP`). Above the
cap the elements that belong to a symmetry class of more than one are stated as
``?`` (and flagged ``symmetry`` in :attr:`ResolvedStereo.unrepresented`), whichever
the source says: this lumps real isomers into one entry, but it is deterministic,
so one configuration never receives two strings. Elements that are alone in their
class are unaffected.

Sources and how they combine
----------------------------
Every linked geometry, then the marks in the SMILES, then a supplied label. Each
fills only the elements the earlier ones left ``?``. An element given by more than
one source must agree, otherwise the deposit is refused with
:data:`W_SPECIES_STEREO_CONTRADICTION`. The sources are token vectors that are
only defined up to an automorphism, so they are aligned over the automorphisms
before they are compared. Torsional conformers share one configuration and
therefore agree; a conformer deposited as the other isomer is a contradiction.

Nothing is inferred: where geometry and marks are all absent an element stays
``?``. The one deduction made is ``-``: when the configuration the other tokens
fix leaves an element with no stereogenic reading, it is marked ``-`` (found by
enumerating the constitution's stereoisomers, up to :data:`_ENUMERATION_CAP`
elements).

Isotopes
--------
The species key strips isotopes, but an entry is isotopologue-aware, so its stereo
frame keeps them: ``C[C@H]([2H])O`` has a stereocentre (CIP ranks by mass number)
although CH2D-CH2OH's species is ethanol. ``stereo_smiles`` is written without
isotopes and is therefore left NULL where stripping them would lose the stereo.

Cumulated chains
----------------
A cumulated chain whose two ends each carry two different substituents is a stereo element, anchored at its two
end atoms. With an even number of double bonds (an allene) it is an axis: ``Ra`` or ``Sa`` (``Ra`` is IUPAC ``M``).
With an odd number of three or more it is ``E``/``Z``. RDKit perceives neither (no CIP label, marks dropped on
sanitising, nothing from 3D), so both are read from geometry here: the torsion about the end-to-end axis between the
two CIP-higher substituents (:func:`chain_geometry_token`; an allene must lie between 10 and 170 degrees, an odd
cumulene within 30 degrees of planar). Allene SMILES marks and, for now, cumulene ``/`` ``\\`` marks are not read
(:data:`CUMULENE_MARKS_READ`). Stated ``?`` whatever the data: a cyclic allene in a ring of eight or fewer atoms, and
a centre or double bond whose stereogenicity depends on an allene's sense (:data:`_ALLENE_DEPENDENT`). See DR-0038.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdCIPLabeler, rdDetermineBonds
from rdkit.Chem.EnumerateStereoisomers import (
    EnumerateStereoisomers,
    StereoEnumerationOptions,
)

from app.api.error_contract import CodedValueError
from app.db.models.common import StereoResolution

logger = logging.getLogger(__name__)

#: Raised when two sources disagree about the same stereo element.
W_SPECIES_STEREO_CONTRADICTION = "species_stereo_contradiction"

#: Raised when a supplied ``stereo_label`` does not fit the molecule it labels
#: (wrong number of tokens, a token of the wrong kind for its element, or a
#: configuration that cannot exist).
W_SPECIES_STEREO_LABEL_INVALID = "species_stereo_label_invalid"

#: The non-blocking warning for an entry that ends with at least one ``?`` that data could have settled.
W_SPECIES_ENTRY_STEREO_UNRESOLVED = "species_entry_stereo_unresolved"

#: The non-blocking warning for an entry with a stereo element that no further data would settle (a symmetry class
#: past the automorphism cap, a cyclic allene in a ring of eight or fewer, an element that depends on an allene's
#: sense, an allene or cumulene given only as SMILES marks): real isomers may share the entry.
W_SPECIES_ENTRY_STEREO_NOT_REPRESENTED = "species_entry_stereo_not_represented"

UNKNOWN = "?"
NOT_STEREOGENIC = "-"
TETRAHEDRAL_TOKENS = frozenset({"R", "S", "r", "s"})
DOUBLE_BOND_TOKENS = frozenset({"E", "Z", "e", "z"})
#: Axis of an allene (or any even cumulene). ``Ra`` is IUPAC's ``M`` (negative torsion between the two
#: CIP-higher substituents), ``Sa`` is ``P``.
AXIAL_TOKENS = frozenset({"Ra", "Sa"})
#: Cis/trans across an odd cumulene (a butatriene and longer): the plain ``E``/``Z`` letters.
CUMULENE_TOKENS = frozenset({"E", "Z"})
VOCABULARY = TETRAHEDRAL_TOKENS | DOUBLE_BOND_TOKENS | AXIAL_TOKENS | {UNKNOWN, NOT_STEREOGENIC}

#: Wire pattern for ``stereo_label``/``stereo_descriptor``; the schema package
#: carries its own copy because it cannot import the backend.
DESCRIPTOR_PATTERN = r"^(Ra|Sa|[RSrsEZez?-])(,(Ra|Sa|[RSrsEZez?-]))*$"

#: Reasons an element is stated ``?`` that no further data would settle (``ResolvedStereo.unrepresented``).
R_AXIAL_SMALL_RING = "axial_small_ring"
R_ALLENE_DEPENDENT = "allene_dependent"
R_AXIAL_MARKS = "axial_marks"
R_CUMULENE_MARKS = "cumulene_marks"
R_SYMMETRY = "symmetry"

#: Above this many potential elements the stereoisomer enumeration is skipped:
#: ``-`` is not deduced, a supplied label is not tested for realisability and
#: ``stereo_smiles`` is left NULL.
_ENUMERATION_CAP = 8

#: Most automorphisms of the constitution examined before the symmetric elements are stated as ``?``.
_AUTOMORPHISM_CAP = 100_000

#: Order in which tokens compare when the smallest string is chosen; ``?`` is last,
#: so a partial descriptor reads ``E,?`` and never ``?,E``.
_ORDER = {"R": 0, "S": 1, "r": 2, "s": 3, "E": 4, "Z": 5, "e": 6, "z": 7, "Ra": 8, "Sa": 9, "-": 10, "?": 11}

_TETRAHEDRAL = "tetrahedral"
_DOUBLE_BOND = "double_bond"
#: Axis of a cumulated chain with an even number of double bonds (an allene): ``Ra``/``Sa`` from geometry.
_AXIAL = "axial"
#: Cis/trans across a cumulated chain with an odd number (three or more) of double bonds: ``E``/``Z``.
_CUMULENE = "cumulene"
#: A centre or double bond whose stereogenicity depends on the sense of an allene (two constitutionally equal
#: arms that can be enantiomorphic). RDKit cannot see it and we do not compute it: always ``?``.
_ALLENE_DEPENDENT = "allene_dependent"
#: Chain elements (anchored at the two chain ends, read from geometry only).
_CHAIN_KINDS = frozenset({_AXIAL, _CUMULENE})
#: A cyclic allene in a ring of at most this size is never read: strained, short-lived, no validated reference.
_SMALL_RING_MAX = 8
#: Allene: the end-to-end torsion between the CIP-higher substituents must lie in [10, 170] degrees (ideal 90).
_ALLENE_TORSION_WINDOW = (10.0, 170.0)
#: Odd cumulene: cis within this torsion, trans beyond 180 minus it, otherwise twisted and unread.
_CUMULENE_PLANAR_TOLERANCE = 30.0


@dataclass(frozen=True)
class StereoElement:
    """One potential stereo element of the constitution graph.

    :param kind: ``tetrahedral``, ``double_bond``, ``axial`` or ``cumulene``.
    :param atoms: One atom index (tetrahedral), the two bonded atom indices
        (double bond) or the two end atoms of the cumulated chain, in the
        constitution molecule's atom numbering.
    :param key: Sort/class key built from canonical ranks.
    """

    kind: str
    atoms: tuple[int, ...]
    key: tuple[int, ...]
    #: Chain elements only: the chain's atoms from one end to the other (constitution_h numbering).
    chain: tuple[int, ...] = ()
    #: Chain elements only: size of the smallest ring through the whole chain, 0 when acyclic.
    ring_size: int = 0

    @property
    def never_settled(self) -> bool:
        """True when no data can settle this element (it is stated ``?`` always)."""

        return self.kind == _ALLENE_DEPENDENT or (
            self.kind == _AXIAL and 0 < self.ring_size <= _SMALL_RING_MAX
        )


@dataclass(frozen=True)
class StereoFrame:
    """The stereo-relevant view of one species entry's molecule.

    :param marked: The isotope-labelled molecule, stereo marks retained.
    :param constitution: The same molecule with all stereo removed (isotopic
        hydrogens stay explicit).
    :param constitution_h: ``constitution`` with every hydrogen explicit, the
        molecule stereo perception runs on (radical and hydrogen counts then
        tell ``CH3`` from ``CH2.``).
    :param elements: Potential stereo elements in canonical order.
    :param marked_index: For each constitution atom, the index of the same atom in
        ``marked`` (they differ when ``marked`` keeps a hydrogen that defines a
        double-bond mark, as in ``[H]/N=N\\\\[H]``).
    """

    marked: Chem.Mol
    constitution: Chem.Mol
    constitution_h: Chem.Mol
    elements: tuple[StereoElement, ...]
    marked_index: tuple[int, ...] = ()
    cache: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_stereo_capable(self) -> bool:
        return bool(self.elements)


@dataclass(frozen=True)
class ResolvedStereo:
    """The outcome of resolving an entry's stereo from all its sources.

    :param descriptor: The canonical descriptor, ``None`` iff the molecule has
        no potential stereo element.
    :param resolution: ``resolved``/``partial``/``unresolved``; ``None`` iff
        ``descriptor`` is ``None``.
    :param stereo_smiles: Canonical isomeric SMILES of the fully resolved
        configuration, when one can be written and round-trips.
    :param unrepresented: Why some ``?`` cannot be settled by the data in hand:
        ``axial_small_ring``, ``allene_dependent``, ``axial_marks``,
        ``cumulene_marks``, ``symmetry`` (a symmetry class past the automorphism
        cap). Empty when every ``?`` is an element the data left unknown.
    :param unknown_elements: How many ``?`` are elements the data could settle.
    """

    descriptor: str | None
    resolution: StereoResolution | None
    stereo_smiles: str | None
    unrepresented: tuple[str, ...] = ()
    unknown_elements: int = 0
    #: Past the automorphism cap only: the tokens the first informative source gave at the positions that were then
    #: stated as ``?`` (``?`` elsewhere). The migration compares them before it lets two such entries merge.
    evidence: tuple[str, ...] = ()

    @property
    def label(self) -> str | None:
        """Display text for ``species_entry.stereo_label``.

        The descriptor, except that a fully unknown configuration displays as
        nothing: ``?`` per element is a statement about the deposit, not a
        label of the molecule.
        """

        if self.resolution is None or self.resolution == StereoResolution.unresolved:
            return None
        return self.descriptor

    @property
    def is_incomplete(self) -> bool:
        return self.resolution in (StereoResolution.partial, StereoResolution.unresolved)


_REMOVE_HS = Chem.RemoveHsParameters()
_REMOVE_HS.removeDefiningBondStereo = True


def constitution_mol(marked: Chem.Mol) -> Chem.Mol:
    """The stereo-blind molecule: all stereo removed, hydrogens stripped again.

    RDKit keeps a hydrogen explicit while it defines a double-bond mark
    (``[H]/N=N\\\\[H]``); once the mark is gone that hydrogen is ordinary, so
    ``[H]/N=N\\\\[H]`` and ``N=N`` have one constitution. An isotopic hydrogen
    stays explicit (it is part of what the molecule is).

    :param marked: Molecule, isotopes cleared for a species key
        (:func:`app.chemistry.species.strip_isotopes`) or kept for an entry.
    """

    constitution = Chem.Mol(marked)
    Chem.RemoveStereochemistry(constitution)
    constitution = Chem.RemoveHs(constitution, _REMOVE_HS)
    Chem.SanitizeMol(constitution)
    return constitution


def _cumulated_bonds(
    mol_h: Chem.Mol, ranks_h: list[int], ranks: list[int]
) -> tuple[set[int], list[StereoElement]]:
    """Double bonds that belong to a cumulated chain, and the chains that are stereo elements.

    A chain is an element when both ends carry two different substituents. Its kind
    is ``axial`` for an even number of double bonds (``Ra``/``Sa``) and ``cumulene``
    (``E``/``Z``) for an odd number of three or more. RDKit names neither, so they are read
    from geometry here, and every bond of the chain is kept out of the ordinary E/Z path.
    """

    double = [b for b in mol_h.GetBonds() if b.GetBondType() == Chem.BondType.DOUBLE]
    parent = {b.GetIdx(): b.GetIdx() for b in double}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_atom: dict[int, list[int]] = {}
    for b in double:
        for a in (b.GetBeginAtomIdx(), b.GetEndAtomIdx()):
            by_atom.setdefault(a, []).append(b.GetIdx())
    for bonds in by_atom.values():
        for other in bonds[1:]:
            parent[find(other)] = find(bonds[0])
    chains: dict[int, list[int]] = {}
    for b in double:
        chains.setdefault(find(b.GetIdx()), []).append(b.GetIdx())

    cumulated: set[int] = set()
    elements: list[StereoElement] = []
    rings = [set(r) for r in mol_h.GetRingInfo().AtomRings()]
    for members in chains.values():
        if len(members) < 2:
            continue
        cumulated.update(members)
        member_set = set(members)
        ends = [a for a, bonds in by_atom.items() if len([x for x in bonds if x in member_set]) == 1]
        capable = 0
        for end in ends:
            atom = mol_h.GetAtomWithIdx(end)
            chain_neighbours = {
                n.GetIdx()
                for n in atom.GetNeighbors()
                if mol_h.GetBondBetweenAtoms(end, n.GetIdx()).GetIdx() in member_set
            }
            others = [n.GetIdx() for n in atom.GetNeighbors() if n.GetIdx() not in chain_neighbours]
            if len(others) == 2 and ranks_h[others[0]] != ranks_h[others[1]]:
                capable += 1
        if capable == 2:
            path = [ends[0]]
            previous = -1
            while path[-1] != ends[1]:
                here = path[-1]
                step = next(
                    n.GetIdx()
                    for n in mol_h.GetAtomWithIdx(here).GetNeighbors()
                    if n.GetIdx() != previous
                    and mol_h.GetBondBetweenAtoms(here, n.GetIdx()).GetIdx() in member_set
                )
                previous = here
                path.append(step)
            ring_size = min((len(r) for r in rings if set(path) <= r), default=0)
            low, high = sorted((ranks[ends[0]], ranks[ends[1]]))
            kind = _AXIAL if len(members) % 2 == 0 else _CUMULENE
            elements.append(
                StereoElement(
                    kind,
                    (ends[0], ends[1]),
                    (low, 2 if kind == _AXIAL else 3, high),
                    chain=tuple(path),
                    ring_size=ring_size,
                )
            )
    return cumulated, elements


def _branch(mol_h: Chem.Mol, start: int, block: int) -> set[int]:
    """Atoms reachable from ``start`` without passing through ``block``."""

    seen = {start}
    stack = [start]
    while stack:
        for n in mol_h.GetAtomWithIdx(stack.pop()).GetNeighbors():
            i = n.GetIdx()
            if i != block and i not in seen:
                seen.add(i)
                stack.append(i)
    return seen


def _tied_arms_with_allene(mol_h: Chem.Mol, centre: int, neighbours: list[int], ranks_h: list[int], axial_atoms: set[int]):
    """The pair of constitutionally equal arms of ``centre`` that each contain an allene, else ``None``."""

    by_rank: dict[int, list[int]] = {}
    for n in neighbours:
        by_rank.setdefault(ranks_h[n], []).append(n)
    pairs = [group for group in by_rank.values() if len(group) == 2]
    if len(pairs) != 1:
        return None
    first, second = pairs[0]
    arm_first = _branch(mol_h, first, centre)
    if second in arm_first:  # a ring joins the arms: not a pair of independent arms
        return None
    if arm_first & axial_atoms and _branch(mol_h, second, centre) & axial_atoms:
        return pairs[0]
    return None


def _allene_dependent_elements(
    mol_h: Chem.Mol, ranks_h: list[int], ranks: list[int], found: list[StereoElement], skipped: set[int]
) -> list[StereoElement]:
    """Centres and double bonds that are stereogenic only through the sense of an allene.

    ``CC=C=CC(C)C=C=CC``: the middle carbon carries two constitutionally equal arms, so RDKit sees no stereo
    element there. When the arms are enantiomorphic (``Ra`` against ``Sa``) it is a pseudo-asymmetric centre.
    Neither RDKit's perception nor its CIP labeller can see allene chirality, so the element is listed and
    always stated ``?``; it is never deduced non-stereogenic.
    """

    axial_atoms = {a for e in found if e.kind == _AXIAL for a in e.chain}
    if not axial_atoms:
        return []
    extra: list[StereoElement] = []
    for atom in mol_h.GetAtoms():
        idx = atom.GetIdx()
        if atom.GetDegree() != 4:
            continue
        neighbours = [n.GetIdx() for n in atom.GetNeighbors()]
        if len({ranks_h[n] for n in neighbours}) != 3:
            continue
        if _tied_arms_with_allene(mol_h, idx, neighbours, ranks_h, axial_atoms) is not None:
            extra.append(StereoElement(_ALLENE_DEPENDENT, (idx,), (ranks[idx], 0, ranks[idx])))
    for bond in mol_h.GetBonds():
        if bond.GetBondType() != Chem.BondType.DOUBLE or bond.GetIdx() in skipped:
            continue
        begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        verdicts = []
        for here, there in ((begin, end), (end, begin)):
            atom = mol_h.GetAtomWithIdx(here)
            others = [n.GetIdx() for n in atom.GetNeighbors() if n.GetIdx() != there]
            if len(others) != 2:
                verdicts.append(None)
            elif ranks_h[others[0]] != ranks_h[others[1]]:
                verdicts.append("distinct")
            elif _tied_arms_with_allene(mol_h, here, [*others, there], ranks_h, axial_atoms) is not None:
                verdicts.append("allene")
            else:
                verdicts.append(None)
        if None in verdicts or "allene" not in verdicts:
            continue
        low, high = sorted((ranks[begin], ranks[end]))
        extra.append(StereoElement(_ALLENE_DEPENDENT, (begin, end), (low, 1, high)))
    return extra


@lru_cache(maxsize=8192)
def unrepresented_stereo_kinds(smiles: str) -> tuple[str, ...]:
    """Stereo elements of a molecule that no data can settle.

    ``axial_small_ring`` (an allene in a ring of eight or fewer) and ``allene_dependent`` (a centre or double
    bond whose stereogenicity depends on an allene's sense) are always ``?`` in an entry's descriptor, so a
    reader must not take that ``?`` for "not yet supplied". Empty for a SMILES that does not parse, or a
    molecule with neither. (Cases that depend on the deposit, not the molecule -- the symmetry cap, allene
    marks -- are not listed here: they need the full resolution.)
    """

    from app.chemistry.species import identity_mol_from_smiles

    try:
        frame = frame_from_mol(identity_mol_from_smiles(smiles))
    except ValueError:
        return ()
    reasons = set()
    for element in frame.elements:
        if element.kind == _ALLENE_DEPENDENT:
            reasons.add(R_ALLENE_DEPENDENT)
        elif element.never_settled:
            reasons.add(R_AXIAL_SMALL_RING)
    return tuple(r for r in (R_AXIAL_SMALL_RING, R_ALLENE_DEPENDENT) if r in reasons)


def frame_from_mol(marked: Chem.Mol) -> StereoFrame:
    """Build the stereo frame for a sanitized molecule.

    :param marked: The entry's molecule with isotopes kept (an entry is
        isotopologue-aware) and stereo marks retained.
    """

    tagged = Chem.Mol(marked)
    for atom in tagged.GetAtoms():
        atom.SetIntProp("_marked_idx", atom.GetIdx())
    constitution = constitution_mol(tagged)
    marked_index = tuple(atom.GetIntProp("_marked_idx") for atom in constitution.GetAtoms())
    constitution_h = Chem.AddHs(constitution)
    ranks = list(Chem.CanonicalRankAtoms(constitution, breakTies=False))
    ranks_h = list(Chem.CanonicalRankAtoms(constitution_h, breakTies=False))
    skipped, found = _cumulated_bonds(constitution_h, ranks_h, ranks)

    for info in Chem.FindPotentialStereo(constitution_h):
        if info.type == Chem.StereoType.Atom_Tetrahedral:
            atom = info.centeredOn
            found.append(StereoElement(_TETRAHEDRAL, (atom,), (ranks[atom], 0, ranks[atom])))
        elif info.type == Chem.StereoType.Bond_Double:
            if info.centeredOn in skipped:
                continue
            bond = constitution_h.GetBondWithIdx(info.centeredOn)
            begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            low, high = sorted((ranks[begin], ranks[end]))
            found.append(StereoElement(_DOUBLE_BOND, (begin, end), (low, 1, high)))
    dependent = _allene_dependent_elements(constitution_h, ranks_h, ranks, found, skipped)
    # RDKit may list such a centre or bond as an ordinary element (and enumerate it as non-stereogenic): replace it.
    taken = {frozenset(e.atoms) for e in dependent}
    found = [e for e in found if e.kind in _CHAIN_KINDS or frozenset(e.atoms) not in taken]
    found.extend(dependent)
    found.sort(key=lambda element: (element.key, element.atoms))
    return StereoFrame(
        marked=marked,
        constitution=constitution,
        constitution_h=constitution_h,
        elements=tuple(found),
        marked_index=marked_index,
    )


# ---------------------------------------------------------------------------
# Symmetry: automorphisms of the constitution, as permutations of the elements
# ---------------------------------------------------------------------------


def _element_index(frame: StereoFrame) -> dict:
    index: dict = {}
    for i, element in enumerate(frame.elements):
        if len(element.atoms) == 1:
            index[("T", element.atoms[0])] = i
        else:
            index[("D", frozenset(element.atoms))] = i
    return index


def element_permutations(frame: StereoFrame) -> tuple[tuple[int, ...], ...]:
    """Every distinct permutation of the elements some automorphism induces.

    ``perm[i]`` is the index of the image of element ``i``. The identity is
    always present. Automorphisms are found on the constitution graph coloured by
    canonical rank (which carries hydrogen count, radical state, charge and
    isotope), so an automorphism never maps ``CH3`` onto ``CH2.``.

    Above :data:`_AUTOMORPHISM_CAP` only the identity is returned and the frame is
    marked ``capped`` (see :func:`mask_capped`).
    """

    cached = frame.cache.get("perms")
    if cached is not None:
        return cached
    n = len(frame.elements)
    identity = tuple(range(n))
    classes: dict = {}
    for element in frame.elements:
        classes[element.key] = classes.get(element.key, 0) + 1
    if n <= 1 or all(count == 1 for count in classes.values()):
        frame.cache["perms"] = (identity,)
        return frame.cache["perms"]

    ranks = list(Chem.CanonicalRankAtoms(frame.constitution, breakTies=False))
    colored = Chem.RWMol(frame.constitution)
    for atom, rank in zip(colored.GetAtoms(), ranks, strict=True):
        atom.SetIsotope(rank + 1)
        atom.SetNoImplicit(True)
    query = Chem.Mol(colored)
    matches = query.GetSubstructMatches(query, uniquify=False, useChirality=False, maxMatches=_AUTOMORPHISM_CAP)
    if len(matches) >= _AUTOMORPHISM_CAP:
        frame.cache["capped"] = True
        frame.cache["perms"] = (identity,)
        return frame.cache["perms"]
    index = _element_index(frame)
    perms = {identity}
    for match in matches:
        image = []
        for element in frame.elements:
            if len(element.atoms) == 1:
                target = index.get(("T", match[element.atoms[0]]))
            else:
                target = index.get(("D", frozenset(match[a] for a in element.atoms)))
            if target is None:
                break
            image.append(target)
        else:
            perms.add(tuple(image))
    frame.cache["perms"] = tuple(sorted(perms))
    return frame.cache["perms"]


def _order_key(tokens) -> tuple[int, ...]:
    return tuple(_ORDER[t] for t in tokens)


def mask_capped(frame: StereoFrame, tokens) -> list[str]:
    """``tokens`` with every element of a multi-member symmetry class set to ``?`` when the
    automorphism cap was hit, else unchanged.

    Past the cap the canonical string cannot be chosen, so the positions it would
    have to permute are not stated. Elements alone in their class are fixed by every
    automorphism and keep their token.
    """

    element_permutations(frame)
    if not frame.cache.get("capped"):
        return list(tokens)
    counts: dict = {}
    for element in frame.elements:
        counts[element.key] = counts.get(element.key, 0) + 1
    return [t if counts[e.key] == 1 else UNKNOWN for t, e in zip(tokens, frame.elements, strict=True)]


def canonical_tokens(frame: StereoFrame, tokens) -> tuple[str, ...]:
    """The smallest token string over every automorphism (the descriptor's tokens)."""

    tokens = mask_capped(frame, tokens)
    best: tuple[str, ...] | None = None
    best_key: tuple[int, ...] | None = None
    for perm in element_permutations(frame):
        candidate = tuple(tokens[perm[i]] for i in range(len(tokens)))
        key = _order_key(candidate)
        if best_key is None or key < best_key:
            best, best_key = candidate, key
    assert best is not None
    return best


# ---------------------------------------------------------------------------
# Per-source tokens
# ---------------------------------------------------------------------------


def _atom_token(atom: Chem.Atom) -> str:
    if not atom.HasProp("_CIPCode"):
        return UNKNOWN
    code = atom.GetProp("_CIPCode")
    return code if code in TETRAHEDRAL_TOKENS else UNKNOWN


def _bond_token(bond: Chem.Bond | None) -> str:
    if bond is None:
        return UNKNOWN
    if bond.HasProp("_CIPCode"):
        code = bond.GetProp("_CIPCode")
        if code in DOUBLE_BOND_TOKENS:
            return code
    return UNKNOWN


def mark_tokens(frame: StereoFrame) -> list[str]:
    """Read each element's CIP label from the stereo marks of the SMILES.

    :returns: One token per ``frame.elements`` entry; ``?`` where unmarked or
        where the marks do not make the element stereogenic yet (:func:`refine`
        turns the latter into ``-`` when the configuration forces it).
    """

    marked = Chem.Mol(frame.marked)
    rdCIPLabeler.AssignCIPLabels(marked)
    tokens: list[str] = []
    for element in frame.elements:
        if element.kind == _CUMULENE and CUMULENE_MARKS_READ:
            tokens.append(cumulene_mark_token(frame, element))
        elif element.kind in (_AXIAL, _ALLENE_DEPENDENT, _CUMULENE):
            tokens.append(UNKNOWN)
        elif element.kind == _TETRAHEDRAL:
            tokens.append(_atom_token(marked.GetAtomWithIdx(frame.marked_index[element.atoms[0]])))
        else:
            begin, end = (frame.marked_index[index] for index in element.atoms)
            tokens.append(_bond_token(marked.GetBondBetweenAtoms(begin, end)))
    return tokens


def _xyz_block(xyz_text: str) -> str:
    lines = xyz_text.strip().splitlines()
    try:
        int(lines[0].strip())
        return xyz_text.strip()
    except (ValueError, IndexError):
        pass
    return f"{len(lines)}\n\n{xyz_text.strip()}"


def _neutralized_template(template: Chem.Mol) -> Chem.Mol:
    """Copy of ``template`` without unpaired-electron bookkeeping.

    ``AssignBondOrdersFromTemplate`` rejects a template that carries radical
    electrons. Zeroing them keeps the connectivity (heavy atoms plus explicit
    hydrogens) that bond-order mapping needs, so open-shell species are
    perceived like closed-shell ones. ``SetNoImplicit`` stops RDKit adding a
    phantom hydrogen into the vacated valence.
    """

    neutral = Chem.Mol(template)
    for atom in neutral.GetAtoms():
        if atom.GetNumRadicalElectrons():
            atom.SetNumRadicalElectrons(0)
            atom.SetNoImplicit(True)
    return neutral


def _skeleton(mol: Chem.Mol) -> Chem.Mol:
    """Element, isotope and connectivity only, for graph matching across notations."""

    skeleton = Chem.RWMol(mol)
    for atom in skeleton.GetAtoms():
        atom.SetFormalCharge(0)
        atom.SetNumRadicalElectrons(0)
        atom.SetIsAromatic(False)
        atom.SetNoImplicit(True)
        atom.SetNumExplicitHs(0)
        atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    for bond in skeleton.GetBonds():
        bond.SetBondType(Chem.BondType.SINGLE)
        bond.SetIsAromatic(False)
        bond.SetStereo(Chem.BondStereo.STEREONONE)
    return skeleton.GetMol()


def geometry_mol(
    frame: StereoFrame, xyz_text: str, isotopes: dict[int, int] | None = None
) -> tuple[Chem.Mol, tuple[int, ...]] | None:
    """Perceive configuration from coordinates.

    :returns: ``(mol, mapping)`` -- the geometry's molecule with stereo assigned,
        isotopes taken from the identity and CIP labels set, and for each
        ``constitution_h`` atom the index of its image in ``mol``; or ``None``
        when the coordinates cannot be mapped onto the graph. A geometry that
        does not fit the graph resolves nothing; it is never a guess.

    :param isotopes: The geometry's per-atom isotope labels (1-based XYZ index to
        mass number), as :func:`app.chemistry.geometry.parse_xyz` reports them. An
        isotopically labelled identity needs them to tell which hydrogen is the
        deuterium; without them the geometry says nothing about such an entry.
    """

    try:
        raw = Chem.MolFromXYZBlock(_xyz_block(xyz_text))
        if raw is None:
            return None
        template = _neutralized_template(frame.constitution_h)
        if any(atom.GetIsotope() for atom in template.GetAtoms()):
            if not isotopes:
                return None
        # ``MolFromXYZBlock`` yields a bondless atom cloud; connectivity has to
        # be perceived before bond orders can be mapped from the template.
        rdDetermineBonds.DetermineConnectivity(raw)
        for index, mass in (isotopes or {}).items():
            if 1 <= index <= raw.GetNumAtoms():
                raw.GetAtomWithIdx(index - 1).SetIsotope(mass)
        mol = AllChem.AssignBondOrdersFromTemplate(template, raw)
        mapping = _skeleton(mol).GetSubstructMatch(_skeleton(template))
        if len(mapping) != template.GetNumAtoms():
            return None
        # The identity is authoritative for isotopes (the composition and isotope
        # checks have already reconciled it with the file), and CIP ranks by mass.
        for template_atom in template.GetAtoms():
            mol.GetAtomWithIdx(mapping[template_atom.GetIdx()]).SetIsotope(template_atom.GetIsotope())
        Chem.AssignStereochemistryFrom3D(mol)
        rdCIPLabeler.AssignCIPLabels(mol)
        return mol, tuple(mapping)
    except (ValueError, RuntimeError):
        return None
    except Exception:  # pragma: no cover - defensive: surface the unexpected
        logger.warning("Unexpected failure perceiving stereo from 3D geometry", exc_info=True)
        return None


_PROBE_DUMMY_Z = 118  # Og: outranks every real ligand


def cip_higher_substituent(mol: Chem.Mol, centre: int, chain_neighbour: int) -> tuple[int, int] | None:
    """The two substituents of a chain end, ``(higher, lower)`` by CIP priority; ``None`` if they tie.

    ``centre`` is an sp2 chain end with the chain neighbour ``chain_neighbour`` and two other substituents.
    Different atomic numbers decide at once. Otherwise a single-centre probe on RDKit's CIP labeller decides:
    the chain bond is cut, a dummy (Og, outranking everything) takes its place with a hydrogen as fourth
    ligand, the centre is tagged counter-clockwise in the order (first, second, dummy, H), and the label it
    gets says which of the two substituents ranks higher. ``mol`` may carry stereo, so rules that read the
    chirality of the substituents are honoured.
    """

    atom = mol.GetAtomWithIdx(centre)
    others = [n.GetIdx() for n in atom.GetNeighbors() if n.GetIdx() != chain_neighbour]
    if len(others) != 2:
        return None
    a, b = others
    za, zb = mol.GetAtomWithIdx(a).GetAtomicNum(), mol.GetAtomWithIdx(b).GetAtomicNum()
    if za != zb:
        return (a, b) if za > zb else (b, a)
    if za == 1:  # two hydrogens: only the mass number can tell them apart (the probe's own H would tie)
        ia, ib = mol.GetAtomWithIdx(a).GetIsotope() or 1, mol.GetAtomWithIdx(b).GetIsotope() or 1
        if ia == ib:
            return None
        return (a, b) if ia > ib else (b, a)
    probe = Chem.RWMol(mol)
    probe.RemoveBond(centre, chain_neighbour)
    dummy = probe.AddAtom(Chem.Atom(_PROBE_DUMMY_Z))
    hydrogen = probe.AddAtom(Chem.Atom(1))
    probe.AddBond(centre, dummy, Chem.BondType.SINGLE)
    probe.AddBond(centre, hydrogen, Chem.BondType.SINGLE)
    probe_centre = probe.GetAtomWithIdx(centre)
    probe_centre.SetNumExplicitHs(0)
    probe_centre.SetNoImplicit(True)
    probe_centre.SetNumRadicalElectrons(0)
    probe_centre.SetChiralTag(Chem.ChiralType.CHI_TETRAHEDRAL_CCW)
    probe.GetAtomWithIdx(chain_neighbour).SetNoImplicit(True)
    order = [n.GetIdx() for n in probe_centre.GetNeighbors()]
    labelled = probe.GetMol()
    labelled.UpdatePropertyCache(strict=False)
    Chem.GetSymmSSSR(labelled)
    rdCIPLabeler.AssignCIPLabels(labelled)
    centre_atom = labelled.GetAtomWithIdx(centre)
    if not centre_atom.HasProp("_CIPCode"):
        return None
    first, second = order[0], order[1]
    # Counter-clockwise in the order (first, second, dummy, H) is "S" exactly when `first` outranks `second`.
    return (first, second) if centre_atom.GetProp("_CIPCode") == "S" else (second, first)


def chain_torsion(
    positions, path: list[int], higher_a: int, higher_b: int
) -> float | None:
    """Torsion in degrees about the end-to-end axis between the two CIP-higher substituents.

    Positive is clockwise looking along the axis from the first end (IUPAC's ``P``), negative ``M``.
    ``None`` for a degenerate (zero-length) axis or a substituent on the axis.
    """

    end_a, end_b = path[0], path[-1]
    axis = positions[end_b] - positions[end_a]
    length = np.linalg.norm(axis)
    if length < 1e-6:
        return None
    axis = axis / length
    va = positions[higher_a] - positions[end_a]
    vb = positions[higher_b] - positions[end_b]
    va = va - axis * np.dot(va, axis)
    vb = vb - axis * np.dot(vb, axis)
    if np.linalg.norm(va) < 1e-6 or np.linalg.norm(vb) < 1e-6:
        return None
    return math.degrees(math.atan2(float(np.dot(axis, np.cross(va, vb))), float(np.dot(va, vb))))


def chain_geometry_token(element: StereoElement, mol: Chem.Mol, mapping) -> str:
    """``Ra``/``Sa`` (allene axis) or ``E``/``Z`` (odd cumulene) read from coordinates; ``?`` if not clear.

    The two CIP-higher substituents (one per end) define the torsion about the end-to-end axis. An allene
    is ``Ra`` for a negative torsion (IUPAC ``M``) and ``Sa`` for a positive one (``P``); a torsion outside
    [10, 170] degrees is a flattened axis and is not read. An odd cumulene is ``Z`` within 30 degrees of
    planar cis, ``E`` within 30 degrees of planar trans, otherwise twisted and not read. Cyclic allenes in
    rings of eight or fewer are never read.
    """

    if element.never_settled or mol.GetNumConformers() == 0:
        return UNKNOWN
    path = [mapping[i] for i in element.chain]
    higher_a = cip_higher_substituent(mol, path[0], path[1])
    higher_b = cip_higher_substituent(mol, path[-1], path[-2])
    if higher_a is None or higher_b is None:
        return UNKNOWN
    torsion = chain_torsion(mol.GetConformer().GetPositions(), path, higher_a[0], higher_b[0])
    if torsion is None:
        return UNKNOWN
    size = abs(torsion)
    if element.kind == _AXIAL:
        low, high = _ALLENE_TORSION_WINDOW
        if not low <= size <= high:
            return UNKNOWN
        return "Ra" if torsion < 0 else "Sa"
    if size <= _CUMULENE_PLANAR_TOLERANCE:
        return "Z"
    if size >= 180.0 - _CUMULENE_PLANAR_TOLERANCE:
        return "E"
    return UNKNOWN


#: Mol-level property carrying the SMILES text an identity molecule was parsed from: sanitisation drops the
#: ``/`` ``\\`` marks of a cumulene and the chirality mark of an allene, so they are re-read from the text.
SMILES_PROP = "_tckdb_smiles"


def _raw_marks_mol(frame: StereoFrame) -> Chem.Mol | None:
    if not frame.marked.HasProp(SMILES_PROP):
        return None
    params = Chem.SmilesParserParams()
    params.sanitize = False
    params.removeHs = False
    return Chem.MolFromSmiles(frame.marked.GetProp(SMILES_PROP), params)


def allene_marks_present(frame: StereoFrame) -> bool:
    """Whether the SMILES carries a chirality mark on an atom between two cumulated double bonds."""

    raw = _raw_marks_mol(frame)
    if raw is None:
        return False
    for atom in raw.GetAtoms():
        if atom.GetChiralTag() in (Chem.ChiralType.CHI_UNSPECIFIED, Chem.ChiralType.CHI_OTHER):
            continue
        doubles = sum(1 for b in atom.GetBonds() if b.GetBondType() == Chem.BondType.DOUBLE)
        if doubles == 2:
            return True
    return False


def cumulene_marks_present(frame: StereoFrame) -> bool:
    """Whether the SMILES carries a ``/`` or ``\\`` on a substituent of a stereogenic cumulene's end."""

    sides = frame.cache.get("mark_sides")
    if sides is None:
        sides = _mark_sides(frame)
        frame.cache["mark_sides"] = sides if sides is not None else {}
    return any(
        end in sides for e in frame.elements if e.kind == _CUMULENE for end in (e.chain[0], e.chain[-1])
    )


def _mark_sides(frame: StereoFrame) -> dict[int, dict[int, int]] | None:
    """For each constitution atom with a directional mark: ``{neighbour -> +1/-1}`` (above/below the atom).

    OpenSMILES: a ``/`` between X and Y, as written, puts Y above X (and a ``\\`` below). So ``A/N`` has N
    above A, while ``N/A`` has A above N, i.e. N below A. ``None`` when the text cannot be read or matched onto
    the constitution.
    """

    raw = _raw_marks_mol(frame)
    if raw is None:
        return None
    marks: list[tuple[int, int, int]] = []  # (atom, neighbour, side of neighbour relative to atom), raw indices
    for bond in raw.GetBonds():
        direction = bond.GetBondDir()
        if direction not in (Chem.BondDir.ENDUPRIGHT, Chem.BondDir.ENDDOWNRIGHT):
            continue
        up = direction == Chem.BondDir.ENDUPRIGHT
        begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        # begin written first: '/' puts end above begin, so begin sees end above (+1) and end sees begin below (-1)
        marks.append((begin, end, 1 if up else -1))
        marks.append((end, begin, -1 if up else 1))
    try:
        for atom in raw.GetAtoms():
            atom.SetIntProp("_raw_idx", atom.GetIdx())
        clean = Chem.Mol(raw)
        Chem.SanitizeMol(clean)
        raw_constitution = constitution_mol(clean)
    except (ValueError, RuntimeError):
        return None
    match = _skeleton(raw_constitution).GetSubstructMatch(_skeleton(frame.constitution))
    if len(match) != frame.constitution.GetNumAtoms():
        return None
    # match[i] is the raw-constitution atom standing for constitution atom i
    to_constitution = {m: i for i, m in enumerate(match)}
    raw_to_constitution: dict[int, int] = {}
    for atom in raw_constitution.GetAtoms():
        raw_to_constitution[atom.GetIntProp("_raw_idx")] = to_constitution[atom.GetIdx()]
    raw_hydrogens = {a.GetIdx() for a in raw.GetAtoms() if a.GetAtomicNum() == 1 and not a.GetIsotope()}
    sides: dict[int, dict[int, int]] = {}
    for atom_idx, neighbour_idx, side in marks:
        if atom_idx not in raw_to_constitution:
            continue
        key = raw_to_constitution[neighbour_idx] if neighbour_idx in raw_to_constitution else (
            -1 if neighbour_idx in raw_hydrogens else None
        )
        if key is None:
            return None
        sides.setdefault(raw_to_constitution[atom_idx], {})[key] = side
    return sides


#: Whether ``/`` ``\\`` marks on an odd cumulene are read. They are not: the reader below passes every butatriene
#: case against InChI's ``/b`` layer (n = 3) but InChI writes no stereo layer for five cumulated double bonds, so
#: n = 5 has no independent witness (tests/services/test_cumulene_marks_vs_inchi.py). An unvalidated reader is not
#: shipped; flip this when it can be validated.
CUMULENE_MARKS_READ = False


def cumulene_mark_token(frame: StereoFrame, element: StereoElement) -> str:
    """``E``/``Z`` of an odd cumulene from the ``/`` ``\\`` marks of the SMILES, read as for a double bond.

    Each end atom's marked substituent sits above or below it; the other substituent is on the opposite
    side. The CIP-higher substituent at each end is found as for geometry, and the element is ``Z`` when the
    two higher substituents are on the same side. ``?`` when the marks are absent, incomplete, contradictory
    or the text cannot be matched onto the molecule.
    """

    sides = frame.cache.get("mark_sides")
    if sides is None:
        sides = _mark_sides(frame)
        frame.cache["mark_sides"] = sides if sides is not None else {}
    mol_h = frame.constitution_h
    path = element.chain
    higher_side = []
    for end, chain_neighbour in ((path[0], path[1]), (path[-1], path[-2])):
        marked = sides.get(end) if sides else None
        if not marked:
            return UNKNOWN
        order = cip_higher_substituent(mol_h, end, chain_neighbour)
        if order is None:
            return UNKNOWN
        verdicts = set()
        for neighbour, side in marked.items():
            if neighbour == -1:  # an explicit hydrogen: whichever substituent of the end is a plain hydrogen
                candidates = [
                    n for n in order
                    if mol_h.GetAtomWithIdx(n).GetAtomicNum() == 1 and not mol_h.GetAtomWithIdx(n).GetIsotope()
                ]
                if len(candidates) != 1:
                    return UNKNOWN
                neighbour = candidates[0]
            if neighbour not in order:
                return UNKNOWN
            verdicts.add(side if neighbour == order[0] else -side)
        if len(verdicts) != 1:
            return UNKNOWN
        higher_side.append(verdicts.pop())
    return "Z" if higher_side[0] == higher_side[1] else "E"


def geometry_tokens(
    frame: StereoFrame, xyz_text: str, isotopes: dict[int, int] | None = None
) -> list[str] | None:
    """Read each element's CIP label from 3D coordinates.

    Torsional conformers of one configuration give the same tokens; only
    configuration is read. ``None`` when the geometry could not be perceived at
    all; ``?`` for an element the coordinates leave undefined (for example a
    collinear sigma-vinyl) or that is not stereogenic in the perceived
    configuration (:func:`refine` tells the two apart).
    """

    perceived = geometry_mol(frame, xyz_text, isotopes)
    if perceived is None:
        return None
    mol, mapping = perceived
    tokens: list[str] = []
    for element in frame.elements:
        if element.kind in _CHAIN_KINDS:
            tokens.append(chain_geometry_token(element, mol, mapping))
        elif element.kind == _ALLENE_DEPENDENT:
            tokens.append(UNKNOWN)
        elif element.kind == _TETRAHEDRAL:
            tokens.append(_atom_token(mol.GetAtomWithIdx(mapping[element.atoms[0]])))
        else:
            begin, end = element.atoms
            tokens.append(_bond_token(mol.GetBondBetweenAtoms(mapping[begin], mapping[end])))
    return tokens


# ---------------------------------------------------------------------------
# Enumerating the constitution's stereoisomers: realisability and "-"
# ---------------------------------------------------------------------------


def _full_tokens(frame: StereoFrame, iso_h: Chem.Mol) -> list[str]:
    """Tokens of a fully specified stereoisomer (explicit hydrogens).

    Chain elements and allene-dependent elements are invisible to RDKit; they come back as ``?`` here and
    :func:`stereoisomers` expands the chain elements itself.
    """

    iso = Chem.Mol(iso_h)
    rdCIPLabeler.AssignCIPLabels(iso)
    specified: dict = {}
    for info in Chem.FindPotentialStereo(iso):
        is_specified = info.specified == Chem.StereoSpecified.Specified
        if info.type == Chem.StereoType.Atom_Tetrahedral:
            specified[("T", info.centeredOn)] = is_specified
        elif info.type == Chem.StereoType.Bond_Double:
            bond = iso.GetBondWithIdx(info.centeredOn)
            specified[("D", frozenset((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx())))] = is_specified
    tokens: list[str] = []
    for element in frame.elements:
        if element.kind in _CHAIN_KINDS or element.kind == _ALLENE_DEPENDENT:
            tokens.append(UNKNOWN)
            continue
        if element.kind == _TETRAHEDRAL:
            token = _atom_token(iso.GetAtomWithIdx(element.atoms[0]))
            is_specified = specified.get(("T", element.atoms[0]), False)
        else:
            token = _bond_token(iso.GetBondBetweenAtoms(*element.atoms))
            is_specified = specified.get(("D", frozenset(element.atoms)), False)
        # In a fully specified stereoisomer an element that still carries no label and is not "specified" is not
        # stereogenic there; a specified element the labeller cannot name stays unknown.
        tokens.append(token if token != UNKNOWN else (UNKNOWN if is_specified else NOT_STEREOGENIC))
    return tokens


def _chain_choices(element: StereoElement) -> tuple[str, ...]:
    """The values a chain element takes across the constitution's stereoisomers."""

    if element.never_settled:
        return (UNKNOWN,)
    return ("Ra", "Sa") if element.kind == _AXIAL else ("E", "Z")


def stereoisomers(frame: StereoFrame) -> list[tuple[tuple[str, ...], Chem.Mol]] | None:
    """Every distinct stereoisomer of the constitution as ``(canonical tokens, mol)``.

    ``None`` when the molecule has more than :data:`_ENUMERATION_CAP` potential
    elements. Cached on the frame. RDKit enumerates tetrahedral centres and double bonds; the cumulated
    chains are crossed with them here (an allene is ``Ra`` or ``Sa``, an odd cumulene ``E`` or ``Z``).
    The one case this misses is documented: an element that depends on an allene's sense is ``?``
    (``allene_dependent``), so such a molecule has fewer descriptors than isomers.
    """

    if "isomers" in frame.cache:
        return frame.cache["isomers"]
    n = len(frame.elements)
    if n == 0 or n > _ENUMERATION_CAP:
        frame.cache["isomers"] = None
        return None
    # RDKit also offers every bond of a cumulated chain, so size the cap from what it reports, not from our elements.
    potential = len(Chem.FindPotentialStereo(frame.constitution_h))
    options = StereoEnumerationOptions(
        onlyUnassigned=True, unique=True, tryEmbedding=False, maxIsomers=2 ** (max(n, potential) + 1)
    )
    slots = [i for i, e in enumerate(frame.elements) if e.kind in _CHAIN_KINDS]
    choices = [_chain_choices(frame.elements[i]) for i in slots]
    seen: dict[tuple[str, ...], Chem.Mol] = {}
    try:
        for iso in EnumerateStereoisomers(frame.constitution_h, options=options):
            base = _full_tokens(frame, iso)
            for combo in itertools.product(*choices):
                tokens = list(base)
                for i, value in zip(slots, combo, strict=True):
                    tokens[i] = value
                seen.setdefault(canonical_tokens(frame, tokens), iso)
    except (ValueError, RuntimeError):
        frame.cache["isomers"] = None
        return None
    frame.cache["isomers"] = list(seen.items())
    return frame.cache["isomers"]


def _alignments(frame: StereoFrame, tokens, isomers) -> list[tuple[str, ...]]:
    """The isomers' token vectors aligned onto ``tokens`` positions that agree with them."""

    aligned = []
    tokens = mask_capped(frame, tokens)
    perms = element_permutations(frame)
    for vector, _mol in isomers:
        for perm in perms:
            u = tuple(vector[perm[i]] for i in range(len(vector)))
            if all(t == UNKNOWN or t == x for t, x in zip(tokens, u, strict=True)):
                aligned.append(u)
    return aligned


def is_realizable(frame: StereoFrame, tokens) -> bool | None:
    """Whether some stereoisomer of the constitution has this configuration.

    ``?`` matches anything. ``None`` when the molecule is too large to enumerate.
    """

    isomers = stereoisomers(frame)
    if isomers is None:
        return None
    return bool(_alignments(frame, tokens, isomers))


def refine(frame: StereoFrame, tokens: list[str]) -> list[str]:
    """Deduce ``-`` where the configuration forces an element to be non-stereogenic.

    ``(S,?,S)`` for pentane-2,3,4-triol can only be the isomer whose C3 is not a
    stereocentre, so C3 is ``-``. Nothing else is inferred: an element that some
    consistent stereoisomer makes stereogenic stays ``?``.

    :raises CodedValueError: ``species_stereo_contradiction`` when no stereoisomer
        has the configuration the sources together state.
    """

    if UNKNOWN not in tokens:
        return tokens
    isomers = stereoisomers(frame)
    if isomers is None:
        return tokens
    aligned = _alignments(frame, tokens, isomers)
    if not aligned:
        raise CodedValueError(
            W_SPECIES_STEREO_CONTRADICTION,
            "The stereochemistry the sources state together does not describe any stereoisomer of this "
            "molecule (an element that is not stereogenic in the configuration the others fix cannot "
            "carry a label). Correct the SMILES marks, the geometry or the label.",
            context={"sources": ["combined sources"], "descriptor": ",".join(canonical_tokens(frame, tokens))},
            message_prefix=False,
        )
    out = list(tokens)
    for i, token in enumerate(tokens):
        if token == UNKNOWN and all(u[i] == NOT_STEREOGENIC for u in aligned):
            out[i] = NOT_STEREOGENIC
    return out


# ---------------------------------------------------------------------------
# Old free-text labels
# ---------------------------------------------------------------------------


def translate_cis_trans(frame: StereoFrame, word: str) -> list[str] | None:
    """``cis``/``trans`` as a token, only where the words mean one thing.

    Unambiguous means: the molecule has exactly one stereo element, a double bond,
    and each end carries exactly one non-hydrogen substituent, so "cis" names the
    relation of those two substituents. The token is *computed* by the CIP
    labeller from that definition (the two substituents declared on the same, or
    opposite, sides), not assumed to be ``Z``/``E``.

    :returns: A one-token vector, or ``None`` when the words are ambiguous here.
    """

    word = word.strip().lower()
    if word not in ("cis", "trans") or len(frame.elements) != 1:
        return None
    element = frame.elements[0]
    if element.kind != _DOUBLE_BOND:
        return None
    mol = Chem.RWMol(frame.constitution_h)
    ends = []
    for atom_index, other in ((element.atoms[0], element.atoms[1]), (element.atoms[1], element.atoms[0])):
        neighbours = [n for n in mol.GetAtomWithIdx(atom_index).GetNeighbors() if n.GetIdx() != other]
        heavy = [n.GetIdx() for n in neighbours if n.GetAtomicNum() != 1]
        if len(neighbours) != 2 or len(heavy) != 1:
            return None
        ends.append(heavy[0])
    bond = mol.GetBondBetweenAtoms(*element.atoms)
    bond.SetStereoAtoms(ends[0], ends[1])
    bond.SetStereo(Chem.BondStereo.STEREOCIS if word == "cis" else Chem.BondStereo.STEREOTRANS)
    labelled = mol.GetMol()
    rdCIPLabeler.AssignCIPLabels(labelled)
    token = _bond_token(labelled.GetBondBetweenAtoms(*element.atoms))
    return None if token == UNKNOWN else [token]


# ---------------------------------------------------------------------------
# A supplied label
# ---------------------------------------------------------------------------


def parse_supplied_label(frame: StereoFrame, label: str) -> list[str]:
    """Validate a supplied label against the molecule it labels (D5).

    :raises CodedValueError: ``species_stereo_label_invalid`` when the label does
        not have one token per stereo element, a token of the wrong kind for its
        element, or names a configuration that cannot exist.
    """

    tokens = [token.strip() for token in label.split(",")]
    kinds = [_kind_text(element) for element in frame.elements]
    expected = ",".join(kinds) if kinds else "none"
    if not frame.elements:
        raise _label_invalid("this molecule has no stereo element, so it takes no stereo label", expected)
    if len(tokens) != len(frame.elements):
        raise _label_invalid(
            f"expected {len(frame.elements)} token(s), one per stereo element, got {len(tokens)}", expected
        )
    for position, (token, element) in enumerate(zip(tokens, frame.elements, strict=True), 1):
        if element.never_settled and token != UNKNOWN:
            why = (
                "depends on the sense of an allene, which is not represented"
                if element.kind == _ALLENE_DEPENDENT
                else f"is a cyclic allene in a ring of {_SMALL_RING_MAX} or fewer, which is not represented"
            )
            raise _label_invalid(f"token {position} ({token!r}) names an element that {why}; say '?'", expected)
        allowed = {
            _TETRAHEDRAL: TETRAHEDRAL_TOKENS,
            _DOUBLE_BOND: DOUBLE_BOND_TOKENS,
            _AXIAL: AXIAL_TOKENS,
            _CUMULENE: CUMULENE_TOKENS,
            _ALLENE_DEPENDENT: frozenset(),
        }[element.kind]
        extra = (UNKNOWN, NOT_STEREOGENIC) if element.kind in (_TETRAHEDRAL, _DOUBLE_BOND) else (UNKNOWN,)
        if token not in allowed and token not in extra:
            raise _label_invalid(
                f"token {position} ({token!r}) cannot label {_element_noun(element)}",
                expected,
            )
    if is_realizable(frame, tokens) is False:
        raise _label_invalid(
            "the label names a configuration that no stereoisomer of this molecule has (for example a "
            "label for an element that is not stereogenic in the configuration the other tokens fix)",
            expected,
        )
    return tokens


def _element_noun(element: StereoElement) -> str:
    if element.kind == _ALLENE_DEPENDENT:
        return "an element that depends on the sense of an allene (only '?')"
    if element.never_settled:
        return f"a cyclic allene in a ring of {_SMALL_RING_MAX} or fewer (only '?')"
    return {
        _TETRAHEDRAL: "a tetrahedral centre",
        _DOUBLE_BOND: "a double bond",
        _AXIAL: "an allene axis (Ra or Sa)",
        _CUMULENE: "a cumulene (E or Z)",
    }[element.kind]


def _kind_text(element: StereoElement) -> str:
    if element.kind == _ALLENE_DEPENDENT:
        return "? (depends on an allene's sense, not represented)"
    if element.never_settled:
        return f"? (cyclic allene, ring of {_SMALL_RING_MAX} or fewer, not represented)"
    return {
        _TETRAHEDRAL: "R/S",
        _DOUBLE_BOND: "E/Z",
        _AXIAL: "Ra/Sa",
        _CUMULENE: "E/Z",
    }[element.kind]


def _label_invalid(problem: str, expected: str) -> CodedValueError:
    return CodedValueError(
        W_SPECIES_STEREO_LABEL_INVALID,
        f"species_entry.stereo_label does not fit the molecule: {problem}. "
        f"Tokens are R, S, r, s, E, Z, e, z, Ra, Sa, - (not stereogenic here) or ? (unknown), one per stereo element in "
        f"canonical order; this molecule's elements are, in order: {expected}.",
        context={"expected_element_kinds": expected, "problem": problem},
        message_prefix=False,
    )


# ---------------------------------------------------------------------------
# Combining sources
# ---------------------------------------------------------------------------


def _describe(frame: StereoFrame, tokens) -> str:
    return ",".join(canonical_tokens(frame, tokens))


def merge_sources(frame: StereoFrame, current: list[str], incoming: list[str], *, first: str, second: str) -> list[str]:
    """Merge two token vectors, each only defined up to an automorphism.

    The incoming vector is aligned onto the current one over every automorphism;
    the alignment must agree wherever both state an element. Among the
    alignments the one with the most agreeing elements is taken, and if the best
    ones disagree on the result the position is ambiguous inside a symmetry class
    and the deposit is refused.

    :raises CodedValueError: ``species_stereo_contradiction`` when no alignment
        agrees, or when the best alignments give different configurations.
    """

    n = len(current)
    best_agree = -1
    results: dict[tuple[str, ...], list[str]] = {}
    for perm in element_permutations(frame):
        u = [incoming[perm[i]] for i in range(n)]
        if any(c != UNKNOWN and x != UNKNOWN and c != x for c, x in zip(current, u, strict=True)):
            continue
        agree = sum(1 for c, x in zip(current, u, strict=True) if c != UNKNOWN and c == x)
        merged = [c if c != UNKNOWN else x for c, x in zip(current, u, strict=True)]
        if agree > best_agree:
            best_agree, results = agree, {}
        if agree == best_agree:
            results.setdefault(canonical_tokens(frame, merged), merged)
    if not results:
        raise CodedValueError(
            W_SPECIES_STEREO_CONTRADICTION,
            f"The stereochemistry given by the {first} contradicts the {second}: "
            f"{_describe(frame, current)} against {_describe(frame, incoming)}. A contradiction is refused "
            "rather than resolved in either direction; correct the SMILES marks, the geometry or the label "
            "so they describe one configuration.",
            context={
                "sources": [first, second],
                "first": _describe(frame, current),
                "second": _describe(frame, incoming),
            },
            message_prefix=False,
        )
    if len(results) > 1:
        raise CodedValueError(
            W_SPECIES_STEREO_CONTRADICTION,
            f"The {first} and the {second} cannot be reconciled: the position they refer to is ambiguous "
            f"inside a symmetry class of this molecule ({_describe(frame, current)} against "
            f"{_describe(frame, incoming)} aligns in more than one way). State the configuration of every "
            "element, or supply a geometry.",
            context={"sources": [first, second], "ambiguous": True},
            message_prefix=False,
        )
    return next(iter(results.values()))


def resolve_stereo(
    frame: StereoFrame,
    *,
    geometries=(),
    supplied_label: str | None = None,
    use_marks: bool = True,
) -> ResolvedStereo:
    """Resolve an entry's stereo from every geometry, then marks, then a label.

    :param frame: The entry molecule's stereo frame.
    :param geometries: Every geometry linked to the entry, as XYZ text or as
        ``(xyz_text, isotopes)`` with the per-atom isotope labels. Torsional
        conformers share one configuration and agree; one that does not is a
        contradiction.
    :param supplied_label: A depositor's descriptor-vocabulary label, if any.
    :param use_marks: Read the marks of ``frame.marked``.
    :raises CodedValueError: On a contradiction, an unfit label or unsupported stereo.
    """

    if not frame.is_stereo_capable:
        if supplied_label is not None:
            parse_supplied_label(frame, supplied_label)  # raises: nothing to label
        return ResolvedStereo(None, None, None)

    named: list[tuple[str, list[str]]] = []
    raw_sources: list[list[str]] = []
    geometries = [g for g in geometries if g]
    for k, source in enumerate(geometries, 1):
        xyz, isotopes = (source, None) if isinstance(source, str) else source
        tokens = geometry_tokens(frame, xyz, isotopes)
        if tokens is not None:
            raw_sources.append(tokens)
            named.append(("geometry" if len(geometries) == 1 else f"conformer geometry {k}", mask_capped(frame, tokens)))
    if use_marks:
        marks = mark_tokens(frame)
        raw_sources.append(marks)
        named.append(("SMILES stereo marks", mask_capped(frame, marks)))
    if supplied_label is not None:
        label_tokens = parse_supplied_label(frame, supplied_label)
        raw_sources.append(label_tokens)
        named.append(("supplied stereo_label", mask_capped(frame, label_tokens)))

    evidence = _symmetric_evidence(frame, raw_sources)
    _refuse_disagreement_past_the_cap(frame, list(zip((n for n, _ in named), raw_sources, strict=True)))
    current = [UNKNOWN] * len(frame.elements)
    owner = ""
    for name, tokens in named:
        current = merge_sources(frame, current, tokens, first=owner or "earlier source", second=name)
        owner = name if not owner else f"{owner} and {name}"
    current = refine(frame, current)
    result = finish(frame, current)
    if use_marks and not geometries and supplied_label is None:
        result = _with_marks_only_reasons(frame, current, result)
    return replace(result, evidence=evidence) if evidence else result


def _with_marks_only_reasons(frame: StereoFrame, tokens, result: ResolvedStereo) -> ResolvedStereo:
    """A deposit whose only source is the SMILES, and whose marks sit on an element that cannot be read from them.

    An allene's chirality mark (``[C@]`` between the two double bonds) is not read: RDKit drops it on
    sanitising and no validated reader exists (#832). Such a deposit is stored as ``?`` and says why, instead of
    inviting more of the same. A cumulene's ``/`` ``\\`` marks are not read either while
    :data:`CUMULENE_MARKS_READ` is off.
    """

    open_axial = sum(
        1 for e, t in zip(frame.elements, tokens, strict=True) if e.kind == _AXIAL and not e.never_settled and t == UNKNOWN
    )
    open_cumulene = sum(1 for e, t in zip(frame.elements, tokens, strict=True) if e.kind == _CUMULENE and t == UNKNOWN)
    reasons = set(result.unrepresented)
    removed = 0
    if open_axial and allene_marks_present(frame):
        reasons.add(R_AXIAL_MARKS)
        removed += open_axial
    if open_cumulene and not CUMULENE_MARKS_READ and cumulene_marks_present(frame):
        reasons.add(R_CUMULENE_MARKS)
        removed += open_cumulene
    if not removed:
        return result
    return replace(result, unrepresented=tuple(sorted(reasons)), unknown_elements=max(0, result.unknown_elements - removed))


def _refuse_disagreement_past_the_cap(frame: StereoFrame, sources: list[tuple[str, list[str]]]) -> None:
    """Past the cap, compare the sources BEFORE their symmetric positions are collapsed to ``?``.

    No automorphism alignment is available there, but the tokens of one symmetry class are interchangeable by
    definition, so a class is compared as a multiset: two sources that both state a whole class must state the same
    tokens, and a source that states only part of it must not state a token the other (complete) one lacks. That
    refuses a real disagreement (``R,R`` marks against an ``S,S`` label) as a contradiction instead of hiding it.

    :raises CodedValueError: ``species_stereo_contradiction``.
    """

    mask = mask_capped(frame, ["x"] * len(frame.elements))
    if not frame.cache.get("capped"):
        return
    classes: dict = {}
    for i, element in enumerate(frame.elements):
        if mask[i] == UNKNOWN:
            classes.setdefault(element.key, []).append(i)
    for a in range(len(sources)):
        for b in range(a + 1, len(sources)):
            (first, ta), (second, tb) = sources[a], sources[b]
            for members in classes.values():
                stated_a = Counter(ta[i] for i in members if ta[i] != UNKNOWN)
                stated_b = Counter(tb[i] for i in members if tb[i] != UNKNOWN)
                full_a = sum(stated_a.values()) == len(members)
                full_b = sum(stated_b.values()) == len(members)
                if full_a and full_b:
                    clash = stated_a != stated_b
                elif full_a:
                    clash = bool(stated_b - stated_a)
                elif full_b:
                    clash = bool(stated_a - stated_b)
                else:
                    clash = False
                if clash:
                    raise CodedValueError(
                        W_SPECIES_STEREO_CONTRADICTION,
                        f"The stereochemistry given by the {first} contradicts the {second} at positions of this "
                        "molecule that are interchangeable by symmetry. A contradiction is refused rather than "
                        "resolved in either direction; correct the SMILES marks, the geometry or the label so they "
                        "describe one configuration.",
                        context={"sources": [first, second]},
                        message_prefix=False,
                    )


def _symmetric_evidence(frame: StereoFrame, sources: list[list[str]]) -> tuple[str, ...]:
    """Past the cap: what the first informative source said at the positions that are stated as ``?``.

    Empty when the cap was not hit or no source said anything there. The positions are those of the source's own atom
    order, so two entries of one configuration can differ here; a comparison that finds a difference is therefore
    conservative (it stops a merge), never permissive.
    """

    masked = mask_capped(frame, ["x"] * len(frame.elements))
    if not frame.cache.get("capped"):
        return ()
    for tokens in sources:
        evidence = tuple(t if masked[i] == UNKNOWN else UNKNOWN for i, t in enumerate(tokens))
        if any(t != UNKNOWN for t in evidence):
            return evidence
    return ()


def finish(frame: StereoFrame, tokens, *, with_smiles: bool = True) -> ResolvedStereo:
    """Descriptor, resolution and SMILES for an already-combined token list."""

    canonical = canonical_tokens(frame, tokens)
    descriptor = ",".join(canonical)
    unknown = sum(1 for token in canonical if token == UNKNOWN)
    if unknown == 0:
        resolution = StereoResolution.resolved
    elif unknown == len(canonical):
        resolution = StereoResolution.unresolved
    else:
        resolution = StereoResolution.partial
    smiles = (
        stereo_smiles_for(frame, canonical)
        if with_smiles and resolution == StereoResolution.resolved
        else None
    )
    reasons: list[str] = []
    for e in frame.elements:
        if e.kind == _ALLENE_DEPENDENT and R_ALLENE_DEPENDENT not in reasons:
            reasons.append(R_ALLENE_DEPENDENT)
        elif e.never_settled and e.kind == _AXIAL and R_AXIAL_SMALL_RING not in reasons:
            reasons.append(R_AXIAL_SMALL_RING)
    reasons.sort()
    stated_unknown = 0
    if frame.cache.get("capped"):
        counts: dict = {}
        for e in frame.elements:
            counts[e.key] = counts.get(e.key, 0) + 1
        masked = [e for e in frame.elements if counts[e.key] > 1 and not e.never_settled]
        if masked:
            reasons.append(R_SYMMETRY)
        stated_unknown = len(masked)
    unrepresented_count = sum(1 for e in frame.elements if e.never_settled)
    unknown_elements = max(0, unknown - unrepresented_count - stated_unknown)
    return ResolvedStereo(descriptor, resolution, smiles, tuple(reasons), unknown_elements)


def descriptor_of_mol(mol: Chem.Mol) -> str | None:
    """Descriptor read from the marks of a molecule alone (no geometry, no label).

    Used for queries (``C/C=C/C`` names ``E``) and to check that a derived SMILES
    round-trips.
    """

    frame = frame_from_mol(mol)
    if not frame.is_stereo_capable:
        return None
    return finish(frame, refine(frame, mark_tokens(frame)), with_smiles=False).descriptor


def stereo_smiles_for(frame: StereoFrame, tokens) -> str | None:
    """Canonical isomeric SMILES (isotopes stripped) of the configuration ``tokens`` name.

    Derived from the tokens alone, by finding the constitution's stereoisomer that
    has them, so it does not depend on which source supplied the configuration.
    ``None`` when no isomer matches, when the SMILES would not round-trip to the
    same descriptor (stripping isotopes can remove the stereo of an isotopically
    chiral centre), or when the molecule has too many elements to enumerate.
    """

    if any(e.kind in _CHAIN_KINDS or e.kind == _ALLENE_DEPENDENT for e in frame.elements):
        return None  # a SMILES cannot carry an allene axis or a cumulene's cis/trans through RDKit
    isomers = stereoisomers(frame)
    if not isomers:
        return None
    target = canonical_tokens(frame, tokens)
    descriptor = ",".join(target)
    for vector, iso_h in isomers:
        if vector != target:
            continue
        for candidate in (Chem.RemoveHs(iso_h), iso_h):
            stripped = Chem.Mol(candidate)
            for atom in stripped.GetAtoms():
                atom.SetIsotope(0)
            smiles = Chem.MolToSmiles(stripped, isomericSmiles=True, canonical=True)
            reparsed = Chem.MolFromSmiles(smiles)
            if reparsed is None:
                continue
            try:
                if descriptor_of_mol(reparsed) == descriptor:
                    return smiles
            except CodedValueError:
                continue
        return None
    return None
