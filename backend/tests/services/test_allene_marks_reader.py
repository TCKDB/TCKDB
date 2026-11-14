"""Behaviour of the allene mark reader that the Centres table does not reach."""

from __future__ import annotations

import pytest
from rdkit import Chem

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from app.db.models.common import StereoResolution
from tests.services import _cumulene_fixtures as fx


def _frame(smiles: str) -> stereo.StereoFrame:
    return stereo.frame_from_mol(identity_mol_from_smiles(smiles))


def _resolve(smiles: str, **kw) -> stereo.ResolvedStereo:
    return stereo.resolve_stereo(_frame(smiles), **kw)


def _token(smiles: str) -> str:
    frame = _frame(smiles)
    (element,) = [e for e in frame.elements if e.kind == "axial"]
    return stereo.allene_mark_token(frame, element)


#: Labels run with Centres 1.2.1 (``M`` is Ra, ``P`` is Sa); not part of the generated table.
REVIEWER_CENTRES = [
    ("[1H]C(F)=[C@]=CC", "Sa"),  # P
    ("[2H]C([1H])=[C@]=CC", "Ra"),  # M
    ("C%10=[C@]=C(F)CCCCCCCC%10", "Ra"),  # M: a two-digit ring closure on an end atom
]


@pytest.mark.parametrize(("smiles", "expected"), REVIEWER_CENTRES)
def test_reviewer_run_centres_labels(smiles: str, expected: str) -> None:
    assert _token(smiles) == expected
    assert _resolve(smiles).descriptor == expected


@pytest.mark.parametrize(
    ("smiles", "twin"),
    [
        ("[1H]C(F)=[C@]=CC", "[H]C(F)=[C@]=CC"),
        ("[1H]OCC(F)=[C@]=CC", "OCC(F)=[C@]=CC"),
        ("[12C]C=[C@]=CC", "CC=[C@]=CC"),
    ],
)
def test_a_standard_isotope_in_the_text_reads_like_the_plain_atom(smiles: str, twin: str) -> None:
    """``[1H]`` is a plain hydrogen to the identity molecule; a raw parse would keep it and misalign (KeyError)."""

    assert _resolve(smiles).descriptor == _resolve(twin).descriptor in ("Ra", "Sa")
    assert stereo.descriptor_of_mol(identity_mol_from_smiles(smiles)) == _resolve(smiles).descriptor


def test_an_unmarked_allene_with_a_standard_isotope_is_unknown_not_an_error() -> None:
    assert _resolve("[1H]C(F)=C=CC").descriptor == "?"
    assert stereo.descriptor_of_mol(identity_mol_from_smiles("[1H]C(F)=C=CC")) == "?"
    assert stereo.descriptor_of_mol(identity_mol_from_smiles("[1H]C(F)=[C@]=CC")) == "Sa"


def test_the_odd_cumulene_with_a_standard_isotope_does_not_crash() -> None:
    """``cumulene_marks_present`` reads the text through the same matching; it crashed on main."""

    assert _resolve("[1H]C(F)=C=C=CC").descriptor == "?"


def test_text_that_cannot_be_matched_reads_nothing_instead_of_raising(monkeypatch) -> None:
    frame = _frame("CC=[C@]=CC")
    (element,) = frame.elements
    extra = Chem.RWMol(stereo._raw_marks_mol(frame))
    extra.AddAtom(Chem.Atom(35))  # an atom the molecule does not have
    assert stereo._raw_to_constitution(extra.GetMol(), frame) is None
    monkeypatch.setattr(stereo, "_raw_marks_mol", lambda f: extra.GetMol())
    assert stereo.allene_mark_token(frame, element) == "?"


def test_a_scanner_that_disagrees_with_the_parser_reads_nothing(monkeypatch) -> None:
    real = stereo._scan_smiles

    def shifted(text):
        atoms = real(text)
        atoms[0].symbol = "N"
        return atoms

    monkeypatch.setattr(stereo, "_scan_smiles", shifted)
    frame = _frame("CC=[C@]=CC")
    (element,) = frame.elements
    assert stereo.allene_mark_token(frame, element) == "?"


@pytest.mark.parametrize(
    "smiles",
    [
        "C%(10)=[C@]=C(F)CCCCCCCC%(10)",  # %(nnn) ring closure: not understood
        "NC(Br)=[C@AL1]=C(O)C",
        "NC(Br)=[C@AL2]=C(O)C",
        "NC(Br)=[C@TH1]=C(O)C",
        "NC(Br)=[C@TH2]=C(O)C",
    ],
)
def test_text_the_scanner_does_not_understand_is_unknown_and_says_why(smiles: str) -> None:
    result = _resolve(smiles)
    assert result.descriptor == "?"
    assert result.unrepresented == (stereo.R_AXIAL_MARKS,)


def test_each_allene_is_judged_by_its_own_mark() -> None:
    # one read, one unmarked: the unmarked one is merely unknown, with no reason
    result = _resolve("CC=[C@]=CCC=C=CC")
    assert result.descriptor == "Ra,?"
    assert result.resolution == StereoResolution.partial
    assert result.unrepresented == ()
    assert result.unknown_elements == 1
    # one unreadable mark, one unmarked: both stay unknown, the reason names the marked one only
    result = _resolve("CC=[C@AL1]=CCC=C=CC")
    assert result.descriptor == "?,?"
    assert result.unrepresented == (stereo.R_AXIAL_MARKS,)
    assert result.unknown_elements == 1


@pytest.mark.parametrize("smiles", ["CC=C=[C@]=C=CC", "CC=C=[C@@]=C=CC"])
def test_a_mark_on_an_even_cumulene_longer_than_an_allene_keeps_its_reason(smiles: str) -> None:
    result = _resolve(smiles)
    assert result.descriptor == "?"
    assert result.unrepresented == (stereo.R_AXIAL_MARKS,)


@pytest.mark.parametrize("torsion", [0.0, 5.0, 175.0, 180.0])
def test_a_flattened_geometry_adds_nothing_to_a_mark(torsion: float) -> None:
    """A geometry that settles nothing (axis flat or nearly) leaves the mark's reading standing."""

    result = _resolve("CC=[C@]=CC", geometries=[fx.xyz(fx.build("CC=C=CC", torsion))])
    assert result.descriptor == "Ra"
    assert result.resolution == StereoResolution.resolved
