"""Entry-level stereo descriptors (DR-0038).

A *species* is a constitution: the stereo-blind canonical SMILES plus charge and
multiplicity. Stereochemistry is resolved only on the *species entry*, as a
canonical **stereo descriptor** string: one token per potential stereo element,
``R``/``S`` for a tetrahedral centre, ``E``/``Z`` for a stereogenic double bond,
``?`` for an element nothing pinned down. Examples: ``E``, ``R,S``, ``E,?``.

Vocabulary
----------
``R S`` / ``E Z``
    CIP labels (RDKit's new CIP labeller, ``rdCIPLabeler``). Never read from
    cis/trans or the bond-stereo enum: those are relative to whichever reference
    atoms RDKit picked and flip with atom order outside legacy perception.
``r s``
    CIP's lower-case labels for a *pseudo-asymmetric* centre (C3 of
    pentane-2,3,4-triol in the ``R,S`` diastereomer).
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
one string. Enumerating the automorphisms is capped (:data:`_AUTOMORPHISM_CAP`);
above the cap the deposit is refused rather than mislabelled.

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

Not representable
-----------------
A stereogenic cumulene (an allene with different substituents at each end, or any
cumulated chain whose ends both carry two different substituents) has axial, not
E/Z, stereo. It is refused with :data:`W_SPECIES_STEREO_UNSUPPORTED` rather than
treated as two double bonds. A cumulated chain that is not stereogenic (allene
itself, a ketene, sulfone S=O bonds) simply contributes no element.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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

#: Raised for stereo this vocabulary cannot represent (axial stereo of a
#: cumulene) or cannot canonicalise within its cap.
W_SPECIES_STEREO_UNSUPPORTED = "species_stereo_unsupported"

#: The non-blocking warning for an entry that ends with at least one ``?``.
W_SPECIES_ENTRY_STEREO_UNRESOLVED = "species_entry_stereo_unresolved"

UNKNOWN = "?"
NOT_STEREOGENIC = "-"
TETRAHEDRAL_TOKENS = frozenset({"R", "S", "r", "s"})
DOUBLE_BOND_TOKENS = frozenset({"E", "Z"})
VOCABULARY = TETRAHEDRAL_TOKENS | DOUBLE_BOND_TOKENS | {UNKNOWN, NOT_STEREOGENIC}

#: Wire pattern for ``stereo_label``/``stereo_descriptor``; the schema package
#: carries its own copy because it cannot import the backend.
DESCRIPTOR_PATTERN = r"^[RSrsEZ?-](,[RSrsEZ?-])*$"

#: Above this many potential elements the stereoisomer enumeration is skipped:
#: ``-`` is not deduced, a supplied label is not tested for realisability and
#: ``stereo_smiles`` is left NULL.
_ENUMERATION_CAP = 8

#: Most automorphisms of the constitution examined before the deposit is refused.
_AUTOMORPHISM_CAP = 100_000

#: Order in which tokens compare when the smallest string is chosen; ``?`` is last,
#: so a partial descriptor reads ``E,?`` and never ``?,E``.
_ORDER = {"R": 0, "S": 1, "r": 2, "s": 3, "E": 4, "Z": 5, "-": 6, "?": 7}

_TETRAHEDRAL = "tetrahedral"
_DOUBLE_BOND = "double_bond"


@dataclass(frozen=True)
class StereoElement:
    """One potential stereo element of the constitution graph.

    :param kind: ``tetrahedral`` or ``double_bond``.
    :param atoms: One atom index (tetrahedral) or the two bonded atom indices
        (double bond), in the constitution molecule's atom numbering.
    :param key: Sort/class key built from canonical ranks.
    """

    kind: str
    atoms: tuple[int, ...]
    key: tuple[int, ...]


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
    """

    descriptor: str | None
    resolution: StereoResolution | None
    stereo_smiles: str | None

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


def _unsupported(problem: str) -> CodedValueError:
    return CodedValueError(
        W_SPECIES_STEREO_UNSUPPORTED,
        f"This molecule's stereochemistry cannot be represented: {problem}. "
        "Axial stereo of a cumulene has no token in the stereo descriptor vocabulary, and treating it as "
        "two E/Z double bonds would be wrong.",
        context={"problem": problem},
        message_prefix=False,
    )


def _cumulated_bonds(mol_h: Chem.Mol, ranks_h: list[int]) -> set[int]:
    """Indices of double bonds that belong to a cumulated chain.

    :raises CodedValueError: ``species_stereo_unsupported`` when a chain's two
        ends each carry two different substituents (axial or cumulene E/Z stereo).
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
    for members in chains.values():
        if len(members) < 2:
            continue
        cumulated.update(members)
        member_set = set(members)
        ring_info = mol_h.GetRingInfo()
        if any(ring_info.NumBondRings(i) and min(len(r) for r in ring_info.BondRings() if i in r) < 8 for i in members):
            # A chain closed in a ring of fewer than eight members cannot take the linear geometry axial stereo needs.
            continue
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
            raise _unsupported(
                "a cumulated double-bond chain (allene or cumulene) has different substituents at both ends"
            )
    return cumulated


def frame_from_mol(marked: Chem.Mol) -> StereoFrame:
    """Build the stereo frame for a sanitized molecule.

    :param marked: The entry's molecule with isotopes kept (an entry is
        isotopologue-aware) and stereo marks retained.
    :raises CodedValueError: ``species_stereo_unsupported`` for a stereogenic
        cumulene.
    """

    tagged = Chem.Mol(marked)
    for atom in tagged.GetAtoms():
        atom.SetIntProp("_marked_idx", atom.GetIdx())
    constitution = constitution_mol(tagged)
    marked_index = tuple(atom.GetIntProp("_marked_idx") for atom in constitution.GetAtoms())
    constitution_h = Chem.AddHs(constitution)
    ranks = list(Chem.CanonicalRankAtoms(constitution, breakTies=False))
    ranks_h = list(Chem.CanonicalRankAtoms(constitution_h, breakTies=False))
    skipped = _cumulated_bonds(constitution_h, ranks_h)

    found: list[StereoElement] = []
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
        if element.kind == _TETRAHEDRAL:
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

    :raises CodedValueError: ``species_stereo_unsupported`` above the cap.
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
        raise _unsupported(
            "the molecule's symmetry is too large to canonicalise its stereo descriptor "
            f"(more than {_AUTOMORPHISM_CAP} automorphisms)"
        )
    index = _element_index(frame)
    perms = {identity}
    for match in matches:
        image = []
        for element in frame.elements:
            if element.kind == _TETRAHEDRAL:
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


def canonical_tokens(frame: StereoFrame, tokens) -> tuple[str, ...]:
    """The smallest token string over every automorphism (the descriptor's tokens)."""

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
        if element.kind == _TETRAHEDRAL:
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
        if element.kind == _TETRAHEDRAL:
            tokens.append(_atom_token(mol.GetAtomWithIdx(mapping[element.atoms[0]])))
        else:
            begin, end = element.atoms
            tokens.append(_bond_token(mol.GetBondBetweenAtoms(mapping[begin], mapping[end])))
    return tokens


# ---------------------------------------------------------------------------
# Enumerating the constitution's stereoisomers: realisability and "-"
# ---------------------------------------------------------------------------


def _full_tokens(frame: StereoFrame, iso_h: Chem.Mol) -> list[str]:
    """Tokens of a fully specified stereoisomer (explicit hydrogens)."""

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


def stereoisomers(frame: StereoFrame) -> list[tuple[tuple[str, ...], Chem.Mol]] | None:
    """Every distinct stereoisomer of the constitution as ``(canonical tokens, mol)``.

    ``None`` when the molecule has more than :data:`_ENUMERATION_CAP` potential
    elements. Cached on the frame.
    """

    if "isomers" in frame.cache:
        return frame.cache["isomers"]
    n = len(frame.elements)
    if n == 0 or n > _ENUMERATION_CAP:
        frame.cache["isomers"] = None
        return None
    options = StereoEnumerationOptions(
        onlyUnassigned=True, unique=True, tryEmbedding=False, maxIsomers=2 ** (n + 1)
    )
    seen: dict[tuple[str, ...], Chem.Mol] = {}
    try:
        for iso in EnumerateStereoisomers(frame.constitution_h, options=options):
            tokens = canonical_tokens(frame, _full_tokens(frame, iso))
            seen.setdefault(tokens, iso)
    except (ValueError, RuntimeError):
        frame.cache["isomers"] = None
        return None
    frame.cache["isomers"] = list(seen.items())
    return frame.cache["isomers"]


def _alignments(frame: StereoFrame, tokens, isomers) -> list[tuple[str, ...]]:
    """The isomers' token vectors aligned onto ``tokens`` positions that agree with them."""

    aligned = []
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
    kinds = ["R/S" if element.kind == _TETRAHEDRAL else "E/Z" for element in frame.elements]
    expected = ",".join(kinds) if kinds else "none"
    if not frame.elements:
        raise _label_invalid("this molecule has no stereo element, so it takes no stereo label", expected)
    if len(tokens) != len(frame.elements):
        raise _label_invalid(
            f"expected {len(frame.elements)} token(s), one per stereo element, got {len(tokens)}", expected
        )
    for position, (token, element) in enumerate(zip(tokens, frame.elements, strict=True), 1):
        allowed = TETRAHEDRAL_TOKENS if element.kind == _TETRAHEDRAL else DOUBLE_BOND_TOKENS
        if token not in allowed and token not in (UNKNOWN, NOT_STEREOGENIC):
            raise _label_invalid(
                f"token {position} ({token!r}) cannot label a "
                f"{'tetrahedral centre' if element.kind == _TETRAHEDRAL else 'double bond'}",
                expected,
            )
    if is_realizable(frame, tokens) is False:
        raise _label_invalid(
            "the label names a configuration that no stereoisomer of this molecule has (for example a "
            "label for an element that is not stereogenic in the configuration the other tokens fix)",
            expected,
        )
    return tokens


def _label_invalid(problem: str, expected: str) -> CodedValueError:
    return CodedValueError(
        W_SPECIES_STEREO_LABEL_INVALID,
        f"species_entry.stereo_label does not fit the molecule: {problem}. "
        f"Tokens are R, S, r, s, E, Z, - (not stereogenic here) or ? (unknown), one per stereo element in "
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
    geometries = [g for g in geometries if g]
    for k, source in enumerate(geometries, 1):
        xyz, isotopes = (source, None) if isinstance(source, str) else source
        tokens = geometry_tokens(frame, xyz, isotopes)
        if tokens is not None:
            named.append(("geometry" if len(geometries) == 1 else f"conformer geometry {k}", tokens))
    if use_marks:
        named.append(("SMILES stereo marks", mark_tokens(frame)))
    if supplied_label is not None:
        named.append(("supplied stereo_label", parse_supplied_label(frame, supplied_label)))

    current = [UNKNOWN] * len(frame.elements)
    owner = ""
    for name, tokens in named:
        current = merge_sources(frame, current, tokens, first=owner or "earlier source", second=name)
        owner = name if not owner else f"{owner} and {name}"
    current = refine(frame, current)
    return finish(frame, current)


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
    return ResolvedStereo(descriptor, resolution, smiles)


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
