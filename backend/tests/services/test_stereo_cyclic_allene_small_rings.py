"""Cyclic allenes in rings of 6, 7 and 8 read Ra/Sa from geometry like any other allene.

These rings are read from geometry, not stated ``?``: force-field embeddings of them are
near-planar, so the fixtures are GFN2-xTB optimised
minima (``tests/fixtures/cyclic_allene_xtb``; the header line of each file gives the method, the allene torsion,
the expected label and how that label was derived). Each ring has both enantiomers: ``_a`` is optimised from an
ETKDG/MMFF start and ``_b`` from the mirror image of that start, so ``_b`` is the mirror image of ``_a`` to 6e-5
angstrom (xtb is deterministic; a test pins it). The reader is also run on the exact mirror image of each.

Hand Newman rule used for every expected label (a script written without ``app.chemistry.stereo``, and again
in numpy without RDKit): bonds from the SMILES connectivity; on each allene end the CIP-higher
substituent is the one of higher atomic number (decisive in every fixture: ring C over H, or O over ring C); look
along the axis from the first end with that end in front; the allene is P (= ``Sa``) when the front substituent
turns clockwise to the rear one by less than 180 degrees, otherwise M (= ``Ra``). Positive torsion is P.

How each expected label is witnessed:

* Rings of 8 and 9: the hand Newman projection and InChI's ``/m`` partition, which flips with the mirror image.
* Rings of 6 and 7: the hand Newman projection only. InChI cannot see an allene axis in a ring of this size (it
  writes no stereo layer whatever the twist), so there is no second oracle. ``test_inchi_writes_no_stereo_layer...``
  records that as a fact: if a future InChI does see these rings, it fails and the layer becomes a witness here.
  The missing InChI comparison is therefore not a coverage gap. The reader does not depend on ring size, and is
  validated against InChI on rings of 8 and 9 and on acyclic allenes.
* A near-planar axis (the racemisation transition state, constrained to about 0 degrees) is outside the
  10 to 170 degree window and reads ``?`` at every ring size.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import inchi

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from app.db.models.common import StereoResolution
from app.services.species_resolution import stereo_unresolved_warning

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cyclic_allene_xtb"

#: name -> (SMILES, ring size, label of fixture ``_a``). The label of ``_b`` is the other one.
#: ``r6oh``/``r7oh`` carry a hydroxyl on one allene carbon, which makes the oxygen (not the ring carbon) the
#: CIP-higher substituent on that end: hand CIP check, O (8) outranks C (6).
CASES = {
    "r6": ("C1=C=CCCC1", 6, "Ra"),
    "r7": ("C1=C=CCCCC1", 7, "Sa"),
    "r8": ("C1=C=CCCCCC1", 8, "Sa"),
    "r9": ("C1=C=CCCCCCC1", 9, "Ra"),
    "r6oh": ("C1=C=C(O)CCC1", 6, "Sa"),
    "r7oh": ("C1=C=C(O)CCCC1", 7, "Sa"),
}
OTHER = {"Ra": "Sa", "Sa": "Ra"}


def frame(smiles: str) -> stereo.StereoFrame:
    return stereo.frame_from_mol(identity_mol_from_smiles(smiles))


def read_xyz(name: str) -> str:
    return (FIXTURES / f"{name}.xyz").read_text()


def mirror_xyz(text: str) -> str:
    """The mirror image (x -> -x) of an xyz block, comment line kept."""

    count, comment, *atoms = text.rstrip("\n").split("\n")
    out = [count, comment]
    for line in atoms:
        symbol, x, y, z = line.split()
        out.append(f"{symbol:2s} {-float(x): .8f} {float(y): .8f} {float(z): .8f}")
    return "\n".join(out) + "\n"


def header(name: str) -> dict[str, str]:
    comment = read_xyz(name).split("\n")[1]
    return {
        "torsion": comment.split("substituents=")[1].split(" deg")[0] if "substituents=" in comment else comment.split("torsion=")[1].split(" deg")[0],
        "expected": comment.split("expected=")[1].split(" ")[0].rstrip(";"),
    }


def torsion_of(name: str, smiles: str) -> float:
    """Torsion between the CIP-higher substituents, from the fixture's coordinates (atom order is the SMILES with H added)."""

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    positions = np.array([[float(v) for v in line.split()[1:4]] for line in read_xyz(name).split("\n")[2:] if line.strip()])
    path = [0, 1, 2]
    higher_a = stereo.cip_higher_substituent(mol, 0, 1)[0]
    higher_b = stereo.cip_higher_substituent(mol, 2, 1)[0]
    return float(stereo.chain_torsion(positions, path, higher_a, higher_b))


def inchi_of(text: str, smiles: str) -> str:
    """InChI of the 3D structure in an xyz block (bonds from the SMILES, coordinates from the block)."""

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    positions = [line.split()[1:4] for line in text.split("\n")[2:] if line.strip()]
    conformer = Chem.Conformer(mol.GetNumAtoms())
    for index, xyz in enumerate(positions):
        conformer.SetAtomPosition(index, tuple(float(v) for v in xyz))
    mol.AddConformer(conformer)
    return inchi.MolBlockToInchi(Chem.MolToMolBlock(mol))


def m_layer(text: str) -> str:
    return next((part for part in text.split("/") if part.startswith("m")), "")


def enantiomers() -> list[tuple[str, str, str, int, str]]:
    rows = []
    for name, (smiles, ring, label_a) in CASES.items():
        rows.append((f"{name}_a", name, smiles, ring, label_a))
        rows.append((f"{name}_b", name, smiles, ring, OTHER[label_a]))
    return rows


@pytest.mark.parametrize(("fixture", "name", "smiles", "ring", "expected"), enantiomers())
def test_the_reader_names_each_qm_enantiomer_and_its_mirror_image(fixture, name, smiles, ring, expected) -> None:
    f = frame(smiles)
    assert f.elements[0].kind == "axial" and not f.elements[0].never_settled
    text = read_xyz(fixture)
    assert header(fixture)["expected"] == expected, "the fixture header and this table must agree"
    assert stereo.geometry_tokens(f, text) == [expected]
    assert stereo.geometry_tokens(f, mirror_xyz(text)) == [OTHER[expected]]


@pytest.mark.parametrize(("fixture", "name", "smiles", "ring", "expected"), enantiomers())
def test_a_qm_twist_is_inside_the_unchanged_window(fixture, name, smiles, ring, expected) -> None:
    assert stereo._ALLENE_TORSION_WINDOW == (10.0, 170.0)
    torsion = torsion_of(fixture, smiles)
    assert 10.0 <= abs(torsion) <= 170.0
    assert abs(torsion - float(header(fixture)["torsion"])) < 0.06
    # IUPAC: M (negative) is Ra, P (positive) is Sa
    assert (torsion < 0) == (expected == "Ra")


@pytest.mark.parametrize("name", list(CASES))
def test_the_two_enantiomers_of_a_ring_are_mirror_images_with_opposite_labels(name) -> None:
    smiles, ring, label_a = CASES[name]
    f = frame(smiles)
    assert stereo.geometry_tokens(f, read_xyz(f"{name}_a")) == [label_a]
    assert stereo.geometry_tokens(f, read_xyz(f"{name}_b")) == [OTHER[label_a]]
    assert torsion_of(f"{name}_a", smiles) == pytest.approx(-torsion_of(f"{name}_b", smiles), abs=0.5)


@pytest.mark.parametrize("name", list(CASES))
def test_enantiomer_b_is_the_mirror_image_of_enantiomer_a(name) -> None:
    def coordinates(text: str) -> np.ndarray:
        return np.array([[float(v) for v in line.split()[1:4]] for line in text.split("\n")[2:] if line.strip()])

    assert np.abs(coordinates(mirror_xyz(read_xyz(f"{name}_a"))) - coordinates(read_xyz(f"{name}_b"))).max() < 1e-3


@pytest.mark.parametrize("name", ["r8", "r9"])
def test_inchi_m_layer_partitions_the_mirror_images_of_rings_of_eight_and_nine(name) -> None:
    """InChI is a second, independent witness for rings of 8 and 9 (it sees the axis there)."""

    smiles, ring, label_a = CASES[name]
    a, b = read_xyz(f"{name}_a"), read_xyz(f"{name}_b")
    layer_a, layer_b = m_layer(inchi_of(a, smiles)), m_layer(inchi_of(b, smiles))
    assert layer_a and layer_b, "InChI should write an /m layer for this ring"
    assert layer_a != layer_b
    # the exact mirror image of one enantiomer carries the other's layer: the partition follows the labels
    assert m_layer(inchi_of(mirror_xyz(a), smiles)) == layer_b
    assert m_layer(inchi_of(mirror_xyz(b), smiles)) == layer_a


@pytest.mark.parametrize("name", ["r6", "r7", "r6oh", "r7oh"])
def test_inchi_writes_no_stereo_layer_for_allenes_in_rings_of_six_and_seven(name) -> None:
    """InChI is blind to these axes, whatever the twist: the hand Newman check is the only witness.

    This pins the gap as a fact. If a future InChI writes a ``/t`` or ``/m`` layer here, this fails: add the
    layer as a witness to the 8/9 test above instead of deleting this one.
    """

    smiles = CASES[name][0]
    for fixture in (f"{name}_a", f"{name}_b"):
        text = inchi_of(read_xyz(fixture), smiles)
        assert "/t" not in text and "/m" not in text, text


@pytest.mark.parametrize("name", ["r6", "r7", "r8"])
def test_a_near_planar_axis_reads_unknown_at_every_small_ring_size(name) -> None:
    """The racemisation transition state (axis constrained to about 0 degrees) is a flattened axis: ``?``."""

    smiles = CASES[name][0]
    fixture = f"{name}_planar"
    assert header(fixture)["expected"] == "?"
    torsion = torsion_of(fixture, smiles)
    assert abs(torsion) < 5.0 and abs(torsion) < stereo._ALLENE_TORSION_WINDOW[0]
    f = frame(smiles)
    text = read_xyz(fixture)
    assert stereo.geometry_tokens(f, text) == ["?"]
    assert stereo.geometry_tokens(f, mirror_xyz(text)) == ["?"]
    resolved = stereo.resolve_stereo(f, geometries=[(text, {})], supplied_label=None)
    assert resolved.descriptor == "?" and resolved.resolution == StereoResolution.unresolved
    assert resolved.unrepresented == () and resolved.unknown_elements == 1


@pytest.mark.parametrize(("fixture", "name", "smiles", "ring", "expected"), enantiomers())
def test_resolving_a_qm_geometry_gives_a_resolved_entry_descriptor(fixture, name, smiles, ring, expected) -> None:
    resolved = stereo.resolve_stereo(frame(smiles), geometries=[(read_xyz(fixture), {})], supplied_label=None)
    assert resolved.descriptor == expected
    assert resolved.resolution == StereoResolution.resolved
    assert resolved.unrepresented == ()


# ---------------------------------------------------------------------------
# Rings of five or fewer stay unrepresented
# ---------------------------------------------------------------------------

FORCE_FIELD = Path(__file__).resolve().parents[1] / "fixtures" / "cyclic_allene_force_field"
#: file -> SMILES. Perceivable twisted 5-ring geometries (ETKDG + MMFF): with the ring guard off the reader gives
#: Ra or Sa on each, so a pass below means the guard held, not that nothing was read.
FIVE_RING_GEOMETRIES = {
    "r5oh_s0": "C1=C=C(O)CC1",
    "r5oh_s3": "C1=C=C(O)CC1",
    "r5fused_s9": "C1=C=CC2CCCCC21",
    "r5fused_s11": "C1=C=CC2CCCCC21",
}


@pytest.mark.parametrize("name", list(FIVE_RING_GEOMETRIES))
def test_a_perceived_twisted_five_ring_geometry_is_still_not_read(name) -> None:
    smiles = FIVE_RING_GEOMETRIES[name]
    text = (FORCE_FIELD / f"{name}.xyz").read_text()
    f = frame(smiles)
    axial = next(i for i, e in enumerate(f.elements) if e.kind == "axial")
    assert f.elements[axial].never_settled
    for geometry in (text, mirror_xyz(text)):
        tokens = stereo.geometry_tokens(f, geometry)
        assert tokens is not None, "the geometry must be perceived as this molecule, or the test checks nothing"
        assert tokens[axial] == "?"
    resolved = stereo.resolve_stereo(f, geometries=[(text, {})], supplied_label=None)
    assert resolved.descriptor.split(",")[axial] == "?"
    assert stereo.R_AXIAL_SMALL_RING in resolved.unrepresented


MARKED_SMALL_RINGS = ["C1=[C@]=CCC1", "C1=[C@@]=CCC1", "C1=[C@]=C(C)CC1", "C1=[C@@]=CC2CCCCC21", "C1=[C@]=CC1"]


@pytest.mark.parametrize("smiles", MARKED_SMALL_RINGS)
def test_a_smiles_mark_on_an_allene_in_a_ring_of_five_or_fewer_is_ignored(smiles) -> None:
    resolved = stereo.resolve_stereo(frame(smiles), geometries=[], supplied_label=None)
    axial = next(i for i, e in enumerate(frame(smiles).elements) if e.kind == "axial")
    assert resolved.descriptor.split(",")[axial] == "?"
    assert stereo.R_AXIAL_SMALL_RING in resolved.unrepresented
    assert stereo.R_AXIAL_MARKS not in resolved.unrepresented
    warning = stereo_unresolved_warning(resolved)
    assert warning is not None and warning.code == stereo.W_SPECIES_ENTRY_STEREO_NOT_REPRESENTED


@pytest.mark.parametrize(
    ("smiles", "ring_size"),
    [
        ("C12=C=C(CC1)CCCC2", 5),  # the allene is in a ring of 5 and one of 7: the smallest decides
        ("C1=C=CC2CCCCC21", 5),
        ("C1=C=CC2(CC2)C1", 5),  # spiro
        ("C1=C=CC2CCCC2C1", 6),  # a ring of 6 fused to one of 5, the allene only in the 6
    ],
)
def test_the_smallest_ring_through_the_allene_decides(smiles, ring_size) -> None:
    element = next(e for e in frame(smiles).elements if e.kind == "axial")
    assert element.ring_size == ring_size
    assert element.never_settled == (ring_size <= 5)


@pytest.mark.parametrize(
    ("smiles", "mark_label"),
    [("C1=[C@@]=CCCC1", "Ra"), ("C1=[C@]=CCCC1", "Sa")],
)
def test_a_mark_on_a_six_ring_allene_is_read_and_agrees_with_the_qm_geometry_only_when_it_should(smiles, mark_label) -> None:
    """r6_a is ``Ra``: the mark that reads ``Ra`` is confirmed by it; the opposite mark contradicts it."""

    f = frame(smiles)
    assert stereo.resolve_stereo(f, geometries=[], supplied_label=None).descriptor == mark_label
    geometry = [(read_xyz("r6_a"), {})]
    if mark_label == "Ra":
        resolved = stereo.resolve_stereo(f, geometries=geometry, supplied_label=None)
        assert resolved.descriptor == "Ra" and resolved.resolution == StereoResolution.resolved
    else:
        with pytest.raises(CodedValueError) as excinfo:
            stereo.resolve_stereo(f, geometries=geometry, supplied_label=None)
        assert excinfo.value.code == stereo.W_SPECIES_STEREO_CONTRADICTION


def test_a_mark_on_a_substituted_six_ring_allene_is_read() -> None:
    assert stereo.resolve_stereo(frame("C1=[C@]=C(F)CCC1"), geometries=[], supplied_label=None).descriptor == "Ra"
