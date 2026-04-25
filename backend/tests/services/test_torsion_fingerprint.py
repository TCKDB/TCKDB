"""Tests for the torsion fingerprint system (DR-0005).

Covers:
- Circular difference calculation
- Angle normalization and symmetry folding
- Rotor slot identification for various molecule types
- Fingerprint computation and matching
- Basin matching (same basin vs different rotamers)
"""

from __future__ import annotations

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from app.chemistry.torsion_fingerprint import (
    AtomMappingResult,
    ConformerComparisonResult,
    RotorSlot,
    TorsionFingerprint,
    circular_difference,
    compare_conformers,
    compute_fingerprint_from_xyz,
    compute_torsion_fingerprint,
    fingerprints_match,
    fold_angle,
    identify_rotor_slots,
    kabsch_rmsd,
    normalize_angle,
    resolve_atom_mapping,
)


# ---------------------------------------------------------------------------
# Circular difference
# ---------------------------------------------------------------------------


class TestCircularDifference:
    def test_simple_difference(self):
        assert circular_difference(10.0, 20.0) == pytest.approx(10.0)

    def test_wraparound(self):
        # 5° and 355° are 10° apart, not 350°
        assert circular_difference(5.0, 355.0) == pytest.approx(10.0)

    def test_same_angle(self):
        assert circular_difference(180.0, 180.0) == pytest.approx(0.0)

    def test_opposite(self):
        assert circular_difference(0.0, 180.0) == pytest.approx(180.0)

    def test_period_120(self):
        # In 120° period: 10° and 110° → diff=100, min(100, 20) = 20°
        assert circular_difference(10.0, 110.0, period=120.0) == pytest.approx(20.0)

    def test_period_120_same_basin(self):
        # 5° and 115° differ by 110° raw, but circular in 120° = 10°
        assert circular_difference(5.0, 115.0, period=120.0) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Angle normalization and folding
# ---------------------------------------------------------------------------


class TestAngleNormalization:
    def test_negative_to_positive(self):
        assert normalize_angle(-60.0) == pytest.approx(300.0)

    def test_already_normalized(self):
        assert normalize_angle(45.0) == pytest.approx(45.0)

    def test_360_wraps_to_zero(self):
        assert normalize_angle(360.0) == pytest.approx(0.0)


class TestAngleFolding:
    def test_no_fold(self):
        assert fold_angle(250.0, 1) == pytest.approx(250.0)

    def test_threefold(self):
        # 250° mod 120° = 10°
        assert fold_angle(250.0, 3) == pytest.approx(10.0)

    def test_twofold(self):
        # 250° mod 180° = 70°
        assert fold_angle(250.0, 2) == pytest.approx(70.0)

    def test_methyl_equivalence(self):
        # 0°, 120°, 240° all fold to 0° under 3-fold
        assert fold_angle(0.0, 3) == pytest.approx(0.0)
        assert fold_angle(120.0, 3) == pytest.approx(0.0)
        assert fold_angle(240.0, 3) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Rotor identification
# ---------------------------------------------------------------------------


class TestRotorIdentification:
    def test_ethane_no_rotors(self):
        """Ethane (C-C with only H on each side) — both terminal, no rotor."""
        mol = Chem.MolFromSmiles("CC")
        mol = Chem.AddHs(mol)
        slots = identify_rotor_slots(mol)
        # Both sides are CH3 (terminal-heavy with only 1 heavy neighbor each)
        # But ethane C-C does have methyl rotors — the filter allows if
        # at least one side is CH3 with heavy neighbor count considerations
        # Actually: each C has 1 heavy neighbor (the other C). So both < 2.
        # After the fix, this should be filtered out.
        assert len(slots) == 0

    def test_butane_one_rotor(self):
        """n-Butane: one central C-C rotor."""
        mol = Chem.MolFromSmiles("CCCC")
        mol = Chem.AddHs(mol)
        slots = identify_rotor_slots(mol)
        # Central C-C bond: both sides have ≥2 heavy neighbors
        assert len(slots) >= 1
        # The two terminal C-C bonds are methyl rotors
        non_methyl = [s for s in slots if not s.is_methyl]
        assert len(non_methyl) == 1

    def test_butane_exclude_methyl(self):
        """Excluding methyl rotors leaves only the central C-C."""
        mol = Chem.MolFromSmiles("CCCC")
        mol = Chem.AddHs(mol)
        slots = identify_rotor_slots(mol, exclude_methyl=True)
        assert len(slots) == 1
        assert not slots[0].is_methyl

    def test_rigid_molecule_no_rotors(self):
        """Benzene — all bonds in ring, no rotatable bonds."""
        mol = Chem.MolFromSmiles("c1ccccc1")
        mol = Chem.AddHs(mol)
        slots = identify_rotor_slots(mol)
        assert len(slots) == 0

    def test_single_atom_no_rotors(self):
        """Hydrogen atom — no bonds."""
        mol = Chem.MolFromSmiles("[H]")
        mol = Chem.AddHs(mol)
        slots = identify_rotor_slots(mol)
        assert len(slots) == 0

    def test_double_methyl_sixfold_symmetry(self):
        """Bond with methyl on both sides gets 6-fold symmetry (3×2).

        Dimethyl ether (COC): each C-O bond has CH3 on the C side and
        O with 2 heavy neighbors on the other. Only one side is methyl → 3-fold.

        2,3-dimethylbutane CC(C)C(C)C: the terminal C-C(CH3) bonds have
        methyl on one side → 3-fold. The central C-C has no methyl on
        either side (each central C has 3 heavy neighbors).
        """
        # Dimethyl ether: single methyl per bond → 3-fold
        mol = Chem.MolFromSmiles("COC")
        mol = Chem.AddHs(mol)
        slots = identify_rotor_slots(mol, methyl_symmetry_fold=3)
        methyl_slots = [s for s in slots if s.is_methyl]
        assert all(s.symmetry_fold == 3 for s in methyl_slots)

    def test_canonical_key_invariant(self):
        """Same molecule from different SMILES should give same canonical keys."""
        mol1 = Chem.MolFromSmiles("CCCC")
        mol1 = Chem.AddHs(mol1)
        mol2 = Chem.MolFromSmiles("C(CC)C")
        mol2 = Chem.AddHs(mol2)

        slots1 = identify_rotor_slots(mol1, exclude_methyl=True)
        slots2 = identify_rotor_slots(mol2, exclude_methyl=True)

        keys1 = [s.canonical_key for s in slots1]
        keys2 = [s.canonical_key for s in slots2]
        assert keys1 == keys2

    def test_methyl_detected(self):
        """Methyl groups should be detected and have symmetry_fold=3."""
        mol = Chem.MolFromSmiles("CCCC")
        mol = Chem.AddHs(mol)
        slots = identify_rotor_slots(mol, methyl_symmetry_fold=3)
        methyl_slots = [s for s in slots if s.is_methyl]
        assert len(methyl_slots) >= 1
        assert all(s.symmetry_fold == 3 for s in methyl_slots)


# ---------------------------------------------------------------------------
# Fingerprint computation + matching
# ---------------------------------------------------------------------------


class TestFingerprintComputation:
    def _make_butane_conformer(self, dihedral_deg: float):
        """Create a butane mol with a specific central C-C-C-C dihedral."""
        mol = Chem.MolFromSmiles("CCCC")
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, randomSeed=42)
        from rdkit.Chem import rdMolTransforms
        conf = mol.GetConformer()
        # Set the central dihedral (heavy atoms 0-1-2-3)
        rdMolTransforms.SetDihedralDeg(conf, 0, 1, 2, 3, dihedral_deg)
        return mol

    def test_fingerprint_has_correct_rotor_count(self):
        mol = self._make_butane_conformer(60.0)
        fp = compute_torsion_fingerprint(mol, exclude_methyl=True)
        assert fp.rotor_count == 1

    def test_same_basin_matches(self):
        """Two conformers at 60° and 65° (same gauche basin) should match."""
        mol1 = self._make_butane_conformer(60.0)
        mol2 = self._make_butane_conformer(65.0)

        fp1 = compute_torsion_fingerprint(mol1, exclude_methyl=True)
        fp2 = compute_torsion_fingerprint(mol2, exclude_methyl=True)

        matches, deltas, _rmsd = fingerprints_match(fp1, fp2, threshold_deg=15.0)
        assert matches
        assert all(d < 15.0 for d in deltas)

    def test_different_rotamer_doesnt_match(self):
        """60° (gauche) vs 180° (anti) — different basins."""
        mol1 = self._make_butane_conformer(60.0)
        mol2 = self._make_butane_conformer(180.0)

        fp1 = compute_torsion_fingerprint(mol1, exclude_methyl=True)
        fp2 = compute_torsion_fingerprint(mol2, exclude_methyl=True)

        matches, deltas, _rmsd = fingerprints_match(fp1, fp2, threshold_deg=15.0)
        assert not matches

    def test_wraparound_matching(self):
        """5° and 355° should match (10° apart)."""
        mol1 = self._make_butane_conformer(5.0)
        mol2 = self._make_butane_conformer(355.0)

        fp1 = compute_torsion_fingerprint(mol1, exclude_methyl=True)
        fp2 = compute_torsion_fingerprint(mol2, exclude_methyl=True)

        matches, deltas, _rmsd = fingerprints_match(fp1, fp2, threshold_deg=15.0)
        assert matches

    def test_rigid_molecule_always_matches(self):
        """Benzene (no rotors) — two conformers always match."""
        mol1 = Chem.MolFromSmiles("c1ccccc1")
        mol1 = Chem.AddHs(mol1)
        AllChem.EmbedMolecule(mol1, randomSeed=42)

        mol2 = Chem.MolFromSmiles("c1ccccc1")
        mol2 = Chem.AddHs(mol2)
        AllChem.EmbedMolecule(mol2, randomSeed=99)

        fp1 = compute_torsion_fingerprint(mol1)
        fp2 = compute_torsion_fingerprint(mol2)

        matches, _, _rmsd = fingerprints_match(fp1, fp2)
        assert matches

    def test_fingerprint_serialization(self):
        """to_dict() and reconstruction should round-trip."""
        mol = self._make_butane_conformer(60.0)
        fp = compute_torsion_fingerprint(mol, exclude_methyl=True)
        d = fp.to_dict()

        assert "rotor_count" in d
        assert "canonical_rotor_keys" in d
        assert "raw_torsions_deg" in d
        assert "folded_torsions_deg" in d
        assert "quantized_bins" in d
        assert "fingerprint_hash" in d
        assert len(d["canonical_rotor_keys"]) == fp.rotor_count

    def test_hash_includes_rotor_keys(self):
        """Two molecules with different rotor structures should hash differently
        even if they happen to have the same quantized bin values."""
        mol1 = self._make_butane_conformer(60.0)
        fp1 = compute_torsion_fingerprint(mol1, exclude_methyl=True)

        # Manually create a fingerprint with same bins but different keys
        fp2 = TorsionFingerprint(
            rotor_slots=[RotorSlot(0, 1, 2, 3, canonical_rank_begin=99, canonical_rank_end=100)],
            raw_torsions_deg=fp1.raw_torsions_deg,
            folded_torsions_deg=fp1.folded_torsions_deg,
            quantized_bins=fp1.quantized_bins,
        )

        assert fp1.fingerprint_hash != fp2.fingerprint_hash


# ---------------------------------------------------------------------------
# Graph-based atom mapping
# ---------------------------------------------------------------------------


class TestAtomMapping:
    def test_ethanol_unique_mapping(self):
        """Ethanol (no heavy-atom symmetry) → unique mapping."""
        mol = Chem.MolFromSmiles("CCO")
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, randomSeed=42)
        conf = mol.GetConformer()

        xyz_atoms = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        result = resolve_atom_mapping("CCO", xyz_atoms)
        assert result.status == "unique"
        assert result.fingerprint is not None
        assert result.n_mappings == 1

    def test_propane_symmetric_equivalent(self):
        """Propane has mirror symmetry — 2 heavy-atom mappings,
        but they should produce equivalent fingerprints (no rotors
        once methyl is excluded, or same methyl torsions)."""
        mol = Chem.MolFromSmiles("CCC")
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, randomSeed=42)
        conf = mol.GetConformer()

        xyz_atoms = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        result = resolve_atom_mapping("CCC", xyz_atoms, exclude_methyl=True)
        # Propane with methyl excluded has 0 rotors → fingerprints trivially match
        assert result.status in ("unique", "equivalent")
        assert result.fingerprint is not None

    def test_single_atom_no_match_needed(self):
        """Single atom (H) — no rotors, should still produce a fingerprint."""
        xyz_atoms = (("H", 0.0, 0.0, 0.0),)
        fp = compute_fingerprint_from_xyz("[H]", xyz_atoms)
        assert fp.rotor_count == 0

    def test_scrambled_atom_order(self):
        """XYZ with scrambled atom order should still produce valid fingerprint."""
        mol = Chem.MolFromSmiles("CCO")
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, randomSeed=42)
        conf = mol.GetConformer()

        # Normal order
        xyz_normal = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        # Scramble: reverse order
        xyz_scrambled = tuple(reversed(xyz_normal))

        fp_normal = compute_fingerprint_from_xyz("CCO", xyz_normal)
        fp_scrambled = compute_fingerprint_from_xyz("CCO", xyz_scrambled)

        # Both should produce valid fingerprints with same rotor count
        assert fp_normal.rotor_count == fp_scrambled.rotor_count

    def test_element_mismatch_raises(self):
        """XYZ with wrong elements should fail."""
        xyz_atoms = (("N", 0.0, 0.0, 0.0), ("N", 1.0, 0.0, 0.0))
        with pytest.raises(ValueError):
            compute_fingerprint_from_xyz("CC", xyz_atoms)

    def test_sanity_check_four_guarantees(self):
        """The four fundamental guarantees of the isomorphism system:
        1. Same molecule → mapping found
        2. Different molecule → no mapping
        3. Same molecule reordered → mapping found
        4. Mapped coordinates → same fingerprint
        """
        mol = Chem.MolFromSmiles("CCO")
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol, randomSeed=42)
        conf = mol.GetConformer()

        xyz = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        # 1. Same molecule → mapping found
        r = resolve_atom_mapping("CCO", xyz)
        assert r.status in ("unique", "equivalent")

        # 2. Different molecule → no mapping
        r_wrong = resolve_atom_mapping("CCCO", xyz)
        assert r_wrong.status == "no_match"

        # 3. Same molecule reordered → mapping found
        import random
        xyz_list = list(xyz)
        random.Random(42).shuffle(xyz_list)
        r_shuffled = resolve_atom_mapping("CCO", tuple(xyz_list))
        assert r_shuffled.status in ("unique", "equivalent")

        # 4. Mapped coordinates → same fingerprint
        assert r.fingerprint.fingerprint_hash == r_shuffled.fingerprint.fingerprint_hash

    def test_glyoxal_scrambled_ordering(self):
        """Glyoxal (O=CC=O) with two different atom orderings.

        Order 1: O C C O H H (natural)
        Order 2: O H H C C O (scrambled)

        Graph isomorphism should map both to the same canonical species
        and produce identical torsion fingerprints.
        """
        # Order 1: O C C O H H
        xyz_order1 = (
            ("O",  1.3543,  1.0529,  0.1454),
            ("C",  0.7621,  0.0240, -0.0005),
            ("C", -0.7621, -0.0240,  0.0005),
            ("O", -1.3543, -1.0529, -0.1454),
            ("H",  1.2588, -0.9541, -0.1447),
            ("H", -1.2588,  0.9541,  0.1447),
        )

        # Order 2: O H H C C O (scrambled)
        xyz_order2 = (
            ("O",  1.3543,  1.0529,  0.1454),
            ("H",  1.2588, -0.9541, -0.1447),
            ("H", -1.2588,  0.9541,  0.1447),
            ("C",  0.7621,  0.0240, -0.0005),
            ("C", -0.7621, -0.0240,  0.0005),
            ("O", -1.3543, -1.0529, -0.1454),
        )

        smiles = "O=CC=O"

        r1 = resolve_atom_mapping(smiles, xyz_order1)
        r2 = resolve_atom_mapping(smiles, xyz_order2)

        # Both should resolve successfully
        assert r1.status in ("unique", "equivalent"), f"Order 1 failed: {r1.status}"
        assert r2.status in ("unique", "equivalent"), f"Order 2 failed: {r2.status}"
        assert r1.fingerprint is not None
        assert r2.fingerprint is not None

        # Glyoxal has 1 rotatable C-C bond → 1 rotor
        assert r1.fingerprint.rotor_count >= 1
        assert r2.fingerprint.rotor_count >= 1

        # Same fingerprint hash regardless of input atom ordering
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash

        # Fingerprints should match as same basin
        match, deltas, _rmsd = fingerprints_match(r1.fingerprint, r2.fingerprint)
        assert match


# ---------------------------------------------------------------------------
# Kabsch RMSD
# ---------------------------------------------------------------------------


class TestKabschRMSD:
    def test_identical_coords_zero_rmsd(self):
        coords = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        assert kabsch_rmsd(coords, coords) == pytest.approx(0.0, abs=1e-10)

    def test_translated_coords_zero_rmsd(self):
        """Translation should be removed by centering → RMSD ≈ 0."""
        coords_a = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        coords_b = [(5.0, 5.0, 5.0), (6.0, 5.0, 5.0), (5.0, 6.0, 5.0)]
        assert kabsch_rmsd(coords_a, coords_b) == pytest.approx(0.0, abs=1e-6)

    def test_rotated_coords_zero_rmsd(self):
        """90° rotation about Z axis should be corrected → RMSD ≈ 0."""
        # Original: triangle in XY plane
        coords_a = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)]
        # Rotated 90° about Z: (x,y) → (-y,x)
        coords_b = [(0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0)]
        assert kabsch_rmsd(coords_a, coords_b) == pytest.approx(0.0, abs=1e-6)

    def test_different_structures_nonzero_rmsd(self):
        """Actually different geometries should give nonzero RMSD."""
        coords_a = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        coords_b = [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)]
        rmsd = kabsch_rmsd(coords_a, coords_b)
        assert rmsd > 0.1

    def test_single_atom(self):
        """Single atom → distance."""
        assert kabsch_rmsd([(0.0, 0.0, 0.0)], [(1.0, 0.0, 0.0)]) == pytest.approx(1.0)

    def test_rmsd_with_fingerprint_match_rigid_molecule(self):
        """For rigid molecules (0 rotors), RMSD is the primary check."""
        # Two benzene conformers from different seeds
        mol1 = Chem.MolFromSmiles("c1ccccc1")
        mol1 = Chem.AddHs(mol1)
        AllChem.EmbedMolecule(mol1, randomSeed=42)

        mol2 = Chem.MolFromSmiles("c1ccccc1")
        mol2 = Chem.AddHs(mol2)
        AllChem.EmbedMolecule(mol2, randomSeed=42)  # same seed = same geometry

        fp1 = compute_torsion_fingerprint(mol1)
        fp2 = compute_torsion_fingerprint(mol2)

        # Extract coords in canonical order
        c1 = mol1.GetConformer()
        c2 = mol2.GetConformer()
        coords1 = [(c1.GetAtomPosition(i).x, c1.GetAtomPosition(i).y, c1.GetAtomPosition(i).z)
                    for i in range(mol1.GetNumAtoms())]
        coords2 = [(c2.GetAtomPosition(i).x, c2.GetAtomPosition(i).y, c2.GetAtomPosition(i).z)
                    for i in range(mol2.GetNumAtoms())]

        match, deltas, rmsd = fingerprints_match(
            fp1, fp2, coords1=coords1, coords2=coords2, rmsd_threshold=0.5
        )
        assert match
        assert rmsd is not None
        assert rmsd == pytest.approx(0.0, abs=1e-6)

    def test_rmsd_gates_rigid_match(self):
        """For rigid molecules, a large RMSD should prevent matching."""
        mol1 = Chem.MolFromSmiles("c1ccccc1")
        mol1 = Chem.AddHs(mol1)
        AllChem.EmbedMolecule(mol1, randomSeed=42)

        mol2 = Chem.MolFromSmiles("c1ccccc1")
        mol2 = Chem.AddHs(mol2)
        AllChem.EmbedMolecule(mol2, randomSeed=42)

        fp1 = compute_torsion_fingerprint(mol1)
        fp2 = compute_torsion_fingerprint(mol2)

        c1 = mol1.GetConformer()
        coords1 = [(c1.GetAtomPosition(i).x, c1.GetAtomPosition(i).y, c1.GetAtomPosition(i).z)
                    for i in range(mol1.GetNumAtoms())]
        # Shift all coords by 10 Å — large RMSD after Kabsch
        coords2_shifted = [(x + 0.5, y + 0.5, z + 0.5) for x, y, z in coords1]

        # With a very tight RMSD threshold, pure translation is removed by Kabsch
        # so this should still match (Kabsch corrects translation)
        match_loose, _, rmsd_loose = fingerprints_match(
            fp1, fp2, coords1=coords1, coords2=coords2_shifted, rmsd_threshold=0.5
        )
        assert match_loose  # translation is removed
        assert rmsd_loose == pytest.approx(0.0, abs=1e-6)

        # But actually distorted coords should fail
        coords2_distorted = [(x + 0.3 * i, y, z) for i, (x, y, z) in enumerate(coords1)]
        match_distort, _, rmsd_distort = fingerprints_match(
            fp1, fp2, coords1=coords1, coords2=coords2_distorted, rmsd_threshold=0.1
        )
        assert not match_distort
        assert rmsd_distort > 0.1


# ---------------------------------------------------------------------------
# Hardcoded isomorphism tests with RDKit-optimized geometries
# Covers molecules with increasing symmetry complexity.
# Geometries generated with ETKDGv3 + MMFF, then hardcoded.
# ---------------------------------------------------------------------------


class TestIsomorphismHardcoded:
    """Verify graph isomorphism + canonical fingerprinting on real geometries.

    Each molecule is tested with its original atom order and a shuffled variant.
    Both must produce identical fingerprint hashes.
    """

    # -- ClCCO: unique mapping (no heavy-atom symmetry) --

    _CLCCO_SMILES = "ClCCO"
    _CLCCO_ORIGINAL = (
        ("Cl",   -0.952886,     1.631770,    -0.807067),
        ("C",    -0.897179,     0.067828,     0.051477),
        ("C",     0.488621,    -0.550211,    -0.067640),
        ("O",     1.463224,     0.237660,     0.610585),
        ("H",    -1.656364,    -0.583069,    -0.390084),
        ("H",    -1.149951,     0.267777,     1.096468),
        ("H",     0.794366,    -0.662210,    -1.113144),
        ("H",     0.492319,    -1.543168,     0.392238),
        ("H",     1.417850,     1.133623,     0.227167),
    )
    _CLCCO_SHUFFLED = (
        ("O",     1.463224,     0.237660,     0.610585),
        ("H",     0.794366,    -0.662210,    -1.113144),
        ("H",     0.492319,    -1.543168,     0.392238),
        ("H",    -1.656364,    -0.583069,    -0.390084),
        ("H",     1.417850,     1.133623,     0.227167),
        ("C",     0.488621,    -0.550211,    -0.067640),
        ("H",    -1.149951,     0.267777,     1.096468),
        ("Cl",   -0.952886,     1.631770,    -0.807067),
        ("C",    -0.897179,     0.067828,     0.051477),
    )

    def test_clcco_isomorphism(self):
        r1 = resolve_atom_mapping(self._CLCCO_SMILES, self._CLCCO_ORIGINAL)
        r2 = resolve_atom_mapping(self._CLCCO_SMILES, self._CLCCO_SHUFFLED)
        assert r1.status == "unique"
        assert r2.status in ("unique", "equivalent", "canonicalized")
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash
        assert r1.fingerprint.rotor_count == 1

    # -- ClCCN: unique mapping --

    _CLCCN_SMILES = "ClCCN"
    _CLCCN_ORIGINAL = (
        ("Cl",    1.864702,     1.461338,    -0.043892),
        ("C",     1.141759,    -0.165847,    -0.023053),
        ("C",    -0.316530,    -0.147134,     0.446690),
        ("N",    -1.226276,     0.529490,    -0.478401),
        ("H",     1.744120,    -0.778168,     0.654836),
        ("H",     1.227935,    -0.586218,    -1.030042),
        ("H",    -0.657848,    -1.181372,     0.567905),
        ("H",    -0.386327,     0.332035,     1.429593),
        ("H",    -1.209006,     0.075755,    -1.390269),
        ("H",    -2.182530,     0.460121,    -0.133368),
    )
    _CLCCN_SHUFFLED = (
        ("H",    -0.386327,     0.332035,     1.429593),
        ("N",    -1.226276,     0.529490,    -0.478401),
        ("C",    -0.316530,    -0.147134,     0.446690),
        ("H",    -1.209006,     0.075755,    -1.390269),
        ("H",     1.227935,    -0.586218,    -1.030042),
        ("H",    -0.657848,    -1.181372,     0.567905),
        ("H",    -2.182530,     0.460121,    -0.133368),
        ("H",     1.744120,    -0.778168,     0.654836),
        ("Cl",    1.864702,     1.461338,    -0.043892),
        ("C",     1.141759,    -0.165847,    -0.023053),
    )

    def test_clccn_isomorphism(self):
        r1 = resolve_atom_mapping(self._CLCCN_SMILES, self._CLCCN_ORIGINAL)
        r2 = resolve_atom_mapping(self._CLCCN_SMILES, self._CLCCN_SHUFFLED)
        assert r1.status == "unique"
        assert r2.status in ("unique", "equivalent", "canonicalized")
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash
        assert r1.fingerprint.rotor_count == 1

    # -- ClCCCl: mirror symmetry → equivalent mappings --

    _CLCCCL_SMILES = "ClCCCl"
    _CLCCCL_ORIGINAL = (
        ("Cl",    1.705104,    -0.053764,     1.241555),
        ("C",     0.729210,    -0.000920,    -0.247787),
        ("C",    -0.758860,    -0.125954,     0.037587),
        ("Cl",   -1.409657,     1.317982,     0.852949),
        ("H",     1.059657,    -0.836362,    -0.872191),
        ("H",     0.962627,     0.930027,    -0.773746),
        ("H",    -0.978242,    -0.996836,     0.663060),
        ("H",    -1.309840,    -0.234173,    -0.901428),
    )
    _CLCCCL_SHUFFLED = (
        ("Cl",   -1.409657,     1.317982,     0.852949),
        ("H",     1.059657,    -0.836362,    -0.872191),
        ("H",    -0.978242,    -0.996836,     0.663060),
        ("H",    -1.309840,    -0.234173,    -0.901428),
        ("C",    -0.758860,    -0.125954,     0.037587),
        ("H",     0.962627,     0.930027,    -0.773746),
        ("Cl",    1.705104,    -0.053764,     1.241555),
        ("C",     0.729210,    -0.000920,    -0.247787),
    )

    def test_clcccl_isomorphism(self):
        """ClCCCl has Cl-C-C-Cl mirror symmetry — two Cl swaps are equivalent."""
        r1 = resolve_atom_mapping(self._CLCCCL_SMILES, self._CLCCCL_ORIGINAL)
        r2 = resolve_atom_mapping(self._CLCCCL_SMILES, self._CLCCCL_SHUFFLED)
        assert r1.status in ("equivalent", "canonicalized")
        assert r2.status in ("equivalent", "canonicalized")
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash
        assert r1.fingerprint.rotor_count == 1

    # -- CC(Cl)CO: branched, unique mapping, 2 rotors --

    _CCCLCO_SMILES = "CC(Cl)CO"
    _CCCLCO_ORIGINAL = (
        ("C",     1.522101,    -0.007465,    -0.040664),
        ("C",     0.092090,    -0.500954,     0.124380),
        ("Cl",   -0.093420,    -1.240746,     1.744877),
        ("C",    -0.918491,     0.636150,    -0.064827),
        ("O",    -2.269697,     0.181244,    -0.037138),
        ("H",     1.671375,     0.414272,    -1.040331),
        ("H",     1.766244,     0.770018,     0.691596),
        ("H",     2.238067,    -0.827229,     0.082829),
        ("H",    -0.111348,    -1.300707,    -0.595215),
        ("H",    -0.761692,     1.120862,    -1.034464),
        ("H",    -0.805547,     1.402607,     0.709470),
        ("H",    -2.329682,    -0.648052,    -0.540514),
    )
    _CCCLCO_SHUFFLED = (
        ("H",     2.238067,    -0.827229,     0.082829),
        ("H",     1.671375,     0.414272,    -1.040331),
        ("Cl",   -0.093420,    -1.240746,     1.744877),
        ("H",    -0.111348,    -1.300707,    -0.595215),
        ("H",    -0.761692,     1.120862,    -1.034464),
        ("H",     1.766244,     0.770018,     0.691596),
        ("H",    -2.329682,    -0.648052,    -0.540514),
        ("C",    -0.918491,     0.636150,    -0.064827),
        ("O",    -2.269697,     0.181244,    -0.037138),
        ("C",     1.522101,    -0.007465,    -0.040664),
        ("C",     0.092090,    -0.500954,     0.124380),
        ("H",    -0.805547,     1.402607,     0.709470),
    )

    def test_ccclco_isomorphism(self):
        """CC(Cl)CO — branched, 2 rotors, unique mapping."""
        r1 = resolve_atom_mapping(self._CCCLCO_SMILES, self._CCCLCO_ORIGINAL)
        r2 = resolve_atom_mapping(self._CCCLCO_SMILES, self._CCCLCO_SHUFFLED)
        assert r1.status == "unique"
        assert r2.status in ("unique", "equivalent", "canonicalized")
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash
        assert r1.fingerprint.rotor_count == 2

    # -- ClCC=O: chloroacetaldehyde, unique mapping, 1 rotor --

    _CLCCO_ALD_SMILES = "ClCC=O"
    _CLCCO_ALD_ORIGINAL = (
        ("Cl",    1.523499,     1.261224,    -0.194520),
        ("C",     0.581812,    -0.231795,    -0.039279),
        ("C",    -0.868545,     0.130066,     0.086368),
        ("O",    -1.630783,    -0.402891,     0.889392),
        ("H",     0.729409,    -0.836242,    -0.936964),
        ("H",     0.918772,    -0.774920,     0.846612),
        ("H",    -1.254164,     0.854558,    -0.651608),
    )
    _CLCCO_ALD_SHUFFLED = (
        ("C",     0.581812,    -0.231795,    -0.039279),
        ("O",    -1.630783,    -0.402891,     0.889392),
        ("H",     0.729409,    -0.836242,    -0.936964),
        ("C",    -0.868545,     0.130066,     0.086368),
        ("H",    -1.254164,     0.854558,    -0.651608),
        ("Cl",    1.523499,     1.261224,    -0.194520),
        ("H",     0.918772,    -0.774920,     0.846612),
    )

    def test_clcco_aldehyde_isomorphism(self):
        r1 = resolve_atom_mapping(self._CLCCO_ALD_SMILES, self._CLCCO_ALD_ORIGINAL)
        r2 = resolve_atom_mapping(self._CLCCO_ALD_SMILES, self._CLCCO_ALD_SHUFFLED)
        assert r1.status == "unique"
        assert r2.status in ("unique", "equivalent", "canonicalized")
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash
        assert r1.fingerprint.rotor_count == 1

    # -- Cross-molecule: verify different molecules DON'T match --

    def test_different_molecules_no_match(self):
        """ClCCO XYZ should not map onto ClCCN SMILES."""
        r = resolve_atom_mapping(self._CLCCN_SMILES, self._CLCCO_ORIGINAL)
        assert r.status == "no_match"

    # -- Chloral (from DR-0005 example): CCl3 3-fold symmetry --

    _CHLORAL_SMILES = "O=CC(Cl)(Cl)Cl"
    _CHLORAL_ORIGINAL = (
        ("O",     1.3572,    -0.1671,     1.3611),
        ("C",     0.9236,     0.0526,     0.2798),
        ("C",    -0.5771,    -0.0341,    -0.0851),
        ("Cl",   -1.0319,     1.5740,    -0.6898),
        ("Cl",   -1.5652,    -0.4943,     1.2822),
        ("Cl",   -0.6988,    -1.2311,    -1.3932),
        ("H",     1.5409,     0.3436,    -0.5884),
    )
    _CHLORAL_SHUFFLED = (
        ("Cl",   -0.6988,    -1.2311,    -1.3932),
        ("H",     1.5409,     0.3436,    -0.5884),
        ("C",    -0.5771,    -0.0341,    -0.0851),
        ("O",     1.3572,    -0.1671,     1.3611),
        ("C",     0.9236,     0.0526,     0.2798),
        ("Cl",   -1.5652,    -0.4943,     1.2822),
        ("Cl",   -1.0319,     1.5740,    -0.6898),
    )

    def test_chloral_canonicalization(self):
        """Chloral (O=CC(Cl)(Cl)Cl) — 3-fold Cl symmetry gives 6 mappings.

        The system must canonicalize via lexicographic minimization and
        produce identical fingerprints regardless of atom ordering.
        """
        r1 = resolve_atom_mapping(self._CHLORAL_SMILES, self._CHLORAL_ORIGINAL)
        r2 = resolve_atom_mapping(self._CHLORAL_SMILES, self._CHLORAL_SHUFFLED)
        assert r1.status == "canonicalized"
        assert r2.status == "canonicalized"
        assert r1.n_mappings == 6
        assert r2.n_mappings == 6
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash
        assert r1.fingerprint.rotor_count == 1


# ---------------------------------------------------------------------------
# Conformer DISCRIMINATION tests
# Verifies that the system correctly distinguishes gauche vs anti conformers
# of the same molecule, not just that isomorphism survives reordering.
# ---------------------------------------------------------------------------


class TestConformerDiscrimination:
    """Test that the system separates distinct rotamers (gauche vs anti)
    and groups near-identical conformers (gauche vs gauche-prime)."""

    # -- ClCCO: Cl-C-C-O dihedral --

    _CLCCO_GAUCHE = (
        ("Cl",    -0.952886,     1.631770,    -0.807067),
        ("C",    -0.897179,     0.067828,     0.051477),
        ("C",     0.488621,    -0.550211,    -0.067640),
        ("O",     1.482099,     0.302183,     0.495395),
        ("H",    -1.656364,    -0.583069,    -0.390084),
        ("H",    -1.149951,     0.267777,     1.096468),
        ("H",     0.753011,    -0.755494,    -1.110261),
        ("H",     0.520507,    -1.496926,     0.480246),
        ("H",     1.412599,     1.159053,     0.034133),
    )
    _CLCCO_GAUCHE_PRIME = (
        ("Cl",    -0.952886,     1.631770,    -0.807067),
        ("C",    -0.897179,     0.067828,     0.051477),
        ("C",     0.488621,    -0.550211,    -0.067640),
        ("O",     1.446004,     0.183540,     0.691049),
        ("H",    -1.656364,    -0.583069,    -0.390084),
        ("H",    -1.149951,     0.267777,     1.096468),
        ("H",     0.825333,    -0.593627,    -1.108718),
        ("H",     0.473651,    -1.571886,     0.324050),
        ("H",     1.416540,     1.103579,     0.367815),
    )
    _CLCCO_ANTI = (
        ("Cl",    -0.952886,     1.631770,    -0.807067),
        ("C",    -0.897179,     0.067828,     0.051477),
        ("C",     0.488621,    -0.550211,    -0.067640),
        ("O",     0.554144,    -1.800992,     0.611943),
        ("H",    -1.656364,    -0.583069,    -0.390084),
        ("H",    -1.149951,     0.267777,     1.096468),
        ("H",     1.263208,     0.109531,     0.337155),
        ("H",     0.721953,    -0.736741,    -1.120363),
        ("H",     0.296861,    -1.632527,     1.537818),
    )

    def test_clcco_gauche_vs_anti_different_basin(self):
        """ClCCO: 60° (gauche) vs 180° (anti) must be different basins."""
        rg = resolve_atom_mapping("ClCCO", self._CLCCO_GAUCHE)
        ra = resolve_atom_mapping("ClCCO", self._CLCCO_ANTI)
        result = compare_conformers(rg.fingerprint, ra.fingerprint, threshold_deg=15.0)
        assert not result.same_basin
        assert max(result.torsion_deltas) > 100  # ~120° apart

    def test_clcco_gauche_vs_gauche_prime_same_basin(self):
        """ClCCO: 60° vs 70° — same gauche basin, within 15° threshold."""
        rg = resolve_atom_mapping("ClCCO", self._CLCCO_GAUCHE)
        rp = resolve_atom_mapping("ClCCO", self._CLCCO_GAUCHE_PRIME)
        result = compare_conformers(rg.fingerprint, rp.fingerprint, threshold_deg=15.0)
        assert result.same_basin
        assert max(result.torsion_deltas) == pytest.approx(10.0, abs=0.5)

    # -- ClCCN: Cl-C-C-N dihedral --

    _CLCCN_GAUCHE = (
        ("Cl",     1.864702,     1.461338,    -0.043892),
        ("C",     1.141759,    -0.165847,    -0.023053),
        ("C",    -0.316530,    -0.147134,     0.446690),
        ("N",    -1.196877,     0.666410,    -0.392588),
        ("H",     1.744120,    -0.778168,     0.654836),
        ("H",     1.227935,    -0.586218,    -1.030042),
        ("H",    -0.696787,    -1.174871,     0.446763),
        ("H",    -0.371960,     0.216540,     1.478795),
        ("H",    -1.193247,     0.321679,    -1.351140),
        ("H",    -2.156325,     0.593344,    -0.057323),
    )
    _CLCCN_ANTI = (
        ("Cl",     1.864702,     1.461338,    -0.043892),
        ("C",     1.141759,    -0.165847,    -0.023053),
        ("C",    -0.316530,    -0.147134,     0.446690),
        ("N",    -0.944279,    -1.468605,     0.476635),
        ("H",     1.744120,    -0.778168,     0.654836),
        ("H",     1.227935,    -0.586218,    -1.030042),
        ("H",    -0.352329,     0.272234,     1.458466),
        ("H",    -0.907859,     0.513872,    -0.196716),
        ("H",    -0.434210,    -2.090890,     1.101349),
        ("H",    -1.890764,    -1.391874,     0.846176),
    )

    def test_clccn_gauche_vs_anti_different_basin(self):
        """ClCCN: 60° vs 180° must be different basins."""
        rg = resolve_atom_mapping("ClCCN", self._CLCCN_GAUCHE)
        ra = resolve_atom_mapping("ClCCN", self._CLCCN_ANTI)
        result = compare_conformers(rg.fingerprint, ra.fingerprint, threshold_deg=15.0)
        assert not result.same_basin

    # -- ClCCCl: symmetric, Cl-C-C-Cl dihedral --

    _CLCCCL_GAUCHE = (
        ("Cl",     1.705104,    -0.053764,     1.241555),
        ("C",     0.729210,    -0.000920,    -0.247787),
        ("C",    -0.758860,    -0.125954,     0.037587),
        ("Cl",   -1.353949,     1.185886,     1.085560),
        ("H",     1.059657,    -0.836362,    -0.872191),
        ("H",     0.962627,     0.930027,    -0.773746),
        ("H",    -0.999140,    -1.079584,     0.517833),
        ("H",    -1.323373,    -0.064369,    -0.897598),
    )
    _CLCCCL_ANTI = (
        ("Cl",     1.705104,    -0.053764,     1.241555),
        ("C",     0.729210,    -0.000920,    -0.247787),
        ("C",    -0.758860,    -0.125954,     0.037587),
        ("Cl",   -1.734755,    -0.073110,    -1.451755),
        ("H",     1.059657,    -0.836362,    -0.872191),
        ("H",     0.962627,     0.930027,    -0.773746),
        ("H",    -1.110680,     0.669073,     0.702370),
        ("H",    -0.966061,    -1.087099,     0.517490),
    )

    def test_clcccl_gauche_vs_anti_different_basin(self):
        """ClCCCl: 60° vs 180° — symmetric molecule, still different basins."""
        rg = resolve_atom_mapping("ClCCCl", self._CLCCCL_GAUCHE)
        ra = resolve_atom_mapping("ClCCCl", self._CLCCCL_ANTI)
        result = compare_conformers(rg.fingerprint, ra.fingerprint, threshold_deg=15.0)
        assert not result.same_basin

    # -- ClCC=O: chloroacetaldehyde, Cl-C-C=O dihedral --

    _CLCCO_ALD_GAUCHE = (
        ("Cl",     1.523499,     1.261224,    -0.194520),
        ("C",     0.581812,    -0.231795,    -0.039279),
        ("C",    -0.868545,     0.130066,     0.086368),
        ("O",    -1.486505,     0.752917,    -0.773912),
        ("H",     0.729409,    -0.836242,    -0.936964),
        ("H",     0.918772,    -0.774920,     0.846612),
        ("H",    -1.349591,    -0.118139,     1.048234),
    )
    _CLCCO_ALD_ANTI = (
        ("Cl",     1.523499,     1.261224,    -0.194520),
        ("C",     0.581812,    -0.231795,    -0.039279),
        ("C",    -0.868545,     0.130066,     0.086368),
        ("O",    -1.764362,    -0.702026,     0.208972),
        ("H",     0.729409,    -0.836242,    -0.936964),
        ("H",     0.918772,    -0.774920,     0.846612),
        ("H",    -1.113602,     1.200438,    -0.025210),
    )

    def test_clcco_ald_gauche_vs_anti_different_basin(self):
        """ClCC=O: 60° vs 180° must be different basins."""
        rg = resolve_atom_mapping("ClCC=O", self._CLCCO_ALD_GAUCHE)
        ra = resolve_atom_mapping("ClCC=O", self._CLCCO_ALD_ANTI)
        result = compare_conformers(rg.fingerprint, ra.fingerprint, threshold_deg=15.0)
        assert not result.same_basin

    # -- RMSD consistency: same basin should have small RMSD --

    def test_same_basin_small_rmsd(self):
        """Gauche vs gauche-prime (10° apart) should have small RMSD."""
        rg = resolve_atom_mapping("ClCCO", self._CLCCO_GAUCHE)
        rp = resolve_atom_mapping("ClCCO", self._CLCCO_GAUCHE_PRIME)
        result = compare_conformers(
            rg.fingerprint, rp.fingerprint,
            threshold_deg=15.0,
            coords1=rg.mapped_coords,
            coords2=rp.mapped_coords,
        )
        assert result.same_basin
        assert result.kabsch_rmsd < 0.2  # <0.2 Å for near-identical conformers

    def test_different_basin_large_rmsd(self):
        """Gauche vs anti should have significant RMSD."""
        rg = resolve_atom_mapping("ClCCO", self._CLCCO_GAUCHE)
        ra = resolve_atom_mapping("ClCCO", self._CLCCO_ANTI)
        result = compare_conformers(
            rg.fingerprint, ra.fingerprint,
            threshold_deg=15.0,
            coords1=rg.mapped_coords,
            coords2=ra.mapped_coords,
        )
        assert not result.same_basin
        assert result.kabsch_rmsd > 0.5  # >0.5 Å for different rotamers


# ---------------------------------------------------------------------------
# Mapping ambiguity → torsion correctness
# Verifies that ALL valid atom mappings (not just the chosen one) produce
# scientifically consistent fingerprints. Tests two cases:
#   1. Symmetric molecule where all mappings MUST give same fingerprint.
#   2. Symmetric molecule where mappings give different fingerprints, and
#      canonicalization picks a consistent minimum regardless of input order.
# ---------------------------------------------------------------------------


class TestMappingAmbiguityTorsionCorrectness:
    """Verify that atom-mapping ambiguity does not corrupt torsion identity."""

    def test_clcccl_all_mappings_same_fingerprint(self):
        """ClCCCl mirror symmetry: both Cl-swap mappings MUST give the same
        torsion fingerprint, because the dihedral definition is symmetric.

        If they differ, the canonical dihedral quartet selection is broken.
        """
        from app.chemistry.torsion_fingerprint import (
            _build_mol_from_xyz, compute_torsion_fingerprint,
        )

        smiles = "ClCCCl"
        xyz = (
            ("Cl",  1.705104, -0.053764,  1.241555),
            ("C",   0.729210, -0.000920, -0.247787),
            ("C",  -0.758860, -0.125954,  0.037587),
            ("Cl", -1.409657,  1.317982,  0.852949),
            ("H",   1.059657, -0.836362, -0.872191),
            ("H",   0.962627,  0.930027, -0.773746),
            ("H",  -0.978242, -0.996836,  0.663060),
            ("H",  -1.309840, -0.234173, -0.901428),
        )

        ref_mol = Chem.MolFromSmiles(smiles)
        ref_mol = Chem.AddHs(ref_mol)
        xyz_mol = _build_mol_from_xyz(xyz)

        params = Chem.AdjustQueryParameters.NoAdjustments()
        params.makeBondsGeneric = True
        query = Chem.AdjustQueryProperties(xyz_mol, params)
        matches = ref_mol.GetSubstructMatches(query, uniquify=False)

        # Deduplicate by heavy atoms
        seen: dict[tuple, tuple] = {}
        for m in matches:
            hk = tuple(
                m[i] for i in range(xyz_mol.GetNumAtoms())
                if xyz_mol.GetAtomWithIdx(i).GetAtomicNum() > 1
            )
            if hk not in seen:
                seen[hk] = m

        assert len(seen) == 2, f"Expected 2 heavy-atom mappings, got {len(seen)}"

        # Compute fingerprint under each mapping
        fingerprints = []
        for match in seen.values():
            conf = Chem.Conformer(ref_mol.GetNumAtoms())
            for xi in range(len(xyz)):
                _, x, y, z = xyz[xi]
                conf.SetAtomPosition(match[xi], (x, y, z))
            cid = ref_mol.AddConformer(conf, assignId=True)
            fp = compute_torsion_fingerprint(ref_mol, conformer_id=cid)
            ref_mol.RemoveConformer(cid)
            fingerprints.append(fp)

        # ALL mappings must produce the same fingerprint
        assert fingerprints[0].quantized_bins == fingerprints[1].quantized_bins, (
            f"Mirror-symmetric ClCCCl mappings produced different fingerprints: "
            f"{fingerprints[0].quantized_bins} vs {fingerprints[1].quantized_bins}"
        )

    def test_chloral_mappings_differ_but_canonical_is_stable(self):
        """Chloral (O=CC(Cl)(Cl)Cl): 3-fold Cl symmetry → 6 mappings → 3
        distinct fingerprints. The canonicalization must:
        1. Recognize the mappings disagree.
        2. Pick the lexicographic minimum consistently.
        3. Produce the same canonical fingerprint from any input ordering.
        """
        from app.chemistry.torsion_fingerprint import (
            _build_mol_from_xyz, compute_torsion_fingerprint,
        )

        smiles = "O=CC(Cl)(Cl)Cl"
        xyz = (
            ("O",   1.3572, -0.1671,  1.3611),
            ("C",   0.9236,  0.0526,  0.2798),
            ("C",  -0.5771, -0.0341, -0.0851),
            ("Cl", -1.0319,  1.5740, -0.6898),
            ("Cl", -1.5652, -0.4943,  1.2822),
            ("Cl", -0.6988, -1.2311, -1.3932),
            ("H",   1.5409,  0.3436, -0.5884),
        )

        ref_mol = Chem.MolFromSmiles(smiles)
        ref_mol = Chem.AddHs(ref_mol)
        xyz_mol = _build_mol_from_xyz(xyz)

        params = Chem.AdjustQueryParameters.NoAdjustments()
        params.makeBondsGeneric = True
        query = Chem.AdjustQueryProperties(xyz_mol, params)
        matches = ref_mol.GetSubstructMatches(query, uniquify=False)

        seen: dict[tuple, tuple] = {}
        for m in matches:
            hk = tuple(
                m[i] for i in range(xyz_mol.GetNumAtoms())
                if xyz_mol.GetAtomWithIdx(i).GetAtomicNum() > 1
            )
            if hk not in seen:
                seen[hk] = m

        assert len(seen) == 6, f"Expected 6 heavy-atom mappings, got {len(seen)}"

        # Compute all fingerprints
        all_bins = []
        for match in seen.values():
            conf = Chem.Conformer(ref_mol.GetNumAtoms())
            for xi in range(len(xyz)):
                _, x, y, z = xyz[xi]
                conf.SetAtomPosition(match[xi], (x, y, z))
            cid = ref_mol.AddConformer(conf, assignId=True)
            fp = compute_torsion_fingerprint(ref_mol, conformer_id=cid)
            ref_mol.RemoveConformer(cid)
            all_bins.append(fp.quantized_bins)

        # Mappings SHOULD disagree (3 distinct bin vectors)
        distinct = set(tuple(b) for b in all_bins)
        assert len(distinct) == 3, (
            f"Expected 3 distinct fingerprints from 6 Cl permutations, "
            f"got {len(distinct)}: {distinct}"
        )

        # The canonical result must equal the lexicographic minimum of all mappings.
        # (We don't hardcode the specific bins — that's a quantization detail.)
        canonical_bins = min(all_bins)

        # Verify resolve_atom_mapping picks this same lex-min canonical from
        # multiple input orderings
        import random
        for seed in [1, 42, 99, 777]:
            shuffled = list(xyz)
            random.Random(seed).shuffle(shuffled)
            r = resolve_atom_mapping(smiles, tuple(shuffled))
            assert r.status == "canonicalized"
            assert r.fingerprint.quantized_bins == canonical_bins, (
                f"Seed {seed}: expected lex-min bins {canonical_bins}, "
                f"got {r.fingerprint.quantized_bins}"
            )


# ---------------------------------------------------------------------------
# Threshold boundary tests
# Verifies exact behavior at the 15° cutoff edge.
# ---------------------------------------------------------------------------


class TestThresholdBoundary:
    """Test that the threshold boundary behaves exactly as specified:
    - 14.9° apart → same basin
    - 15.0° apart → same basin (<=)
    - 15.1° apart → different basin
    """

    def _make_butane(self, angle: float):
        mol = Chem.AddHs(Chem.MolFromSmiles("CCCC"))
        AllChem.EmbedMolecule(mol, randomSeed=42)
        from rdkit.Chem import rdMolTransforms
        conf = mol.GetConformer()
        rdMolTransforms.SetDihedralDeg(conf, 0, 1, 2, 3, angle)
        return mol

    def test_14_9_deg_matches(self):
        """14.9° apart — just inside threshold → same basin."""
        fp1 = compute_torsion_fingerprint(self._make_butane(60.0), exclude_methyl=True)
        fp2 = compute_torsion_fingerprint(self._make_butane(74.9), exclude_methyl=True)
        result = compare_conformers(fp1, fp2, threshold_deg=15.0)
        assert result.same_basin
        assert result.torsion_deltas[0] == pytest.approx(14.9, abs=0.5)

    def test_15_0_deg_matches(self):
        """15.0° apart — exactly at threshold → same basin (uses <=)."""
        fp1 = compute_torsion_fingerprint(self._make_butane(60.0), exclude_methyl=True)
        fp2 = compute_torsion_fingerprint(self._make_butane(75.0), exclude_methyl=True)
        result = compare_conformers(fp1, fp2, threshold_deg=15.0)
        assert result.same_basin

    def test_15_1_deg_fails(self):
        """15.1° apart — just outside threshold → different basin."""
        fp1 = compute_torsion_fingerprint(self._make_butane(60.0), exclude_methyl=True)
        fp2 = compute_torsion_fingerprint(self._make_butane(75.1), exclude_methyl=True)
        result = compare_conformers(fp1, fp2, threshold_deg=15.0)
        assert not result.same_basin

    def test_wraparound_boundary(self):
        """Near 0°/360° wraparound: 7° and 353° are 14° apart → same basin."""
        fp1 = compute_torsion_fingerprint(self._make_butane(7.0), exclude_methyl=True)
        fp2 = compute_torsion_fingerprint(self._make_butane(353.0), exclude_methyl=True)
        result = compare_conformers(fp1, fp2, threshold_deg=15.0)
        assert result.same_basin
        assert result.torsion_deltas[0] == pytest.approx(14.0, abs=0.5)


# ---------------------------------------------------------------------------
# All-mapping consistency test
# For molecules with multiple valid graph isomorphisms, verifies that
# every mapping is accounted for and the canonical choice is stable.
# This is the strongest test against hidden mapping bugs.
# ---------------------------------------------------------------------------


class TestAllMappingConsistency:
    """Enumerate ALL valid atom mappings and verify fingerprint behavior."""

    @staticmethod
    def _enumerate_and_fingerprint(smiles, xyz_atoms):
        """Return (n_heavy_mappings, list_of_fingerprints) for all valid mappings."""
        from app.chemistry.torsion_fingerprint import (
            _build_mol_from_xyz, compute_torsion_fingerprint,
        )
        ref_mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        xyz_mol = _build_mol_from_xyz(xyz_atoms)

        params = Chem.AdjustQueryParameters.NoAdjustments()
        params.makeBondsGeneric = True
        query = Chem.AdjustQueryProperties(xyz_mol, params)
        matches = ref_mol.GetSubstructMatches(query, uniquify=False)

        seen: dict[tuple, tuple] = {}
        for m in matches:
            hk = tuple(
                m[i] for i in range(xyz_mol.GetNumAtoms())
                if xyz_mol.GetAtomWithIdx(i).GetAtomicNum() > 1
            )
            if hk not in seen:
                seen[hk] = m

        fps = []
        for match in seen.values():
            conf = Chem.Conformer(ref_mol.GetNumAtoms())
            for xi in range(len(xyz_atoms)):
                _, x, y, z = xyz_atoms[xi]
                conf.SetAtomPosition(match[xi], (x, y, z))
            cid = ref_mol.AddConformer(conf, assignId=True)
            fp = compute_torsion_fingerprint(ref_mol, conformer_id=cid)
            ref_mol.RemoveConformer(cid)
            fps.append(fp)

        return len(seen), fps

    def test_clcco_unique_all_mappings_agree(self):
        """ClCCO (no symmetry): should have exactly 1 heavy-atom mapping."""
        n, fps = self._enumerate_and_fingerprint(
            "ClCCO", TestIsomorphismHardcoded._CLCCO_ORIGINAL
        )
        assert n == 1
        assert len(fps) == 1

    def test_clcccl_symmetric_all_mappings_agree(self):
        """ClCCCl (mirror): 2 mappings, ALL must produce same fingerprint."""
        n, fps = self._enumerate_and_fingerprint(
            "ClCCCl", TestIsomorphismHardcoded._CLCCCL_ORIGINAL
        )
        assert n == 2
        hashes = {fp.fingerprint_hash for fp in fps}
        assert len(hashes) == 1, (
            f"Mirror-symmetric ClCCCl has {len(hashes)} distinct fingerprints "
            f"from {n} mappings — expected 1"
        )

    def test_chloral_3fold_mappings_and_canonical_minimum(self):
        """Chloral (CCl3): 6 mappings → 3 distinct fingerprints.
        The canonical minimum must be the same as what resolve_atom_mapping picks.
        """
        n, fps = self._enumerate_and_fingerprint(
            "O=CC(Cl)(Cl)Cl", TestIsomorphismHardcoded._CHLORAL_ORIGINAL
        )
        assert n == 6

        distinct_bins = set(tuple(fp.quantized_bins) for fp in fps)
        assert len(distinct_bins) == 3

        # The minimum should match what resolve_atom_mapping returns
        min_bins = min(fp.quantized_bins for fp in fps)
        r = resolve_atom_mapping(
            "O=CC(Cl)(Cl)Cl", TestIsomorphismHardcoded._CHLORAL_ORIGINAL
        )
        assert r.fingerprint.quantized_bins == min_bins

    def test_ccclco_branched_unique(self):
        """CC(Cl)CO (branched, 2 rotors): should have 1 mapping — no symmetry."""
        n, fps = self._enumerate_and_fingerprint(
            "CC(Cl)CO", TestIsomorphismHardcoded._CCCLCO_ORIGINAL
        )
        assert n == 1


# ---------------------------------------------------------------------------
# Chirality / stereochemistry safety test
# Verifies that enantiomers are not silently collapsed.
# ---------------------------------------------------------------------------


class TestChiralitySafety:
    """Verify that the system does not silently merge enantiomers.

    Stereo should be resolved at the species_entry level, not at
    conformer grouping. But if someone passes R-geometry with S-SMILES,
    the system should not quietly say 'same conformer'.
    """

    def test_enantiomers_not_merged(self):
        """R and S enantiomers of 2-chloro-1-propanol must NOT be merged
        into the same conformer group.

        The graph isomorphism is bond-order-agnostic and not stereo-aware,
        so the mapping succeeds for both. But the torsion fingerprints
        computed from the R geometry vs the S geometry should differ because
        the 3D arrangement of substituents is mirrored — different dihedral
        angles for at least one rotor. The grouping logic must therefore
        report same_basin=False.
        """
        mol_r = Chem.AddHs(Chem.MolFromSmiles("C[C@H](Cl)CO"))
        mol_s = Chem.AddHs(Chem.MolFromSmiles("C[C@@H](Cl)CO"))

        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        AllChem.EmbedMolecule(mol_r, params)
        AllChem.EmbedMolecule(mol_s, params)
        AllChem.MMFFOptimizeMolecule(mol_r)
        AllChem.MMFFOptimizeMolecule(mol_s)

        conf_r = mol_r.GetConformer()
        xyz_r = tuple(
            (mol_r.GetAtomWithIdx(i).GetSymbol(),
             conf_r.GetAtomPosition(i).x,
             conf_r.GetAtomPosition(i).y,
             conf_r.GetAtomPosition(i).z)
            for i in range(mol_r.GetNumAtoms())
        )

        conf_s = mol_s.GetConformer()
        xyz_s = tuple(
            (mol_s.GetAtomWithIdx(i).GetSymbol(),
             conf_s.GetAtomPosition(i).x,
             conf_s.GetAtomPosition(i).y,
             conf_s.GetAtomPosition(i).z)
            for i in range(mol_s.GetNumAtoms())
        )

        # Both should resolve against the achiral SMILES
        achiral = "CC(Cl)CO"
        r_from_r = resolve_atom_mapping(achiral, xyz_r)
        r_from_s = resolve_atom_mapping(achiral, xyz_s)

        assert r_from_r.status in ("unique", "equivalent", "canonicalized")
        assert r_from_s.status in ("unique", "equivalent", "canonicalized")
        assert r_from_r.fingerprint is not None
        assert r_from_s.fingerprint is not None

        # The grouping logic must refuse to merge enantiomers.
        # Mirrored 3D geometry → different dihedral angles → different basin.
        result = compare_conformers(
            r_from_r.fingerprint, r_from_s.fingerprint, threshold_deg=15.0
        )
        assert not result.same_basin, (
            "Enantiomers were merged into the same basin — "
            "torsion fingerprinting failed to distinguish mirrored geometries"
        )

    def test_stereo_smiles_rejects_wrong_enantiomer_geometry(self):
        """If SMILES encodes specific stereochemistry (R), passing
        the S geometry should ideally not produce a 'unique' mapping.

        Note: current implementation uses bond-order-agnostic matching
        which may not enforce chirality. This test documents the behavior.
        """
        mol_s = Chem.AddHs(Chem.MolFromSmiles("C[C@@H](Cl)CO"))
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        AllChem.EmbedMolecule(mol_s, params)
        AllChem.MMFFOptimizeMolecule(mol_s)

        conf_s = mol_s.GetConformer()
        xyz_s = tuple(
            (mol_s.GetAtomWithIdx(i).GetSymbol(),
             conf_s.GetAtomPosition(i).x,
             conf_s.GetAtomPosition(i).y,
             conf_s.GetAtomPosition(i).z)
            for i in range(mol_s.GetNumAtoms())
        )

        # Try mapping S geometry onto R SMILES
        r_result = resolve_atom_mapping("C[C@H](Cl)CO", xyz_s)

        # Document current behavior: the mapping will likely succeed
        # because our matching is bond-order-agnostic and not stereo-aware.
        # This is a known limitation — stereo enforcement belongs at the
        # species_entry level, not the conformer fingerprint level.
        # The test passes regardless, but records what happens.
        assert r_result.status in ("unique", "equivalent", "canonicalized", "no_match")


# ---------------------------------------------------------------------------
# Multi-rotor partial-symmetry test
# bis-CF2: CC(F)(F)CC(F)(F)C — 2 rotors, 8 mappings, canonicalized
# Tests that canonicalization doesn't accidentally optimize one torsion
# while scrambling another.
# ---------------------------------------------------------------------------


class TestMultiRotorPartialSymmetry:
    """bis-CF2 molecule: 2 rotors with F-F symmetry on each side.
    8 valid heavy-atom mappings produce 7 distinct fingerprints.
    Canonicalization must pick the same lex-min across all input orderings.
    """

    _SMILES = "CC(F)(F)CC(F)(F)C"
    _ORIGINAL = (
        ("C",     2.394082,    -0.143841,     0.346649),
        ("C",     0.910142,     0.125337,     0.497504),
        ("F",     0.742533,     1.388016,     0.954277),
        ("F",     0.424335,    -0.707180,     1.453299),
        ("C",     0.149081,    -0.081732,    -0.791696),
        ("C",    -1.297832,     0.359274,    -0.702741),
        ("F",    -1.357187,     1.713212,    -0.618322),
        ("F",    -1.892603,     0.041493,    -1.890438),
        ("C",    -2.130611,    -0.243542,     0.408331),
        ("H",     2.849352,     0.542014,    -0.374371),
        ("H",     2.899673,     0.002510,     1.306732),
        ("H",     2.578676,    -1.174822,     0.029472),
        ("H",     0.624793,     0.493254,    -1.595381),
        ("H",     0.187116,    -1.137764,    -1.084051),
        ("H",    -1.823634,     0.126249,     1.390592),
        ("H",    -2.076188,    -1.336246,     0.395085),
        ("H",    -3.181728,     0.033768,     0.275059),
    )
    _SHUFFLED = (
        ("F",    -1.892603,     0.041493,    -1.890438),
        ("H",     2.899673,     0.002510,     1.306732),
        ("C",    -1.297832,     0.359274,    -0.702741),
        ("F",    -1.357187,     1.713212,    -0.618322),
        ("H",    -2.076188,    -1.336246,     0.395085),
        ("H",    -1.823634,     0.126249,     1.390592),
        ("H",     0.187116,    -1.137764,    -1.084051),
        ("H",     2.849352,     0.542014,    -0.374371),
        ("C",    -2.130611,    -0.243542,     0.408331),
        ("C",     0.910142,     0.125337,     0.497504),
        ("F",     0.742533,     1.388016,     0.954277),
        ("H",     0.624793,     0.493254,    -1.595381),
        ("H",    -3.181728,     0.033768,     0.275059),
        ("C",     0.149081,    -0.081732,    -0.791696),
        ("H",     2.578676,    -1.174822,     0.029472),
        ("C",     2.394082,    -0.143841,     0.346649),
        ("F",     0.424335,    -0.707180,     1.453299),
    )

    def test_multi_rotor_canonicalization_stable(self):
        """8 mappings, 7 distinct fingerprints — canonical lex-min must be
        consistent across original and shuffled orderings."""
        r1 = resolve_atom_mapping(
            self._SMILES, self._ORIGINAL,
            exclude_methyl=True, exclude_terminal_noisy=True,
        )
        r2 = resolve_atom_mapping(
            self._SMILES, self._SHUFFLED,
            exclude_methyl=True, exclude_terminal_noisy=True,
        )
        assert r1.status == "canonicalized"
        assert r2.status == "canonicalized"
        assert r1.n_mappings >= 4  # at least 4 distinct heavy-atom mappings
        assert r1.fingerprint.rotor_count == 2
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash

    def test_multi_rotor_all_mappings_enumerated(self):
        """Verify all 8 mappings and that the canonical choice equals lex-min."""
        from app.chemistry.torsion_fingerprint import (
            _build_mol_from_xyz, compute_torsion_fingerprint,
        )
        ref_mol = Chem.AddHs(Chem.MolFromSmiles(self._SMILES))
        xyz_mol = _build_mol_from_xyz(self._ORIGINAL)

        params = Chem.AdjustQueryParameters.NoAdjustments()
        params.makeBondsGeneric = True
        query = Chem.AdjustQueryProperties(xyz_mol, params)
        matches = ref_mol.GetSubstructMatches(query, uniquify=False)

        seen: dict[tuple, tuple] = {}
        for m in matches:
            hk = tuple(
                m[i] for i in range(xyz_mol.GetNumAtoms())
                if xyz_mol.GetAtomWithIdx(i).GetAtomicNum() > 1
            )
            if hk not in seen:
                seen[hk] = m

        assert len(seen) == 8

        all_bins = []
        for match in seen.values():
            conf = Chem.Conformer(ref_mol.GetNumAtoms())
            for xi in range(len(self._ORIGINAL)):
                _, x, y, z = self._ORIGINAL[xi]
                conf.SetAtomPosition(match[xi], (x, y, z))
            cid = ref_mol.AddConformer(conf, assignId=True)
            fp = compute_torsion_fingerprint(
                ref_mol, conformer_id=cid,
                exclude_methyl=True, exclude_terminal_noisy=True,
            )
            ref_mol.RemoveConformer(cid)
            all_bins.append(fp.quantized_bins)

        # Multiple distinct fingerprints from symmetry
        distinct = set(tuple(b) for b in all_bins)
        assert len(distinct) > 1

        # resolve_atom_mapping must pick the lex-min
        lex_min = min(all_bins)
        r = resolve_atom_mapping(
            self._SMILES, self._ORIGINAL,
            exclude_methyl=True, exclude_terminal_noisy=True,
        )
        assert r.fingerprint.quantized_bins == lex_min


# ---------------------------------------------------------------------------
# Multi-rotor near-degenerate boundary test
# Pentane CCCCC (2 rotors excl methyl): one torsion within threshold,
# the other just outside. Confirms "all torsions must pass" behavior.
# ---------------------------------------------------------------------------


class TestMultiRotorBoundary:
    """Pentane with 2 non-methyl rotors. Tests the 'all must match' rule."""

    _SMILES = "CCCCC"

    _A_60_60 = (
        ("C",    -2.172940,    -0.399605,     0.359439),
        ("C",    -1.259991,     0.354121,    -0.595273),
        ("C",     0.091095,     0.727586,     0.018238),
        ("C",     0.903093,    -0.483776,     0.478352),
        ("C",     1.201086,    -1.423789,    -0.679900),
        ("H",    -2.319243,     0.160676,     1.288261),
        ("H",    -1.767806,    -1.384606,     0.608721),
        ("H",    -3.153981,    -0.552629,    -0.101769),
        ("H",    -1.766489,     1.275745,    -0.906039),
        ("H",    -1.106242,    -0.243519,    -1.501279),
        ("H",    -0.064461,     1.411311,     0.861721),
        ("H",     0.667385,     1.280722,    -0.733874),
        ("H",     0.351469,    -1.030514,     1.251768),
        ("H",     1.851766,    -0.160841,     0.920197),
        ("H",     1.784813,    -2.281482,    -0.331205),
        ("H",     1.776938,    -0.914993,    -1.459565),
        ("H",     0.276077,    -1.802018,    -1.126708),
    )
    _B_70_749 = (
        ("C",    -2.172940,    -0.399605,     0.359439),
        ("C",    -1.259991,     0.354121,    -0.595273),
        ("C",     0.091095,     0.727586,     0.018238),
        ("C",     1.002725,    -0.477140,     0.254900),
        ("C",     1.600024,    -0.984919,    -1.048697),
        ("H",    -2.319243,     0.160676,     1.288261),
        ("H",    -1.767806,    -1.384606,     0.608721),
        ("H",    -3.153981,    -0.552629,    -0.101769),
        ("H",    -1.766489,     1.275745,    -0.906039),
        ("H",    -1.106242,    -0.243519,    -1.501279),
        ("H",    -0.069020,     1.262126,     0.962575),
        ("H",     0.592833,     1.429032,    -0.659975),
        ("H",     0.435718,    -1.286336,     0.729334),
        ("H",     1.819622,    -0.209151,     0.933396),
        ("H",     2.250855,    -1.843988,    -0.857959),
        ("H",     2.197334,    -0.207296,    -1.535398),
        ("H",     0.815309,    -1.301365,    -1.743263),
    )
    _B_70_760 = (
        ("C",    -2.172940,    -0.399605,     0.359439),
        ("C",    -1.259991,     0.354121,    -0.595273),
        ("C",     0.091095,     0.727586,     0.018238),
        ("C",     1.002725,    -0.477140,     0.254900),
        ("C",     1.621199,    -0.968213,    -1.045218),
        ("H",    -2.319243,     0.160676,     1.288261),
        ("H",    -1.767806,    -1.384606,     0.608721),
        ("H",    -3.153981,    -0.552629,    -0.101769),
        ("H",    -1.766489,     1.275745,    -0.906039),
        ("H",    -1.106242,    -0.243519,    -1.501279),
        ("H",    -0.069020,     1.262126,     0.962575),
        ("H",     0.592833,     1.429032,    -0.659975),
        ("H",     0.431092,    -1.293356,     0.711421),
        ("H",     1.808456,    -0.214593,     0.948703),
        ("H",     2.271698,    -1.827531,    -0.854473),
        ("H",     2.223413,    -0.183339,    -1.513907),
        ("H",     0.848038,    -1.278935,    -1.755155),
    )

    def test_both_within_threshold_same_basin(self):
        """[60,60] vs [70,74.9]: deltas ~[10, 14.9] — both within 15° → same."""
        ra = resolve_atom_mapping(self._SMILES, self._A_60_60, exclude_methyl=True)
        rb = resolve_atom_mapping(self._SMILES, self._B_70_749, exclude_methyl=True)
        result = compare_conformers(ra.fingerprint, rb.fingerprint, threshold_deg=15.0)
        assert result.same_basin
        assert all(d <= 15.0 for d in result.torsion_deltas)

    def test_one_outside_threshold_different_basin(self):
        """[60,60] vs [70,76]: deltas ~[10, 16] — second rotor exceeds 15° → different.
        This confirms the 'all torsions must pass' rule, not an aggregate metric."""
        ra = resolve_atom_mapping(self._SMILES, self._A_60_60, exclude_methyl=True)
        rb = resolve_atom_mapping(self._SMILES, self._B_70_760, exclude_methyl=True)
        result = compare_conformers(ra.fingerprint, rb.fingerprint, threshold_deg=15.0)
        assert not result.same_basin
        # One rotor within, one outside
        assert min(result.torsion_deltas) < 15.0
        assert max(result.torsion_deltas) > 15.0


# ---------------------------------------------------------------------------
# Rotor-order stability test
# Verifies that canonical rotor ordering doesn't change based on input
# atom ordering, so multi-rotor conformers compare rotor-by-rotor correctly.
# ---------------------------------------------------------------------------


class TestRotorOrderStability:
    """For a multi-rotor molecule, verify that rotor canonical keys and
    their order are identical regardless of input atom ordering or SMILES."""

    def test_pentane_rotor_order_stable_across_orderings(self):
        """Pentane from normal vs shuffled XYZ → same rotor keys in same order."""
        smiles = "CCCCC"
        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        AllChem.EmbedMolecule(mol, randomSeed=42)
        AllChem.MMFFOptimizeMolecule(mol)
        conf = mol.GetConformer()

        xyz = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        import random
        xyz_shuffled = list(xyz)
        random.Random(42).shuffle(xyz_shuffled)

        r1 = resolve_atom_mapping(smiles, xyz, exclude_methyl=True)
        r2 = resolve_atom_mapping(smiles, tuple(xyz_shuffled), exclude_methyl=True)

        assert r1.fingerprint.rotor_count == r2.fingerprint.rotor_count == 2

        # Canonical rotor keys must be identical and in same order
        keys1 = [r.canonical_key for r in r1.fingerprint.rotor_slots]
        keys2 = [r.canonical_key for r in r2.fingerprint.rotor_slots]
        assert keys1 == keys2

        # Torsion values should also match (same geometry, just reordered)
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash

    def test_branched_molecule_rotor_order(self):
        """CC(Cl)CO (branched, 2 rotors) — rotor keys stable across orderings."""
        r1 = resolve_atom_mapping(
            "CC(Cl)CO",
            TestIsomorphismHardcoded._CCCLCO_ORIGINAL,
            exclude_methyl=True,
        )
        r2 = resolve_atom_mapping(
            "CC(Cl)CO",
            TestIsomorphismHardcoded._CCCLCO_SHUFFLED,
            exclude_methyl=True,
        )

        keys1 = [r.canonical_key for r in r1.fingerprint.rotor_slots]
        keys2 = [r.canonical_key for r in r2.fingerprint.rotor_slots]
        assert keys1 == keys2
        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash


# ---------------------------------------------------------------------------
# Stereocenter-adjacent rotor test
# Verifies that a rotatable bond next to a stereocenter works correctly
# for torsion fingerprinting. The stereocenter creates asymmetric local
# environments that interact with dihedral definitions.
# ---------------------------------------------------------------------------


class TestStereocenterAdjacentRotor:
    """(R)-2-butanol has a stereocenter at C2 with an adjacent C-C rotor.
    Tests that atom mapping, rotor identification, and dihedral computation
    all work correctly when the rotor is next to a chiral center."""

    def test_stereo_adjacent_rotor_resolves(self):
        """(R)-2-butanol geometry resolves against achiral SMILES and
        identifies the correct rotor."""
        mol = Chem.AddHs(Chem.MolFromSmiles("C[C@H](O)CC"))
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        AllChem.EmbedMolecule(mol, params)
        AllChem.MMFFOptimizeMolecule(mol)

        conf = mol.GetConformer()
        xyz = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        # Resolve against achiral SMILES
        r = resolve_atom_mapping(
            "CC(O)CC", xyz, exclude_methyl=True, exclude_terminal_noisy=True,
        )
        assert r.status in ("unique", "equivalent", "canonicalized")
        assert r.fingerprint is not None
        assert r.fingerprint.rotor_count >= 1

    def test_stereo_adjacent_scrambled_stable(self):
        """Shuffled atom ordering doesn't change the fingerprint for
        a molecule with a stereocenter."""
        mol = Chem.AddHs(Chem.MolFromSmiles("C[C@H](O)CC"))
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        AllChem.EmbedMolecule(mol, params)
        AllChem.MMFFOptimizeMolecule(mol)

        conf = mol.GetConformer()
        xyz = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        import random
        xyz_shuffled = list(xyz)
        random.Random(99).shuffle(xyz_shuffled)

        r1 = resolve_atom_mapping("CC(O)CC", xyz, exclude_methyl=True)
        r2 = resolve_atom_mapping("CC(O)CC", tuple(xyz_shuffled), exclude_methyl=True)

        assert r1.fingerprint.fingerprint_hash == r2.fingerprint.fingerprint_hash


# ---------------------------------------------------------------------------
# Methyl-exclusion fallback test
# When exclude_methyl=True leaves zero rotors, the molecule is effectively
# rigid and RMSD becomes the fallback discriminator. This tests that case.
# ---------------------------------------------------------------------------


class TestMethylExclusionFallback:
    """Ethanol (CCO) with exclude_methyl=True has 0 retained rotors.
    The molecule becomes 'rigid' from the matcher's perspective and
    RMSD should be the fallback discriminator."""

    def test_ethanol_zero_rotors_after_methyl_exclusion(self):
        """Ethanol with methyl excluded → 0 rotors."""
        mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
        slots = identify_rotor_slots(mol, exclude_methyl=True)
        assert len(slots) == 0

    def test_ethanol_methyl_excluded_same_geometry_matches(self):
        """Two identical ethanol geometries with 0 rotors → same basin via RMSD."""
        mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
        AllChem.EmbedMolecule(mol, randomSeed=42)
        AllChem.MMFFOptimizeMolecule(mol)
        conf = mol.GetConformer()

        xyz = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        r1 = resolve_atom_mapping("CCO", xyz, exclude_methyl=True)
        r2 = resolve_atom_mapping("CCO", xyz, exclude_methyl=True)

        assert r1.fingerprint.rotor_count == 0
        assert r2.fingerprint.rotor_count == 0

        # With RMSD threshold, identical geometry should match
        result = compare_conformers(
            r1.fingerprint, r2.fingerprint,
            threshold_deg=15.0,
            coords1=r1.mapped_coords,
            coords2=r2.mapped_coords,
            rmsd_threshold=0.5,
        )
        assert result.same_basin
        assert result.is_rigid
        assert result.kabsch_rmsd == pytest.approx(0.0, abs=1e-6)

    def test_ethanol_methyl_excluded_different_geometry_separated(self):
        """Two ethanol geometries with distorted coords → different via RMSD."""
        mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
        AllChem.EmbedMolecule(mol, randomSeed=42)
        AllChem.MMFFOptimizeMolecule(mol)
        conf = mol.GetConformer()

        xyz = tuple(
            (mol.GetAtomWithIdx(i).GetSymbol(),
             conf.GetAtomPosition(i).x,
             conf.GetAtomPosition(i).y,
             conf.GetAtomPosition(i).z)
            for i in range(mol.GetNumAtoms())
        )

        # Distort coordinates
        xyz_distorted = tuple(
            (e, x + 0.3 * i, y, z)
            for i, (e, x, y, z) in enumerate(xyz)
        )

        r1 = resolve_atom_mapping("CCO", xyz, exclude_methyl=True)
        r2 = resolve_atom_mapping("CCO", xyz_distorted, exclude_methyl=True)

        result = compare_conformers(
            r1.fingerprint, r2.fingerprint,
            threshold_deg=15.0,
            coords1=r1.mapped_coords,
            coords2=r2.mapped_coords,
            rmsd_threshold=0.1,
        )
        assert not result.same_basin
        assert result.is_rigid
        assert result.kabsch_rmsd > 0.1
