"""Unit tests for ``derive_stereo_label_from_3d`` (DR-0018, DR-0031).

This function assigns configurational CIP labels (R/S, E/Z) from 3D geometry.
It was silently dead for months: ``MolFromXYZBlock`` produced a bondless atom
cloud, ``AssignBondOrdersFromTemplate`` then raised ``ValueError: No matching
found``, and a blanket ``except Exception: return None`` swallowed it — so every
input returned ``None`` and every stereoisomer merged into one ``SpeciesEntry``.

These tests pin the repaired behaviour:
- E/Z double bonds and R/S centres are labelled from geometry;
- achiral / no-stereo inputs return ``None``;
- the label string is deterministic under atom re-ordering (canonical rank);
- only *configuration* is labelled — two rotamers of the same configuration
  yield the same label (never a torsional artefact).

Geometries are generated with RDKit ETKDG from a configuration-bearing SMILES,
so the coordinates faithfully encode the intended stereochemistry.
"""

from __future__ import annotations

import random

from rdkit import Chem
from rdkit.Chem import AllChem

from app.chemistry.species import derive_stereo_label_from_3d


def _xyz_from_smiles(smiles: str, *, seed: int = 0xC0FFEE) -> str:
    """Embed a 3D conformer for ``smiles`` and return its XYZ block."""
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0, smiles
    AllChem.MMFFOptimizeMolecule(mol)
    return Chem.MolToXYZBlock(mol)


def _permute_xyz_atoms(xyz_block: str, *, seed: int) -> str:
    """Return the same geometry with its coordinate lines shuffled.

    Header (atom count + comment) is preserved; only the atom rows are
    reordered. This is a stronger determinism probe than re-embedding: the
    physical configuration is byte-for-byte identical, only the atom *order*
    differs.
    """
    lines = xyz_block.strip().splitlines()
    header, coords = lines[:2], lines[2:]
    rng = random.Random(seed)
    rng.shuffle(coords)
    return "\n".join(header + coords)


# ---------------------------------------------------------------------------
# E/Z double-bond configuration
# ---------------------------------------------------------------------------


class TestEZDoubleBonds:
    def test_cis_diazene_is_Z(self) -> None:
        xyz = _xyz_from_smiles(r"[H]/N=N\[H]")
        assert derive_stereo_label_from_3d("N=N", xyz) == "Z"

    def test_trans_diazene_is_E(self) -> None:
        xyz = _xyz_from_smiles(r"[H]/N=N/[H]")
        assert derive_stereo_label_from_3d("N=N", xyz) == "E"

    def test_cis_2_butene_is_Z(self) -> None:
        xyz = _xyz_from_smiles(r"C/C=C\C")
        assert derive_stereo_label_from_3d("CC=CC", xyz) == "Z"

    def test_trans_2_butene_is_E(self) -> None:
        xyz = _xyz_from_smiles(r"C/C=C/C")
        assert derive_stereo_label_from_3d("CC=CC", xyz) == "E"

    def test_hand_written_trans_2_butene_is_E(self) -> None:
        # Independent, hand-placed planar geometry (not RDKit-embedded from a
        # stereo-SMILES) to break the circularity of the ETKDG-based fixtures.
        # C1=C2 double bond along x; the two methyl carbons sit on opposite
        # sides of the double bond (trans) -> E.
        hand_trans_2_butene = """12

C   -1.90   0.55   0.00
C   -0.60   0.55   0.00
C    0.60  -0.55   0.00
C    1.90  -0.55   0.00
H   -0.10   1.50   0.00
H    0.10  -1.50   0.00
H   -2.30   1.56   0.00
H   -2.60  -0.10   0.52
H   -1.85   0.20  -1.03
H    2.30  -1.56   0.00
H    2.60   0.10  -0.52
H    1.85  -0.20   1.03
"""
        assert derive_stereo_label_from_3d("CC=CC", hand_trans_2_butene) == "E"


# ---------------------------------------------------------------------------
# Tetrahedral chiral-centre configuration
# ---------------------------------------------------------------------------


class TestChiralCentres:
    def test_enantiomers_get_opposite_labels(self) -> None:
        # CHFClBr is the textbook single-stereocentre case. The two hand
        # configurations must produce opposite CIP labels from geometry alone.
        r_like = derive_stereo_label_from_3d(
            "[CH](F)(Cl)Br", _xyz_from_smiles("[C@H](F)(Cl)Br")
        )
        s_like = derive_stereo_label_from_3d(
            "[CH](F)(Cl)Br", _xyz_from_smiles("[C@@H](F)(Cl)Br")
        )
        assert {r_like, s_like} == {"R", "S"}
        assert r_like != s_like


# ---------------------------------------------------------------------------
# Achiral / no-stereo inputs return None
# ---------------------------------------------------------------------------


class TestNoStereoReturnsNone:
    def test_benzene(self) -> None:
        assert derive_stereo_label_from_3d("c1ccccc1", _xyz_from_smiles("c1ccccc1")) is None

    def test_methane(self) -> None:
        assert derive_stereo_label_from_3d("C", _xyz_from_smiles("C")) is None

    def test_hydrazine_n2h4(self) -> None:
        assert derive_stereo_label_from_3d("NN", _xyz_from_smiles("NN")) is None

    def test_dihydrogen(self) -> None:
        assert derive_stereo_label_from_3d("[H][H]", _xyz_from_smiles("[H][H]")) is None

    def test_unparseable_geometry_returns_none(self) -> None:
        assert derive_stereo_label_from_3d("CC=CC", "not an xyz block") is None


# ---------------------------------------------------------------------------
# Determinism: identical label regardless of uploaded atom ordering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_permuted_atom_order_same_geometry_same_label(self) -> None:
        # Strongest determinism probe: ONE fixed geometry of a genuine
        # 2-stereocentre molecule (3-bromobutan-2-... backbone, mixed R/S so the
        # emit order actually matters), with its XYZ atom rows repeatedly
        # shuffled. Without canonical-rank ordering the label would flip between
        # "S,R" and "R,S" and the species would spuriously split into two
        # entries. Every permutation must yield an identical string.
        base = _xyz_from_smiles("C[C@H](Br)[C@H](Br)C")
        base_label = derive_stereo_label_from_3d("CC(Br)C(Br)C", base)
        assert base_label is not None and "," in base_label
        for seed in range(6):
            permuted = _permute_xyz_atoms(base, seed=seed)
            assert derive_stereo_label_from_3d("CC(Br)C(Br)C", permuted) == base_label

    def test_meso_dibromobutane_stable_under_permutation(self) -> None:
        # meso-2,3-dibromobutane: a hard 2-stereocentre case. One geometry,
        # permuted atom order, must give a single stable label string.
        base = _xyz_from_smiles("C[C@H](Br)[C@@H](Br)C")
        base_label = derive_stereo_label_from_3d("CC(Br)C(Br)C", base)
        assert base_label is not None and "," in base_label
        labels = {
            derive_stereo_label_from_3d(
                "CC(Br)C(Br)C", _permute_xyz_atoms(base, seed=seed)
            )
            for seed in range(6)
        }
        assert labels == {base_label}


# ---------------------------------------------------------------------------
# Open-shell / radical species (formerly a KNOWN LIMITATION, now supported)
# ---------------------------------------------------------------------------


class TestRadicalStereo:
    """Configurational stereo is now labelled for open-shell/radical species.

    Formerly a known limitation: ``AssignBondOrdersFromTemplate`` raised
    ``ValueError`` on a template carrying radical electrons, so every radical
    returned ``None`` and stereoisomeric radicals merged into one
    ``SpeciesEntry``. The template's radical electrons are now neutralized on a
    copy before bond-order mapping, so the geometry-derived CIP labels come
    through exactly as for closed-shell species. These tests pin the fix.
    """

    def test_e_crotyl_radical_is_E(self) -> None:
        # E-crotyl radical: a genuine stereogenic double bond on an open-shell
        # species. Formerly None; now correctly "E".
        xyz = _xyz_from_smiles(r"C/C=C/[CH2]")
        assert derive_stereo_label_from_3d("CC=C[CH2]", xyz) == "E"

    def test_z_crotyl_radical_is_Z(self) -> None:
        # Z-crotyl radical: opposite double-bond configuration. Formerly None.
        xyz = _xyz_from_smiles(r"C/C=C\[CH2]")
        assert derive_stereo_label_from_3d("CC=C[CH2]", xyz) == "Z"

    def test_e_and_z_crotyl_radicals_are_distinct(self) -> None:
        # The two stereoisomeric radicals must no longer merge: distinct labels.
        e_label = derive_stereo_label_from_3d(
            "CC=C[CH2]", _xyz_from_smiles(r"C/C=C/[CH2]")
        )
        z_label = derive_stereo_label_from_3d(
            "CC=C[CH2]", _xyz_from_smiles(r"C/C=C\[CH2]")
        )
        assert {e_label, z_label} == {"E", "Z"}

    def test_chiral_radical_enantiomers_get_opposite_labels(self) -> None:
        # A radical bearing a tetrahedral stereocentre. The two hand
        # configurations must yield opposite CIP labels from geometry alone.
        r_like = derive_stereo_label_from_3d(
            "[CH](F)(Cl)[CH2]", _xyz_from_smiles("[C@H](F)(Cl)[CH2]")
        )
        s_like = derive_stereo_label_from_3d(
            "[CH](F)(Cl)[CH2]", _xyz_from_smiles("[C@@H](F)(Cl)[CH2]")
        )
        assert {r_like, s_like} == {"R", "S"}
        assert r_like != s_like

    def test_methyl_radical_has_no_configurational_stereo(self) -> None:
        # [CH3] is an open-shell species with no stereocentre or E/Z bond:
        # the radical path must still return None (no spurious label).
        assert derive_stereo_label_from_3d("[CH3]", _xyz_from_smiles("[CH3]")) is None

    def test_two_rotamers_of_a_radical_share_one_label(self) -> None:
        # Conformer-vs-configuration safety holds for radicals too: different
        # torsional conformers of the same E-crotyl configuration must all
        # yield "E", never a torsional artefact.
        labels = {
            derive_stereo_label_from_3d("CC=C[CH2]", _xyz_from_smiles(r"C/C=C/[CH2]", seed=seed))
            for seed in (1, 7, 42, 101)
        }
        assert labels == {"E"}


# ---------------------------------------------------------------------------
# Conformer-vs-configuration safety: rotamers must not change the label
# ---------------------------------------------------------------------------


class TestConformerSafety:
    def test_two_rotamers_of_E_2_pentene_both_E(self) -> None:
        # Different torsional conformers of the SAME E configuration. The
        # ethyl/methyl single bonds rotate freely; that torsion must never be
        # mistaken for configurational stereo.
        labels = {
            derive_stereo_label_from_3d("CCC=CC", _xyz_from_smiles(r"CC/C=C/C", seed=seed))
            for seed in (1, 7, 42, 101)
        }
        assert labels == {"E"}
