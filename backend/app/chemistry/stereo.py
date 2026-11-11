"""Entry-level stereo descriptors.

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
cumulene within 30 degrees of planar): a cyclic allene in a ring of six or more reads like an acyclic one, and a
near-planar axis (a racemisation transition state) stays ``?``. An allene's chirality mark (``[C@]`` between the two
double bonds) is read from the SMILES text (:func:`allene_mark_token`, checked against CDK and the Centres labeller);
cumulene ``/`` ``\\`` marks are not read for now (:data:`CUMULENE_MARKS_READ`). Stated ``?`` whatever the data: a cyclic
allene in a ring of five or fewer atoms, and a centre or double bond whose stereogenicity depends on an allene's
sense and is not the plain case below (:data:`_ALLENE_DEPENDENT`). The plain case is a centre with two
constitutionally equal arms that each hold one allene axis (``CC=C=CC(C)C=C=CC``): ``-`` when the two senses are
equal, ``r`` or ``s`` when they differ, because an ``Ra`` arm outranks an ``Sa`` arm (IUPAC 2013 P-92.5, Rule 5); see
:func:`pseudo_geometry_token`.
"""

from __future__ import annotations

import itertools
import logging
import math
import re
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
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    PythonCheck,
    ScientificCheck,
)

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
#: past the automorphism cap, a cyclic allene in a ring of five or fewer, an element that depends on an allene's
#: sense, a cumulene given only as SMILES marks, an allene mark that cannot be read): real isomers may share the entry.
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
#: A cumulated chain with a tied pair of stereo-bearing substituents at an end: stereogenic through the arms' senses.
R_CUMULENE_TIED_END = "cumulene_tied_end"
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
#: arms that can be enantiomorphic). RDKit cannot see it. A centre of the plain class (``StereoElement.arms``)
#: is computed here; every other element of this kind is always ``?``.
_ALLENE_DEPENDENT = "allene_dependent"
#: Chain elements (anchored at the two chain ends, read from geometry only).
_CHAIN_KINDS = frozenset({_AXIAL, _CUMULENE})
#: A cyclic allene in a ring of at most this size is never read: no QM geometry has been checked for it (a ring of
#: four or five atoms may not even have the allene as a minimum). Rings of 6, 7 and 8 are read (#832).
_SMALL_RING_MAX = 5
#: Allene: the end-to-end torsion between the CIP-higher substituents must lie in [10, 170] degrees (ideal 90).
#:
#: The window is the same for every ring size that is read. Cyclic allenes in rings of 6, 7 and 8
#: relax to a twisted axis in QM (about 50 to 70 degrees; fixtures in ``tests/fixtures/cyclic_allene_xtb``) and are
#: read as Ra/Sa like any other; only a near-planar axis (the racemisation transition state) is left ``?``.
#: Do not read the missing InChI test for rings of 6 and 7 as a coverage gap: InChI cannot see an allene axis in a
#: ring of that size (it writes no stereo layer whatever the twist), so those rings are covered by a hand Newman
#: check of each fixture and by this reader's independence of ring size, which is validated against InChI's ``/m``
#: layer on rings of 8 and 9 and on acyclic allenes.
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
    #: Chain elements only: an end carries a tied pair of stereo-bearing substituents (``CC=C=CC(C=C=CC)=C=C(C)Cl``).
    #: The chain is then stereogenic through the senses of those arms, which needs a token we do not compute.
    tied_end: bool = False
    #: Allene-dependent centre only: the ``chain`` of each of its two tied arms' allene axes. Present when the
    #: centre is read (``-`` when the two senses are equal, ``r``/``s`` when they differ, see
    #: :func:`pseudo_centre_options`); empty when the class is not handled and the element stays ``?``.
    arms: tuple[tuple[int, ...], ...] = ()

    @property
    def never_settled(self) -> bool:
        """True when no data can settle this element (it is stated ``?`` always)."""

        return (
            (self.kind == _ALLENE_DEPENDENT and not self.arms)
            or self.tied_end
            or (self.kind == _AXIAL and 0 < self.ring_size <= _SMALL_RING_MAX)
        )

    @property
    def reason(self) -> str | None:
        """The ``not_represented`` reason of a never-settled element, else ``None``."""

        if self.kind == _ALLENE_DEPENDENT and not self.arms:
            return R_ALLENE_DEPENDENT
        if self.tied_end:
            return R_CUMULENE_TIED_END
        if self.never_settled:
            return R_AXIAL_SMALL_RING
        return None


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
        ``axial_small_ring``, ``allene_dependent``, ``cumulene_tied_end``, ``axial_marks``,
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
    mol_h: Chem.Mol, ranks_h: list[int], ranks: list[int], potential=()
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
    #: (ends, other substituents per end, path, kind, ring size) of every chain whose ends can be stereogenic
    candidates: list[tuple[list[int], list[list[int]], list[int], str, int]] = []
    for members in chains.values():
        if len(members) < 2:
            continue
        cumulated.update(members)
        member_set = set(members)
        ends = [a for a, bonds in by_atom.items() if len([x for x in bonds if x in member_set]) == 1]
        end_others: list[list[int]] = []
        for end in ends:
            atom = mol_h.GetAtomWithIdx(end)
            chain_neighbours = {
                n.GetIdx()
                for n in atom.GetNeighbors()
                if mol_h.GetBondBetweenAtoms(end, n.GetIdx()).GetIdx() in member_set
            }
            end_others.append([n.GetIdx() for n in atom.GetNeighbors() if n.GetIdx() not in chain_neighbours])
        if len(ends) != 2 or any(len(o) != 2 for o in end_others):
            continue
        path = [ends[0]]
        previous = -1
        while path[-1] != ends[1]:
            here = path[-1]
            step = next(
                n.GetIdx()
                for n in mol_h.GetAtomWithIdx(here).GetNeighbors()
                if n.GetIdx() != previous and mol_h.GetBondBetweenAtoms(here, n.GetIdx()).GetIdx() in member_set
            )
            previous = here
            path.append(step)
        ring_size = min((len(r) for r in rings if set(path) <= r), default=0)
        kind = _AXIAL if len(members) % 2 == 0 else _CUMULENE
        candidates.append((ends, end_others, path, kind, ring_size))

    def element(ends, path, kind, ring_size, tied_end):
        low, high = sorted((ranks[ends[0]], ranks[ends[1]]))
        return StereoElement(
            kind,
            (ends[0], ends[1]),
            (low, 2 if kind == _AXIAL else 3, high),
            chain=tuple(path),
            ring_size=ring_size,
            tied_end=tied_end,
        )

    # Stereo content: every ordinary centre and double bond RDKit lists, plus the chains accepted so far.
    content: set[int] = set()
    for info in potential:
        if info.type == Chem.StereoType.Atom_Tetrahedral:
            content.add(info.centeredOn)
        elif info.type == Chem.StereoType.Bond_Double and info.centeredOn not in cumulated:
            bond = mol_h.GetBondWithIdx(info.centeredOn)
            content.update((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()))
    pending = []
    for ends, end_others, path, kind, ring_size in candidates:
        tied = [ranks_h[o[0]] == ranks_h[o[1]] for o in end_others]
        if not any(tied):
            elements.append(element(ends, path, kind, ring_size, False))
            content.update(path)
        else:
            pending.append((ends, end_others, path, kind, ring_size, tied))
    changed = True
    while changed and pending:
        changed = False
        for item in list(pending):
            ends, end_others, path, kind, ring_size, tied = item
            ok = all(
                (not is_tied) or any(_branch(mol_h, m, end) & content for m in others)
                for end, others, is_tied in zip(ends, end_others, tied, strict=True)
            )
            if ok:
                elements.append(element(ends, path, kind, ring_size, True))
                content.update(path)
                pending.remove(item)
                changed = True
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


def _tied_group_status(
    mol_h: Chem.Mol,
    centre: int,
    neighbours: list[int],
    ranks_h: list[int],
    chains: list[tuple[int, ...]],
    content_atoms: set[int],
) -> tuple[bool, bool]:
    """``(every tied group has stereo content, some tied group holds allene axes in the sense below)`` for ``centre``.

    Whether ``centre`` has a group of constitutionally equal neighbours whose arms hold allene axes.

    Every tie pattern is looked at (3+1, 2+2, 2+1+1, four identical): the neighbours are grouped by rank and each
    group of two or more is tested on its own. Swapping two tied arms inverts the centre and leaves every ``Ra`` /
    ``Sa`` unchanged, so the centre is genuinely stereogenic whenever the arms carry enantiomorphic axes, and
    neither RDKit nor the allene-blind enumeration can see that. A group qualifies when

    * two or more of its arms are independent branches that each contain an axis, or
    * arms joined by a ring (or a spiro or bridged system: they share a branch once the centre is removed) hold two
      or more axes in that joined branch.

    Joined arms with a single axis (two arms of one ring, the axis opposite the centre) do not qualify: the swap
    that exchanges them is a symmetry of that ring and fixes the axis, so the centre is an ordinary one.

    A centre is stereogenic only if *every* tied group can differ in sense, so a tied group whose arms hold no
    stereo content at all (the two hydrogens of a CH2, a pair of methyls) makes the centre ordinary whatever else
    it carries; ``content_atoms`` are the atoms of every stereo element.
    """

    axial_atoms = {a for chain in chains for a in chain}
    by_rank: dict[int, list[int]] = {}
    for n in neighbours:
        by_rank.setdefault(ranks_h[n], []).append(n)
    tied = [g for g in by_rank.values() if len(g) >= 2]
    for members in tied:
        if not any(_branch(mol_h, m, centre) & content_atoms for m in members):
            return False, False
    qualifies = False
    for members in tied:
        branches = {m: _branch(mol_h, m, centre) for m in members}
        remaining = list(members)
        independent_with_axis = 0
        while remaining:
            component = [remaining.pop(0)]
            joined = set(branches[component[0]])
            grew = True
            while grew:
                grew = False
                for other in list(remaining):
                    if other in joined or branches[other] & joined:
                        remaining.remove(other)
                        component.append(other)
                        joined |= branches[other]
                        grew = True
            if len(component) == 1:
                independent_with_axis += bool(joined & axial_atoms)
            elif sum(1 for chain in chains if set(chain) <= joined) >= 2:
                qualifies = True
        if independent_with_axis >= 2:
            qualifies = True
    return True, qualifies


def _tied_group_depends_on_allene(
    mol_h: Chem.Mol,
    centre: int,
    neighbours: list[int],
    ranks_h: list[int],
    chains: list[tuple[int, ...]],
    content_atoms: set[int],
) -> bool:
    """A centre is allene-dependent when some tied group qualifies and every tied group has stereo content."""

    every_group_has_content, qualifies = _tied_group_status(mol_h, centre, neighbours, ranks_h, chains, content_atoms)
    return every_group_has_content and qualifies


def _allene_dependent_elements(
    mol_h: Chem.Mol, ranks_h: list[int], ranks: list[int], found: list[StereoElement], skipped: set[int]
) -> list[StereoElement]:
    """Centres and double bonds that are stereogenic only through the sense of an allene.

    ``CC=C=CC(C)C=C=CC``: the middle carbon carries two constitutionally equal arms, so RDKit sees no stereo
    element there. When the arms are enantiomorphic (``Ra`` against ``Sa``) it is a pseudo-asymmetric centre. The
    same holds for every other tie pattern (a spiro or quaternary centre with two tied pairs, four identical arms,
    arms joined by a ring). Neither RDKit's perception nor its CIP labeller can see allene chirality, so the
    element is listed. The plain class (:func:`_pseudo_centre_arms`) is computed; the others are always stated
    ``?``, never deduced non-stereogenic. Over-flagging is safe (``?`` and a warning); a wrong ``-`` is not.
    """

    chains = [e.chain for e in found if e.kind == _AXIAL]
    if not chains:
        return []
    content_atoms = {a for e in found for a in (e.chain or e.atoms)}
    centres: list[int] = []
    for atom in mol_h.GetAtoms():
        idx = atom.GetIdx()
        if atom.GetDegree() != 4:
            continue
        neighbours = [n.GetIdx() for n in atom.GetNeighbors()]
        if _tied_group_depends_on_allene(mol_h, idx, neighbours, ranks_h, chains, content_atoms):
            centres.append(idx)
    bonds: list[StereoElement] = []
    for bond in mol_h.GetBonds():
        if bond.GetBondType() != Chem.BondType.DOUBLE or bond.GetIdx() in skipped:
            continue
        begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        # Same rule as for a centre: an end with distinct substituents is compatible; an end with a tied pair is
        # compatible only if the pair carries stereo content (axes -> "allene", ordinary R/S or E/Z -> "content"),
        # and the bond is flagged when some end is "allene" and no end is incompatible.
        verdicts: list[str | None] = []
        for here, there in ((begin, end), (end, begin)):
            atom = mol_h.GetAtomWithIdx(here)
            others = [n.GetIdx() for n in atom.GetNeighbors() if n.GetIdx() != there]
            if len(others) != 2:
                verdicts.append(None)
            elif ranks_h[others[0]] != ranks_h[others[1]]:
                verdicts.append("distinct")
            else:
                has_content, qualifies = _tied_group_status(mol_h, here, others, ranks_h, chains, content_atoms)
                verdicts.append(None if not has_content else "allene" if qualifies else "content")
        if None in verdicts or "allene" not in verdicts:
            continue
        low, high = sorted((ranks[begin], ranks[end]))
        # A double bond is left ``?``: its pseudo-asymmetric e/z has no independent witness.
        bonds.append(StereoElement(_ALLENE_DEPENDENT, (begin, end), (low, 1, high)))
    other_dependent = set(centres) | {a for e in bonds for a in e.atoms}
    axial = {e.chain: e for e in found if e.kind == _AXIAL}
    extra: list[StereoElement] = []
    for idx in centres:
        arms = _pseudo_centre_arms(mol_h, idx, ranks_h, axial, content_atoms, other_dependent - {idx})
        extra.append(StereoElement(_ALLENE_DEPENDENT, (idx,), (ranks[idx], 0, ranks[idx]), arms=arms))
    return extra + bonds


def _pseudo_centre_arms(
    mol_h: Chem.Mol,
    centre: int,
    ranks_h: list[int],
    axial: dict[tuple[int, ...], StereoElement],
    content_atoms: set[int],
    other_dependent: set[int],
) -> tuple[tuple[int, ...], ...]:
    """The two arms' axis chains when ``centre`` is a plain pseudo-asymmetric centre, else ``()``.

    The handled class is the one the independent witness covers: a tetrahedral centre with exactly two
    constitutionally equal neighbours (the arms) and two others that differ from each other and from the arms;
    the arms are separate branches (no ring or spiro joins them), each holding exactly one allene axis that can
    be read and nothing else stereogenic, and no other allene-dependent element. Then the centre is ``-`` when
    the axes have the same sense and ``r``/``s`` when they differ. Every other tie pattern (3+1, 2+2, four equal
    arms, ring-shared or spiro arms, arms with further stereo content) returns ``()`` and stays ``?``. So do an
    arm root that is itself isotopic, and a ligand that ties the arms once isotopes are ignored (``CD3`` beside
    two ``CH3``-ended arms): the Rule 2 isotope decision would then fall between the arms and that ligand, and
    the marker, which acts at sphere 1, would put the arms in the wrong place.
    """

    neighbours = [n.GetIdx() for n in mol_h.GetAtomWithIdx(centre).GetNeighbors()]
    by_rank = Counter(ranks_h[n] for n in neighbours)
    if sorted(by_rank.values()) != [1, 1, 2]:
        return ()
    arms = [n for n in neighbours if by_rank[ranks_h[n]] == 2]
    # The marker that ranks the Ra arm above the Sa arm is a mass number on the arm's root atom, so a root that
    # already carries an isotope cannot take it: the centre is left ``?``.
    if any(mol_h.GetAtomWithIdx(a).GetIsotope() for a in arms):
        return ()
    # Canonical ranks count isotopes, so a ligand that differs from the arms only by an isotope deep inside (a CD3
    # arm beside two CH3 arms) looks distinct yet ties the arms at CIP Rule 1, where the isotope is decided later
    # (Rule 2) than the marker. Its true place relative to the arms is then not the marker's: leave the centre ``?``.
    blind = Chem.Mol(mol_h)
    for atom in blind.GetAtoms():
        atom.SetIsotope(0)
        atom.SetFormalCharge(0)
    blind_ranks = list(Chem.CanonicalRankAtoms(blind, breakTies=False))
    if any(blind_ranks[n] == blind_ranks[arms[0]] for n in neighbours if n not in arms):
        return ()
    branches = [_branch(mol_h, a, centre) for a in arms]
    if branches[0] & branches[1] or centre in branches[0] | branches[1]:
        return ()
    found_chains: list[tuple[int, ...]] = []
    for branch in branches:
        inside = [c for c in axial if set(c) <= branch]
        if len(inside) != 1 or axial[inside[0]].never_settled:
            return ()
        if (content_atoms | other_dependent) & branch != set(inside[0]):
            return ()
        found_chains.append(inside[0])
    return tuple(found_chains)


@lru_cache(maxsize=8192)
def unrepresented_stereo_kinds(smiles: str) -> tuple[str, ...]:
    """Stereo elements of a molecule that no data can settle.

    ``axial_small_ring`` (an allene in a ring of five or fewer), ``allene_dependent`` (a centre or double bond whose
    stereogenicity depends on an allene's sense) and ``cumulene_tied_end`` (a chain with a tied pair of
    stereo-bearing arms at an end) are always ``?`` in an entry's descriptor, so a
    reader must not take that ``?`` for "not yet supplied". Empty for a SMILES that does not parse, or a
    molecule with neither. (Cases that depend on the deposit, not the molecule -- the symmetry cap, allene
    marks -- are not listed here: they need the full resolution.)
    """

    from app.chemistry.species import identity_mol_from_smiles

    try:
        frame = frame_from_mol(identity_mol_from_smiles(smiles))
    except ValueError:
        return ()
    reasons = {e.reason for e in frame.elements if e.reason}
    return tuple(r for r in (R_AXIAL_SMALL_RING, R_ALLENE_DEPENDENT, R_CUMULENE_TIED_END) if r in reasons)


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
    potential = list(Chem.FindPotentialStereo(constitution_h))
    skipped, found = _cumulated_bonds(constitution_h, ranks_h, ranks, potential)

    for info in potential:
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
        elif element.kind == _AXIAL:
            tokens.append(allene_mark_token(frame, element))
        elif element.kind in (_AXIAL, _ALLENE_DEPENDENT, _CUMULENE):
            tokens.append(UNKNOWN)
        elif element.kind == _TETRAHEDRAL:
            tokens.append(_atom_token(marked.GetAtomWithIdx(frame.marked_index[element.atoms[0]])))
        else:
            begin, end = (frame.marked_index[index] for index in element.atoms)
            tokens.append(_bond_token(marked.GetBondBetweenAtoms(begin, end)))
    slot = _chain_slots(frame)
    for i, element in enumerate(frame.elements):
        if element.kind == _ALLENE_DEPENDENT and element.arms:
            tokens[i] = pseudo_mark_token(frame, element, tuple(tokens[slot[arm]] for arm in element.arms))
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
    rings of five or fewer are never read (see :data:`_ALLENE_TORSION_WINDOW` for what is validated for the rest).
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
    raw = Chem.MolFromSmiles(frame.marked.GetProp(SMILES_PROP), params)
    if raw is not None:
        # a standard isotope ([1H], [12C]) names nothing: the identity molecule has cleared it, so must the raw one
        for atom in raw.GetAtoms():
            if atom.GetIsotope() and _standard_isotope(atom.GetSymbol(), atom.GetIsotope()):
                atom.SetIsotope(0)
    return raw


def _standard_isotope(symbol: str, mass_number: int) -> bool:
    from app.chemistry.isotopes import normalize_isotope

    try:
        return normalize_isotope(symbol, mass_number) is None
    except (KeyError, ValueError):
        return False


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
        sides = _mark_sides(frame) or {}
        frame.cache["mark_sides"] = sides
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
    raw_to_constitution = _raw_to_constitution(raw, frame)
    if raw_to_constitution is None:
        return None
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


_ORGANIC_SUBSET = ("Cl", "Br", "B", "C", "N", "O", "P", "S", "F", "I", "b", "c", "n", "o", "p", "s", "*")
_NORMAL_VALENCE = {"C": 4, "c": 4}


@dataclass
class _TextAtom:
    """One atom of a SMILES text, in text order, with its neighbours in the order the text writes them.

    ``neighbours`` holds ``(atom index, bond order)`` for a bonded atom and ``("H", 0)`` for a hydrogen that
    is not an atom of its own (an implicit one, or the count of a bracket atom). OpenSMILES puts that hydrogen
    immediately after the preceding atom, before any ring closure or branch; a ring-closure digit takes its
    place at the digit, on both of its atoms.
    """

    symbol: str
    isotope: int = 0
    mark: str = ""
    has_from: bool = False
    hydrogens: int = 0
    neighbours: list = field(default_factory=list)


def _scan_smiles(text: str) -> list[_TextAtom] | None:
    """A minimal reader of the neighbour order of a SMILES text; ``None`` when the text is not understood."""

    atoms: list[_TextAtom] = []
    stack: list[int] = []
    previous: int | None = None
    bond_order = 1
    rings: dict[str, tuple[int, int, int]] = {}  # digit -> (atom, position in its neighbour list, order)
    i = 0
    n = len(text)

    def attach(atom: _TextAtom, hydrogens: int) -> None:
        nonlocal previous, bond_order
        index = len(atoms)
        atoms.append(atom)
        atom.hydrogens = hydrogens
        if previous is not None:
            atoms[previous].neighbours.append((index, bond_order))
            atom.neighbours.append((previous, bond_order))
            atom.has_from = True
        bond_order = 1
        previous = index

    while i < n:
        c = text[i]
        if c == "(":
            if previous is None:
                return None
            stack.append(previous)
            i += 1
        elif c == ")":
            if not stack:
                return None
            previous = stack.pop()
            i += 1
        elif c in "-=#:/\\$":
            bond_order = {"=": 2, "#": 3}.get(c, 1)
            i += 1
        elif c == ".":
            previous = None
            i += 1
        elif c == "[":
            j = text.find("]", i)
            if j < 0:
                return None
            body = text[i + 1 : j]
            m = re.fullmatch(r"(\d*)([A-Za-z][a-z]?|\*)(@{1,2})?(?:H(\d*))?([+-]+\d*|)(?::\d+)?", body)
            if m is None:
                return None
            isotope = int(m.group(1)) if m.group(1) else 0
            hcount = 0 if m.group(4) is None else (int(m.group(4)) if m.group(4) else 1)
            attach(_TextAtom(m.group(2), isotope, m.group(3) or ""), hcount)
            i = j + 1
        elif c.isdigit() or c == "%":
            if c == "%":
                digit, i = text[i : i + 3], i + 3
            else:
                digit, i = c, i + 1
            if previous is None:
                return None
            if digit in rings:
                opener, position, order = rings.pop(digit)
                order = max(order, bond_order)
                atoms[opener].neighbours[position] = (previous, order)
                atoms[previous].neighbours.append((opener, order))
            else:
                rings[digit] = (previous, len(atoms[previous].neighbours), bond_order)
                atoms[previous].neighbours.append(("R", digit))
            bond_order = 1
        else:
            token = next((t for t in _ORGANIC_SUBSET if text.startswith(t, i)), None)
            if token is None:
                return None
            attach(_TextAtom(token), -1)
            i += len(token)
    if rings or stack:
        return None
    for atom in atoms:
        count = atom.hydrogens
        if count < 0:  # organic-subset atom: implicit hydrogens fill the normal valence
            valence = _NORMAL_VALENCE.get(atom.symbol)
            if valence is None:
                continue
            used = sum(order for _, order in atom.neighbours)
            count = max(0, valence - used)
        for _ in range(count):  # right after the "from" atom, before ring closures and branches
            atom.neighbours.insert(1 if atom.has_from else 0, ("H", 0))
    return atoms


def allene_mark_token(frame: StereoFrame, element: StereoElement) -> str:
    """``Ra``/``Sa`` of an allene from the chirality mark on its central atom (OpenSMILES extended tetrahedral).

    The four substituents of the two ends are listed in the order the text writes them (the first end's, then
    the second's; an implicit or bracket hydrogen right after its owner, a ring-closure digit at its digit).
    ``@`` looks from the first one with the rest counter-clockwise, ``@@`` clockwise. The list is permuted to
    ``(CIP-higher at the first end, lower there, higher at the second end, lower there)``; in that order
    ``@@`` is ``Ra`` and ``@`` is ``Sa`` (constant fixed by hand on ``NC(Br)=[C@@]=C(O)C``, whose first end
    outranks N with Br and whose second outranks C with O, and checked against an independent labeller).
    ``?`` when the text is absent, unmarked, not understood or does not match the molecule.
    """

    if element.never_settled or len(element.chain) != 3 or not frame.marked.HasProp(SMILES_PROP):
        return UNKNOWN
    atoms = _scan_smiles(frame.marked.GetProp(SMILES_PROP))
    raw = _raw_marks_mol(frame)
    if atoms is None or raw is None or len(atoms) != raw.GetNumAtoms():
        return UNKNOWN
    if any(a.symbol.capitalize() != r.GetSymbol() for a, r in zip(atoms, raw.GetAtoms(), strict=True)):
        return UNKNOWN  # the scanner and the parser disagree about the text: read nothing
    raw_to_constitution = _raw_to_constitution(raw, frame)
    if raw_to_constitution is None:
        return UNKNOWN
    constitution_to_raw = {c: r for r, c in raw_to_constitution.items()}
    end_a, centre, end_b = element.chain
    if centre not in constitution_to_raw:
        return UNKNOWN
    raw_centre = constitution_to_raw[centre]
    mark = atoms[raw_centre].mark
    if mark not in ("@", "@@"):
        return UNKNOWN
    ends = [n for n, order in atoms[raw_centre].neighbours if n != "H" and n != "R" and order == 2]
    if len(ends) != 2:
        return UNKNOWN
    mol_h = frame.constitution_h
    labelled: list[int] = []  # the four substituents in text order, as mol_h atoms
    targets: list[int] = []  # (higher, lower) at the first written end, then at the second
    for raw_end in ends:
        end = raw_to_constitution.get(raw_end)
        if end not in (end_a, end_b):
            return UNKNOWN
        chain_neighbour = element.chain[1] if end == end_a else element.chain[-2]
        order = cip_higher_substituent(mol_h, end, chain_neighbour)
        if order is None:
            return UNKNOWN
        slots = [(k, o) for k, o in atoms[raw_end].neighbours if k != raw_centre]
        if len(slots) != 2:
            return UNKNOWN
        used: set[int] = set()
        for kind, _ in slots:
            if kind == "H":
                isotope: int | None = 0
            elif kind == "R" or atoms[kind].symbol != "H":
                isotope = None
            else:
                isotope = atoms[kind].isotope
                if isotope and _standard_isotope("H", isotope):
                    isotope = 0
            if isotope is None:
                mapped = raw_to_constitution.get(kind)
            else:
                mapped = next(
                    (
                        a
                        for a in order
                        if mol_h.GetAtomWithIdx(a).GetAtomicNum() == 1
                        and mol_h.GetAtomWithIdx(a).GetIsotope() == isotope
                        and a not in used
                    ),
                    None,
                )
            if mapped is None or mapped not in order:
                return UNKNOWN
            used.add(mapped)
            labelled.append(mapped)
        targets.extend(order)
    if len(set(labelled)) != 4 or sorted(labelled) != sorted(targets):
        return UNKNOWN
    # parity of the permutation taking the text order to (higher, lower, higher, lower)
    permutation = [labelled.index(t) for t in targets]
    swaps = 0
    seen = [False] * 4
    for start in range(4):
        if seen[start]:
            continue
        length, j = 0, start
        while not seen[j]:
            seen[j] = True
            j = permutation[j]
            length += 1
        swaps += length - 1
    clockwise = (mark == "@@") != (swaps % 2 == 1)
    return "Ra" if clockwise else "Sa"


def pseudo_mark_token(frame: StereoFrame, element: StereoElement, arm_tokens: tuple[str, ...]) -> str:
    """``r``/``s`` of an allene-dependent centre from the chirality mark on the centre's own atom in the SMILES.

    The mark is on the centre (``[C@H]``), which RDKit drops on sanitising when the two arms are
    constitutionally equal; here the raw parse keeps it. As for geometry (:func:`pseudo_geometry_token`) the
    ``Ra`` arm's root atom takes a mass number one above natural, which makes the centre a real stereocentre
    that survives sanitising, and the CIP labeller names it. Only the centre's own tag is kept (the allene's
    tags are read by :func:`allene_mark_token`). ``-`` when the arms' senses are equal, ``?`` when they are not
    both known, the text is unmarked or it cannot be matched onto the molecule.
    """

    options = pseudo_centre_options(arm_tokens)
    if len(options) == 1:
        return options[0]  # equal senses: ``-``; a sense not known: ``?``
    raw = _raw_marks_mol(frame)
    mapping = _raw_to_constitution(raw, frame) if raw is not None else None
    if raw is None or mapping is None:
        return UNKNOWN
    to_raw = {c: r for r, c in mapping.items()}
    mol_h = frame.constitution_h
    centre = element.atoms[0]
    ra_chain = element.arms[0] if arm_tokens[0] == "Ra" else element.arms[1]
    root = next(
        (n.GetIdx() for n in mol_h.GetAtomWithIdx(centre).GetNeighbors() if set(ra_chain) <= _branch(mol_h, n.GetIdx(), centre)),
        None,
    )
    if root is None or centre not in to_raw or root not in to_raw:
        return UNKNOWN
    raw_centre, raw_root = to_raw[centre], to_raw[root]
    tags = (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW)
    if raw.GetAtomWithIdx(raw_centre).GetChiralTag() not in tags or raw.GetAtomWithIdx(raw_root).GetIsotope():
        return UNKNOWN
    marked = Chem.Mol(raw)
    for atom in marked.GetAtoms():
        if atom.GetIdx() != raw_centre:
            atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    root_atom = marked.GetAtomWithIdx(raw_root)
    root_atom.SetIsotope(Chem.GetPeriodicTable().GetMostCommonIsotope(root_atom.GetAtomicNum()) + 1)
    try:
        Chem.SanitizeMol(marked)
        Chem.AssignStereochemistry(marked, cleanIt=True, force=True)
        rdCIPLabeler.AssignCIPLabels(marked)
    except (ValueError, RuntimeError):
        return UNKNOWN
    code = _atom_token(marked.GetAtomWithIdx(raw_centre))
    return code.lower() if code in ("R", "S") else UNKNOWN


def _allene_centre_marked(frame: StereoFrame, element: StereoElement) -> bool:
    """Whether the SMILES puts a chirality mark on the middle atom of this axial chain."""

    raw = _raw_marks_mol(frame)
    if raw is None:
        return False
    mapping = _raw_to_constitution(raw, frame)
    if mapping is None:  # the text cannot be matched onto the molecule: any mark on an allene counts
        return allene_marks_present(frame)
    centre = element.chain[len(element.chain) // 2]  # the middle atom of an allene or of any even cumulene
    unmarked = (Chem.ChiralType.CHI_UNSPECIFIED, Chem.ChiralType.CHI_OTHER)
    return any(c == centre and raw.GetAtomWithIdx(r).GetChiralTag() not in unmarked for r, c in mapping.items())


def _raw_to_constitution(raw: Chem.Mol, frame: StereoFrame) -> dict[int, int] | None:
    """Raw-parse atom index -> constitution atom index; ``None`` when the text cannot be matched onto the molecule."""

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
    to_constitution = {m: i for i, m in enumerate(match)}
    mapping: dict[int, int] = {}
    for atom in raw_constitution.GetAtoms():
        index = to_constitution.get(atom.GetIdx())
        if index is None:  # the text keeps an atom the molecule does not: it cannot be matched
            return None
        mapping[atom.GetIntProp("_raw_idx")] = index
    return mapping


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
            try:
                tokens.append(chain_geometry_token(element, mol, mapping))
            except (ValueError, RuntimeError, KeyError):  # a geometry the labeller cannot probe says nothing
                tokens.append(UNKNOWN)
        elif element.kind == _ALLENE_DEPENDENT:
            tokens.append(UNKNOWN)  # a centre with readable arms is filled below, once the axes are known
        elif element.kind == _TETRAHEDRAL:
            tokens.append(_atom_token(mol.GetAtomWithIdx(mapping[element.atoms[0]])))
        else:
            begin, end = element.atoms
            tokens.append(_bond_token(mol.GetBondBetweenAtoms(mapping[begin], mapping[end])))
    slot = _chain_slots(frame)
    for i, element in enumerate(frame.elements):
        if element.kind == _ALLENE_DEPENDENT and element.arms:
            try:
                tokens[i] = pseudo_geometry_token(
                    frame, element, mol, mapping, tuple(tokens[slot[arm]] for arm in element.arms)
                )
            except (ValueError, RuntimeError, KeyError):
                tokens[i] = UNKNOWN
    return tokens


def _chain_slots(frame: StereoFrame) -> dict[tuple[int, ...], int]:
    """Element index of each cumulated chain, by its atom path."""

    return {e.chain: i for i, e in enumerate(frame.elements) if e.kind in _CHAIN_KINDS}


def pseudo_centre_options(arm_tokens: tuple[str, ...]) -> tuple[str, ...]:
    """The tokens an allene-dependent centre takes for two arm senses: ``-`` for equal, ``r``/``s`` for unequal.

    Equal senses make the arms identical (swapping them is a symmetry), so the centre is not stereogenic.
    Opposite senses make the arms enantiomorphic, so exchanging two of the centre's ligands gives a different
    molecule: pseudo-asymmetric ``r`` or ``s``. ``?`` when a sense is not known.
    """

    if UNKNOWN in arm_tokens or not all(t in AXIAL_TOKENS for t in arm_tokens):
        return (UNKNOWN,)
    return (NOT_STEREOGENIC,) if arm_tokens[0] == arm_tokens[1] else ("r", "s")


def pseudo_geometry_token(
    frame: StereoFrame, element: StereoElement, mol: Chem.Mol, mapping, arm_tokens: tuple[str, ...]
) -> str:
    """``-``/``r``/``s`` of an allene-dependent centre read from coordinates, given its arms' ``Ra``/``Sa``.

    The rule (IUPAC 2013 P-92.5, Rule 5): an ``Ra`` (``M``) arm outranks an ``Sa`` (``P``) arm, then ordinary CIP
    on the centre. It is applied by giving the root atom of the ``Ra`` arm a mass number one above its natural
    one: that decides the tie between the two arms (Rule 2) and nothing else, because the arms are
    constitutionally identical and differ from the other ligands at Rule 1 (:func:`_pseudo_centre_arms` leaves a
    centre ``?`` where a ligand ties the arms once isotopes are ignored). The centre's chirality is read from
    the coordinates and labelled by the CIP labeller; the label is the pseudo-asymmetric ``r``/``s``.
    """

    options = pseudo_centre_options(arm_tokens)
    if len(options) == 1:
        return options[0]
    if mol.GetNumConformers() == 0:
        return UNKNOWN
    mol_h = frame.constitution_h
    centre = element.atoms[0]
    ra_chain = element.arms[0] if arm_tokens[0] == "Ra" else element.arms[1]
    roots = [n.GetIdx() for n in mol_h.GetAtomWithIdx(centre).GetNeighbors()]
    root = next(r for r in roots if set(ra_chain) <= _branch(mol_h, r, centre))
    marked = Chem.Mol(mol)
    atom = marked.GetAtomWithIdx(mapping[root])
    if atom.GetIsotope():
        return UNKNOWN
    atom.SetIsotope(Chem.GetPeriodicTable().GetMostCommonIsotope(atom.GetAtomicNum()) + 1)
    Chem.AssignStereochemistryFrom3D(marked)
    rdCIPLabeler.AssignCIPLabels(marked)
    code = _atom_token(marked.GetAtomWithIdx(mapping[centre]))
    return code.lower() if code in ("R", "S") else UNKNOWN


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
    A centre whose two arms hold allene axes takes ``-`` when the senses are equal and ``r``/``s`` when they
    differ (:func:`pseudo_centre_options`). The cases still missed are documented: an allene-dependent element
    of any other class stays ``?`` (``allene_dependent``), so such a molecule has fewer descriptors than isomers.
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
    slot_of = _chain_slots(frame)
    pseudo = [i for i, e in enumerate(frame.elements) if e.kind == _ALLENE_DEPENDENT and e.arms]
    seen: dict[tuple[str, ...], Chem.Mol] = {}
    try:
        for iso in EnumerateStereoisomers(frame.constitution_h, options=options):
            base = _full_tokens(frame, iso)
            for combo in itertools.product(*choices):
                tokens = list(base)
                for i, value in zip(slots, combo, strict=True):
                    tokens[i] = value
                centre_options = [
                    pseudo_centre_options(tuple(tokens[slot_of[arm]] for arm in frame.elements[i].arms))
                    for i in pseudo
                ]
                for picks in itertools.product(*centre_options):
                    for i, value in zip(pseudo, picks, strict=True):
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
    consistent stereoisomer makes stereogenic stays ``?``. That includes the axes:
    ``?,Ra,-`` at a centre between two allenes forces the first axis to ``Ra`` (a ``-`` there means equal
    senses), but only ``-`` is ever deduced, so the axis stays ``?``. The inference is deliberately not drawn.

    :raises CodedValueError: ``species_stereo_contradiction`` when no stereoisomer
        has the configuration the sources together state.
    """

    # A fully stated vector is checked too: sources that each pass alone can combine into a configuration no
    # stereoisomer has (equal allene senses with an ``r`` between them, ``R,S`` around a ``-`` that needs ``R,R``).
    # Past the enumeration cap there is no realizable set to check against, and the tokens stand as before.
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
    """Validate a supplied label against the molecule it labels.

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
            if element.reason is None:  # unreachable: a never-settled element always names its reason
                raise RuntimeError("a never-settled stereo element has no reason")
            why = {
                R_ALLENE_DEPENDENT: "depends on the sense of an allene, which is not represented",
                R_CUMULENE_TIED_END: "is a cumulated chain with a pair of stereo-bearing arms at one end, which is not represented",
                R_AXIAL_SMALL_RING: f"is a cyclic allene in a ring of {_SMALL_RING_MAX} or fewer, which is not represented",
            }[element.reason]
            raise _label_invalid(f"token {position} ({token!r}) names an element that {why}; say '?'", expected)
        allowed = {
            _TETRAHEDRAL: TETRAHEDRAL_TOKENS,
            _DOUBLE_BOND: DOUBLE_BOND_TOKENS,
            _AXIAL: AXIAL_TOKENS,
            _CUMULENE: CUMULENE_TOKENS,
            _ALLENE_DEPENDENT: frozenset({"r", "s"}),
        }[element.kind]
        extra = (
            (UNKNOWN, NOT_STEREOGENIC)
            if element.kind in (_TETRAHEDRAL, _DOUBLE_BOND, _ALLENE_DEPENDENT)
            else (UNKNOWN,)
        )
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
    if element.kind == _ALLENE_DEPENDENT and element.arms:
        return "a centre between two allene axes (r, s or -)"
    if element.kind == _ALLENE_DEPENDENT:
        return "an element that depends on the sense of an allene (only '?')"
    if element.tied_end:
        return "a cumulated chain with stereo-bearing arms at one end (only '?')"
    if element.never_settled:
        return f"a cyclic allene in a ring of {_SMALL_RING_MAX} or fewer (only '?')"
    return {
        _TETRAHEDRAL: "a tetrahedral centre",
        _DOUBLE_BOND: "a double bond",
        _AXIAL: "an allene axis (Ra or Sa)",
        _CUMULENE: "a cumulene (E or Z)",
    }[element.kind]


def _kind_text(element: StereoElement) -> str:
    if element.kind == _ALLENE_DEPENDENT and element.arms:
        return "r/s (pseudo-asymmetric, from the two allene senses)"
    if element.kind == _ALLENE_DEPENDENT:
        return "? (depends on an allene's sense, not represented)"
    if element.tied_end:
        return "? (cumulated chain with stereo-bearing arms at one end, not represented)"
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

    An allene's chirality mark (``[C@]`` between the two double bonds) is read by :func:`allene_mark_token`; a
    mark that reader cannot use (a text it does not understand, atoms it cannot match onto the molecule) leaves the
    axis ``?`` and says why, instead of inviting more of the same. A cumulene's ``/`` ``\\`` marks are not read either while
    :data:`CUMULENE_MARKS_READ` is off.
    """

    open_axial = sum(
        1
        for e, t in zip(frame.elements, tokens, strict=True)
        if e.kind == _AXIAL and not e.never_settled and t == UNKNOWN and _allene_centre_marked(frame, e)
    )
    open_cumulene = sum(1 for e, t in zip(frame.elements, tokens, strict=True) if e.kind == _CUMULENE and not e.never_settled and t == UNKNOWN)
    reasons = set(result.unrepresented)
    removed = 0
    if open_axial:
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
        if e.reason and e.reason not in reasons:
            reasons.append(e.reason)
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


# ---------------------------------------------------------------------------
# Scientific check registration (see backend/app/scientific_checks)
# ---------------------------------------------------------------------------

CHECK_SPECIES_ENTRY_STEREO_AGREES = ScientificCheck(
    group="Stereochemistry of a species entry",
    sort_key=1,
    code=W_SPECIES_STEREO_CONTRADICTION,
    asserts=(
        "A species entry states one stereo configuration: its geometries, SMILES marks and "
        "any supplied stereo label agree wherever they state the same element, up to the "
        "molecule's symmetry."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    enforced_by=(
        PythonCheck(resolve_stereo),
        PythonCheck(merge_sources),
        PythonCheck(_refuse_disagreement_past_the_cap),
    ),
    escape_hatch=(
        "State ``?`` for the element, omit the label, or deposit the other isomer as its own "
        "species entry."
    ),
    thresholds=(
        ConstantThreshold(
            name="allene_torsion_window",
            value=_ALLENE_TORSION_WINDOW[0],
            unit="degree (lower bound; the upper bound is 170)",
            rationale=(
                "An allene's end-to-end torsion is read only inside this window, because outside it "
                "the axis is flattened and states nothing."
            ),
        ),
        ConstantThreshold(
            name="odd_cumulene_planar_tolerance",
            value=_CUMULENE_PLANAR_TOLERANCE,
            unit="degree",
            rationale=(
                "An odd cumulene is Z within this torsion of planar, E within it of anti-planar, and "
                "otherwise unread."
            ),
        ),
        ConstantThreshold(
            name="automorphism_cap",
            value=_AUTOMORPHISM_CAP,
            unit="automorphisms examined",
            rationale="Past this many automorphisms the sources are compared as multisets instead.",
        ),
        ConstantThreshold(
            name="enumeration_cap",
            value=_ENUMERATION_CAP,
            unit="potential stereo elements",
            rationale="Above this many potential elements the stereoisomer enumeration is skipped.",
        ),
    ),
)
