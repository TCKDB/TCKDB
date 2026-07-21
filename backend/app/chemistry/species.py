from __future__ import annotations

import logging

from rdkit import Chem
from rdkit.Chem import AllChem, inchi, rdDetermineBonds

from app.db.models.common import MoleculeKind, StereoKind
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload

logger = logging.getLogger(__name__)


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

    :param smiles: Input SMILES string for the uploaded species identity.
    :returns: Sanitized RDKit molecule with atom maps removed and hydrogens stripped.
    :raises ValueError: If RDKit cannot parse the SMILES string.
    """

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError("RDKit failed to parse species_entry.smiles")

    ident = Chem.Mol(mol)
    for atom in ident.GetAtoms():
        atom.SetAtomMapNum(0)
    ident = Chem.RemoveHs(ident)
    Chem.SanitizeMol(ident)
    return ident


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

    Uses RDKit's ``FindPotentialStereo`` to detect stereocenters and E/Z bonds
    from the 2D graph.  Returns the kind and, for single-chiral-center molecules,
    the R/S label if the SMILES specifies it.

    :param mol: Sanitized RDKit molecule.
    :returns: ``(stereo_kind, stereo_label)`` tuple.
    """

    stereo_info = list(Chem.FindPotentialStereo(mol))

    chiral_atoms = [
        si for si in stereo_info
        if si.type == Chem.StereoType.Atom_Tetrahedral
    ]
    ez_bonds = [
        si for si in stereo_info
        if si.type == Chem.StereoType.Bond_Double
    ]

    if not chiral_atoms and not ez_bonds:
        return StereoKind.achiral, None

    # Both chiral centres and E/Z bonds → diastereomer
    if chiral_atoms and ez_bonds:
        return StereoKind.diastereomer, None

    if chiral_atoms:
        if len(chiral_atoms) >= 2:
            return StereoKind.diastereomer, None
        # Single chiral centre → enantiomer pair
        chiral_centres = Chem.FindMolChiralCenters(mol, includeUnassigned=True)
        label = chiral_centres[0][1] if chiral_centres else None
        if label == "?":
            label = None
        return StereoKind.enantiomer, label

    # E/Z only
    return StereoKind.ez_isomer, None


def _xyz_text_to_xyz_block(xyz_text: str) -> str:
    """Convert raw coordinate lines to a standard XYZ block with header.

    Handles both formats: bare coordinate lines (``Atom x y z``) and
    full XYZ files (atom count + comment + coordinates).
    """

    lines = xyz_text.strip().splitlines()
    # Detect whether the first line is an atom count (integer)
    try:
        int(lines[0].strip())
        return xyz_text.strip()  # already has header
    except (ValueError, IndexError):
        pass
    # Count coordinate lines and prepend header
    n_atoms = len(lines)
    return f"{n_atoms}\n\n{xyz_text.strip()}"


def _neutralize_radicals_for_template(template: Chem.Mol) -> Chem.Mol:
    """Return a copy of ``template`` with unpaired-electron bookkeeping removed.

    ``AssignBondOrdersFromTemplate`` rejects a template that carries radical
    electrons (``ValueError``), so open-shell species could never be mapped and
    always fell back to ``None``. Zeroing the radical electrons produces a
    template whose *connectivity* (heavy atoms + explicit Hs) is identical to
    the radical's real geometry — only the unpaired-electron bookkeeping is
    dropped — which is all ``AssignBondOrdersFromTemplate`` needs to map bond
    orders. ``AssignStereochemistryFrom3D`` then reads CIP labels from the
    coordinates exactly as it does for closed-shell species.

    The copy is mutated, never the caller's molecule. Only atoms that actually
    carry a radical electron are touched, so this is a strict no-op for
    closed-shell templates (their labels are unchanged). ``SetNoImplicit(True)``
    is set on the freed atoms so RDKit does not re-add a phantom implicit H to
    fill the vacated valence — the template already has all Hs explicit (via
    ``AddHs``), and the H count must stay matched to the geometry's atoms.

    :param template: ``AddHs``-expanded template molecule (may be open-shell).
    :returns: A copy safe to pass to ``AssignBondOrdersFromTemplate``.
    """

    neutralized = Chem.Mol(template)
    for atom in neutralized.GetAtoms():
        if atom.GetNumRadicalElectrons():
            atom.SetNumRadicalElectrons(0)
            atom.SetNoImplicit(True)
    return neutralized


def derive_stereo_label_from_3d(smiles: str, xyz_text: str) -> str | None:
    """Assign R/S and E/Z labels from 3D geometry.

    Builds an RDKit mol from the XYZ coordinates, perceives connectivity so the
    bare atom cloud gains a bond graph, maps bond orders from the SMILES
    template, then uses ``AssignStereochemistryFrom3D`` to determine CIP labels.

    Only *configurational* stereochemistry is labelled — tetrahedral chiral
    centres and stereogenic double bonds. Torsional conformers/rotamers of the
    same configuration yield the same label, so the label is safe to use as
    part of ``SpeciesEntry`` identity.

    The emitted labels are ordered by canonical atom rank (not the uploaded
    atom order), so the same configuration produces an identical label string
    regardless of how the SMILES/XYZ atoms happened to be ordered.

    Open-shell / radical species are supported: the SMILES template's radical
    electrons are neutralized on a copy (see
    ``_neutralize_radicals_for_template``) so ``AssignBondOrdersFromTemplate``
    accepts it, while the geometry-derived stereo result is unchanged. Radical
    stereoisomers (e.g. E/Z crotyl, a chiral radical R/S) are therefore now
    distinguished. If a particular radical geometry still cannot be resolved,
    the function falls back to ``None`` (safe) rather than a wrong label.

    :param smiles: SMILES string for bond-order template.
    :param xyz_text: XYZ coordinate text (with or without header lines).
    :returns: Stereo label string (e.g. ``"R"``, ``"S"``, ``"E"``, ``"R,E"``),
              or ``None`` if no stereo or assignment fails.
    """

    try:
        xyz_block = _xyz_text_to_xyz_block(xyz_text)
        raw_mol = Chem.MolFromXYZBlock(xyz_block)
        if raw_mol is None:
            return None

        template = Chem.MolFromSmiles(smiles)
        if template is None:
            return None
        template = Chem.AddHs(template)
        # Drop unpaired-electron bookkeeping so open-shell templates map;
        # strict no-op for closed-shell species (same labels as before).
        template = _neutralize_radicals_for_template(template)

        # ``MolFromXYZBlock`` yields a bare atom cloud with zero bonds. Perceive
        # connectivity from the geometry first, otherwise
        # ``AssignBondOrdersFromTemplate`` has no bonds to map orders onto and
        # raises ``ValueError: No matching found``.
        rdDetermineBonds.DetermineConnectivity(raw_mol)

        mol = AllChem.AssignBondOrdersFromTemplate(template, raw_mol)
        Chem.AssignStereochemistryFrom3D(mol)

        # Canonical, input-order-independent ranking of atoms. Keying each label
        # to the canonical rank of its stereo atom makes the joined label string
        # deterministic regardless of the uploaded atom ordering (so a
        # two-stereocentre species does not spuriously split into "R,S" vs
        # "S,R" entries).
        ranks = list(Chem.CanonicalRankAtoms(mol, breakTies=True))
        keyed_labels: list[tuple[int, str]] = []

        # Chiral centres (tetrahedral configuration only).
        for atom_idx, cip in Chem.FindMolChiralCenters(mol, includeUnassigned=False):
            keyed_labels.append((ranks[atom_idx], cip))

        # E/Z double bonds (stereogenic double-bond configuration only).
        for bond in mol.GetBonds():
            stereo = bond.GetStereo()
            if stereo in (Chem.BondStereo.STEREOZ, Chem.BondStereo.STEREOCIS):
                label = "Z"
            elif stereo in (Chem.BondStereo.STEREOE, Chem.BondStereo.STEREOTRANS):
                label = "E"
            else:
                continue
            bond_rank = min(ranks[bond.GetBeginAtomIdx()], ranks[bond.GetEndAtomIdx()])
            keyed_labels.append((bond_rank, label))

        if not keyed_labels:
            return None

        keyed_labels.sort(key=lambda item: item[0])
        return ",".join(label for _, label in keyed_labels)

    except (ValueError, RuntimeError):
        # Expected RDKit failures: unparseable geometry, template/atom-count
        # mismatch, or bond perception giving a graph the template cannot map
        # onto. These are legitimately "no label" outcomes.
        return None
    except Exception:  # pragma: no cover - defensive: surface the unexpected
        # A genuinely unexpected failure must not be swallowed invisibly (that
        # is exactly how this function stayed dead for months). Log it so a
        # future breakage is visible, but keep the callers' "no label" contract.
        logger.warning(
            "Unexpected failure deriving stereo label from 3D geometry "
            "(smiles=%r); returning None",
            smiles,
            exc_info=True,
        )
        return None


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

    :param payload: Upload-facing species-entry identity payload.
    :returns: ``(canonical_smiles, inchi_key)`` for the graph identity.
    :raises ValueError:
        If the payload is not a supported molecule upload or if the stated
        charge disagrees with the parsed SMILES identity.

    Multiplicity is NOT validated against the SMILES: standard SMILES does
    not encode spin state, so the radical count RDKit infers is only a
    hint. The uploaded ``multiplicity`` is authoritative — this is what
    lets singlet CH₂ (SMILES ``[CH2]`` implies a triplet) and the singlet/
    triplet O₂ states be represented. Species identity carries multiplicity
    as part of its unique key; see DR-0031.
    """

    if payload.molecule_kind != MoleculeKind.molecule:
        raise ValueError("Conformer upload currently supports only molecule species")

    ident = identity_mol_from_smiles(payload.smiles)
    charge = formal_charge(ident)

    if charge != payload.charge:
        raise ValueError(
            f"species_entry.charge={payload.charge} does not match SMILES charge {charge}"
        )

    canonical_smiles = Chem.MolToSmiles(ident, canonical=True)
    inchi_key = inchi.MolToInchiKey(ident)
    return canonical_smiles, inchi_key
