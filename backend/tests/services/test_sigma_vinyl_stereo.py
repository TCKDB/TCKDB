"""Radical stereo perception from geometry (D1, D6).

A sigma-vinyl radical such as 1-propenyl (``[CH]=CC``) has a stereogenic C=C:
the E and Z isomers are different molecules, and neither SMILES nor InChI can
say which one was computed. Geometry is the only place the configuration
exists, so ``derive_stereo_label_from_3d`` must read it from there -- and must
return no label (never a guess) where geometry does not decide it.

These tests pin behaviour that already holds. The template's radicals are
neutralised only to map bond orders. For a sigma-vinyl radical such as
``[CH]=CC`` RDKit's sanitiser still leaves the unpaired electron on the mapped
molecule, so a bent geometry reads E or Z from the coordinates. (It does not do
so for every radical: ``[O][O]``, ``[CH3]`` and ``C[C](F)Cl`` lose theirs, which
does not matter because none of them has a stereo element that depends on it.)

Geometries here are explicit coordinates (sigma-vinyl) or RDKit embeddings with
a fixed seed whose relevant geometry is asserted before it is used.
"""

from __future__ import annotations

import math

import pytest
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem import rdMolTransforms as T

from app.chemistry.species import classify_stereo_kind, derive_stereo_label_from_3d
from app.db.models.common import StereoKind

# 1-propenyl, planar C=C with a tetrahedral methyl, H-C1=C2 angle about 137
# degrees (a bent sigma-vinyl radical).
# Atoms: C1(radical) C2 H1(on C1) C3(methyl) H2(on C2) H3 H4 H5 (methyl Hs).
# Z: H1 and C3 on the same side of C1=C2.  E: H1 mirrored in y,
# so C3 lies opposite H1.
_PROPENYL_Z = """8

C 0.00000 0.00000 0.00000
C 1.31500 0.00000 0.00000
H -0.79352 0.73997 0.00000
C 2.16461 1.23619 0.00000
H 1.84344 -0.95334 0.00000
H 1.54755 2.14077 0.00000
H 2.80957 1.27341 0.88412
H 2.80957 1.27341 -0.88412
"""
_PROPENYL_E = """8

C 0.00000 0.00000 0.00000
C 1.31500 0.00000 0.00000
H -0.79352 -0.73997 0.00000
C 2.16461 1.23619 0.00000
H 1.84344 -0.95334 0.00000
H 1.54755 2.14077 0.00000
H 2.80957 1.27341 0.88412
H 2.80957 1.27341 -0.88412
"""

# The same E geometry with every atom row shuffled (atom order must not matter).
_PROPENYL_E_SHUFFLED = """8

H 2.80957 1.27341 -0.88412
C 2.16461 1.23619 0.00000
H 1.84344 -0.95334 0.00000
C 0.00000 0.00000 0.00000
H 1.54755 2.14077 0.00000
H -0.79352 -0.73997 0.00000
H 2.80957 1.27341 0.88412
C 1.31500 0.00000 0.00000
"""

_PROPENYL_LINEAR = """8

C 0.00000 0.00000 0.00000
C 1.31500 0.00000 0.00000
H -1.08000 0.00000 0.00000
C 2.16461 1.23619 0.00000
H 1.84344 -0.95334 0.00000
H 1.54755 2.14077 0.00000
H 2.80957 1.27341 0.88412
H 2.80957 1.27341 -0.88412
"""


def _coords(xyz: str) -> list[tuple[str, tuple[float, float, float]]]:
    rows = []
    for line in xyz.strip().splitlines()[2:]:
        el, x, y, z = line.split()
        rows.append((el, (float(x), float(y), float(z))))
    return rows


def _dihedral_deg(p0, p1, p2, p3) -> float:
    def sub(a, b):
        return tuple(x - y for x, y in zip(a, b, strict=True))

    def cross(a, b):
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    b0, b1, b2 = sub(p1, p0), sub(p2, p1), sub(p3, p2)
    n1, n2 = cross(b0, b1), cross(b1, b2)
    m1 = cross(n1, tuple(x / math.sqrt(dot(b1, b1)) for x in b1))
    return math.degrees(math.atan2(dot(m1, n2), dot(n1, n2)))


def _angle_deg(a, b, c) -> float:
    u = tuple(x - y for x, y in zip(a, b, strict=True))
    v = tuple(x - y for x, y in zip(c, b, strict=True))
    cos = sum(x * y for x, y in zip(u, v, strict=True)) / (math.hypot(*u) * math.hypot(*v))
    return math.degrees(math.acos(cos))


def _embed_xyz(smiles: str, *, seed: int) -> tuple[Chem.Mol, str]:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0, smiles
    if AllChem.MMFFHasAllMoleculeParams(mol):
        AllChem.MMFFOptimizeMolecule(mol)
    return mol, Chem.MolToXYZBlock(mol)


# ---------------------------------------------------------------------------
# Fixture sanity: the geometries are what the tests say they are
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("xyz", "expected_dihedral"),
    [(_PROPENYL_Z, 0.0), (_PROPENYL_E, 180.0)],
)
def test_propenyl_fixture_geometry(xyz: str, expected_dihedral: float) -> None:
    atoms = _coords(xyz)
    c1, c2, h1, c3 = (a[1] for a in (atoms[0], atoms[1], atoms[2], atoms[3]))
    # H1-C1=C2 is bent (a real sigma-vinyl), not linear: E/Z is defined.
    assert 120.0 < _angle_deg(h1, c1, c2) < 150.0
    assert abs(abs(_dihedral_deg(h1, c1, c2, c3)) - expected_dihedral) < 1.0


# ---------------------------------------------------------------------------
# sigma-vinyl radical: E and Z from geometry
# ---------------------------------------------------------------------------


class TestSigmaVinylRadical:
    def test_z_propenyl_is_Z(self) -> None:
        assert derive_stereo_label_from_3d("[CH]=CC", _PROPENYL_Z) == "Z"

    def test_e_propenyl_is_E(self) -> None:
        assert derive_stereo_label_from_3d("[CH]=CC", _PROPENYL_E) == "E"

    def test_e_and_z_propenyl_get_different_labels(self) -> None:
        labels = {
            derive_stereo_label_from_3d("[CH]=CC", _PROPENYL_E),
            derive_stereo_label_from_3d("[CH]=CC", _PROPENYL_Z),
        }
        assert labels == {"E", "Z"}

    def test_atom_order_does_not_change_the_label(self) -> None:
        assert derive_stereo_label_from_3d("[CH]=CC", _PROPENYL_E_SHUFFLED) == "E"

    def test_smiles_spelling_does_not_change_the_label(self) -> None:
        # Same radical written from the other end.
        assert derive_stereo_label_from_3d("CC=[CH]", _PROPENYL_E) == "E"
        assert derive_stereo_label_from_3d("CC=[CH]", _PROPENYL_Z) == "Z"

    def test_linear_sigma_vinyl_geometry_gets_no_label(self) -> None:
        # H-C1=C2 collinear: the configuration is geometrically undefined, so
        # no label is returned. Never guess.
        assert derive_stereo_label_from_3d("[CH]=CC", _PROPENYL_LINEAR) is None

    def test_stereo_kind_agrees_with_perception(self) -> None:
        # classify_stereo_kind reads the (radical) identity mol, not the
        # neutralised template: sigma-vinyl is an E/Z isomer, so a geometry
        # label is expected -- and it gets one.
        kind, label = classify_stereo_kind(Chem.MolFromSmiles("[CH]=CC"))
        assert kind == StereoKind.ez_isomer
        assert label is None  # the graph cannot carry E/Z; geometry does
        assert derive_stereo_label_from_3d("[CH]=CC", _PROPENYL_E) in {"E", "Z"}


# ---------------------------------------------------------------------------
# Substituted sigma-vinyl radicals: E and Z from asserted bent geometry
# ---------------------------------------------------------------------------


def _vinyl_radical_xyz(closed_smiles: str, c1: int, c2: int, ref: int, *, dihedral: float) -> str:
    """Embed the closed-shell alkene, delete the H on ``c1`` and return the radical's XYZ.

    The remaining substituent X on ``c1`` is asserted bent (X-C1=C2 between 115
    and 135 degrees) and cis (0) or trans (180) to ``ref`` about C1=C2.
    """
    mol = Chem.AddHs(Chem.MolFromSmiles(closed_smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=11) == 0
    AllChem.MMFFOptimizeMolecule(mol)
    pos = mol.GetConformer().GetPositions()
    nbrs = [n.GetIdx() for n in mol.GetAtomWithIdx(c1).GetNeighbors()]
    h = next(i for i in nbrs if mol.GetAtomWithIdx(i).GetAtomicNum() == 1)
    x = next(i for i in nbrs if i not in (h, c2))
    assert 115.0 < _angle_deg(pos[x], pos[c1], pos[c2]) < 135.0
    assert abs(abs(_dihedral_deg(pos[x], pos[c1], pos[c2], pos[ref])) - dihedral) < 5.0
    keep = [i for i in range(mol.GetNumAtoms()) if i != h]
    return "\n".join(
        [str(len(keep)), ""]
        + [f"{mol.GetAtomWithIdx(i).GetSymbol()} {pos[i][0]:.5f} {pos[i][1]:.5f} {pos[i][2]:.5f}" for i in keep]
    )


@pytest.mark.parametrize(
    ("radical", "closed_e", "closed_z", "c1", "c2", "ref"),
    [
        ("C[C]=CC", "C/C=C/C", r"C/C=C\C", 1, 2, 3),
        ("[C](F)=CC", "F/C=C/C", r"F/C=C\C", 1, 2, 3),
        ("C=C[C]=CC", "C=C/C=C/C", r"C=C/C=C\C", 2, 3, 4),
    ],
)
def test_substituted_sigma_vinyl_e_and_z(radical, closed_e, closed_z, c1, c2, ref) -> None:
    e_xyz = _vinyl_radical_xyz(closed_e, c1, c2, ref, dihedral=180.0)
    z_xyz = _vinyl_radical_xyz(closed_z, c1, c2, ref, dihedral=0.0)
    assert derive_stereo_label_from_3d(radical, e_xyz) == "E"
    assert derive_stereo_label_from_3d(radical, z_xyz) == "Z"


# ---------------------------------------------------------------------------
# Safe cases: no false stereo from radical centres
# ---------------------------------------------------------------------------


class TestNoFalseStereo:
    # Smoke checks only: neither species has a possible stereo element, so no
    # stereo-relevant mutation can fail these.
    def test_dioxygen_biradical_smoke(self) -> None:
        _, xyz = _embed_xyz("[O][O]", seed=7)
        assert derive_stereo_label_from_3d("[O][O]", xyz) is None

    def test_ammonium_charged_smoke(self) -> None:
        _, xyz = _embed_xyz("[NH3+]", seed=7)
        assert derive_stereo_label_from_3d("[NH3+]", xyz) is None

    @pytest.mark.parametrize("embed_smiles", ["C[C@H](F)Cl", "C[C@@H](F)Cl"])
    def test_pyramidal_radical_centre_gets_no_cip_label(self, embed_smiles: str) -> None:
        # C[C](F)Cl with a genuinely pyramidal centre (the H of a chiral
        # closed-shell analogue deleted): a radical centre must NOT receive R/S,
        # even though its signed volume is clearly non-zero.
        mol = Chem.AddHs(Chem.MolFromSmiles(embed_smiles))
        assert AllChem.EmbedMolecule(mol, randomSeed=7) == 0
        AllChem.MMFFOptimizeMolecule(mol)
        pos = mol.GetConformer().GetPositions()
        centre = 1
        h = next(n.GetIdx() for n in mol.GetAtomWithIdx(centre).GetNeighbors() if n.GetAtomicNum() == 1)
        subs = [n.GetIdx() for n in mol.GetAtomWithIdx(centre).GetNeighbors() if n.GetIdx() != h]
        v = [tuple(pos[i] - pos[centre]) for i in subs]
        angle_sum = sum(_angle_deg(pos[a], pos[centre], pos[b]) for a, b in ((subs[0], subs[1]), (subs[0], subs[2]), (subs[1], subs[2])))
        triple = abs(sum(
            v[0][i] * (v[1][(i + 1) % 3] * v[2][(i + 2) % 3] - v[1][(i + 2) % 3] * v[2][(i + 1) % 3])
            for i in range(3)
        ))
        assert angle_sum < 345.0  # pyramidal, not planar (360)
        assert triple > 1.0  # clearly non-zero signed volume
        keep = [i for i in range(mol.GetNumAtoms()) if i != h]
        xyz = "\n".join(
            [str(len(keep)), ""]
            + [f"{mol.GetAtomWithIdx(i).GetSymbol()} {pos[i][0]:.5f} {pos[i][1]:.5f} {pos[i][2]:.5f}" for i in keep]
        )
        assert derive_stereo_label_from_3d("C[C](F)Cl", xyz) is None

    def test_allyl_radical_has_no_label(self) -> None:
        _, xyz = _embed_xyz("[CH2]C=C", seed=7)
        assert derive_stereo_label_from_3d("[CH2]C=C", xyz) is None

    @pytest.mark.parametrize(("embed_smiles", "expected"), [(r"[CH2]/C=C/C", "E"), (r"[CH2]/C=C\C", "Z")])
    def test_crotyl_radical_ez_from_geometry(self, embed_smiles: str, expected: str) -> None:
        mol, xyz = _embed_xyz(embed_smiles, seed=7)
        conf = mol.GetConformer()
        match = mol.GetSubstructMatch(Chem.MolFromSmarts("[CH2]C=C[CH3]"))
        dihedral = abs(T.GetDihedralDeg(conf, *match))
        assert (dihedral > 150.0) if expected == "E" else (dihedral < 30.0)
        assert derive_stereo_label_from_3d("[CH2]C=CC", xyz) == expected


# ---------------------------------------------------------------------------
# Closed-shell control and torsional rotamers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("embed_smiles", "expected"), [(r"C/C=C/C", "E"), (r"C/C=C\C", "Z")])
def test_closed_shell_2_butene_control(embed_smiles: str, expected: str) -> None:
    mol, xyz = _embed_xyz(embed_smiles, seed=7)
    match = mol.GetSubstructMatch(Chem.MolFromSmarts("C[CH]=[CH]C"))
    dihedral = abs(T.GetDihedralDeg(mol.GetConformer(), *match))
    assert (dihedral > 150.0) if expected == "E" else (dihedral < 30.0)
    assert derive_stereo_label_from_3d("CC=CC", xyz) == expected


def test_two_rotamers_of_E_2_pentene_both_E() -> None:
    labels = set()
    for seed in (3, 11, 29, 77):
        mol, xyz = _embed_xyz(r"CC/C=C/C", seed=seed)
        labels.add(derive_stereo_label_from_3d("CCC=CC", xyz))
    assert labels == {"E"}


def test_rotamers_of_E_propenyl_methyl_rotor_keep_E() -> None:
    # Rotate the methyl group of E-1-propenyl about C2-C3 by 60 degrees: a
    # torsional change, not a configurational one.
    atoms = _coords(_PROPENYL_E)
    c2, c3 = atoms[1][1], atoms[3][1]
    axis = tuple(b - a for a, b in zip(c2, c3, strict=True))
    norm = math.sqrt(sum(x * x for x in axis))
    ux, uy, uz = (x / norm for x in axis)
    ang = math.radians(60.0)
    cos, sin = math.cos(ang), math.sin(ang)

    def rot(p):
        v = tuple(a - b for a, b in zip(p, c3, strict=True))
        dot = ux * v[0] + uy * v[1] + uz * v[2]
        cr = (uy * v[2] - uz * v[1], uz * v[0] - ux * v[2], ux * v[1] - uy * v[0])
        out = tuple(
            v[i] * cos + cr[i] * sin + (ux, uy, uz)[i] * dot * (1 - cos) for i in range(3)
        )
        return tuple(o + b for o, b in zip(out, c3, strict=True))

    lines = ["8", ""]
    for idx, (el, p) in enumerate(atoms):
        q = rot(p) if idx in (5, 6, 7) else p
        lines.append(f"{el} {q[0]:.4f} {q[1]:.4f} {q[2]:.4f}")
    assert derive_stereo_label_from_3d("[CH]=CC", "\n".join(lines)) == "E"


# ---------------------------------------------------------------------------
# Entry resolution: E and Z 1-propenyl are two entries of one species
# ---------------------------------------------------------------------------


def test_e_and_z_propenyl_resolve_to_distinct_entries(db_engine) -> None:
    from sqlalchemy.orm import Session

    from app.schemas.fragments.geometry import GeometryPayload
    from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
    from app.services.species_resolution import resolve_species_entry

    identity = SpeciesEntryIdentityPayload(smiles="[CH]=CC", charge=0, multiplicity=2)
    with Session(db_engine) as session:
        trans = session.begin()
        try:
            e_entry = resolve_species_entry(
                session, identity, geometry=GeometryPayload(xyz_text=_PROPENYL_E)
            )
            z_entry = resolve_species_entry(
                session, identity, geometry=GeometryPayload(xyz_text=_PROPENYL_Z)
            )
            session.flush()
            assert e_entry.species_id == z_entry.species_id
            assert e_entry.species.stereo_kind == StereoKind.ez_isomer
            assert e_entry.id != z_entry.id
            assert (e_entry.stereo_label, z_entry.stereo_label) == ("E", "Z")
        finally:
            trans.rollback()
