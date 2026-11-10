"""The allene SMILES-mark reader against an independent labeller (#832, part 2).

``tests/fixtures/allene_marks_centres.json`` holds 442 SMILES texts, each with the label the Centres CIP labeller
(Hanson et al. 2018, SiMolecule/centres 1.2.1 on CDK 2.13) gives it: ``M`` is ``Ra`` and ``P`` is ``Sa``. Neither
tool is part of this repository and CI has no Java, so the table is generated once by
``backend/scripts/dev/generate_allene_marks_witness.py`` (the file header records the jar URLs, versions, sha256s
and the command) and committed. The texts are 17 molecules, both mark spellings, as written and respelled by CDK
under 12 random atom and bond orders: central atom first, ring closures on an end atom, bracket-hydrogen ends,
explicit hydrogen atoms, an isotopic hydrogen end. Two rows are texts for which Centres labels nothing (a ring digit
on the central atom; ten other rows with one are labelled directly); they carry the label of CDK's canonical re-export and are marked ``cdk-canonical-roundtrip``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from tests.services import _cumulene_fixtures as fx

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "allene_marks_centres.json"
_DOCUMENT = json.loads(_FIXTURE.read_text())
CASES = _DOCUMENT["cases"]


def _token(smiles: str) -> str:
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    (element,) = [e for e in frame.elements if e.kind == "axial"]
    return stereo.allene_mark_token(frame, element)


def _id(case: dict) -> str:
    return f"{case['smiles']}-{case['origin']}"


def test_the_table_is_the_documented_one() -> None:
    provenance = _DOCUMENT["provenance"]
    assert provenance["centres"]["sha256"] and provenance["cdk"]["sha256"]
    assert len(CASES) == 442
    assert {c["expected"] for c in CASES} == {"Ra", "Sa"}
    assert sum(1 for c in CASES if c["via"] == "cdk-canonical-roundtrip") == 2
    assert len({c["base"] for c in CASES}) == 17


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_the_reader_agrees_with_the_independent_labeller(case: dict) -> None:
    assert _token(case["smiles"]) == case["expected"]
    # ... and the reader is wired into the resolution of an entry deposited with these marks alone
    frame = stereo.frame_from_mol(identity_mol_from_smiles(case["smiles"]))
    tokens = stereo.resolve_stereo(frame).descriptor.split(",")
    (position,) = [i for i, e in enumerate(frame.elements) if e.kind == "axial"]
    assert tokens[position] == case["expected"]
    assert all(t == "?" for i, t in enumerate(tokens) if i != position)  # nothing but the axis is marked
    if case["via"] == "cdk-canonical-roundtrip":
        assert _token(case["roundtrip_smiles"]) == case["expected"]


def test_flipping_the_mark_flips_the_token_for_every_molecule() -> None:
    by_key = {(c["base"], c["mark"], c["origin"]): c["expected"] for c in CASES}
    assert len(by_key) == len(CASES)
    for (base, mark, origin), expected in by_key.items():
        assert by_key[(base, "@@" if mark == "@" else "@", origin)] != expected


def test_the_opensmiles_example_is_ra() -> None:
    """``NC(Br)=[C@]=C(O)C``, the only allene example of the OpenSMILES specification (3.8.4).

    Provenance: opensmiles.org/opensmiles.html, the depiction ``depict/tetrahedral-allene.gif``, read by hand.
    Br is up and in the plane at the NH2/Br end; at the other end OH is hashed (behind) and CH3 is wedged (in
    front). Looking along the axis from the first end, Br to O runs counter-clockwise: M, that is Ra. The
    specification states no configuration in words; this one case rests on reading the picture.
    """

    assert _token("NC(Br)=[C@]=C(O)C") == "Ra"
    assert _token("NC(Br)=[C@@]=C(O)C") == "Sa"


def test_an_isotopic_hydrogen_end_reads_the_same_from_marks_and_from_a_geometry() -> None:
    """D against H makes the axis of ``[2H]C=C=CC`` stereogenic. The geometry reader needs the isotope map, keyed
    by 1-based atom serial like the XYZ lines (a map keyed from 0 matches no atom and reads nothing)."""

    plain = "[2H]C=C=CC"
    for torsion, expected, marked in ((-90.0, "Ra", "[2H]C=[C@]=CC"), (90.0, "Sa", "[2H]C=[C@@]=CC")):
        geometry = fx.build(plain, torsion)
        isotopes = {a.GetIdx() + 1: a.GetIsotope() for a in geometry.GetAtoms() if a.GetIsotope()}
        frame = stereo.frame_from_mol(identity_mol_from_smiles(plain))
        assert stereo.geometry_tokens(frame, fx.xyz(geometry), isotopes) == [expected]
        assert _token(marked) == expected


def test_no_token_without_a_mark() -> None:
    frame = stereo.frame_from_mol(identity_mol_from_smiles("CC=C=CC"))
    (element,) = frame.elements
    assert stereo.allene_mark_token(frame, element) == "?"


def test_the_centre_between_two_allenes_is_read_from_the_marks() -> None:
    """Part 3 of #832: with both axes marked the centre is ``-``, ``r`` or ``s`` (Centres: M,P with the centre mark
    ``@`` is ``r``; M,M is not stereogenic). Unmarked, nothing is stated and no reason is given: data could settle it.
    The fuller witness (every skeleton, every mark combination, respellings) is ``test_allene_pseudo_centre.py``."""

    def resolved(smiles: str) -> stereo.ResolvedStereo:
        return stereo.resolve_stereo(stereo.frame_from_mol(identity_mol_from_smiles(smiles)))

    unmarked = resolved("CC=C=CC(C)C=C=CC")
    assert unmarked.descriptor == "?,?,?" and "allene_dependent" not in unmarked.unrepresented
    assert resolved("CC=[C@]=C[C@H](C)C=[C@@]=CC").descriptor == "Ra,Sa,r"
    assert resolved("CC=[C@]=C[C@H](C)C=[C@]=CC").descriptor == "Ra,Ra,-"
