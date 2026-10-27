from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping

from rdkit import Chem
from rdkit.Chem import inchi
from tckdb_schemas.fragments.identity import ELECTRON_SMILES

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.isotopes import normalize_isotope, validate_isotope
from app.db.models.common import MoleculeKind, StereoKind
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)

logger = logging.getLogger(__name__)

#: Raised when a species entry's declared charge is not the formal charge of
#: its own SMILES.
W_SPECIES_SMILES_CHARGE_MISMATCH = "species_smiles_charge_mismatch"

#: The value stored in ``species.inchi_key`` for the free electron.
#:
#: ``species.inchi_key`` is ``CHAR(27) NOT NULL`` and an electron has no
#: InChI, so the row needs *something*. This is deliberately **not** a
#: plausible InChIKey: a real one is ``[A-Z]{14}-[A-Z]{10}-[A-Z]``, and this
#: fails that shape in its first block, so it can never collide with a real
#: key, can never be returned by an InChIKey lookup, and cannot be mistaken
#: for a chemical identifier by a reader. Exactly 27 characters, because the
#: column is ``CHAR`` and would otherwise pad.
#:
#: The alternative — relaxing ``inchi_key`` to nullable — is the more honest
#: schema and was rejected for reach: half a dozen read schemas type it
#: ``str``, and widening all of them to admit one row that no structure search
#: can return is a larger, more disruptive change than a sentinel that is
#: self-describing at every point it can be read.
ELECTRON_INCHI_KEY = "ELECTRON-NO-INCHIKEY-EXISTS"


def is_electron(payload: SpeciesEntryIdentityPayload) -> bool:
    """Return whether an identity payload declares a free electron.

    One predicate for every caller that has to branch, so no two of them can
    disagree about what an electron looks like. The payload schema has already
    pinned ``smiles``, ``charge`` and ``multiplicity`` to each other by the
    time this is asked (``SpeciesIdentityPayload.check_electron_identity``), so
    the kind alone is sufficient and authoritative.
    """

    return payload.molecule_kind == MoleculeKind.electron


def formal_charge(mol: Chem.Mol) -> int:
    """Return the total formal charge of an RDKit molecule.

    :param mol: RDKit molecule to inspect.
    :returns: Sum of per-atom formal charges.
    """

    return sum(atom.GetFormalCharge() for atom in mol.GetAtoms())


def spin_multiplicity(mol: Chem.Mol) -> int:
    """Estimate spin multiplicity from radical electrons.

    :param mol: RDKit molecule to inspect.
    :returns: ``total_radical_electrons + 1``.
    """

    total_radicals = sum(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms())
    return total_radicals + 1


def identity_mol_from_smiles(smiles: str) -> Chem.Mol:
    """Build a canonicalized identity molecule from SMILES.

    Isotope labels written in the SMILES (``[2H]``, ``[13C]``, ``[18O]``)
    are **retained**: they are the atom-resolved isotope content of the
    deposit, and ``Chem.RemoveHs`` deliberately keeps isotope-labelled
    hydrogens explicit so a deuterium never silently collapses into an
    implicit-H count. A mass number that names the element's most abundant
    isotope (``[1H]``, ``[12C]``) carries no information and is normalized
    away, so ``[1H]C([1H])([1H])[1H]`` and ``C`` are the same molecule.

    :param smiles: Input SMILES string for the uploaded species identity.
    :returns: Sanitized RDKit molecule with atom maps removed, standard-isotope
        labels normalized away, and non-isotopic hydrogens stripped.
    :raises ValueError: If RDKit cannot parse the SMILES string or the SMILES
        names an isotope that does not exist for its element.
    """

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError("RDKit failed to parse species_entry.smiles")

    ident = Chem.Mol(mol)
    for atom in ident.GetAtoms():
        atom.SetAtomMapNum(0)
        mass_number = atom.GetIsotope()
        if not mass_number:
            continue
        symbol = atom.GetSymbol()
        validate_isotope(symbol, mass_number, context="species_entry.smiles")
        if normalize_isotope(symbol, mass_number) is None:
            atom.SetIsotope(0)
    ident = Chem.RemoveHs(ident)
    Chem.SanitizeMol(ident)
    return ident


def strip_isotopes(mol: Chem.Mol) -> Chem.Mol:
    """Return a copy of ``mol`` with every isotope label cleared.

    Isotopologues are the *same* chemical species — one molecular graph, one
    potential energy surface — differing only in nuclear masses. Species-level
    identity is therefore isotope-blind, and isotopic resolution lives one
    level down on ``species_entry``. This helper produces the isotope-blind
    graph used for ``species.smiles`` and ``species.inchi_key``.

    :param mol: Identity molecule, possibly carrying isotope labels.
    :returns: A sanitized copy with no isotope labels and no leftover explicit
        hydrogens (a deuterium becomes an ordinary implicit H).
    """

    bare = Chem.Mol(mol)
    for atom in bare.GetAtoms():
        atom.SetIsotope(0)
    bare = Chem.RemoveHs(bare)
    Chem.SanitizeMol(bare)
    return bare


def canonical_isotope_key(smiles: str) -> str | None:
    """Derive the canonical, atom-resolved isotope key for a species entry.

    The key is the canonical SMILES of the isotope-labelled identity molecule.
    Because RDKit's canonical ranking accounts for isotope labels, the key is
    independent of the uploaded atom order and distinguishes *isotopomers*,
    not merely isotopologues: ``[2H]CO`` (CH2D-OH) and ``[2H]OC`` (CH3-OD)
    are the same isotopologue but produce different keys, as they must —
    they have different frequencies, rotational constants and ZPE.

    ``None`` is returned when every atom sits at its most abundant isotope.
    That is the *stable* all-standard key: it does not depend on RDKit's
    canonical-SMILES output at all, so every pre-existing species entry keeps
    exactly the identity it already had.

    :param smiles: Input SMILES string for the uploaded species identity.
    :returns: Canonical isotopic SMILES, or ``None`` for an all-standard species.
    :raises ValueError: If RDKit cannot parse the SMILES string.
    """

    ident = identity_mol_from_smiles(smiles)
    if not any(atom.GetIsotope() for atom in ident.GetAtoms()):
        return None
    return Chem.MolToSmiles(ident, canonical=True)


def isotope_substitutions(smiles: str) -> dict[tuple[str, int], int]:
    """Count the non-standard isotope substitutions declared by a SMILES.

    :param smiles: Input SMILES string for the uploaded species identity.
    :returns: Mapping of ``(element_symbol, mass_number)`` to substituted-atom
        count. Empty for an all-standard species.
    :raises ValueError: If RDKit cannot parse the SMILES string.
    """

    counts: dict[tuple[str, int], int] = {}
    for atom in identity_mol_from_smiles(smiles).GetAtoms():
        mass_number = atom.GetIsotope()
        if mass_number:
            key = (atom.GetSymbol(), mass_number)
            counts[key] = counts.get(key, 0) + 1
    return counts


def element_counts_from_smiles(smiles: str) -> Counter[str]:
    """Count the elements a SMILES declares, the way a geometry counts them.

    Three normalisations make the result comparable against
    ``geometry_atom.element`` and against another SMILES:

    * **Hydrogens are made explicit.** A SMILES carries most of its hydrogens
      implicitly (``C`` is methane, one atom in the graph and five in the
      molecule), while an XYZ lists every nucleus. Without
      :func:`rdkit.Chem.AddHs` every organic species would appear to be
      missing its hydrogens.
    * **Isotopes collapse to their element.** ``[2H]`` counts as ``H``. The
      canonical form stores the element, not the nuclide, and isotopic
      substitution changes masses rather than composition — counting
      isotope-resolved would refuse every isotopologue whose geometry, quite
      correctly, lists the same elements as the ordinary compound. Isotope
      agreement is a separate check with its own multiset comparison
      (:func:`app.services.species_resolution.assert_geometry_isotopes_match_identity`).
    * **Symbols are resolved to elements** through
      :func:`~app.chemistry.geometry.resolve_element_symbol`, because the other
      side of every comparison is an XYZ element column that depositors fill in
      their own spelling: they disagree about capitalisation (``CL``, ``cl``)
      and a hand-written file may write hydrogen's isotopes as elements
      (``D``, ``T``). ``b4e7c1d20f83`` canonicalises the case on the way in,
      which makes the first of those rare rather than impossible. Since
      ``docs/adr/0022`` (2026-10-03) ``parse_xyz`` stores a ``D``/``T`` as
      ``H`` plus a mass number, so only rows deposited before that still hold
      the ``D``/``T`` symbol — which cannot be rewritten, so this call stays. RDKit's
      ``GetSymbol()`` produces neither spelling, so this call is a no-op on
      this side — it is here so that both sides of every comparison are counted
      by one rule rather than two that must be remembered to agree.

    :param smiles: SMILES string to count.
    :returns: Mapping of element symbol to atom count.
    :raises ValueError: If RDKit cannot parse the SMILES string.
    """

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit failed to parse SMILES: {smiles}")
    mol = Chem.AddHs(mol)
    counts: Counter[str] = Counter()
    for atom in mol.GetAtoms():
        counts[resolve_element_symbol(atom.GetSymbol())] += 1
    return counts


def species_smiles_has_any_bonds(smiles: str) -> bool:
    """Whether *smiles* names a species with at least one bond.

    Hydrogens are made explicit first, for the same reason
    :func:`element_counts_from_smiles` makes them explicit: a bare
    ``Chem.MolFromSmiles`` graph never carries a bond for an implicit
    hydrogen, so ``"C"`` (methane) reports zero bonds until
    :func:`rdkit.Chem.AddHs` adds the four C-H bonds the molecule
    actually has. Left un-added, every organic species with only
    implicit hydrogens would misreport as bondless.

    A single free atom -- ``"[H]"``, ``"[O]"`` -- has no bond to add
    either way; this is the monatomic case a bond-additivity correction's
    componentless zero is honest for.

    :param smiles: SMILES string to inspect.
    :returns: ``True`` if the molecule has at least one bond (to any atom,
        explicit or implicit), ``False`` for a single free atom.
    :raises ValueError: If RDKit cannot parse the SMILES string.
    """

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit failed to parse SMILES: {smiles}")
    mol = Chem.AddHs(mol)
    return mol.GetNumBonds() > 0


def format_element_counts(counts: Mapping[str, int]) -> str:
    """Render an element count as a formula, for an error a human can read.

    **Hill notation**, which is what a chemist reads: carbon first, hydrogen
    second, then every other element alphabetically — and, when there is no
    carbon, everything alphabetically including hydrogen. Plain alphabetical
    ordering renders chloromethane as ``CClH3``, a string nobody recognises as
    the molecule they deposited, in an error message whose whole job is to be
    recognised.

    :param counts: Element symbol to atom count. Elements counted zero times
        are omitted rather than rendered with no subscript.
    :returns: The molecular formula, e.g. ``CH3Cl`` or ``H2O``.
    """

    present = {element: count for element, count in counts.items() if count}
    ordered: list[str] = []
    if "C" in present:
        ordered.append("C")
        if "H" in present:
            ordered.append("H")
    ordered.extend(sorted(element for element in present if element not in ordered))

    return "".join(
        f"{element}{present[element] if present[element] > 1 else ''}"
        for element in ordered
    )


def derive_unmapped_smiles(smiles: str) -> str:
    """Strip atom-mapping numbers from SMILES and return canonical form.

    :param smiles: Input SMILES, possibly with atom-mapping (e.g. ``[CH3:1]``).
    :returns: Canonical SMILES with all ``:N`` atom maps removed.
    :raises ValueError: If RDKit cannot parse the SMILES.
    """

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"RDKit failed to parse SMILES: {smiles}")
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    return Chem.MolToSmiles(mol, canonical=True)


def classify_stereo_kind(
    mol: Chem.Mol,
) -> tuple[StereoKind, str | None]:
    """Classify stereochemistry kind from molecular graph topology.

    The elements are the ones the entry descriptor is built from
    (:func:`app.chemistry.stereo.frame_from_mol`, perceived with every hydrogen
    explicit), so a chiral radical such as ``C[CH]C(C)O`` is classified like its
    closed-shell relatives and a stereogenic allene counts as an axial element.
    This feeds the species' content ref (``stereo_kind``), not its key.
    Returns the kind and, for single-chiral-center molecules, the R/S label if
    the SMILES specifies it.

    :param mol: Sanitized RDKit molecule.
    :returns: ``(stereo_kind, stereo_label)`` tuple.
    """

    kinds = [element.kind for element in stereo.frame_from_mol(mol).elements]
    chiral = sum(1 for kind in kinds if kind in ("tetrahedral", "axial"))
    bonds = len(kinds) - chiral

    if not kinds:
        return StereoKind.achiral, None

    # Both chiral elements and E/Z bonds, or several chiral elements → diastereomer
    if chiral and bonds:
        return StereoKind.diastereomer, None

    if chiral:
        if chiral >= 2:
            return StereoKind.diastereomer, None
        # Single chiral element → enantiomer pair
        chiral_centres = Chem.FindMolChiralCenters(mol, includeUnassigned=True)
        label = chiral_centres[0][1] if chiral_centres else None
        if label == "?":
            label = None
        return StereoKind.enantiomer, label

    # E/Z only
    return StereoKind.ez_isomer, None


def derive_stereo_label_from_3d(smiles: str, xyz_text: str) -> str | None:
    """Read the stereo descriptor a geometry alone supports.

    Only *configurational* stereochemistry is read -- tetrahedral centres and
    stereogenic double bonds -- so torsional conformers of one configuration give
    the same result. The tokens are ordered by canonical rank of the
    stereo-stripped constitution (see :mod:`app.chemistry.stereo`), independent of
    the uploaded atom order. An element the coordinates leave undefined is ``?``
    (a collinear sigma-vinyl, a flat centre); nothing is guessed.

    Open-shell species are perceived like closed-shell ones, including sigma-vinyl
    radicals whose E/Z exists only in the geometry.

    :param smiles: SMILES supplying the bond-order template (marks are ignored).
    :param xyz_text: XYZ coordinate text (with or without header lines).
    :returns: The descriptor (e.g. ``"R"``, ``"E"``, ``"E,?"``), or ``None``
        when the molecule has no stereo element or the geometry resolves none.
    """

    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    if not frame.is_stereo_capable:
        return None
    tokens = stereo.geometry_tokens(frame, xyz_text)
    if tokens is None or all(token == stereo.UNKNOWN for token in tokens):
        return None
    return stereo.finish(frame, stereo.refine(frame, tokens), with_smiles=False).descriptor


# Totally symmetric irreducible representation for common point groups.
# Used to derive ground-state term symbols for closed-shell species.
_TOTALLY_SYMMETRIC_IRREP: dict[str, str] = {
    "C1": "A",
    "Cs": "A'",
    "Ci": "Ag",
    "C2": "A",
    "C2v": "A1",
    "C2h": "Ag",
    "C3v": "A1",
    "C4v": "A1",
    "C5v": "A1",
    "C6v": "A1",
    "D2": "A",
    "D2h": "Ag",
    "D3": "A1",
    "D3h": "A1'",
    "D3d": "A1g",
    "D4h": "A1g",
    "D5h": "A1'",
    "D6h": "A1g",
    "Cinfv": "Sigma",
    "Dinfh": "Sigma_g+",
    "Td": "A1",
    "Oh": "A1g",
    "Ih": "Ag",
    "Kh": "S",
}


def derive_term_symbol(
    multiplicity: int,
    *,
    point_group: str | None = None,
    is_linear: bool | None = None,
    is_closed_shell: bool | None = None,
) -> str | None:
    """Derive a closed-shell ground-state term symbol when symmetry is known.

    A spin multiplicity and molecular point group do not determine the spatial
    electronic state of an open-shell species.  In particular, substituting the
    totally symmetric irreducible representation can turn distinct states into
    the same species-entry identity.  We therefore derive a symbol only for a
    closed-shell singlet with a recognized point group.

    ``is_linear`` is retained for API compatibility, but linearity alone is not
    sufficient: it cannot determine reflection parity or, for homonuclear
    species, gerade/ungerade symmetry.  ``None`` also means "not reported" to
    existing callers and must not be interpreted as "monoatomic". Multiplicity
    one does not prove a closed shell, so callers must state that separately.

    :param multiplicity: Spin multiplicity (2S+1).
    :param point_group: Point group label (e.g. ``"C2v"``, ``"Cinfv"``).
    :param is_linear: Molecular linearity, retained for caller compatibility.
    :param is_closed_shell: Whether a trusted source establishes a closed shell.
    :returns: A defensible closed-shell term symbol, or ``None``.
    """

    del is_linear

    if multiplicity != 1 or is_closed_shell is not True:
        return None

    if point_group is not None:
        irrep = _TOTALLY_SYMMETRIC_IRREP.get(point_group)
        if irrep is not None:
            return f"1{irrep}"

    return None


def canonical_species_identity(
    payload: SpeciesEntryIdentityPayload,
) -> tuple[str, str]:
    """Canonicalize upload identity data into species-level keys.

    The key is the **constitution**: the stereo-blind canonical SMILES (isotopes
    stripped, every stereo mark removed) plus charge and multiplicity, and the
    InChIKey of that same molecule. ``C/C=C/C`` and ``CC=CC`` are one species;
    which stereoisomer a deposit is lives on the species entry
    (:mod:`app.chemistry.stereo`, DR-0038).

    :param payload: Upload-facing species-entry identity payload.
    :returns: ``(canonical_smiles, inchi_key)`` for the constitution.
    :raises ValueError:
        If the payload is not a supported molecule or electron upload or if
        the stated charge disagrees with the parsed SMILES identity.

    A free electron short-circuits every step below: it has no molecular
    graph to canonicalize and no InChI, so it resolves to the reserved
    identity pair (see :data:`ELECTRON_INCHI_KEY`). Its charge is pinned by
    the payload schema rather than reconciled against a structure, because
    there is no structure to reconcile it against.

    Multiplicity is NOT validated against the SMILES: standard SMILES does
    not encode spin state, so the radical count RDKit infers is only a
    hint. The uploaded ``multiplicity`` is authoritative — this is what
    lets singlet CH₂ (SMILES ``[CH2]`` implies a triplet) and the singlet/
    triplet O₂ states be represented. Species identity carries multiplicity
    as part of its unique key; see DR-0031 (key now stereo-blind, DR-0038).

    Isotope labels are stripped before canonicalization. Isotopologues share
    one molecular graph and one potential energy surface, so they share a
    ``species`` row; the isotopic resolution is carried by the species entry's
    ``isotope_key`` (see :func:`canonical_isotope_key`). This also keeps
    ``inchi_key`` isotope-blind, so an InChIKey lookup finds every
    isotopologue of a compound rather than only the ordinary one.
    """

    if payload.molecule_kind == MoleculeKind.electron:
        return ELECTRON_SMILES, ELECTRON_INCHI_KEY

    if payload.molecule_kind != MoleculeKind.molecule:
        raise ValueError("Conformer upload currently supports only molecule species")

    ident = identity_mol_from_smiles(payload.smiles)
    charge = formal_charge(ident)

    if charge != payload.charge:
        raise CodedValueError(
            W_SPECIES_SMILES_CHARGE_MISMATCH,
            f"species_entry.charge={payload.charge} does not match SMILES charge {charge}",
            context={
                "declared_charge": payload.charge,
                "smiles_charge": charge,
            },
            message_prefix=False,
        )

    constitution = stereo.constitution_mol(strip_isotopes(ident))
    canonical_smiles = Chem.MolToSmiles(constitution, canonical=True)
    inchi_key = inchi.MolToInchiKey(constitution)
    return canonical_smiles, inchi_key


CHECK_SMILES_CHARGE_MATCHES_DECLARED = ScientificCheck(
    group="A structure against its own label",
    # Shifted from 3 to 4 by #143 so that the calculation-geometry composition
    # rule sits beside the two conformer-geometry rules it mirrors, rather
    # than after the charge rules that have nothing to do with it.
    sort_key=4,
    code=W_SPECIES_SMILES_CHARGE_MISMATCH,
    asserts=(
        "A species entry's declared charge equals the summed formal charge of "
        "its own SMILES."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, and the anchor the reaction-level charge law stands on: "
        "``validate_reaction_charge_conservation`` sums ``Species.charge`` and "
        "is only meaningful because each value has already been reconciled "
        "with the structure it labels. Per ADR 0008 the blocking tier owns the "
        "rule and the others cite it, which is why "
        "``assert_geometry_composition_matches_identity`` deliberately does "
        "not re-check charge."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            canonical_species_identity,
            note=(
                "Charge is compared against ``formal_charge`` of the sanitized "
                "identity molecule — the sum of RDKit per-atom formal charges, "
                "which is a notation convention rather than an electron count. "
                "A referee may object that formal-charge assignment on "
                "hypervalent, zwitterionic or dative-bonded SMILES is "
                "notation-dependent."
            ),
        ),
    ),
    escape_hatch=(
        "A free electron short-circuits before the comparison, returning a "
        "pinned identity pair. Multiplicity is deliberately **not** validated "
        "against the SMILES at all: standard SMILES does not encode spin "
        "state, so RDKit's inferred radical count is only a hint and the "
        "uploaded multiplicity is authoritative — which is what lets singlet "
        "CH2 (whose SMILES ``[CH2]`` implies a triplet) and the singlet and "
        "triplet states of O2 be represented."
    ),
)
