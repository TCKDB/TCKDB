"""Prototype (#832 part 2): the allene SMILES-mark reader against an independent labeller.

NOT SHIPPED. ``stereo.ALLENE_MARKS_READ`` is on only on the prototype branch ``allene-smiles-marks-832-prototype``.

The expected token of every case below was produced by CDK's SMILES reader and the Centres CIP labeller
(Hanson et al. 2018; SiMolecule/centres 1.2.1, cdk-2.13) from the very same text: ``M`` is ``Ra`` and ``P`` is
``Sa``. Neither tool is part of this repository. The spellings are the 17 base molecules as written plus CDK's own
respellings of them under random atom and bond orderings (central atom first, ring closures on an end atom,
bracket-hydrogen ends, explicit hydrogen atoms, an isotopic hydrogen end).
"""

from __future__ import annotations

import pytest

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles

CASES = [
    ('C(=[C@@]=CC)C', 'Ra'),
    ('CC=[C@]=CC', 'Ra'),
    ('[C@@](=CC)=CC', 'Ra'),
    ('C(=[C@]=CC)C', 'Sa'),
    ('CC=[C@@]=CC', 'Sa'),
    ('[C@](=CC)=CC', 'Sa'),
    ('[C@](=C(F)C)=C(C)Cl', 'Ra'),
    ('ClC(=[C@@]=C(F)C)C', 'Ra'),
    ('ClC(C)=[C@]=C(C)F', 'Ra'),
    ('[C@](=C(C)F)=C(Cl)C', 'Ra'),
    ('CC(Cl)=[C@]=C(F)C', 'Ra'),
    ('CC(=[C@]=C(F)C)Cl', 'Ra'),
    ('[C@@](=C(F)C)=C(C)Cl', 'Sa'),
    ('ClC(=[C@]=C(F)C)C', 'Sa'),
    ('ClC(C)=[C@@]=C(C)F', 'Sa'),
    ('[C@@](=C(C)F)=C(Cl)C', 'Sa'),
    ('CC(Cl)=[C@@]=C(F)C', 'Sa'),
    ('CC(=[C@@]=C(F)C)Cl', 'Sa'),
    ('FC(=[C@@]=CC)Cl', 'Ra'),
    ('CC=[C@@]=C(Cl)F', 'Ra'),
    ('C(F)(=[C@@]=CC)Cl', 'Ra'),
    ('C(Cl)(F)=[C@]=CC', 'Ra'),
    ('ClC(=[C@]=CC)F', 'Ra'),
    ('FC(=[C@]=CC)Cl', 'Sa'),
    ('CC=[C@]=C(Cl)F', 'Sa'),
    ('C(F)(=[C@]=CC)Cl', 'Sa'),
    ('C(Cl)(F)=[C@@]=CC', 'Sa'),
    ('ClC(=[C@@]=CC)F', 'Sa'),
    ('C(C(C)C)=[C@@]=CO', 'Ra'),
    ('[C@@](=CO)=CC(C)C', 'Ra'),
    ('[C@@](=CC(C)C)=CO', 'Ra'),
    ('OC=[C@]=CC(C)C', 'Ra'),
    ('C(C(C)C)=[C@]=CO', 'Sa'),
    ('[C@](=CO)=CC(C)C', 'Sa'),
    ('[C@](=CC(C)C)=CO', 'Sa'),
    ('OC=[C@@]=CC(C)C', 'Sa'),
    ('C(C(C)O)=[C@@]=CC', 'Ra'),
    ('[C@@](=CC)=CC(C)O', 'Ra'),
    ('[C@@](=CC(O)C)=CC', 'Ra'),
    ('C(C(O)C)=[C@@]=CC', 'Ra'),
    ('CC=[C@]=CC(C)O', 'Ra'),
    ('C(C(C)O)=[C@]=CC', 'Sa'),
    ('[C@](=CC)=CC(C)O', 'Sa'),
    ('[C@](=CC(O)C)=CC', 'Sa'),
    ('C(C(O)C)=[C@]=CC', 'Sa'),
    ('CC=[C@@]=CC(C)O', 'Sa'),
    ('[C@](=C(O)C)=C(N)Br', 'Ra'),
    ('BrC(=[C@@]=C(O)C)N', 'Ra'),
    ('BrC(N)=[C@]=C(C)O', 'Ra'),
    ('[C@](=C(C)O)=C(Br)N', 'Ra'),
    ('NC(Br)=[C@]=C(O)C', 'Ra'),
    ('NC(=[C@]=C(O)C)Br', 'Ra'),
    ('[C@@](=C(O)C)=C(N)Br', 'Sa'),
    ('BrC(=[C@]=C(O)C)N', 'Sa'),
    ('BrC(N)=[C@@]=C(C)O', 'Sa'),
    ('[C@@](=C(C)O)=C(Br)N', 'Sa'),
    ('NC(Br)=[C@@]=C(O)C', 'Sa'),
    ('NC(=[C@@]=C(O)C)Br', 'Sa'),
    ('C(=[C@@]=C(Cl)F)Br', 'Sa'),
    ('FC(=[C@]=CBr)Cl', 'Sa'),
    ('[C@](=CBr)=C(F)Cl', 'Sa'),
    ('BrC=[C@]=C(Cl)F', 'Sa'),
    ('FC(Cl)=[C@]=CBr', 'Sa'),
    ('C(=[C@]=C(Cl)F)Br', 'Ra'),
    ('FC(=[C@@]=CBr)Cl', 'Ra'),
    ('[C@@](=CBr)=C(F)Cl', 'Ra'),
    ('BrC=[C@@]=C(Cl)F', 'Ra'),
    ('FC(Cl)=[C@@]=CBr', 'Ra'),
    ('C(=[C@@]=CC)[2H]', 'Ra'),
    ('[2H]C=[C@]=CC', 'Ra'),
    ('[C@@](=C[2H])=CC', 'Ra'),
    ('C(=[C@@]=C[2H])C', 'Ra'),
    ('C(=[C@]=CC)[2H]', 'Sa'),
    ('[2H]C=[C@@]=CC', 'Sa'),
    ('[C@](=C[2H])=CC', 'Sa'),
    ('C(=[C@]=C[2H])C', 'Sa'),
    ('FC1=[C@]=CCCCCCCCC1', 'Ra'),
    ('C1CCCC(F)=[C@@]=CCCCC1', 'Ra'),
    ('C1CCCCCCC(=[C@@]=CC1)F', 'Ra'),
    ('C1CCCCCCCC(=[C@@]=C1)F', 'Ra'),
    ('C1CCCCC=[C@]=C(CCC1)F', 'Ra'),
    ('C1CCCCCCCC=[C@]=C1F', 'Ra'),
    ('FC1=[C@@]=CCCCCCCCC1', 'Sa'),
    ('C1CCCC(F)=[C@]=CCCCC1', 'Sa'),
    ('C1CCCCCCC(=[C@]=CC1)F', 'Sa'),
    ('C1CCCCCCCC(=[C@]=C1)F', 'Sa'),
    ('C1CCCCC=[C@@]=C(CCC1)F', 'Sa'),
    ('C1CCCCCCCC=[C@@]=C1F', 'Sa'),
    ('C1CCCCC(=[C@@]=C(CCC1)F)Cl', 'Ra'),
    ('C1CCCC(=[C@]=C(F)CCCC1)Cl', 'Ra'),
    ('C1CCCCCC(=[C@]=C(F)CC1)Cl', 'Ra'),
    ('C=1(F)CCCCCCCCC(=[C@]1)Cl', 'Ra'),
    ('C1C(Cl)=[C@@]=C(CCCCCCC1)F', 'Ra'),
    ('C1CC(=[C@]=C(Cl)CCCCCC1)F', 'Ra'),
    ('C1CCCCC(=[C@]=C(CCC1)F)Cl', 'Sa'),
    ('C1CCCC(=[C@@]=C(F)CCCC1)Cl', 'Sa'),
    ('C1CCCCCC(=[C@@]=C(F)CC1)Cl', 'Sa'),
    ('C=1(F)CCCCCCCCC(=[C@@]1)Cl', 'Sa'),
    ('C1C(Cl)=[C@]=C(CCCCCCC1)F', 'Sa'),
    ('C1CC(=[C@@]=C(Cl)CCCCCC1)F', 'Sa'),
    ('C1CCCC=[C@]=C(F)CCCC1', 'Sa'),
    ('C1=[C@]=C(F)CCCCCCCC1', 'Ra'),
    ('C1C(F)=[C@]=CCCCCCCC1', 'Sa'),
    ('C1CCCC=[C@@]=C(F)CCCC1', 'Ra'),
    ('C1=[C@@]=C(F)CCCCCCCC1', 'Sa'),
    ('C1C(F)=[C@@]=CCCCCCCC1', 'Ra'),
    ('C(=[C@@]=CCl)F', 'Ra'),
    ('FC=[C@]=CCl', 'Ra'),
    ('[C@@](=CF)=CCl', 'Ra'),
    ('C(=[C@@]=CF)Cl', 'Ra'),
    ('C(=[C@]=CCl)F', 'Sa'),
    ('FC=[C@@]=CCl', 'Sa'),
    ('[C@](=CF)=CCl', 'Sa'),
    ('C(=[C@]=CF)Cl', 'Sa'),
    ('CC=[C@@]=CF', 'Sa'),
    ('C(=[C@]=CF)C', 'Sa'),
    ('C(C)=[C@]=CF', 'Sa'),
    ('[C@](=CC)=CF', 'Sa'),
    ('C(=[C@]=CC)F', 'Sa'),
    ('CC=[C@]=CF', 'Ra'),
    ('C(=[C@@]=CF)C', 'Ra'),
    ('C(C)=[C@@]=CF', 'Ra'),
    ('[C@@](=CC)=CF', 'Ra'),
    ('C(=[C@@]=CC)F', 'Ra'),
    ('[C@](=C(Cl)[H])=C([H])F', 'Ra'),
    ('FC(=[C@@]=C(Cl)[H])[H]', 'Ra'),
    ('FC([H])=[C@]=C([H])Cl', 'Ra'),
    ('[C@](=C([H])Cl)=C(F)[H]', 'Ra'),
    ('[H]C(F)=[C@]=C(Cl)[H]', 'Ra'),
    ('[H]C(=[C@]=C(Cl)[H])F', 'Ra'),
    ('[C@@](=C(Cl)[H])=C([H])F', 'Sa'),
    ('FC(=[C@]=C(Cl)[H])[H]', 'Sa'),
    ('FC([H])=[C@@]=C([H])Cl', 'Sa'),
    ('[C@@](=C([H])Cl)=C(F)[H]', 'Sa'),
    ('[H]C(F)=[C@@]=C(Cl)[H]', 'Sa'),
    ('[H]C(=[C@@]=C(Cl)[H])F', 'Sa'),
    ('[C@](=C(Cl)C)=C(F)[H]', 'Sa'),
    ('[H]C(=[C@@]=C(Cl)C)F', 'Sa'),
    ('[H]C(F)=[C@]=C(C)Cl', 'Sa'),
    ('[C@](=C(C)Cl)=C([H])F', 'Sa'),
    ('FC([H])=[C@]=C(Cl)C', 'Sa'),
    ('FC(=[C@]=C(Cl)C)[H]', 'Sa'),
    ('[C@@](=C(Cl)C)=C(F)[H]', 'Ra'),
    ('[H]C(=[C@]=C(Cl)C)F', 'Ra'),
    ('[H]C(F)=[C@@]=C(C)Cl', 'Ra'),
    ('[C@@](=C(C)Cl)=C([H])F', 'Ra'),
    ('FC([H])=[C@@]=C(Cl)C', 'Ra'),
    ('FC(=[C@@]=C(Cl)C)[H]', 'Ra'),
    ('[C@](=C([H])Cl)=C([2H])F', 'Sa'),
    ('FC(=[C@@]=C([H])Cl)[2H]', 'Sa'),
    ('FC([2H])=[C@]=C(Cl)[H]', 'Sa'),
    ('[C@](=C(Cl)[H])=C(F)[2H]', 'Sa'),
    ('[2H]C(F)=[C@]=C([H])Cl', 'Sa'),
    ('[2H]C(=[C@]=C([H])Cl)F', 'Sa'),
    ('[C@@](=C([H])Cl)=C([2H])F', 'Ra'),
    ('FC(=[C@]=C([H])Cl)[2H]', 'Ra'),
    ('FC([2H])=[C@@]=C(Cl)[H]', 'Ra'),
    ('[C@@](=C(Cl)[H])=C(F)[2H]', 'Ra'),
    ('[2H]C(F)=[C@@]=C([H])Cl', 'Ra'),
    ('[2H]C(=[C@@]=C([H])Cl)F', 'Ra'),
    ('CC=[C@]=C(F)Cl', 'Ra'),
    ('CC=[C@@]=C(F)Cl', 'Sa'),
    ('C1(Cl)=[C@]=C(F)CCCCCCCC1', 'Ra'),
    ('C1(Cl)=[C@@]=C(F)CCCCCCCC1', 'Sa'),
    ('C1CCCCCCCC(F)=[C@]=C1', 'Sa'),
    ('C1CCCCCCCC(F)=[C@@]=C1', 'Ra'),
    ('F[CH]=[C@]=[CH]Cl', 'Ra'),
    ('F[CH]=[C@@]=[CH]Cl', 'Sa'),
    ('C[CH]=[C@]=[CH]C(C)O', 'Ra'),
    ('C[CH]=[C@@]=[CH]C(C)O', 'Sa'),
    ('[CH](C)=[C@]=[CH]F', 'Sa'),
    ('[CH](C)=[C@@]=[CH]F', 'Ra'),
]

def _token(smiles: str) -> str:
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    (element,) = [e for e in frame.elements if e.kind == "axial"]
    return stereo.allene_mark_token(frame, element)


@pytest.mark.parametrize(("smiles", "expected"), CASES)
def test_the_mark_reader_agrees_with_the_independent_labeller(smiles: str, expected: str) -> None:
    assert _token(smiles) == expected


def test_the_opensmiles_example_is_ra() -> None:
    """``NC(Br)=[C@]=C(O)C``, the specification's own example, depicted at opensmiles.org (tetrahedral-allene.gif).

    Reading the picture: along the axis from the NH2/Br end, Br is up and in the plane; at the other end OH is
    hashed (behind) and CH3 is wedged (in front). Br -> O runs counter-clockwise: M, that is Ra.
    """

    assert _token("NC(Br)=[C@]=C(O)C") == "Ra"
    assert _token("NC(Br)=[C@@]=C(O)C") == "Sa"


def test_no_token_without_a_mark() -> None:
    frame = stereo.frame_from_mol(identity_mol_from_smiles("CC=C=CC"))
    (element,) = frame.elements
    assert stereo.allene_mark_token(frame, element) == "?"


def test_the_pseudo_asymmetric_centre_between_two_allenes_stays_unknown() -> None:
    """Part 3 is on hold: the centre stays ``?`` with its reason, whatever the marks say."""

    for smiles in ("CC=C=CC(C)C=C=CC", "CC=[C@]=C[C@H](C)C=[C@@]=CC", "CC=[C@]=C[C@H](C)C=[C@]=CC"):
        result = stereo.resolve_stereo(stereo.frame_from_mol(identity_mol_from_smiles(smiles)))
        assert result.descriptor.split(",")[-1] == "?"
        assert "allene_dependent" in result.unrepresented


def test_the_reader_flips_with_the_mark_everywhere() -> None:
    by_text = dict(CASES)
    flipped = 0
    for smiles, token in CASES:
        twin = smiles.replace("[C@@]", "\0").replace("[C@]", "[C@@]").replace("\0", "[C@]")
        if twin in by_text and twin != smiles:
            assert by_text[twin] != token
            flipped += 1
    assert flipped >= 20
