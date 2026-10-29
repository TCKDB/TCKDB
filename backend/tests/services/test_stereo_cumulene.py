"""Allene (Ra/Sa) and odd-cumulene (E/Z) stereo in the entry descriptor (#827).

RDKit 2025.09 has no allene or odd-cumulene stereo at all (no CIP label, marks dropped on sanitising, no 3D
perception, ``EnumerateStereoisomers`` returns one isomer), so the tokens come from our own geometric
perception. Fixtures are *constructed* (see ``_cumulene_fixtures``): ETKDG does not constrain a cumulated chain.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem, inchi, rdCIPLabeler

from app.api.error_contract import CodedValueError
from app.chemistry import stereo
from app.chemistry.species import identity_mol_from_smiles
from app.db.models.common import StereoResolution
from app.services.species_resolution import stereo_unresolved_warning
from tests.services import _cumulene_fixtures as fx


def frame(smiles: str) -> stereo.StereoFrame:
    return stereo.frame_from_mol(identity_mol_from_smiles(smiles))


def resolve(smiles: str, *, geometries=(), label: str | None = None) -> stereo.ResolvedStereo:
    return stereo.resolve_stereo(
        frame(smiles),
        geometries=[(fx.xyz(g), isotopes_of(g)) if isinstance(g, Chem.Mol) else g for g in geometries],
        supplied_label=label,
    )


def isotopes_of(geometry: Chem.Mol) -> dict[int, int]:
    return {a.GetIdx() + 1: a.GetIsotope() for a in geometry.GetAtoms() if a.GetIsotope()}


def tokens(smiles: str, geometry: Chem.Mol) -> list[str]:
    return stereo.geometry_tokens(frame(smiles), fx.xyz(geometry), isotopes_of(geometry))


def inchi_m_layer(mol: Chem.Mol) -> str:
    """The ``/m`` (absolute-stereo parity) layer of the InChI InChI computes from the 3D coordinates."""

    text = inchi.MolBlockToInchi(Chem.MolToMolBlock(mol))
    return next((part for part in text.split("/") if part.startswith("m")), "")


ALLENES = ["CC=C=CC", "CC(Cl)=C=C(F)C", "CC=C=C(F)Cl", "[2H]C=C=CC", "CC=C=CC(C)O", "C(F)(Cl)=C=CBr", "OC=C=CC(C)C"]


# ---------------------------------------------------------------------------
# Allene axis: Ra / Sa from geometry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("smiles", ALLENES)
def test_allene_enantiomers_get_opposite_axis_tokens(smiles: str) -> None:
    m = fx.build(smiles, -90.0)
    axial = next(i for i, e in enumerate(frame(smiles).elements) if e.kind == "axial")
    assert tokens(smiles, m)[axial] == "Ra"
    assert tokens(smiles, fx.mirror(m))[axial] == "Sa"
    assert tokens(smiles, fx.build(smiles, 90.0))[axial] == "Sa"


@pytest.mark.parametrize("smiles", ["CC=C=CC", "CC(Cl)=C=C(F)C"])
def test_ra_is_the_negative_torsion_m_and_agrees_with_the_signed_volume_rule(smiles: str) -> None:
    """Two derivations of the same token: IUPAC torsion sign (Ra = M) and the elongated-tetrahedron volume,
    calibrated on the R/S that RDKit's own labeller gives CHFClBr."""

    probe = Chem.AddHs(Chem.MolFromSmiles("C(F)(Cl)Br"))
    AllChem.EmbedMolecule(probe, randomSeed=1)
    Chem.AssignStereochemistryFrom3D(probe)
    rdCIPLabeler.AssignCIPLabels(probe)
    pos = probe.GetConformer().GetPositions()
    index = {a.GetSymbol(): a.GetIdx() for a in probe.GetAtoms() if a.GetSymbol() != "H"}
    hydrogen = next(a.GetIdx() for a in probe.GetAtoms() if a.GetSymbol() == "H")

    def volume(p1, p2, p3, p4):
        return float(np.dot(p1 - p4, np.cross(p2 - p4, p3 - p4)))

    r_sign = volume(pos[index["Br"]], pos[index["Cl"]], pos[index["F"]], pos[hydrogen]) > 0
    # the sign of the volume that RDKit's own labeller calls "R" (calibration, not an assumption)
    r_positive = r_sign if probe.GetAtomWithIdx(0).GetProp("_CIPCode") == "R" else not r_sign
    for torsion in (-90.0, 90.0, -70.0, 120.0):
        m = fx.build(smiles, torsion)
        path = fx.chain_ends_and_path(m)
        high_a = stereo.cip_higher_substituent(m, path[0], path[1])
        high_b = stereo.cip_higher_substituent(m, path[-1], path[-2])
        p = m.GetConformer().GetPositions()
        v = volume(p[high_a[0]], p[high_a[1]], p[high_b[0]], p[high_b[1]]) > 0
        by_volume = "Ra" if v == r_positive else "Sa"
        by_torsion = "Ra" if torsion < 0 else "Sa"
        assert by_volume == by_torsion == tokens(smiles, m)[0]


@pytest.mark.parametrize("smiles", ["CC=C=CC", "CC(Cl)=C=C(F)C", "CC=C=C(F)Cl"])
def test_the_axis_token_is_invariant_to_rotation_atom_order_and_xyz_shuffling(smiles: str) -> None:
    m = fx.build(smiles, -90.0)
    for seed in range(4):
        for variant in (fx.rotated(m, seed), fx.renumbered(m, seed), fx.renumbered(fx.rotated(m, seed), seed + 9)):
            assert tokens(smiles, variant) == ["Ra"], seed
            assert tokens(smiles, fx.mirror(variant)) == ["Sa"], seed


def test_the_axis_token_does_not_depend_on_how_the_smiles_is_written() -> None:
    m = fx.build("CC(Cl)=C=C(F)C", -90.0)
    spellings = ["CC(Cl)=C=C(F)C", "C(F)(C)=C=C(C)Cl", "FC(C)=C=C(Cl)C", "C(=C=C(C)Cl)(C)F"]
    results = {resolve(s, geometries=[m]).descriptor for s in spellings}
    assert results == {"Ra"}


@pytest.mark.parametrize(
    "smiles", ["CC=C=CC", "CC(Cl)=C=C(F)C", "CC=C=C(F)Cl", "CC=C=CC(C)O", "[2H]C=C=CC", "OC=C=CC(C)C"]
)
def test_the_axis_token_partitions_geometries_exactly_as_inchis_m_layer_does(smiles: str) -> None:
    by_token: dict[str, set[str]] = {}
    for base in (fx.build(smiles, -90.0, seed=s) for s in (1, 2, 3)):
        for variant in (base, fx.rotated(base, 1), fx.renumbered(base, 2), fx.mirror(base), fx.mirror(fx.rotated(base, 3))):
            token = tokens(smiles, variant)[[e.kind for e in frame(smiles).elements].index("axial")]
            by_token.setdefault(token, set()).add(inchi_m_layer(variant))
    assert set(by_token) == {"Ra", "Sa"}
    assert all(len(layers) == 1 for layers in by_token.values()), by_token
    assert by_token["Ra"] != by_token["Sa"]


@pytest.mark.parametrize("smiles", ["C1=C=C(C)CCCCCC1", "C1=C=C(C)CCCCCCC1", "C1=C=CCCCCCCC1"])
def test_twisted_ring_allenes_of_nine_or_more_are_read_and_mirror_cleanly(smiles: str) -> None:
    seen = {"Ra": set(), "Sa": set()}
    for seed in range(8):
        m = Chem.AddHs(Chem.MolFromSmiles(smiles))
        if AllChem.EmbedMolecule(m, randomSeed=seed) != 0:
            continue
        token = tokens(smiles, m)[0]
        if token == "?":  # a flattened embedding is not read: the guard, not a failure
            continue
        mirrored = tokens(smiles, fx.mirror(m))[0]
        assert {token, mirrored} == {"Ra", "Sa"}
        # InChI sees an allene axis only when it is twisted far enough (about 30 degrees and more between the
        # higher substituents); where it is blind there is nothing to compare, where it sees, it must agree.
        for token_i, geometry in ((token, m), (mirrored, fx.mirror(m))):
            layer = inchi_m_layer(geometry)
            if layer:
                seen[token_i].add(layer)
    assert seen["Ra"] and seen["Sa"]
    assert len(seen["Ra"]) == 1 and len(seen["Sa"]) == 1 and seen["Ra"] != seen["Sa"]


@pytest.mark.parametrize("size", [6, 7, 8])
def test_cyclic_allenes_in_rings_of_eight_or_fewer_stay_unknown_whatever_the_geometry(size: int) -> None:
    smiles = "C1=C=C(C)" + "C" * (size - 3) + "1"
    assert [e.ring_size for e in frame(smiles).elements] == [size]
    for seed in range(4):
        m = Chem.AddHs(Chem.MolFromSmiles(smiles))
        AllChem.EmbedMolecule(m, randomSeed=seed)
        assert tokens(smiles, m) == ["?"]
        assert tokens(smiles, fx.mirror(m)) == ["?"]
    r = resolve(smiles, geometries=[m])
    assert r.descriptor == "?" and r.unrepresented == (stereo.R_AXIAL_SMALL_RING,)
    assert stereo.unrepresented_stereo_kinds(smiles) == (stereo.R_AXIAL_SMALL_RING,)


def test_a_nine_membered_ring_allene_is_not_small() -> None:
    smiles = "C1=C=C(C)CCCCCC1"
    assert frame(smiles).elements[0].ring_size == 9
    assert not frame(smiles).elements[0].never_settled
    assert stereo.unrepresented_stereo_kinds(smiles) == ()


@pytest.mark.parametrize("torsion", [0.0, 5.0, 9.0, 171.0, 175.0, 180.0, -180.0, -3.0, -176.0])
def test_a_flattened_allene_axis_is_not_read(torsion: float) -> None:
    m = fx.build("CC=C=CC", torsion)
    assert tokens("CC=C=CC", m) == ["?"]
    r = resolve("CC=C=CC", geometries=[m])
    assert (r.descriptor, r.resolution) == ("?", StereoResolution.unresolved)
    assert r.unrepresented == ()  # a better geometry would settle it, so it is "unresolved", not "not represented"


@pytest.mark.parametrize(("torsion", "expected"), [(11.0, "Sa"), (-11.0, "Ra"), (169.0, "Sa"), (-169.0, "Ra"), (90.0, "Sa")])
def test_the_axis_window_edges(torsion: float, expected: str) -> None:
    assert tokens("CC=C=CC", fx.build("CC=C=CC", torsion)) == [expected]


# ---------------------------------------------------------------------------
# Odd cumulenes: E / Z from geometry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "smiles", ["CC=C=C=CC", "FC=C=C=CCl", "CC(F)=C=C=C(Cl)C", "CC=C=C=C=C=CC", "FC=C=C=C=C=CCl", "[2H]C=C=C=CC"]
)
def test_odd_cumulenes_are_z_when_planar_cis_and_e_when_planar_trans(smiles: str) -> None:
    assert tokens(smiles, fx.build(smiles, 0.0)) == ["Z"]
    assert tokens(smiles, fx.build(smiles, 180.0)) == ["E"]
    assert tokens(smiles, fx.build(smiles, -180.0)) == ["E"]
    assert resolve(smiles, geometries=[fx.build(smiles, 180.0)]).descriptor == "E"
    assert resolve(smiles, geometries=[fx.build(smiles, 0.0)]).stereo_smiles is None


@pytest.mark.parametrize("smiles", ["CC=C=C=CC", "FC=C=C=C=C=CCl"])
def test_odd_cumulene_tokens_are_invariant_to_rotation_and_atom_order(smiles: str) -> None:
    for torsion, expected in ((0.0, "Z"), (180.0, "E")):
        m = fx.build(smiles, torsion)
        for seed in range(3):
            assert tokens(smiles, fx.rotated(m, seed)) == [expected]
            assert tokens(smiles, fx.renumbered(m, seed)) == [expected]
            assert tokens(smiles, fx.mirror(m)) == [expected]  # cis/trans is not chiral


@pytest.mark.parametrize(("torsion", "expected"), [(29.0, "Z"), (31.0, "?"), (60.0, "?"), (90.0, "?"), (149.0, "?"), (151.0, "E")])
def test_a_twisted_odd_cumulene_is_not_read(torsion: float, expected: str) -> None:
    assert tokens("CC=C=C=CC", fx.build("CC=C=C=CC", torsion)) == [expected]


def test_odd_cumulene_marks_are_not_read_so_the_marks_alone_leave_it_unknown() -> None:
    for smiles in ("C/C=C=C=C/C", "C/C=C=C=C\\C", "CC=C=C=CC"):
        r = resolve(smiles)
        assert r.descriptor == "?" and r.resolution == StereoResolution.unresolved, smiles
    assert resolve("C/C=C=C=C/C").unrepresented == (stereo.R_CUMULENE_MARKS,)
    assert resolve("CC=C=C=CC").unrepresented == ()


def test_odd_cumulene_marks_do_not_override_or_contradict_a_geometry() -> None:
    assert resolve("C/C=C=C=C\\C", geometries=[fx.build("CC=C=C=CC", 180.0)]).descriptor == "E"


# ---------------------------------------------------------------------------
# Sources: contradictions, labels
# ---------------------------------------------------------------------------


def test_two_geometries_of_opposite_allene_sense_contradict() -> None:
    m = fx.build("CC=C=CC", -90.0)
    with pytest.raises(CodedValueError) as excinfo:
        resolve("CC=C=CC", geometries=[m, fx.mirror(m)])
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_CONTRADICTION


def test_two_conformers_of_one_allene_sense_agree() -> None:
    a, b = fx.build("CC=C=CC", -80.0), fx.build("CC=C=CC", -100.0, seed=5)
    assert resolve("CC=C=CC", geometries=[a, b]).descriptor == "Ra"


def test_a_flat_geometry_beside_a_twisted_one_does_not_contradict() -> None:
    assert resolve("CC=C=CC", geometries=[fx.build("CC=C=CC", 0.0), fx.build("CC=C=CC", -90.0)]).descriptor == "Ra"


def test_geometry_against_a_label_of_the_other_sense_contradicts() -> None:
    m = fx.build("CC=C=CC", -90.0)
    with pytest.raises(CodedValueError) as excinfo:
        resolve("CC=C=CC", geometries=[m], label="Sa")
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_CONTRADICTION
    assert resolve("CC=C=CC", geometries=[m], label="Ra").descriptor == "Ra"


def test_odd_cumulene_geometry_against_a_label_of_the_other_form_contradicts() -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve("CC=C=C=CC", geometries=[fx.build("CC=C=C=CC", 180.0)], label="Z")
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_CONTRADICTION


@pytest.mark.parametrize("label", ["Ra", "Sa", "?"])
def test_an_allene_label_may_be_ra_sa_or_unknown(label: str) -> None:
    r = resolve("CC=C=CC", label=label)
    assert r.descriptor == label


@pytest.mark.parametrize("label", ["R", "S", "E", "Z", "e", "-", "RA", "Ra,Sa", "M", "P"])
def test_any_other_allene_label_is_invalid(label: str) -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve("CC=C=CC", label=label)
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID


@pytest.mark.parametrize("label", ["E", "Z", "?"])
def test_an_odd_cumulene_label_may_be_e_z_or_unknown(label: str) -> None:
    assert resolve("CC=C=C=CC", label=label).descriptor == label


@pytest.mark.parametrize("label", ["Ra", "Sa", "R", "e", "-"])
def test_an_odd_cumulene_label_of_another_kind_is_invalid(label: str) -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve("CC=C=C=CC", label=label)
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID


def test_ra_cannot_label_a_double_bond_or_a_centre() -> None:
    for smiles in ("CC=CC", "CC(O)CC"):
        with pytest.raises(CodedValueError) as excinfo:
            resolve(smiles, label="Ra")
        assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID


def test_a_small_ring_allene_takes_only_an_unknown_label() -> None:
    with pytest.raises(CodedValueError) as excinfo:
        resolve("C1=C=C(C)CCC1", label="Ra")
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID
    assert "ring" in str(excinfo.value)
    assert resolve("C1=C=C(C)CCC1", label="?").descriptor == "?"


def test_an_allene_label_with_a_centre_is_checked_per_position() -> None:
    f = frame("CC=C=CC(C)O")
    kinds = [e.kind for e in f.elements]
    assert sorted(kinds) == ["axial", "tetrahedral"]
    axial_first = kinds[0] == "axial"
    ok = "Ra,R" if axial_first else "R,Ra"
    wrong = "R,R" if axial_first else "Ra,Ra"
    assert resolve("CC=C=CC(C)O", label=ok).resolution == StereoResolution.resolved
    with pytest.raises(CodedValueError):
        resolve("CC=C=CC(C)O", label=wrong)


@pytest.mark.parametrize(
    ("value", "ok"),
    [("Ra", True), ("Sa", True), ("Ra,Sa", True), ("R,Ra,E,?", True), ("Sa,-", True), ("RA", False), ("Rb", False), ("a", False), ("Ra,", False), ("Ra Sa", False)],
)
def test_the_descriptor_pattern_accepts_ra_sa_as_whole_tokens(value: str, ok: bool) -> None:
    import re

    assert bool(re.fullmatch(stereo.DESCRIPTOR_PATTERN, value)) is ok


def test_the_schema_package_pattern_matches_the_backend_pattern() -> None:
    from tckdb_schemas.fragments.identity import STEREO_DESCRIPTOR_PATTERN

    for value in ("Ra", "Sa,Ra", "R,S,E,Z,e,z,r,s,?,-", "Ra,?,Sa", "R", "Rb", "Ra,,Sa", "", "ra"):
        import re

        assert bool(STEREO_DESCRIPTOR_PATTERN.fullmatch(value)) == bool(re.fullmatch(stereo.DESCRIPTOR_PATTERN, value)), value


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def _warning(smiles: str, **kw):
    return stereo_unresolved_warning(resolve(smiles, **kw))


def test_an_allene_with_no_data_is_unresolved_not_unrepresented() -> None:
    w = _warning("CC=C=CC")
    assert w.code == stereo.W_SPECIES_ENTRY_STEREO_UNRESOLVED
    assert "allene" in w.message


def test_a_resolved_allene_has_no_warning() -> None:
    assert _warning("CC=C=CC", label="Ra") is None
    assert _warning("CC=C=CC", geometries=[fx.build("CC=C=CC", 90.0)]) is None


def test_a_small_ring_allene_warns_not_represented_even_with_a_geometry() -> None:
    m = Chem.AddHs(Chem.MolFromSmiles("C1=C=C(C)CCC1"))
    AllChem.EmbedMolecule(m, randomSeed=1)
    for kw in ({}, {"geometries": [m]}):
        w = _warning("C1=C=C(C)CCC1", **kw)
        assert w.code == stereo.W_SPECIES_ENTRY_STEREO_NOT_REPRESENTED
        assert "ring" in w.message


def test_an_allene_deposited_with_marks_only_warns_not_represented_and_says_so() -> None:
    w = _warning("CC=[C@]=CC")
    assert w.code == stereo.W_SPECIES_ENTRY_STEREO_NOT_REPRESENTED
    assert "marks" in w.message and "geometry" in w.message
    assert resolve("CC=[C@]=CC").unrepresented == (stereo.R_AXIAL_MARKS,)


def test_allene_marks_are_never_read_into_a_token() -> None:
    assert resolve("CC=[C@]=CC").descriptor == resolve("CC=[C@@]=CC").descriptor == "?"
    # ... but a geometry or a label next to the marks settles it: the warning is then gone
    assert _warning("CC=[C@]=CC", label="Sa") is None
    assert _warning("CC=[C@]=CC", geometries=[fx.build("CC=C=CC", 90.0)]) is None


def test_odd_cumulene_marks_only_warn_not_represented_and_say_so() -> None:
    w = _warning("C/C=C=C=C/C")
    assert w.code == stereo.W_SPECIES_ENTRY_STEREO_NOT_REPRESENTED
    assert "marks" in w.message
    assert _warning("CC=C=C=CC").code == stereo.W_SPECIES_ENTRY_STEREO_UNRESOLVED


def test_a_flat_geometry_with_marks_is_unresolved_for_a_cumulene_and_settleable() -> None:
    w = _warning("CC=C=C=CC", geometries=[fx.build("CC=C=C=CC", 90.0)])
    assert w.code == stereo.W_SPECIES_ENTRY_STEREO_UNRESOLVED


def test_the_symmetry_cap_still_warns_not_represented(monkeypatch) -> None:
    monkeypatch.setattr(stereo, "_AUTOMORPHISM_CAP", 2)
    w = _warning("CC(O)C(O)C(C)O")
    assert w.code == stereo.W_SPECIES_ENTRY_STEREO_NOT_REPRESENTED


# ---------------------------------------------------------------------------
# Enumeration: one descriptor per isomer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("smiles", "count"),
    [
        ("CC=C=CC", 2),
        ("CC(Cl)=C=C(F)C", 2),
        ("CC=C=CC=C=CC", 3),  # Ra,Ra / Sa,Sa / meso Ra,Sa
        ("CC=C=CC(C)O", 4),
        ("CC=C=CC=CC", 4),
        ("CC=C=C=CC", 2),
        ("CC=C=C=C=C=CC", 2),
        ("C1=C=C(C)CCCCCC1", 2),
    ],
)
def test_the_number_of_descriptors_equals_the_number_of_isomers(smiles: str, count: int) -> None:
    isomers = stereo.stereoisomers(frame(smiles))
    assert len(isomers) == count
    assert len({",".join(t) for t, _ in isomers}) == count


def test_meso_allene_pair_is_one_descriptor_and_the_enantiomer_pair_two() -> None:
    labels = {",".join(t) for t, _ in stereo.stereoisomers(frame("CC=C=CC=C=CC"))}
    assert labels == {"Ra,Ra", "Ra,Sa", "Sa,Sa"}


def test_documented_limit_a_centre_that_depends_on_an_allene_is_unknown_not_non_stereogenic() -> None:
    """``CC=C=CC(C)C=C=CC`` has 4 stereoisomers (``Ra,Ra``, ``Sa,Sa`` and the two meso forms whose middle carbon is
    pseudo-asymmetric r or s) but only 3 descriptors are possible: the centre is stated ``?`` (#832)."""

    smiles = "CC=C=CC(C)C=C=CC"
    f = frame(smiles)
    assert sorted(e.kind for e in f.elements) == ["allene_dependent", "axial", "axial"]
    isomers = stereo.stereoisomers(f)
    descriptors = {",".join(t) for t, _ in isomers}
    assert len(descriptors) == 3 < 4
    assert all(t.count("-") == 0 and t.count("?") == 1 for t, _ in isomers)
    assert stereo.unrepresented_stereo_kinds(smiles) == (stereo.R_ALLENE_DEPENDENT,)
    # nothing deduces the centre away
    refined = stereo.refine(f, [("?" if e.kind == "allene_dependent" else "Ra") for e in f.elements])
    assert refined[[e.kind for e in f.elements].index("allene_dependent")] == "?"
    r = resolve(smiles)
    assert stereo.R_ALLENE_DEPENDENT in r.unrepresented
    w = stereo_unresolved_warning(r)
    assert w.code == stereo.W_SPECIES_ENTRY_STEREO_NOT_REPRESENTED
    assert "allene" in w.message


def test_the_allene_dependent_position_takes_only_an_unknown_label() -> None:
    f = frame("CC=C=CC(C)C=C=CC")
    kinds = [e.kind for e in f.elements]
    dependent = kinds.index("allene_dependent")
    ok = ["Ra"] * 3
    ok[dependent] = "?"
    assert resolve("CC=C=CC(C)C=C=CC", label=",".join(ok)).resolution == StereoResolution.partial
    bad = ["Ra"] * 3
    bad[dependent] = "r"
    with pytest.raises(CodedValueError) as excinfo:
        resolve("CC=C=CC(C)C=C=CC", label=",".join(bad))
    assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID


def test_an_ordinary_centre_beside_one_allene_is_not_mistaken_for_an_allene_dependent_one() -> None:
    assert [e.kind for e in frame("CC=C=CC(C)O").elements].count("allene_dependent") == 0
    assert "allene_dependent" not in [e.kind for e in frame("CC(C)C(C)C=C=CC").elements]


def test_a_double_bond_whose_ends_hold_enantiomorphic_allene_arms_is_allene_dependent() -> None:
    kinds = [e.kind for e in frame("CC=C=CC(C=C=CC)=CC").elements]
    assert "allene_dependent" in kinds


def test_the_enumeration_with_a_stereocentre_beside_an_allene_is_complete() -> None:
    labels = {",".join(t) for t, _ in stereo.stereoisomers(frame("CC=C=CC(C)O"))}
    assert len(labels) == 4 and all("-" not in x for x in labels)


def test_stereo_smiles_is_null_for_any_entry_with_a_cumulene_element() -> None:
    assert resolve("CC=C=CC(C)O", label="Ra,R" if frame("CC=C=CC(C)O").elements[0].kind == "axial" else "R,Ra").stereo_smiles is None
    assert resolve("CC=C=CC", label="Sa").stereo_smiles is None
    assert resolve("CC=C=C=CC", label="E").stereo_smiles is None
    assert resolve("CC=CC", label="E").stereo_smiles is not None


def test_descriptor_of_mol_reads_nothing_for_an_allene_from_marks() -> None:
    assert stereo.descriptor_of_mol(identity_mol_from_smiles("CC=[C@]=CC")) == "?"


def test_a_dihedral_of_nan_or_a_zero_length_axis_is_not_read() -> None:
    positions = np.zeros((6, 3))
    assert stereo.chain_torsion(positions, [0, 1, 2], 3, 4) is None
    assert math.isfinite(stereo.chain_torsion(np.array([[0, 0, 0], [0, 0, 1.3], [0, 0, 2.6], [1, 0, -0.5], [0, 1, 3.1]], float), [0, 1, 2], 3, 4))


def test_cip_priority_probe_agrees_with_rdkit_on_ordinary_double_bonds() -> None:
    """The substituent order used for the axis comes from a one-centre probe of the CIP labeller; on an ordinary
    double bond it must reproduce RDKit's own E/Z for the same coordinates."""

    from tests.services.test_stereo_descriptor import embed_xyz  # noqa: F401  (documented dependency)

    checked = 0
    for smiles in ["CC=CC(C)C", "CC=C(C)C(C)(C)C", "OC(C)C=CC(O)C", "C=CC(Cl)Br", "CC=CC(F)Cl", "[2H]C=CC", "CC=C(Cl)C(Cl)(Cl)C", "CC=C(C1CC1)C(C)C", "FC=CC1CCC(C)CC1"]:
        for seed in range(4):
            m = Chem.AddHs(Chem.MolFromSmiles(smiles))
            if AllChem.EmbedMolecule(m, randomSeed=seed) != 0:
                continue
            Chem.AssignStereochemistryFrom3D(m)
            rdCIPLabeler.AssignCIPLabels(m)
            pos = m.GetConformer().GetPositions()
            for b in m.GetBonds():
                if b.GetBondType() != Chem.BondType.DOUBLE or not b.HasProp("_CIPCode"):
                    continue
                a, c = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
                ha, hc = stereo.cip_higher_substituent(m, a, c), stereo.cip_higher_substituent(m, c, a)
                t = stereo.chain_torsion(pos, [a, c], ha[0], hc[0])
                assert ("Z" if abs(t) < 90 else "E") == b.GetProp("_CIPCode"), (smiles, seed)
                checked += 1
    assert checked >= 20


def test_a_geometry_the_cip_probe_cannot_handle_says_nothing_instead_of_failing(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("labeller failure")

    geometry = fx.build("CC=C=CC", 90.0)
    monkeypatch.setattr(stereo, "cip_higher_substituent", boom)
    assert tokens("CC=C=CC", geometry) == ["?"]


# ---------------------------------------------------------------------------
# A centre between two allenes that share a ring (review blocker)
# ---------------------------------------------------------------------------

RING_BETWEEN_ALLENES = ["CC1C=C=CCCCCC=C=C1", "OC1C=C=CCCCCCCC=C=C1"]


@pytest.mark.parametrize("smiles", RING_BETWEEN_ALLENES)
def test_a_centre_between_two_ring_allenes_is_allene_dependent_never_non_stereogenic(smiles: str) -> None:
    f = frame(smiles)
    assert sorted(e.kind for e in f.elements) == ["allene_dependent", "axial", "axial"]
    isomers = stereo.stereoisomers(f)
    assert all("-" not in t for t, _ in isomers)
    assert {",".join(t) for t, _ in isomers} == {"Ra,Ra,?", "Ra,Sa,?", "Sa,Sa,?"}
    assert stereo.unrepresented_stereo_kinds(smiles) == (stereo.R_ALLENE_DEPENDENT,)
    assert stereo.R_ALLENE_DEPENDENT in resolve(smiles).unrepresented


@pytest.mark.parametrize("smiles", RING_BETWEEN_ALLENES)
def test_the_ring_case_from_geometry_leaves_the_centre_unknown(smiles: str) -> None:
    read = 0
    for seed in range(12):
        m = Chem.AddHs(Chem.MolFromSmiles(smiles))
        if AllChem.EmbedMolecule(m, randomSeed=seed) != 0:
            continue
        r = resolve(smiles, geometries=[m])
        assert "-" not in r.descriptor.split(","), (seed, r.descriptor)
        if r.descriptor.count("?") == 1:
            read += 1
            assert stereo.R_ALLENE_DEPENDENT in r.unrepresented
            assert r.resolution == StereoResolution.partial
    assert read


@pytest.mark.parametrize("smiles", RING_BETWEEN_ALLENES)
def test_the_ring_case_label_cannot_state_the_centre(smiles: str) -> None:
    dependent = [e.kind for e in frame(smiles).elements].index("allene_dependent")
    for token in ("-", "r", "s", "R"):
        tokens = ["Ra", "Ra", "Ra"]
        tokens[dependent] = token
        with pytest.raises(CodedValueError) as excinfo:
            resolve(smiles, label=",".join(tokens))
        assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID
    tokens = ["Ra", "Ra", "Ra"]
    tokens[dependent] = "?"
    assert resolve(smiles, label=",".join(tokens)).resolution == StereoResolution.partial


def test_a_ring_with_one_allene_keeps_its_ordinary_centre() -> None:
    assert "allene_dependent" not in [e.kind for e in frame("CC1CCCCC=C=CCCCC1").elements]


# ---------------------------------------------------------------------------
# Every tie pattern, not one shape (review blocker): a centre with two tied PAIRS, four identical arms, spiro
# ---------------------------------------------------------------------------

SPIRO = "C12(C=C=CCCCC=C=C1)C=C=CCCCC=C=C2"
BOND_ALLENYL_CARBINOL = "CC=C=CC(C=C=CC)=C(C(C)O)C(C)O"
ACYCLIC_2_2 = "CC=C=CC(C=C=CC)(C=C=CCC)C=C=CCC"
TETRA_ALLENYL = "C(C=C=CC)(C=C=CC)(C=C=CC)C=C=CC"
TWO_ONE_ONE = "CC=C=CC(C=C=CC)(C)O"

#: template -> (is the centre allene-dependent?, number of descriptors enumerated)
TIE_PATTERNS = {
    "spiro, two allenes per ring": (SPIRO, True, 6),
    "quaternary 2+2": (ACYCLIC_2_2, True, 9),
    "four identical arms": (TETRA_ALLENYL, True, 5),
    "2+1+1": (TWO_ONE_ONE, True, 3),
    "ring pair, three ranks": ("CC1C=C=CCCCCC=C=C1", True, 3),
    "double bond with tied allenyl arms": ("CC=C=CC(=CC)C=C=CC", True, 3),
    # ordinary centres that must stay a correct '-': the swap of the tied arms fixes the axes, or a tied group
    # holds no stereo content at all
    "spiro, one allene per ring": ("C12(CCCCC=C=CCCCC1)CCCCC=C=CCCCC2", False, 3),
    "one ring allene opposite a centre": ("CC1CCCCC=C=CCCCC1", False, 2),
    "allene beside an ordinary centre": ("CC=C=CC(C)O", False, 4),
    "gem-dimethyl between allenes": ("CC=C=CC(C)(C)C=C=CC", False, 3),
    "symmetric cyclohexane between allenes": ("CC=C=CC1(C=C=CC)CCCCC1", False, 3),
    "gem-diethyl, one tied allene pair": ("CC=C=CC(C=C=CC)(CC)CC", False, 3),
    # the R/S-bearing arms must be tested as a tied group too, whichever group is met first
    "tied allenyl pair beside tied carbinol pair": ("CC(O)C(C=C=CC)(C=C=CC)C(C)O", True, 9),
    "tied carbinol pair beside tied allenyl pair": ("CC=C=CC(C=C=CC)(C(C)O)C(C)O", True, 9),
    # double bonds follow the same rule: an allenyl pair at one end and ordinary stereo at the other is flagged,
    # and a non-stereo identical pair (two methyls) at the other end is an ordinary non-stereogenic bond
    "double bond: allenyl pair against carbinol pair": ("CC=C=CC(C=C=CC)=C(C(C)O)C(C)O", True, 9),
    "double bond: allenyl pair against methyls": ("CC=C=CC(C=C=CC)=C(C)C", False, 3),
}


@pytest.mark.parametrize(("name", "case"), list(TIE_PATTERNS.items()))
def test_every_tie_pattern_flags_a_dependent_element_and_never_deduces_a_dash(name: str, case) -> None:
    import random

    smiles, dependent, count = case
    molecule = Chem.MolFromSmiles(smiles)
    spellings = [smiles]
    for seed in range(8):  # the answer must not depend on the order the atoms are written in
        random.seed(seed)
        spellings.append(Chem.MolToSmiles(molecule, doRandom=True))
    for spelling in spellings:
        f = frame(spelling)
        kinds = [e.kind for e in f.elements]
        assert ("allene_dependent" in kinds) is dependent, (name, spelling, kinds)
        isomers = stereo.stereoisomers(f)
        descriptors = {",".join(t) for t, _ in isomers}
        assert len(descriptors) == count, (name, spelling, sorted(descriptors))
        if dependent:
            for t, _ in isomers:
                assert "-" not in t, (name, spelling, t)  # a '-' beside an allene axis is a deduction nothing supports
            assert stereo.R_ALLENE_DEPENDENT in resolve(spelling).unrepresented


def _uff(smiles: str, seed: int) -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0
    AllChem.UFFOptimizeMolecule(mol, maxIters=2000)
    return mol


@pytest.mark.parametrize("smiles", [SPIRO, ACYCLIC_2_2, TETRA_ALLENYL, TWO_ONE_ONE, BOND_ALLENYL_CARBINOL])
def test_a_relaxed_geometry_and_its_mirror_never_state_the_dependent_centre(smiles: str) -> None:
    order = [e.kind for e in frame(smiles).elements]
    dependent = order.index("allene_dependent")
    for seed in range(3):
        m = _uff(smiles, seed)
        for variant in (m, fx.mirror(m)):
            r = resolve(smiles, geometries=[variant])
            tokens = r.descriptor.split(",")
            assert tokens[dependent] == "?" and "-" not in tokens, (smiles, seed, r.descriptor)
            assert stereo.R_ALLENE_DEPENDENT in r.unrepresented
        read = tokens_of(smiles, m)
        mirrored = tokens_of(smiles, fx.mirror(m))
        for a, b in zip(read, mirrored, strict=True):
            if a in ("Ra", "Sa"):
                assert b == ("Sa" if a == "Ra" else "Ra")


def tokens_of(smiles: str, geometry: Chem.Mol) -> list[str]:
    return stereo.geometry_tokens(frame(smiles), fx.xyz(geometry), isotopes_of(geometry))


@pytest.mark.parametrize("smiles", [SPIRO, ACYCLIC_2_2, TETRA_ALLENYL])
def test_a_label_cannot_state_a_tie_pattern_centre(smiles: str) -> None:
    order = [e.kind for e in frame(smiles).elements]
    dependent = order.index("allene_dependent")
    for token in ("-", "R", "r"):
        tokens = ["Ra"] * len(order)
        tokens[dependent] = token
        with pytest.raises(CodedValueError) as excinfo:
            resolve(smiles, label=",".join(tokens))
        assert excinfo.value.code == stereo.W_SPECIES_STEREO_LABEL_INVALID


def test_a_spiro_with_one_allene_per_ring_keeps_its_dash() -> None:
    smiles = "C12(CCCCC=C=CCCCC1)CCCCC=C=CCCCC2"
    labels = {",".join(t) for t, _ in stereo.stereoisomers(frame(smiles))}
    assert labels == {"Ra,Ra,-", "Ra,Sa,-", "Sa,Sa,-"}
    assert stereo.R_ALLENE_DEPENDENT not in resolve(smiles, label="Ra,Sa,-").unrepresented


def test_the_ordinary_analogue_of_the_double_bond_case_really_has_both_ends_as_enantiomorphic_pairs() -> None:
    """``CC(Cl)C(C(C)Cl)=C(C(C)O)C(C)O`` has 10 isomers with E/Z at the bond: why the allenyl version may not be ``-``."""

    labels = {",".join(t) for t, _ in stereo.stereoisomers(frame("CC(Cl)C(C(C)Cl)=C(C(C)O)C(C)O"))}
    assert len(labels) == 10 and any(label.startswith(("E", "Z", "e", "z")) for label in labels)
