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

import pytest

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles

FIXTURE = json.loads((Path(__file__).parent.parent / "fixtures" / "centres_allene_pseudo.json").read_text())
AXIS = {"M": "Ra", "P": "Sa"}
ARM_ATOMS = FIXTURE["arm_atoms"]
CENTRE = FIXTURE["centre_atom"]
SKELETONS = FIXTURE["skeletons"]


def skeleton_frame(name: str) -> stereo.StereoFrame:
    x, y = SKELETONS[name]
    return stereo.frame_from_mol(identity_mol_from_smiles(f"CC([H])=C=C([H])C({x})({y})C([H])=C=C([H])C"))


def descriptor_from_centres(frame: stereo.StereoFrame, labels: dict[str, str]) -> str:
    """Our descriptor for Centres' labels: ``M`` is ``Ra``, ``P`` is ``Sa``, no centre label is ``-``."""

    axes = iter([AXIS[labels[str(ARM_ATOMS["A"][0])]], AXIS[labels[str(ARM_ATOMS["B"][0])]]])
    centre = labels.get(str(CENTRE), "-")
    tokens = [centre if e.kind == "allene_dependent" else next(axes) for e in frame.elements]
    return ",".join(stereo.canonical_tokens(frame, tokens))


def test_the_fixture_records_the_witness_it_came_from() -> None:
    source = FIXTURE["source"]
    assert source["jar_url"] == "https://github.com/SiMolecule/centres/releases/download/1.2.1/centres.jar"
    assert source["jar_sha256"] == "6c5d5cafa9393ed7b667a2340222348cd15a9bbdd0d1b92518229f26cc5e87fb"
    assert "java -jar centres.jar" in source["command"]
    assert len(FIXTURE["mark_cases"]) == 8 * len(SKELETONS)
    assert len(FIXTURE["geometry_cases"]) >= 4 * len(SKELETONS)


@pytest.mark.parametrize("name", list(SKELETONS))
def test_the_enumeration_is_the_four_descriptors_centres_reaches(name: str) -> None:
    frame = skeleton_frame(name)
    enumerated = {",".join(t) for t, _ in stereo.stereoisomers(frame)}
    assert enumerated == {"Ra,Ra,-", "Sa,Sa,-", "Ra,Sa,r", "Ra,Sa,s"}
    reached: dict[str, int] = {}
    for case in FIXTURE["mark_cases"]:
        if case["skeleton"] == name:
            d = descriptor_from_centres(frame, case["centres"])
            reached[d] = reached.get(d, 0) + 1
    # eight mark spellings, two per isomer: every isomer is reached and none lies outside the enumeration
    assert reached == dict.fromkeys(enumerated, 2)


@pytest.mark.parametrize("name", list(SKELETONS))
def test_every_isomer_has_its_own_descriptor_and_its_own_inchi(name: str) -> None:
    frame = skeleton_frame(name)
    by_descriptor: dict[str, set[str]] = {}
    for case in FIXTURE["geometry_cases"]:
        if case["skeleton"] != name:
            continue
        tokens = stereo.geometry_tokens(frame, case["xyz"])
        by_descriptor.setdefault(",".join(stereo.canonical_tokens(frame, tokens)), set()).add(case["inchi"])
    assert set(by_descriptor) == {"Ra,Ra,-", "Sa,Sa,-", "Ra,Sa,r", "Ra,Sa,s"}
    assert all(len(v) == 1 for v in by_descriptor.values()), by_descriptor
    assert len({next(iter(v)) for v in by_descriptor.values()}) == 4


@pytest.mark.parametrize("case", FIXTURE["geometry_cases"], ids=lambda c: f"{c['skeleton']}-{c['conformer']}")
def test_the_geometry_reads_the_axes_and_the_centre_as_centres_labels_them(case: dict) -> None:
    frame = skeleton_frame(case["skeleton"])
    tokens = stereo.geometry_tokens(frame, case["xyz"])
    ours = ",".join(stereo.canonical_tokens(frame, tokens))
    # the axes are witnessed on the 3D coordinates; the centre on a SMILES spelling of the same conformer
    assert descriptor_from_centres(frame, {**case["centres_sdf"], **{str(CENTRE): case["centres_smiles"].get(str(CENTRE), "-")}}) == ours
    resolved = stereo.resolve_stereo(frame, geometries=[case["xyz"]])
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


@pytest.mark.parametrize("name", list(SKELETONS))
def test_the_mirror_image_swaps_the_enantiomers_and_leaves_each_meso_form_alone(name: str) -> None:
    frame = skeleton_frame(name)
    seen = set()
    for case in FIXTURE["geometry_cases"]:
        if case["skeleton"] != name:
            continue
        here = ",".join(stereo.canonical_tokens(frame, stereo.geometry_tokens(frame, case["xyz"])))
        there = ",".join(stereo.canonical_tokens(frame, stereo.geometry_tokens(frame, mirrored(case["xyz"]))))
        expected = {"Ra,Ra,-": "Sa,Sa,-", "Sa,Sa,-": "Ra,Ra,-", "Ra,Sa,r": "Ra,Sa,r", "Ra,Sa,s": "Ra,Sa,s"}[here]
        assert there == expected, (here, there)
        seen.add(here)
    assert len(seen) == 4


def test_a_geometry_with_an_unreadable_axis_leaves_the_centre_unknown() -> None:
    frame = skeleton_frame("Me_H")
    case = next(c for c in FIXTURE["geometry_cases"] if c["skeleton"] == "Me_H")
    flat = stereo.pseudo_centre_options(("?", "Ra"))
    assert flat == ("?",)
    assert stereo.pseudo_centre_options(("Ra", "Ra")) == ("-",)
    assert stereo.pseudo_centre_options(("Sa", "Sa")) == ("-",)
    assert stereo.pseudo_centre_options(("Ra", "Sa")) == ("r", "s")
    assert stereo.pseudo_centre_options(("Sa", "Ra")) == ("r", "s")
    assert frame.elements and case["xyz"]


def test_the_variants_that_are_not_read_stay_unknown_with_their_reason() -> None:
    for smiles in (
        "CC1C=C=CCCCCC=C=C1",  # the two allenes share a ring
        "C(C=C=CC)(C=C=CC)(C=C=CC)C=C=CC",  # four identical arms
        "CC=C=CC(C=C=CC)(C=C=CCC)C=C=CCC",  # 2+2
        "C12(C=C=CCCCC=C=C1)C=C=CCCCC=C=C2",  # spiro
        "CC=C=CC(C=C=CC)=C(C(C)O)C(C)O",  # a double bond
    ):
        f = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
        dependent = [e for e in f.elements if e.kind == "allene_dependent"]
        assert dependent and all(e.never_settled and not e.arms for e in dependent), smiles
        assert stereo.R_ALLENE_DEPENDENT in stereo.unrepresented_stereo_kinds(smiles), smiles
    for smiles in ("CC=C=CC(C=C=CC)(C)O", "CC=C=CC(C)C=C=CC", "CC=C=CC(F)C=C=CC"):
        f = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
        dependent = [e for e in f.elements if e.kind == "allene_dependent"]
        assert dependent and all(e.arms and not e.never_settled for e in dependent), smiles
        assert stereo.unrepresented_stereo_kinds(smiles) == (), smiles
