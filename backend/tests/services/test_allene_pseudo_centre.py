"""The centre between two allene axes: ``-``, ``r`` or ``s`` (#832, part 3).

``CC=C=CC(C)C=C=CC`` has four stereoisomers. ``Ra,Ra`` and ``Sa,Sa`` are an enantiomeric pair whose middle carbon
is not stereogenic; ``Ra,Sa`` gives two achiral (meso) forms in which it is pseudo-asymmetric, ``r`` or ``s``. The
rule: an ``Ra`` (``M``) arm outranks an ``Sa`` (``P``) arm (IUPAC 2013 P-92.5 Rule 5), then ordinary CIP.

The independent witness is Centres 1.2.1 (Hanson et al. 2018), which labels the centre. CI has no Java, so its
results are committed in ``tests/fixtures/centres_allene_pseudo.json`` (regenerate with
``backend/scripts/dev/generate_centres_allene_fixture.py``; the file header records the jar, its sha256 and the
command). RDKit has no allene chirality, so it cannot serve as the witness; InChI is a second one for distinctness.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from app.db.models.common import StereoResolution
from tests.services import _cumulene_fixtures as fx

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "centres_allene_pseudo.json").read_text())
AXIS = {"M": "Ra", "P": "Sa"}
SKELETONS = FIXTURE["skeletons"]
HANDLED = [name for name, sk in SKELETONS.items() if sk["handled"]]
UNHANDLED = [name for name, sk in SKELETONS.items() if not sk["handled"]]


def skeleton_frame(name: str) -> stereo.StereoFrame:
    return stereo.frame_from_mol(identity_mol_from_smiles(SKELETONS[name]["template"]))


def descriptor_from_centres(frame: stereo.StereoFrame, name: str, labels: dict[str, str]) -> str:
    """Our descriptor for Centres' labels: ``M`` is ``Ra``, ``P`` is ``Sa``, no centre label is ``-``."""

    sk = SKELETONS[name]
    axes = iter([AXIS[labels[str(sk["arm_a"][0])]], AXIS[labels[str(sk["arm_b"][0])]]])
    centre = labels.get(str(sk["centre"]), "-")
    tokens = [centre if e.kind == "allene_dependent" else next(axes) for e in frame.elements]
    return ",".join(stereo.canonical_tokens(frame, tokens))


def test_the_fixture_records_the_witness_it_came_from() -> None:
    source = FIXTURE["source"]
    assert source["jar_url"] == "https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar"
    assert source["jar_sha256"] == "6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb"
    assert "java -jar centres.jar" in source["command"]
    assert len([c for c in FIXTURE["mark_cases"] if c["skeleton"] in HANDLED]) == 8 * len(HANDLED)
    assert len(FIXTURE["geometry_cases"]) >= 4 * len(HANDLED)


@pytest.mark.parametrize("name", HANDLED)
def test_the_enumeration_is_the_four_descriptors_centres_reaches(name: str) -> None:
    frame = skeleton_frame(name)
    enumerated = {",".join(t) for t, _ in stereo.stereoisomers(frame)}
    assert enumerated == {"Ra,Ra,-", "Sa,Sa,-", "Ra,Sa,r", "Ra,Sa,s"}
    reached: dict[str, int] = {}
    for case in FIXTURE["mark_cases"]:
        if case["skeleton"] == name:
            d = descriptor_from_centres(frame, name, case["centres"])
            reached[d] = reached.get(d, 0) + 1
    # eight mark spellings, two per isomer: every isomer is reached and none lies outside the enumeration
    assert reached == dict.fromkeys(enumerated, 2)


@pytest.mark.parametrize("name", HANDLED)
def test_every_isomer_has_its_own_descriptor_and_its_own_inchi(name: str) -> None:
    frame = skeleton_frame(name)
    by_descriptor: dict[str, set[str]] = {}
    for case in FIXTURE["geometry_cases"]:
        if case["skeleton"] != name:
            continue
        tokens = stereo.geometry_tokens(frame, case["xyz"], {int(k): v for k, v in case["isotopes"].items()} or None)
        by_descriptor.setdefault(",".join(stereo.canonical_tokens(frame, tokens)), set()).add(case["inchi"])
    assert set(by_descriptor) == {"Ra,Ra,-", "Sa,Sa,-", "Ra,Sa,r", "Ra,Sa,s"}
    assert all(len(v) == 1 for v in by_descriptor.values()), by_descriptor
    assert len({next(iter(v)) for v in by_descriptor.values()}) == 4


@pytest.mark.parametrize("case", FIXTURE["geometry_cases"], ids=lambda c: f"{c['skeleton']}-{c['conformer']}")
def test_the_geometry_reads_the_axes_and_the_centre_as_centres_labels_them(case: dict) -> None:
    frame = skeleton_frame(case["skeleton"])
    isotopes = {int(k): v for k, v in case["isotopes"].items()} or None
    tokens = stereo.geometry_tokens(frame, case["xyz"], isotopes)
    ours = ",".join(stereo.canonical_tokens(frame, tokens))
    # the axes are witnessed on the 3D coordinates; the centre on a SMILES spelling of the same conformer
    centre = str(SKELETONS[case["skeleton"]]["centre"])
    witnessed = {**case["centres_sdf"], centre: case["centres_smiles"].get(centre, "-")}
    witnessed = {k: v for k, v in witnessed.items() if k != centre or v != "-"}
    assert descriptor_from_centres(frame, case["skeleton"], witnessed) == ours
    resolved = stereo.resolve_stereo(frame, geometries=[(case["xyz"], isotopes)])
    assert resolved.descriptor == ours
    assert resolved.resolution.value == "resolved"
    assert stereo.R_ALLENE_DEPENDENT not in resolved.unrepresented


def mirrored(xyz: str) -> str:
    lines = xyz.splitlines()
    out = lines[:2]
    for line in lines[2:]:
        symbol, x, y, z = line.split()[:4]
        out.append(f"{symbol} {-float(x):.6f} {y} {z}")
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("name", HANDLED)
def test_the_mirror_image_swaps_the_enantiomers_and_leaves_each_meso_form_alone(name: str) -> None:
    frame = skeleton_frame(name)
    seen = set()
    for case in FIXTURE["geometry_cases"]:
        if case["skeleton"] != name:
            continue
        iso = {int(k): v for k, v in case["isotopes"].items()} or None
        here = ",".join(stereo.canonical_tokens(frame, stereo.geometry_tokens(frame, case["xyz"], iso)))
        there = ",".join(stereo.canonical_tokens(frame, stereo.geometry_tokens(frame, mirrored(case["xyz"]), iso)))
        expected = {"Ra,Ra,-": "Sa,Sa,-", "Sa,Sa,-": "Ra,Ra,-", "Ra,Sa,r": "Ra,Sa,r", "Ra,Sa,s": "Ra,Sa,s"}[here]
        assert there == expected, (here, there)
        seen.add(here)
    assert len(seen) == 4


def _with_first_axis_flattened(case: dict) -> str:
    """The case's coordinates with the first allene's end-to-end torsion turned to 0 (a flat, unreadable axis)."""

    params = Chem.SmilesParserParams()
    params.removeHs = False
    mol = Chem.AddHs(Chem.MolFromSmiles(SKELETONS[case["skeleton"]]["template"], params))
    lines = case["xyz"].splitlines()[2:]
    positions = np.array([[float(v) for v in line.split()[1:4]] for line in lines])
    path = [1, 3, 4]
    higher_a = stereo.cip_higher_substituent(mol, 1, 3)
    higher_b = stereo.cip_higher_substituent(mol, 4, 3)
    current = stereo.chain_torsion(positions, path, higher_a[0], higher_b[0])
    moving = fx._subtree(mol, 5, set(path)) | fx._subtree(mol, 6, set(path))
    fx._rotate(positions, moving, positions[4], positions[4] - positions[1], np.radians(-current))
    out = [str(len(lines)), ""]
    out += [f"{line.split()[0]} {x:.6f} {y:.6f} {z:.6f}" for line, (x, y, z) in zip(lines, positions, strict=True)]
    return "\n".join(out) + "\n"


def test_a_geometry_with_a_flattened_axis_leaves_that_axis_and_the_centre_unknown() -> None:
    case = next(c for c in FIXTURE["geometry_cases"] if c["skeleton"] == "Me_H")
    frame = skeleton_frame("Me_H")
    kinds = [e.kind for e in frame.elements]
    flat = stereo.geometry_tokens(frame, _with_first_axis_flattened(case))
    assert flat.count("?") == 2 and flat[kinds.index("allene_dependent")] == "?", flat
    # the unflattened coordinates are read in full, so it is the flat axis that costs the centre
    assert "?" not in stereo.geometry_tokens(frame, case["xyz"])
    resolved = stereo.resolve_stereo(frame, geometries=[_with_first_axis_flattened(case)])
    assert resolved.resolution == StereoResolution.partial
    assert stereo.R_ALLENE_DEPENDENT not in resolved.unrepresented


def test_the_centre_options_follow_the_senses() -> None:
    assert stereo.pseudo_centre_options(("?", "Ra")) == ("?",)
    assert stereo.pseudo_centre_options(("Ra", "Ra")) == ("-",)
    assert stereo.pseudo_centre_options(("Sa", "Sa")) == ("-",)
    assert stereo.pseudo_centre_options(("Ra", "Sa")) == ("r", "s")
    assert stereo.pseudo_centre_options(("Sa", "Ra")) == ("r", "s")


def _dependent(smiles: str) -> list[stereo.StereoElement]:
    f = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    return [e for e in f.elements if e.kind == "allene_dependent"]


LEFT_UNKNOWN = {
    "the two allenes share a ring": "CC1C=C=CCCCCC=C=C1",
    "four identical arms": "C(C=C=CC)(C=C=CC)(C=C=CC)C=C=CC",
    "2+2": "CC=C=CC(C=C=CC)(C=C=CCC)C=C=CCC",
    "spiro": "C12(C=C=CCCCC=C=C1)C=C=CCCCC=C=C2",
    "a double bond": "CC=C=CC(C=C=CC)=C(C(C)O)C(C)O",
    # a ligand that differs from the arms only by an isotope deep inside ties them at Rule 1 (review round 1)
    "a CD3-ended arm beside O": "CC=C=CC(O)(C=C=CC([2H])([2H])[2H])C=C=CC",
    "a CD3-ended arm beside H": "CC=C=CC(C=C=CC([2H])([2H])[2H])C=C=CC",
    "a 13C-ended arm beside O": "CC=C=CC(O)(C=C=C[13CH3])C=C=CC",
    "a 13C-ended arm beside H": "CC=C=CC(C=C=C[13CH3])C=C=CC",
    # an isotopic arm root cannot take the marker
    "an isotopic arm root": "CC=C=[13CH]C(C)[13CH]=C=CC",
    # the arm holds more stereo content than its axis (guard on the arm's content)
    "a carbinol on one arm": "CC(O)C=C=CC(C)C=C=CC(C)O",
    "a double bond in each arm": "C/C=C/C=C=CC(C)C=C=C/C=C/C",
}
HANDLED_SMILES = [
    "CC=C=CC(C)C=C=CC",
    "CC=C=CC(F)C=C=CC",
    "CC=C=CC(C=C=CC)(C)O",
    "CC=C=CC([2H])C=C=CC",
    "CC=C=CC(C([2H])([2H])[2H])C=C=CC",
    "CC=C=CC([N+](C)(C)C)C=C=CC",  # a charged ligand that differs from the arms by constitution
]


@pytest.mark.parametrize(("why", "smiles"), list(LEFT_UNKNOWN.items()))
def test_the_variants_that_are_not_read_stay_unknown_with_their_reason(why: str, smiles: str) -> None:
    dependent = _dependent(smiles)
    assert dependent and all(e.never_settled and not e.arms for e in dependent), why
    assert stereo.R_ALLENE_DEPENDENT in stereo.unrepresented_stereo_kinds(smiles), why


@pytest.mark.parametrize("smiles", HANDLED_SMILES)
def test_the_plain_class_is_read(smiles: str) -> None:
    dependent = _dependent(smiles)
    assert dependent and all(e.arms and not e.never_settled for e in dependent), smiles
    assert stereo.unrepresented_stereo_kinds(smiles) == ()
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    assert len({",".join(t) for t, _ in stereo.stereoisomers(frame)}) == 4


@pytest.mark.parametrize("name", UNHANDLED)
def test_a_ligand_tying_the_arms_without_isotopes_stays_unknown_though_centres_labels_it(name: str) -> None:
    """Centres labels the centre ``r``/``s`` here (recorded in the fixture); the reader does not state it.

    The arms and the CD3-ended third arm tie at CIP Rule 1 and are separated only by the isotope (Rule 2), which
    the marker would decide in the wrong order. Stating nothing is correct; stating the marker's answer was not.
    """

    sk = SKELETONS[name]
    frame = skeleton_frame(name)
    dependent = [e for e in frame.elements if e.kind == "allene_dependent"]
    assert dependent and all(not e.arms for e in dependent)
    labelled = [c for c in FIXTURE["mark_cases"] if c["skeleton"] == name and str(sk["centre"]) in c["centres"]]
    assert labelled and {c["centres"][str(sk["centre"])] for c in labelled} <= {"r", "s"}
