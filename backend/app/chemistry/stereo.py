"""Entry-level stereo descriptors (DR-0038).

A *species* is a constitution: the stereo-blind canonical SMILES plus charge and
multiplicity. Stereochemistry is resolved only on the *species entry*, as a
canonical **stereo descriptor** string: one token per potential stereo element,
``R``/``S`` for a tetrahedral centre, ``E``/``Z`` for a stereogenic double bond,
``?`` for an element nothing pinned down. Examples: ``E``, ``R,S``, ``E,?``.

Why a descriptor and not a SMILES
---------------------------------
A SMILES cannot encode the E/Z configuration of a sigma-vinyl radical such as
1-propenyl (``[CH]=CC``), yet the two isomers are different molecules with
different geometries. The descriptor is computed from a molecule object, never
from SMILES text, so geometry-perceived configurations are first-class. A
canonical isomeric SMILES is stored beside it as derived search text wherever
one can be written (:func:`stereo_smiles_for`); it is never the key.

Order of the tokens
-------------------
Elements are ordered by canonical atom rank of the stereo-*stripped*
constitution molecule (``CanonicalRankAtoms`` without tie-breaking), so the
order is the same whether the input carried stereo marks or not. Elements that
are equivalent under the graph's symmetry share a rank key; within such a class
the tokens are sorted (specified tokens alphabetically, ``?`` last), so the
descriptor of ``(2E,4Z)-hexa-2,4-diene`` and ``(2Z,4E)-hexa-2,4-diene`` -- the
same molecule -- is one string. The price is that a correlation *between* two
symmetric classes is not represented; it is documented in DR-0038.

Sources and how they combine
----------------------------
Three sources, in order: the **geometry** (3D perception), the **marks** in the
SMILES, then a **supplied label**. Each fills only the elements the earlier ones
left ``?``. An element given by more than one source must agree, otherwise the
deposit is refused with :data:`W_SPECIES_STEREO_CONTRADICTION`. Agreement is
decided per symmetry class, as a multiset, because within a class the sources
cannot name the same element.

Nothing is inferred: where geometry and marks are both absent, an element stays
``?``.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

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
#: (wrong number of tokens, or a token of the wrong kind for its element).
W_SPECIES_STEREO_LABEL_INVALID = "species_stereo_label_invalid"

#: The non-blocking warning for an entry that ends with at least one ``?``.
W_SPECIES_ENTRY_STEREO_UNRESOLVED = "species_entry_stereo_unresolved"

UNKNOWN = "?"
TETRAHEDRAL_TOKENS = frozenset({"R", "S"})
DOUBLE_BOND_TOKENS = frozenset({"E", "Z"})
VOCABULARY = TETRAHEDRAL_TOKENS | DOUBLE_BOND_TOKENS | {UNKNOWN}

#: Wire pattern for ``stereo_label``/``stereo_descriptor``; the schema package
#: carries its own copy because it cannot import the backend.
DESCRIPTOR_PATTERN = r"^[RSEZ?](,[RSEZ?])*$"

#: Above this many potential elements the SMILES enumeration is skipped and
#: ``stereo_smiles`` is left NULL (2**10 stereoisomers is already generous).
_MAX_ENUMERATED_ELEMENTS = 10

_TETRAHEDRAL = "tetrahedral"
_DOUBLE_BOND = "double_bond"


@dataclass(frozen=True)
class StereoElement:
    """One potential stereo element of the constitution graph.

    :param kind: ``tetrahedral`` or ``double_bond``.
    :param atoms: One atom index (tetrahedral) or the two bonded atom indices
        (double bond), in the constitution molecule's atom numbering.
    :param key: Sort/equivalence key built from canonical ranks; two elements
        with the same key are equivalent under the graph's symmetry.
    """

    kind: str
    atoms: tuple[int, ...]
    key: tuple[int, ...]


@dataclass(frozen=True)
class StereoFrame:
    """The stereo-relevant view of one species entry's molecule.

    :param marked: Isotope-free molecule, stereo marks retained.
    :param constitution: The same molecule with all stereo removed.
    :param elements: Potential stereo elements in canonical order.
    """

    marked: Chem.Mol
    constitution: Chem.Mol
    elements: tuple[StereoElement, ...]
    #: For each constitution atom, the index of the same atom in ``marked``
    #: (they differ when ``marked`` keeps a hydrogen that defines a double-bond
    #: mark, as in ``[H]/N=N\\[H]``).
    marked_index: tuple[int, ...] = ()

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
    (``[H]/N=N\\[H]``); once the mark is gone that hydrogen is ordinary, so
    ``[H]/N=N\\[H]`` and ``N=N`` have one constitution.

    :param marked: Isotope-free molecule (:func:`app.chemistry.species.strip_isotopes`).
    """

    constitution = Chem.Mol(marked)
    Chem.RemoveStereochemistry(constitution)
    constitution = Chem.RemoveHs(constitution, _REMOVE_HS)
    Chem.SanitizeMol(constitution)
    return constitution


def frame_from_mol(marked: Chem.Mol) -> StereoFrame:
    """Build the stereo frame for an isotope-free, sanitized molecule.

    :param marked: Molecule with isotopes cleared (and hydrogens stripped where
        they do not define a mark); stereo marks are kept.
    """

    tagged = Chem.Mol(marked)
    for atom in tagged.GetAtoms():
        atom.SetIntProp("_marked_idx", atom.GetIdx())
    constitution = constitution_mol(tagged)
    marked_index = tuple(atom.GetIntProp("_marked_idx") for atom in constitution.GetAtoms())
    ranks = list(Chem.CanonicalRankAtoms(constitution, breakTies=False))

    found: list[StereoElement] = []
    for info in Chem.FindPotentialStereo(constitution):
        if info.type == Chem.StereoType.Atom_Tetrahedral:
            atom = info.centeredOn
            found.append(StereoElement(_TETRAHEDRAL, (atom,), (ranks[atom], 0, ranks[atom])))
        elif info.type == Chem.StereoType.Bond_Double:
            bond = constitution.GetBondWithIdx(info.centeredOn)
            begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
            low, high = sorted((ranks[begin], ranks[end]))
            found.append(StereoElement(_DOUBLE_BOND, (begin, end), (low, 1, high)))
    found.sort(key=lambda element: (element.key, element.atoms))
    return StereoFrame(
        marked=marked,
        constitution=constitution,
        elements=tuple(found),
        marked_index=marked_index,
    )


# ---------------------------------------------------------------------------
# Per-source tokens
# ---------------------------------------------------------------------------


def _atom_token(atom: Chem.Atom) -> str:
    if not atom.HasProp("_CIPCode"):
        return UNKNOWN
    code = atom.GetProp("_CIPCode").upper()
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

    :returns: One token per ``frame.elements`` entry; ``?`` where unmarked.
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
    """Element-and-connectivity-only copy, for graph matching across notations."""

    skeleton = Chem.RWMol(mol)
    for atom in skeleton.GetAtoms():
        atom.SetFormalCharge(0)
        atom.SetNumRadicalElectrons(0)
        atom.SetIsotope(0)
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
    frame: StereoFrame, xyz_text: str
) -> tuple[Chem.Mol, tuple[int, ...]] | None:
    """Perceive configuration from coordinates.

    :returns: ``(mol, mapping)`` -- the geometry's molecule with stereo assigned
        and CIP labels set, and for each constitution atom (heavy atoms, then
        hydrogens as added by ``AddHs``) the index of its image in ``mol``; or
        ``None`` when the coordinates cannot be mapped onto the graph. A
        bonded-wrongly or unparseable geometry resolves nothing; it is never a
        guess.
    """

    try:
        raw = Chem.MolFromXYZBlock(_xyz_block(xyz_text))
        if raw is None:
            return None
        template = _neutralized_template(Chem.AddHs(frame.constitution))
        # ``MolFromXYZBlock`` yields a bondless atom cloud; connectivity has to
        # be perceived before bond orders can be mapped from the template.
        rdDetermineBonds.DetermineConnectivity(raw)
        mol = AllChem.AssignBondOrdersFromTemplate(template, raw)
        Chem.AssignStereochemistryFrom3D(mol)
        rdCIPLabeler.AssignCIPLabels(mol)
        mapping = _skeleton(mol).GetSubstructMatch(_skeleton(template))
        if len(mapping) != template.GetNumAtoms():
            return None
        return mol, tuple(mapping)
    except (ValueError, RuntimeError):
        return None
    except Exception:  # pragma: no cover - defensive: surface the unexpected
        logger.warning("Unexpected failure perceiving stereo from 3D geometry", exc_info=True)
        return None


def geometry_tokens(frame: StereoFrame, xyz_text: str) -> list[str] | None:
    """Read each element's CIP label from 3D coordinates.

    Torsional conformers of one configuration give the same tokens; only
    configuration is read. ``None`` when the geometry could not be perceived at
    all; ``?`` for an element the coordinates leave undefined (for example a
    collinear sigma-vinyl).
    """

    perceived = geometry_mol(frame, xyz_text)
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


def parse_supplied_label(frame: StereoFrame, label: str) -> list[str]:
    """Validate a supplied label against the molecule it labels (D5).

    :raises CodedValueError: ``species_stereo_label_invalid`` when the label
        does not have one token per stereo element, or a token of the wrong
        kind for its element.
    """

    tokens = [token.strip() for token in label.split(",")]
    kinds = [
        "R/S" if element.kind == _TETRAHEDRAL else "E/Z" for element in frame.elements
    ]
    expected = ",".join(kinds) if kinds else "none"
    if not frame.elements:
        raise _label_invalid(
            "this molecule has no stereo element, so it takes no stereo label",
            expected,
        )
    if len(tokens) != len(frame.elements):
        raise _label_invalid(
            f"expected {len(frame.elements)} token(s), one per stereo element, got {len(tokens)}",
            expected,
        )
    for position, (token, element) in enumerate(zip(tokens, frame.elements, strict=True), 1):
        allowed = TETRAHEDRAL_TOKENS if element.kind == _TETRAHEDRAL else DOUBLE_BOND_TOKENS
        if token != UNKNOWN and token not in allowed:
            raise _label_invalid(
                f"token {position} ({token!r}) cannot label a "
                f"{'tetrahedral centre' if element.kind == _TETRAHEDRAL else 'double bond'}",
                expected,
            )
    return tokens


def _label_invalid(problem: str, expected: str) -> CodedValueError:
    return CodedValueError(
        W_SPECIES_STEREO_LABEL_INVALID,
        f"species_entry.stereo_label does not fit the molecule: {problem}. "
        f"Tokens are R, S, E, Z or ? (unknown), one per stereo element in canonical "
        f"order; this molecule's elements are, in order: {expected}.",
        context={"expected_element_kinds": expected, "problem": problem},
        message_prefix=False,
    )


# ---------------------------------------------------------------------------
# Combining sources
# ---------------------------------------------------------------------------


def _by_class(frame: StereoFrame, tokens: list[str]) -> dict[tuple[int, ...], list[str]]:
    classes: dict[tuple[int, ...], list[str]] = {}
    for element, token in zip(frame.elements, tokens, strict=True):
        classes.setdefault(element.key, []).append(token)
    return classes


def _class_order(tokens: list[str]) -> list[str]:
    return sorted(tokens, key=lambda token: (token == UNKNOWN, token))


def _render(frame: StereoFrame, classes: dict[tuple[int, ...], list[str]]) -> list[str]:
    ordered: list[str] = []
    for key in dict.fromkeys(element.key for element in frame.elements):
        ordered.extend(_class_order(classes[key]))
    return ordered


def combine_sources(
    frame: StereoFrame,
    sources: list[tuple[str, list[str] | None]],
) -> list[str]:
    """Merge per-source tokens, earlier sources first; refuse disagreement.

    :param sources: ``(source_name, tokens)`` in priority order; ``tokens`` is
        ``None`` for a source that is absent or could not be read.
    :returns: The final canonical token list.
    :raises CodedValueError: ``species_stereo_contradiction`` when two sources
        name different configurations for the same element.
    """

    merged = {key: [UNKNOWN] * len(tokens) for key, tokens in _by_class(frame, [UNKNOWN] * len(frame.elements)).items()}
    owners: dict[tuple[int, ...], str] = {}
    for name, tokens in sources:
        if tokens is None:
            continue
        for key, incoming in _by_class(frame, tokens).items():
            have = Counter(token for token in merged[key] if token != UNKNOWN)
            bring = Counter(token for token in incoming if token != UNKNOWN)
            if not (have <= bring or bring <= have):
                position = [element.key for element in frame.elements].index(key) + 1
                raise CodedValueError(
                    W_SPECIES_STEREO_CONTRADICTION,
                    f"The stereochemistry given by the {owners.get(key, 'earlier source')} "
                    f"contradicts the {name} at stereo element {position} of the species "
                    f"entry: {_describe(have)} against {_describe(bring)}. A contradiction "
                    "is refused rather than resolved in either direction; correct the "
                    "SMILES marks, the geometry or the label so they describe one "
                    "configuration.",
                    context={
                        "sources": [owners.get(key, "earlier source"), name],
                        "element_position": position,
                        "first": sorted(have.elements()),
                        "second": sorted(bring.elements()),
                    },
                    message_prefix=False,
                )
            if bring.total() > have.total():
                merged[key] = _class_order(incoming)
                owners[key] = name if key not in owners else f"{owners[key]} and {name}"
            elif key not in owners and bring.total():
                owners[key] = name
    return _render(frame, merged)


def _describe(counter: Counter) -> str:
    return ",".join(sorted(counter.elements())) or "nothing"


def resolve_stereo(
    frame: StereoFrame,
    *,
    geometry_xyz: str | None = None,
    supplied_label: str | None = None,
    use_marks: bool = True,
) -> ResolvedStereo:
    """Resolve an entry's stereo from geometry, then marks, then a label.

    :param frame: The entry molecule's stereo frame.
    :param geometry_xyz: The first linked geometry, if any.
    :param supplied_label: A depositor's descriptor-vocabulary label, if any.
    :param use_marks: Read the marks of ``frame.marked``.
    :raises CodedValueError: On a contradiction or an unfit label.
    """

    if not frame.is_stereo_capable:
        if supplied_label is not None:
            parse_supplied_label(frame, supplied_label)  # raises: nothing to label
        return ResolvedStereo(None, None, None)

    sources: list[tuple[str, list[str] | None]] = []
    if geometry_xyz:
        sources.append(("geometry", geometry_tokens(frame, geometry_xyz)))
    if use_marks:
        sources.append(("SMILES stereo marks", mark_tokens(frame)))
    if supplied_label is not None:
        sources.append(("supplied stereo_label", parse_supplied_label(frame, supplied_label)))
    tokens = combine_sources(frame, sources)
    return finish(frame, tokens)


def finish(frame: StereoFrame, tokens: list[str]) -> ResolvedStereo:
    """Descriptor, resolution and SMILES for an already-combined token list."""

    descriptor = ",".join(tokens)
    unknown = sum(1 for token in tokens if token == UNKNOWN)
    if unknown == 0:
        resolution = StereoResolution.resolved
    elif unknown == len(tokens):
        resolution = StereoResolution.unresolved
    else:
        resolution = StereoResolution.partial
    smiles = stereo_smiles_for(frame, descriptor) if resolution == StereoResolution.resolved else None
    return ResolvedStereo(descriptor, resolution, smiles)


def descriptor_of_mol(mol: Chem.Mol) -> str | None:
    """Descriptor read from the marks of an isotope-free molecule (no geometry)."""

    frame = frame_from_mol(mol)
    if not frame.is_stereo_capable:
        return None
    return ",".join(combine_sources(frame, [("SMILES stereo marks", mark_tokens(frame))]))


def stereo_smiles_for(frame: StereoFrame, descriptor: str) -> str | None:
    """Canonical isomeric SMILES of the configuration ``descriptor`` names.

    Derived from the descriptor alone, by enumerating the constitution's
    stereoisomers, so it does not depend on which source supplied the
    configuration. ``None`` when no isomer matches, when the SMILES would not
    round-trip to the same descriptor (a sigma-vinyl radical's E/Z cannot be
    written), or when the molecule has too many elements to enumerate.
    """

    if not frame.elements or len(frame.elements) > _MAX_ENUMERATED_ELEMENTS:
        return None
    options = StereoEnumerationOptions(
        onlyUnassigned=True,
        unique=True,
        tryEmbedding=False,
        maxIsomers=2 ** len(frame.elements),
    )
    # Heavy atoms only first (``C/C=C/C``); a configuration that needs its
    # hydrogens to be written (``[H]/N=N\\[H]``) is retried with them explicit.
    # Both are functions of the constitution and the descriptor alone.
    for base in (frame.constitution, Chem.AddHs(frame.constitution)):
        try:
            for isomer in EnumerateStereoisomers(base, options=options):
                if descriptor_of_mol(isomer) != descriptor:
                    continue
                smiles = Chem.MolToSmiles(isomer, isomericSmiles=True, canonical=True)
                reparsed = Chem.MolFromSmiles(smiles)
                if reparsed is not None and descriptor_of_mol(reparsed) == descriptor:
                    return smiles
        except (ValueError, RuntimeError):
            continue
    return None

