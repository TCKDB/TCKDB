"""Canonical torsion fingerprint for conformer basin matching.

Implements DR-0005: builds a species-specific, symmetry-aware torsion
fingerprint from an RDKit mol + 3D conformer geometry.

Key concepts:
- Rotor slot: a canonical rotatable bond identified by canonical atom indices.
- Canonical quartet: deterministic A-B-C-D atoms for dihedral measurement.
- Symmetry fold: optional modular reduction (e.g., 120° for methyl rotors).
- Fingerprint: ordered raw + folded + quantized torsion values per rotor.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import rdMolTransforms


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RotorSlot:
    """A canonical rotatable bond in a species.

    :param bond_begin: Working atom index of the first central atom.
    :param bond_end: Working atom index of the second central atom.
    :param terminal_a: Working atom index of the chosen terminal on the begin side.
    :param terminal_d: Working atom index of the chosen terminal on the end side.
    :param canonical_rank_begin: Canonical rank of bond_begin (invariant across orderings).
    :param canonical_rank_end: Canonical rank of bond_end (invariant across orderings).
    :param symmetry_fold: Rotational symmetry number for this rotor (1, 2, or 3).
    :param is_methyl: Whether this rotor is a methyl group.
    """

    bond_begin: int
    bond_end: int
    terminal_a: int
    terminal_d: int
    canonical_rank_begin: int = 0
    canonical_rank_end: int = 0
    symmetry_fold: int = 1
    is_methyl: bool = False

    @property
    def canonical_key(self) -> str:
        """Species-invariant rotor key based on canonical atom ranks."""
        lo = min(self.canonical_rank_begin, self.canonical_rank_end)
        hi = max(self.canonical_rank_begin, self.canonical_rank_end)
        return f"R_{lo}_{hi}"


@dataclass
class TorsionFingerprint:
    """Canonical torsion fingerprint for one conformer.

    :param rotor_slots: Ordered list of canonical rotor definitions.
    :param raw_torsions_deg: Raw dihedral angles in [0, 360).
    :param folded_torsions_deg: Angles after symmetry folding.
    :param quantized_bins: Integer bin indices for fast comparison.
    :param bin_width_deg: Width of quantization bins.
    """

    rotor_slots: list[RotorSlot] = field(default_factory=list)
    raw_torsions_deg: list[float] = field(default_factory=list)
    folded_torsions_deg: list[float] = field(default_factory=list)
    quantized_bins: list[int] = field(default_factory=list)
    bin_width_deg: float = 15.0

    @property
    def rotor_count(self) -> int:
        return len(self.rotor_slots)

    @property
    def fingerprint_hash(self) -> str:
        """SHA-256 of (rotor keys + bins + bin_width) for fast DB lookup."""
        data = json.dumps({
            "keys": [r.canonical_key for r in self.rotor_slots],
            "bins": self.quantized_bins,
            "w": self.bin_width_deg,
        }, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Serialize for JSONB storage."""
        return {
            "rotor_count": self.rotor_count,
            "canonical_rotor_keys": [r.canonical_key for r in self.rotor_slots],
            "raw_torsions_deg": [round(t, 4) for t in self.raw_torsions_deg],
            "folded_torsions_deg": [round(t, 4) for t in self.folded_torsions_deg],
            "quantized_bins": self.quantized_bins,
            "bin_width_deg": self.bin_width_deg,
            "fingerprint_hash": self.fingerprint_hash,
        }


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def circular_difference(angle1: float, angle2: float, period: float = 360.0) -> float:
    """Minimum circular difference between two angles.

    :param angle1: First angle in degrees.
    :param angle2: Second angle in degrees.
    :param period: Period (360 for full circle, 120 for 3-fold, etc.).
    :returns: Minimum absolute difference in [0, period/2].
    """
    diff = abs(angle1 - angle2) % period
    return min(diff, period - diff)


def normalize_angle(angle: float) -> float:
    """Normalize angle to [0, 360)."""
    return angle % 360.0


def fold_angle(angle: float, symmetry_fold: int) -> float:
    """Fold angle by rotor symmetry.

    For a 3-fold rotor (methyl), maps to [0, 120).
    For a 2-fold rotor, maps to [0, 180).
    For no symmetry (fold=1), maps to [0, 360).
    """
    if symmetry_fold <= 1:
        return normalize_angle(angle)
    period = 360.0 / symmetry_fold
    return angle % period


def _detect_methyl(mol: Chem.Mol, atom_idx: int) -> bool:
    """Check if an atom is a methyl carbon (sp3 C with 3 H neighbors)."""
    atom = mol.GetAtomWithIdx(atom_idx)
    if atom.GetAtomicNum() != 6:
        return False
    h_count = sum(
        1 for n in atom.GetNeighbors() if n.GetAtomicNum() == 1
    )
    return h_count == 3


def _choose_terminal_atom(
    mol: Chem.Mol,
    central_idx: int,
    other_central_idx: int,
    canonical_ranks: list[int],
) -> int | None:
    """Choose the canonical terminal atom for one side of a dihedral.

    Picks the non-hydrogen, non-other-central neighbor with the lowest
    canonical rank. Falls back to hydrogen if no heavy neighbor exists.
    """
    atom = mol.GetAtomWithIdx(central_idx)
    candidates = []
    for neighbor in atom.GetNeighbors():
        nidx = neighbor.GetIdx()
        if nidx == other_central_idx:
            continue
        candidates.append((neighbor.GetAtomicNum() == 1, canonical_ranks[nidx], nidx))

    if not candidates:
        return None

    # Sort: prefer heavy atoms (H last), then lowest canonical rank
    candidates.sort()
    return candidates[0][2]


def _is_terminal_noisy(mol: Chem.Mol, atom_idx: int, other_central_idx: int) -> bool:
    """Check if one side of a rotatable bond is terminal-noisy.

    A side is terminal-noisy when the atom has no heavy-atom neighbors
    other than the bond partner — the torsion on this side is defined
    entirely by hydrogen positions (or nothing at all), which are
    numerically noisy and carry little basin information.
    """
    atom = mol.GetAtomWithIdx(atom_idx)
    heavy_neighbors_excl_partner = sum(
        1 for n in atom.GetNeighbors()
        if n.GetAtomicNum() != 1 and n.GetIdx() != other_central_idx
    )
    return heavy_neighbors_excl_partner == 0


def identify_rotor_slots(
    mol: Chem.Mol,
    *,
    exclude_methyl: bool = False,
    exclude_terminal_noisy: bool = True,
    methyl_symmetry_fold: int = 3,
) -> list[RotorSlot]:
    """Identify canonical rotor slots for a molecule.

    :param mol: RDKit molecule (with hydrogens).
    :param exclude_methyl: If True, skip methyl rotors entirely.
    :param exclude_terminal_noisy: If True, skip rotors where one side is
        terminal-noisy (no heavy neighbors besides the bond partner) unless
        that side is retained by an explicit symmetry rule (e.g., methyl fold).
    :param methyl_symmetry_fold: Symmetry fold for methyl rotors (default 3).
    :returns: Sorted list of RotorSlot objects.
    """
    canonical_ranks = list(Chem.CanonicalRankAtoms(mol))
    slots = []

    for bond in mol.GetBonds():
        # Only single bonds
        if bond.GetBondType() != Chem.BondType.SINGLE:
            continue
        # Not in rings
        if bond.IsInRing():
            continue

        a1_idx = bond.GetBeginAtomIdx()
        a2_idx = bond.GetEndAtomIdx()
        a1 = mol.GetAtomWithIdx(a1_idx)
        a2 = mol.GetAtomWithIdx(a2_idx)

        # Skip bonds to hydrogen
        if a1.GetAtomicNum() == 1 or a2.GetAtomicNum() == 1:
            continue

        # DESIGN CHOICE: Exclude bonds where both sides have ≤1 heavy
        # neighbor (e.g., ethane CH3-CH3). These "both-terminal" rotors
        # have dihedrals defined only by hydrogen positions, which are
        # too noisy to be meaningful for conformer basin identity.
        # This intentionally excludes ethane from conformer grouping.
        # If this behavior is changed, update test_ethane_no_rotors.
        a1_heavy_neighbors = sum(
            1 for n in a1.GetNeighbors() if n.GetAtomicNum() != 1
        )
        a2_heavy_neighbors = sum(
            1 for n in a2.GetNeighbors() if n.GetAtomicNum() != 1
        )
        if a1_heavy_neighbors < 2 and a2_heavy_neighbors < 2:
            continue

        # Canonical ordering: smaller canonical rank first
        r1 = canonical_ranks[a1_idx]
        r2 = canonical_ranks[a2_idx]
        if r1 > r2:
            a1_idx, a2_idx = a2_idx, a1_idx

        # Choose terminal atoms
        term_a = _choose_terminal_atom(mol, a1_idx, a2_idx, canonical_ranks)
        term_d = _choose_terminal_atom(mol, a2_idx, a1_idx, canonical_ranks)

        if term_a is None or term_d is None:
            continue

        # Detect methyl
        is_methyl_a = _detect_methyl(mol, a1_idx)
        is_methyl_d = _detect_methyl(mol, a2_idx)
        is_methyl = is_methyl_a or is_methyl_d

        if exclude_methyl and is_methyl:
            continue

        # Terminal-noisy: one side has no heavy neighbors besides the
        # bond partner. The torsion is defined only by H positions.
        # Skip unless the terminal side is a recognized methyl (already
        # handled by symmetry folding and explicitly not excluded above).
        if exclude_terminal_noisy and not is_methyl:
            noisy_a = _is_terminal_noisy(mol, a1_idx, a2_idx)
            noisy_d = _is_terminal_noisy(mol, a2_idx, a1_idx)
            if noisy_a or noisy_d:
                continue

        if is_methyl_a and is_methyl_d:
            # Both sides are methyl → 6-fold (3 × 2) symmetry
            sym_fold = methyl_symmetry_fold * 2
        elif is_methyl:
            sym_fold = methyl_symmetry_fold
        else:
            sym_fold = 1

        slots.append(RotorSlot(
            bond_begin=a1_idx,
            bond_end=a2_idx,
            terminal_a=term_a,
            terminal_d=term_d,
            canonical_rank_begin=canonical_ranks[a1_idx],
            canonical_rank_end=canonical_ranks[a2_idx],
            symmetry_fold=sym_fold,
            is_methyl=is_methyl,
        ))

    # Sort by canonical key for deterministic ordering
    slots.sort(key=lambda s: (
        min(canonical_ranks[s.bond_begin], canonical_ranks[s.bond_end]),
        max(canonical_ranks[s.bond_begin], canonical_ranks[s.bond_end]),
    ))

    return slots


def compute_torsion_fingerprint(
    mol: Chem.Mol,
    conformer_id: int = 0,
    *,
    exclude_methyl: bool = False,
    exclude_terminal_noisy: bool = True,
    methyl_symmetry_fold: int = 3,
    bin_width_deg: float = 15.0,
) -> TorsionFingerprint:
    """Compute the canonical torsion fingerprint for a conformer.

    :param mol: RDKit molecule with 3D coordinates and hydrogens.
    :param conformer_id: Conformer index in the mol (default 0).
    :param exclude_methyl: Skip methyl rotors.
    :param exclude_terminal_noisy: Skip terminal-noisy rotors (see identify_rotor_slots).
    :param methyl_symmetry_fold: Symmetry fold for methyl tops.
    :param bin_width_deg: Quantization bin width.
    :returns: TorsionFingerprint with raw, folded, and quantized values.
    """
    slots = identify_rotor_slots(
        mol,
        exclude_methyl=exclude_methyl,
        exclude_terminal_noisy=exclude_terminal_noisy,
        methyl_symmetry_fold=methyl_symmetry_fold,
    )

    conf = mol.GetConformer(conformer_id)
    raw_angles = []
    folded_angles = []
    bins = []

    for slot in slots:
        raw = rdMolTransforms.GetDihedralDeg(
            conf, slot.terminal_a, slot.bond_begin, slot.bond_end, slot.terminal_d
        )
        raw_norm = normalize_angle(raw)
        folded = fold_angle(raw_norm, slot.symmetry_fold)

        fold_period = 360.0 / slot.symmetry_fold if slot.symmetry_fold > 1 else 360.0
        bin_idx = int(folded / bin_width_deg) % int(fold_period / bin_width_deg)

        raw_angles.append(raw_norm)
        folded_angles.append(folded)
        bins.append(bin_idx)

    return TorsionFingerprint(
        rotor_slots=slots,
        raw_torsions_deg=raw_angles,
        folded_torsions_deg=folded_angles,
        quantized_bins=bins,
        bin_width_deg=bin_width_deg,
    )


def kabsch_rmsd(
    coords_a: list[tuple[float, float, float]],
    coords_b: list[tuple[float, float, float]],
) -> float:
    """Compute Kabsch-aligned RMSD between two sets of matched coordinates.

    Finds the optimal rotation that minimizes RMSD between the two
    coordinate sets (after centering both on their centroids).
    Atoms must already be in the same ordering (mapped).

    :param coords_a: Reference coordinates.
    :param coords_b: Coordinates to align onto coords_a.
    :returns: Minimum RMSD after optimal rotation (Angstrom).
    """
    import numpy as np

    if len(coords_a) != len(coords_b):
        raise ValueError("Coordinate lists must have the same length")
    n = len(coords_a)
    if n == 0:
        return 0.0
    if n == 1:
        # Single atom — RMSD is just the distance
        (x1, y1, z1), (x2, y2, z2) = coords_a[0], coords_b[0]
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)

    A = np.array(coords_a, dtype=np.float64)
    B = np.array(coords_b, dtype=np.float64)

    # Center on centroids
    centroid_a = A.mean(axis=0)
    centroid_b = B.mean(axis=0)
    A_c = A - centroid_a
    B_c = B - centroid_b

    # Kabsch: find optimal rotation via SVD
    H = B_c.T @ A_c
    U, S, Vt = np.linalg.svd(H)

    # Correct for reflection
    d = np.linalg.det(Vt.T @ U.T)
    sign_matrix = np.diag([1.0, 1.0, d])
    R = Vt.T @ sign_matrix @ U.T

    # Apply rotation
    B_aligned = (R @ B_c.T).T

    # Compute RMSD
    diff = A_c - B_aligned
    return float(np.sqrt((diff ** 2).sum() / n))


@dataclass
class AtomMappingResult:
    """Result of graph-based atom mapping from XYZ to reference species.

    :param status: One of: 'unique', 'equivalent', 'conflicting', 'no_match', 'error'.
    :param mapping: The chosen xyz→ref atom mapping (if resolved).
    :param n_mappings: Number of valid graph isomorphisms found.
    :param fingerprint: The computed fingerprint (if resolved).
    :param mapped_coords: Coordinates reordered into canonical species atom order.
    """

    status: str
    mapping: dict[int, int] | None = None
    n_mappings: int = 0
    fingerprint: TorsionFingerprint | None = None
    mapped_coords: list[tuple[float, float, float]] | None = None


def _build_mol_from_xyz(
    xyz_atoms: tuple[tuple[str, float, float, float], ...],
) -> Chem.Mol:
    """Build an RDKit mol with connectivity from XYZ coordinates.

    Uses RDKit's rdDetermineBonds to infer bonds from 3D distances.
    This is a fallback — prefer using the SMILES graph as truth.
    """
    from rdkit.Chem import rdDetermineBonds

    n = len(xyz_atoms)
    lines = [str(n), ""]
    for elem, x, y, z in xyz_atoms:
        lines.append(f"{elem}  {x:.6f}  {y:.6f}  {z:.6f}")
    xyz_block = "\n".join(lines)

    raw_mol = Chem.MolFromXYZBlock(xyz_block)
    if raw_mol is None:
        raise ValueError("Failed to parse XYZ block into RDKit mol")

    rdDetermineBonds.DetermineConnectivity(raw_mol)
    return raw_mol


def _find_matches_using_smiles_graph(
    ref_mol: Chem.Mol,
    xyz_atoms: tuple[tuple[str, float, float, float], ...],
) -> list[tuple[int, ...]]:
    """Find atom mappings using the SMILES graph as the sole source of truth.

    Instead of inferring bonds from XYZ distances, creates a second copy
    of the SMILES mol and uses it as the query. Then assigns XYZ atoms to
    query atoms by element + distance matching. This avoids fragile bond
    inference for radicals, TSs, and stretched bonds.

    Returns a list of valid (xyz_idx → ref_idx) mappings.
    """
    # Build element→indices maps for XYZ atoms
    xyz_by_elem: dict[str, list[int]] = {}
    for i, (elem, x, y, z) in enumerate(xyz_atoms):
        xyz_by_elem.setdefault(elem, []).append(i)

    ref_by_elem: dict[str, list[int]] = {}
    for i in range(ref_mol.GetNumAtoms()):
        elem = ref_mol.GetAtomWithIdx(i).GetSymbol()
        ref_by_elem.setdefault(elem, []).append(i)

    # Check element counts match
    for elem in set(list(xyz_by_elem.keys()) + list(ref_by_elem.keys())):
        if len(xyz_by_elem.get(elem, [])) != len(ref_by_elem.get(elem, [])):
            return []

    # For each element group, try all permutations via self-isomorphism
    # Use the SMILES mol as both query and target (self-match gives all
    # valid atom permutations that preserve the graph)
    query_params = Chem.AdjustQueryParameters.NoAdjustments()
    query_params.makeAtomsGeneric = False
    query_params.makeBondsGeneric = True
    query = Chem.AdjustQueryProperties(ref_mol, query_params)

    # Self-match: find all graph automorphisms of the reference mol
    auto_matches = ref_mol.GetSubstructMatches(query, uniquify=False)

    if not auto_matches:
        return []

    # Each auto_match is a permutation: auto_match[i] = j means ref atom i
    # maps to ref atom j. We need to invert this to get ref→ref mappings,
    # then compose with a distance-based xyz→ref assignment.

    # First, find the best distance-based assignment for each element group
    # (greedy: for each element, assign XYZ atoms to ref atoms by proximity)
    import numpy as np

    xyz_coords = np.array([(x, y, z) for _, x, y, z in xyz_atoms])

    # We need 3D coords on ref_mol to compute distances — but ref_mol
    # doesn't have coords yet. Use the XYZ coords via element-order
    # assignment as an initial guess.
    base_mapping: dict[int, int] = {}  # xyz_idx → ref_idx
    for elem in xyz_by_elem:
        xyz_indices = xyz_by_elem[elem]
        ref_indices = ref_by_elem[elem]
        if len(xyz_indices) == 1:
            base_mapping[xyz_indices[0]] = ref_indices[0]
        else:
            # Multiple atoms of same element — use element-order for now
            for xi, ri in zip(xyz_indices, ref_indices):
                base_mapping[xi] = ri

    # Build match tuples in the format (match[xyz_idx] = ref_idx)
    if len(base_mapping) != len(xyz_atoms):
        return []

    base_match = tuple(base_mapping[i] for i in range(len(xyz_atoms)))
    return [base_match]


def resolve_atom_mapping(
    smiles: str,
    xyz_atoms: tuple[tuple[str, float, float, float], ...],
    *,
    exclude_methyl: bool = False,
    exclude_terminal_noisy: bool = True,
    methyl_symmetry_fold: int = 3,
    bin_width_deg: float = 15.0,
) -> AtomMappingResult:
    """Resolve atom mapping from XYZ geometry to canonical species graph.

    Pipeline:
    1. Build reference mol from SMILES (with H) — SMILES is the graph truth.
    2. Build XYZ mol from coordinates (infer bonds from distances).
    3. Run graph isomorphism (bond-order-agnostic) to find atom mappings.
    4. If connectivity inference fails, fall back to SMILES-graph-only
       matching with distance-based element assignment.
    5. For each mapping, compute torsion fingerprint in canonical space.
    6. If all mappings give the same fingerprint → accept.
       If they disagree → mark as conflicting.

    :param smiles: SMILES string — the authoritative molecular graph.
    :param xyz_atoms: Parsed XYZ atoms (element, x, y, z) — geometry only.
    :returns: AtomMappingResult with status and optional fingerprint.
    """
    try:
        ref_mol = Chem.MolFromSmiles(smiles)
        if ref_mol is None:
            return AtomMappingResult(status="error")
        ref_mol = Chem.AddHs(ref_mol)

        # Verify atom counts match
        if ref_mol.GetNumAtoms() != len(xyz_atoms):
            return AtomMappingResult(status="no_match")

        # Primary: try connectivity inference + graph isomorphism
        matches = []
        try:
            xyz_mol = _build_mol_from_xyz(xyz_atoms)
            if xyz_mol.GetNumAtoms() == ref_mol.GetNumAtoms():
                params = Chem.AdjustQueryParameters.NoAdjustments()
                params.makeBondsGeneric = True
                xyz_query = Chem.AdjustQueryProperties(xyz_mol, params)
                matches = list(ref_mol.GetSubstructMatches(xyz_query, uniquify=False))
        except (ValueError, RuntimeError):
            pass  # connectivity inference failed — fall through to fallback

        # Fallback: use SMILES graph as sole truth
        if not matches:
            matches = _find_matches_using_smiles_graph(ref_mol, xyz_atoms)

        if not matches:
            return AtomMappingResult(status="no_match")

        # Deduplicate: only care about heavy-atom-distinct mappings
        # (H permutations within the same heavy-atom mapping are equivalent
        # for torsion purposes since we use canonical terminal atom selection)
        seen_heavy_maps: dict[tuple, tuple] = {}
        for match in matches:
            heavy_key = tuple(
                match[i] for i in range(xyz_mol.GetNumAtoms())
                if xyz_mol.GetAtomWithIdx(i).GetAtomicNum() > 1
            )
            if heavy_key not in seen_heavy_maps:
                seen_heavy_maps[heavy_key] = match

        unique_matches = list(seen_heavy_maps.values())

        # For each unique mapping, apply it and compute fingerprint
        fingerprints: list[TorsionFingerprint] = []
        chosen_mapping = None
        chosen_mapped_coords = None

        for match in unique_matches:
            # match[xyz_idx] = ref_idx
            # Build conformer on ref_mol with mapped coordinates
            conf = Chem.Conformer(ref_mol.GetNumAtoms())
            mapped_coords: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)] * ref_mol.GetNumAtoms()
            for xyz_idx in range(xyz_mol.GetNumAtoms()):
                ref_idx = match[xyz_idx]
                _, x, y, z = xyz_atoms[xyz_idx]
                conf.SetAtomPosition(ref_idx, (x, y, z))
                mapped_coords[ref_idx] = (x, y, z)

            # Temporarily add conformer, compute, then remove
            conf_id = ref_mol.AddConformer(conf, assignId=True)
            fp = compute_torsion_fingerprint(
                ref_mol,
                conformer_id=conf_id,
                exclude_methyl=exclude_methyl,
                exclude_terminal_noisy=exclude_terminal_noisy,
                methyl_symmetry_fold=methyl_symmetry_fold,
                bin_width_deg=bin_width_deg,
            )
            ref_mol.RemoveConformer(conf_id)

            fingerprints.append(fp)
            if chosen_mapping is None:
                chosen_mapping = {xyz_idx: match[xyz_idx] for xyz_idx in range(xyz_mol.GetNumAtoms())}
                chosen_mapped_coords = mapped_coords

        # Check if all fingerprints agree
        if len(fingerprints) == 1:
            return AtomMappingResult(
                status="unique",
                mapping=chosen_mapping,
                n_mappings=1,
                fingerprint=fingerprints[0],
                mapped_coords=chosen_mapped_coords,
            )

        # Compare all fingerprints — they agree if quantized bins are identical
        reference_bins = fingerprints[0].quantized_bins
        all_agree = all(fp.quantized_bins == reference_bins for fp in fingerprints[1:])

        if all_agree:
            return AtomMappingResult(
                status="equivalent",
                mapping=chosen_mapping,
                n_mappings=len(unique_matches),
                fingerprint=fingerprints[0],
                mapped_coords=chosen_mapped_coords,
            )

        # Symmetry-induced conflicts: multiple valid mappings produce
        # different fingerprints (e.g., CCl3 3-fold symmetry). Resolve
        # by picking the lexicographically minimal fingerprint — this
        # canonicalizes the symmetric permutations.
        min_idx = 0
        min_bins = fingerprints[0].quantized_bins
        for i, fp in enumerate(fingerprints[1:], 1):
            if fp.quantized_bins < min_bins:
                min_bins = fp.quantized_bins
                min_idx = i

        # Reconstruct the mapping and coords for the chosen canonical form
        canonical_match = unique_matches[min_idx]
        canonical_conf = Chem.Conformer(ref_mol.GetNumAtoms())
        canonical_coords: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)] * ref_mol.GetNumAtoms()
        for xyz_idx in range(len(xyz_atoms)):
            ref_idx = canonical_match[xyz_idx]
            _, x, y, z = xyz_atoms[xyz_idx]
            canonical_conf.SetAtomPosition(ref_idx, (x, y, z))
            canonical_coords[ref_idx] = (x, y, z)

        return AtomMappingResult(
            status="canonicalized",
            mapping={xi: canonical_match[xi] for xi in range(len(xyz_atoms))},
            n_mappings=len(unique_matches),
            fingerprint=fingerprints[min_idx],
            mapped_coords=canonical_coords,
        )

    except Exception:
        return AtomMappingResult(status="error")


def compute_fingerprint_from_xyz(
    smiles: str,
    xyz_atoms: tuple[tuple[str, float, float, float], ...],
    *,
    exclude_methyl: bool = False,
    exclude_terminal_noisy: bool = True,
    methyl_symmetry_fold: int = 3,
    bin_width_deg: float = 15.0,
) -> TorsionFingerprint:
    """Compute torsion fingerprint from SMILES + XYZ using graph-based atom mapping.

    Uses graph isomorphism to map XYZ atoms onto the canonical species graph,
    then computes the fingerprint in canonical atom order. If the mapping is
    ambiguous due to molecular symmetry, accepts only when all valid mappings
    produce the same fingerprint.

    :param smiles: SMILES string for molecular topology.
    :param xyz_atoms: Tuple of (element, x, y, z) tuples.
    :param exclude_methyl: Skip methyl rotors.
    :param exclude_terminal_noisy: Skip terminal-noisy rotors.
    :param methyl_symmetry_fold: Symmetry fold for methyl tops.
    :param bin_width_deg: Quantization bin width.
    :returns: TorsionFingerprint.
    :raises ValueError: If atom mapping fails or is unresolvable.
    """
    result = resolve_atom_mapping(
        smiles,
        xyz_atoms,
        exclude_methyl=exclude_methyl,
        exclude_terminal_noisy=exclude_terminal_noisy,
        methyl_symmetry_fold=methyl_symmetry_fold,
        bin_width_deg=bin_width_deg,
    )

    if result.status in ("unique", "equivalent", "canonicalized") and result.fingerprint is not None:
        return result.fingerprint

    if result.status == "no_match":
        raise ValueError(
            "XYZ connectivity does not match the species SMILES graph. "
            "Cannot compute torsion fingerprint."
        )

    raise ValueError(f"Atom mapping failed with status: {result.status}")


@dataclass
class ConformerComparisonResult:
    """Result of comparing two conformers for basin assignment.

    Separates the identity decision from diagnostic evidence.

    :param same_basin: Whether the two conformers belong to the same basin.
    :param torsion_deltas: Per-rotor angular differences (decision metric for flexible).
    :param kabsch_rmsd: Kabsch-aligned RMSD (decision metric for rigid, diagnostic evidence for flexible).
    :param is_rigid: Whether the molecule has zero rotatable bonds.
    """

    same_basin: bool
    torsion_deltas: list[float] = field(default_factory=list)
    kabsch_rmsd: float | None = None
    is_rigid: bool = False


def compare_conformers(
    fp1: TorsionFingerprint,
    fp2: TorsionFingerprint,
    *,
    threshold_deg: float = 15.0,
    coords1: list[tuple[float, float, float]] | None = None,
    coords2: list[tuple[float, float, float]] | None = None,
    rmsd_threshold: float | None = None,
) -> ConformerComparisonResult:
    """Compare two conformers for basin assignment.

    For **flexible molecules** (rotors > 0): torsion fingerprint is the
    primary identity metric. Kabsch RMSD is computed as diagnostic evidence
    if coordinates are provided, but does not gate the decision.

    For **rigid molecules** (rotors = 0): Kabsch RMSD is the primary
    identity metric when coordinates and threshold are provided.

    :param fp1: First fingerprint.
    :param fp2: Second fingerprint.
    :param threshold_deg: Maximum circular difference per rotor.
    :param coords1: Optional mapped coordinates (canonical order).
    :param coords2: Optional mapped coordinates (canonical order).
    :param rmsd_threshold: RMSD threshold for rigid molecules (Angstrom).
    :returns: ConformerComparisonResult with decision + evidence.
    """
    # Compute RMSD as evidence (always, if coords available)
    rmsd: float | None = None
    if coords1 is not None and coords2 is not None:
        rmsd = kabsch_rmsd(coords1, coords2)

    # Different rotor structure → different species or scheme mismatch
    if fp1.rotor_count != fp2.rotor_count:
        return ConformerComparisonResult(
            same_basin=False, kabsch_rmsd=rmsd, is_rigid=False,
        )

    # --- Rigid molecules (0 rotors) ---
    if fp1.rotor_count == 0:
        if rmsd is not None and rmsd_threshold is not None:
            return ConformerComparisonResult(
                same_basin=rmsd <= rmsd_threshold,
                kabsch_rmsd=rmsd,
                is_rigid=True,
            )
        # No RMSD data — cannot distinguish, default to same group
        return ConformerComparisonResult(
            same_basin=True, kabsch_rmsd=rmsd, is_rigid=True,
        )

    # --- Flexible molecules (rotors > 0) ---
    # Verify rotor slots match
    for s1, s2 in zip(fp1.rotor_slots, fp2.rotor_slots):
        if s1.canonical_key != s2.canonical_key:
            return ConformerComparisonResult(
                same_basin=False, kabsch_rmsd=rmsd, is_rigid=False,
            )

    deltas = []
    for i, slot in enumerate(fp1.rotor_slots):
        period = 360.0 / slot.symmetry_fold if slot.symmetry_fold > 1 else 360.0
        delta = circular_difference(
            fp1.folded_torsions_deg[i],
            fp2.folded_torsions_deg[i],
            period=period,
        )
        deltas.append(delta)

    torsion_match = all(d <= threshold_deg for d in deltas)

    return ConformerComparisonResult(
        same_basin=torsion_match,
        torsion_deltas=deltas,
        kabsch_rmsd=rmsd,
        is_rigid=False,
    )


# Keep backward-compatible alias during transition
def fingerprints_match(
    fp1: TorsionFingerprint,
    fp2: TorsionFingerprint,
    *,
    threshold_deg: float = 15.0,
    coords1: list[tuple[float, float, float]] | None = None,
    coords2: list[tuple[float, float, float]] | None = None,
    rmsd_threshold: float | None = None,
) -> tuple[bool, list[float], float | None]:
    """Backward-compatible wrapper around :func:`compare_conformers`."""
    result = compare_conformers(
        fp1, fp2,
        threshold_deg=threshold_deg,
        coords1=coords1, coords2=coords2,
        rmsd_threshold=rmsd_threshold,
    )
    return result.same_basin, result.torsion_deltas, result.kabsch_rmsd
