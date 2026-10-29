"""Validation of the odd-cumulene ``/`` ``\\`` marks reader against InChI's ``/b`` stereo layer.

The marks reader (:func:`app.chemistry.stereo.cumulene_mark_token`) applies the double-bond convention of
OpenSMILES to a cumulated chain with an odd number of double bonds. RDKit parses no stereo from such marks,
so there is no toolkit reference at n = 3 or 5. The check here is therefore a triangle:

1. a hand-derived relation for each SMILES (is the marked substituent on one end on the same side as the
   marked substituent on the other?), written down below from the OpenSMILES rule;
2. a planar geometry *constructed* to have exactly that relation, whose InChI ``/b`` layer is the independent
   witness: it must be one string for every spelling of one relation and a different string for the other;
3. the code under test, which must name the same configuration from the marks as the geometry reader does
   from the constructed coordinates.

The reader is shipped only if every case agrees. At n = 1 the same side logic is compared with RDKit's own
CIP labels.
"""

from __future__ import annotations

import pytest
from rdkit import Chem
from rdkit.Chem import inchi, rdCIPLabeler

from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from tests.services import _cumulene_fixtures as fx

# (SMILES with marks, relation of the two MARKED substituents, their atom indices in the SMILES)
CASES = [
    # n = 3, ends F/H and Cl/H
    ("F/C=C=C=C/Cl", "trans", (0, 5)),
    ("F/C=C=C=C\\Cl", "cis", (0, 5)),
    ("Cl/C=C=C=C/F", "trans", (0, 5)),
    ("Cl/C=C=C=C\\F", "cis", (0, 5)),
    ("C(\\F)=C=C=C/Cl", "trans", (1, 5)),
    ("C(/F)=C=C=C/Cl", "cis", (1, 5)),
    ("C(/F)=C=C=C\\Cl", "trans", (1, 5)),
    # n = 3, a mark on the lower-priority substituent, and a fully substituted end
    ("FC(/C)=C=C=C/Cl", "cis", (2, 6)),
    ("C/C(F)=C=C=C(Cl)\\C", "cis", (0, 7)),
    ("C/C(F)=C=C=C(Cl)/C", "trans", (0, 7)),
    # n = 3, methyl ends
    ("C/C=C=C=C/C", "trans", (0, 5)),
    ("C/C=C=C=C\\C", "cis", (0, 5)),
    # n = 5
    ("F/C=C=C=C=C=C/Cl", "trans", (0, 7)),
    ("F/C=C=C=C=C=C\\Cl", "cis", (0, 7)),
    ("Cl/C=C=C=C=C=C/F", "trans", (0, 7)),
    ("C/C=C=C=C=C=C\\C", "cis", (0, 7)),
    ("C/C=C=C=C=C=C/C", "trans", (0, 7)),
    ("C(/F)=C=C=C=C=C/Cl", "cis", (1, 7)),
]


def _plain(smiles: str) -> str:
    return smiles.replace("/", "").replace("\\", "")


def _inchi(mol: Chem.Mol) -> str:
    return inchi.MolBlockToInchi(Chem.MolToMolBlock(Chem.RemoveHs(mol)))


def _geometry_token(smiles: str, geometry: Chem.Mol) -> str:
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    (token,) = stereo.geometry_tokens(frame, fx.xyz(geometry))
    return token


def _marks_token(smiles: str) -> str:
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    (element,) = frame.elements
    return stereo.cumulene_mark_token(frame, element)


@pytest.mark.parametrize(("smiles", "relation", "marked"), CASES)
def test_marks_reader_names_the_configuration_of_the_constructed_geometry(smiles, relation, marked) -> None:
    geometry = fx.build(_plain(smiles), 0.0 if relation == "cis" else 180.0, between=marked)
    assert _marks_token(smiles) == _geometry_token(_plain(smiles), geometry)
    assert _marks_token(smiles) in ("E", "Z")


def _group(kind_filter) -> dict[tuple[str, str], set[str]]:
    seen: dict[tuple[str, str], set[str]] = {}
    for smiles, relation, marked in CASES:
        if not kind_filter(smiles):
            continue
        constitution = Chem.MolToSmiles(Chem.MolFromSmiles(_plain(smiles)))
        geometry = fx.build(_plain(smiles), 0.0 if relation == "cis" else 180.0, between=marked)
        for seed in (None, 1, 2):  # the constructed geometry, then two reorderings of its atoms
            g = geometry if seed is None else fx.renumbered(geometry, seed)
            seen.setdefault((constitution, relation), set()).add(_inchi(g))
    return seen


def _bonds_in_chain(constitution: str) -> int:
    return constitution.count("=")


def test_inchi_gives_one_b_layer_per_relation_and_tells_cis_from_trans_for_a_butatriene() -> None:
    seen = _group(lambda s: True)
    checked = 0
    for constitution in {c for c, _ in seen}:
        cis, trans = seen.get((constitution, "cis")), seen.get((constitution, "trans"))
        if cis is None or trans is None or _bonds_in_chain(constitution) != 3:
            continue
        assert len(cis) == 1 and len(trans) == 1, (constitution, cis, trans)
        assert cis != trans
        checked += 1
    assert checked >= 3


def test_inchi_is_blind_to_cis_trans_across_five_cumulated_double_bonds() -> None:
    """The reason the marks reader is not shipped (see ``stereo.CUMULENE_MARKS_READ``).

    InChI writes no ``/b`` layer for a chain of five cumulated double bonds, so its string is identical for the
    cis and the trans isomer and cannot witness the marks reader at n = 5. If a later InChI does, this test
    fails and the validation can be completed.
    """

    seen = _group(lambda s: True)
    n5 = {c for c, _ in seen if _bonds_in_chain(c) == 5}
    assert n5
    for constitution in n5:
        cis, trans = seen[(constitution, "cis")], seen[(constitution, "trans")]
        assert cis == trans and len(cis) == 1
        assert "/b" not in next(iter(cis))


def test_every_constitution_in_the_set_has_both_relations_except_the_one_sided_ones() -> None:
    seen = _group(lambda s: True)
    both = {c for c, r in seen if (c, "cis") in seen and (c, "trans") in seen}
    assert len(both) >= 5  # n = 3 and n = 5, differently substituted ends


def test_a_mark_on_an_explicit_hydrogen_reads_like_the_mark_on_the_other_substituent() -> None:
    # [H]/C(F)=...: the hydrogen sits below its carbon, so the fluorine is above, exactly as in C(/F)=...
    assert _marks_token("[H]/C(F)=C=C=C/Cl") == _marks_token("C(/F)=C=C=C/Cl") == "Z"
    assert _marks_token("[H]\\C(F)=C=C=C/Cl") == _marks_token("C(\\F)=C=C=C/Cl") == "E"


def test_an_unmarked_or_half_marked_cumulene_stays_unknown() -> None:
    assert _marks_token("FC=C=C=CCl") == "?"
    assert _marks_token("F/C=C=C=CCl") == "?"
    assert _marks_token("FC=C=C=C/Cl") == "?"


# ---- n = 1 control: the same side logic against RDKit's own CIP labels --------------------------------------


@pytest.mark.parametrize(
    "smiles",
    [
        "F/C=C/Cl",
        "F/C=C\\Cl",
        "Cl/C=C/F",
        "C(\\F)=C/Cl",
        "C/C(F)=C(Cl)\\C",
        "C/C(F)=C(Cl)/C",
        "[H]/C(F)=C/Cl",
        "FC(/C)=C/Cl",
    ],
)
def test_the_side_logic_agrees_with_rdkit_for_an_ordinary_double_bond(smiles: str) -> None:
    mol = Chem.MolFromSmiles(smiles)
    rdCIPLabeler.AssignCIPLabels(mol)
    reference = next(b.GetProp("_CIPCode") for b in mol.GetBonds() if b.HasProp("_CIPCode"))
    frame = stereo.frame_from_mol(identity_mol_from_smiles(smiles))
    (element,) = frame.elements
    synthetic = stereo.StereoElement(
        stereo._CUMULENE, element.atoms, element.key, chain=tuple(element.atoms), ring_size=0
    )
    assert stereo.cumulene_mark_token(frame, synthetic) == reference


def test_the_reader_is_not_wired_in_while_n_equals_five_cannot_be_validated() -> None:
    """Shipped behaviour: marks on a cumulene say nothing (``?``); the reader above is validated for n = 3 only."""

    assert stereo.CUMULENE_MARKS_READ is False
    frame = stereo.frame_from_mol(identity_mol_from_smiles("F/C=C=C=C/Cl"))
    assert stereo.mark_tokens(frame) == ["?"]
