"""The no-optimisation duplicate rule recognises a rigidly moved polyatomic geometry (#667).

With no ``opt`` linked, two ``sp`` links on one structure are refused (#610).
For one atom every position is one structure (#623); a polyatomic geometry was
still compared by stored row, so a water geometry translated by 1 Angstrom (a
different row) walked past the rule. It is now the same structure when the two
are one shape moved rigidly: same atoms (in any order since #679), Kabsch-aligned RMSD
within the rounding of the coordinates as deposited.

``geom_hash`` is not that comparison and is not changed: it is invariant only
to the digits a coordinate is written with (``parse_xyz`` formats ``.12f``),
not to translation, rotation, atom order or element-symbol case.

The tolerance is ``sqrt(3)/2 * (10**-d_a + 10**-d_b)`` for coordinates written
to ``d_a`` and ``d_b`` decimals (floored at four): a coordinate is within half
a unit of its last decimal, an atom within ``sqrt(3)`` times that, and two
deposits add. For two six-decimal geometries that is about 1.7e-6 Angstrom, so
the boundary tests below use six-decimal coordinates and steps of 1e-6 and
1e-5 Angstrom.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.chemistry.torsion_fingerprint import kabsch_rmsd
from tests.api.test_api_composite_role_consistency import _composite, _sp
from tests.api.test_api_composite_role_consistency import _payload as _water_payload

PRODUCTS = pytest.mark.parametrize("product", ["thermo", "statmech"])

# Written to six decimals with no round digits, as an ESS prints them: the rule reads the precision a
# geometry was written to off its coordinates, and 0.757 would read as three decimals.
_WATER = [
    ("O", (0.0, 0.0, 0.117301)),
    ("H", (0.0, 0.757143, -0.469207)),
    ("H", (0.0, -0.757143, -0.469207)),
]


def _xyz(atoms, decimals: int = 6) -> str:
    lines = [str(len(atoms)), "geometry"]
    lines += [f"{el} {x:.{decimals}f} {y:.{decimals}f} {z:.{decimals}f}" for el, (x, y, z) in atoms]
    return "\n".join(lines)


def _rotation(axis, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    return np.array(
        [
            [c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
            [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
            [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)],
        ]
    )


def _moved(atoms, *, rotation=None, shift=(0.0, 0.0, 0.0)):
    out = []
    for el, c in atoms:
        v = np.asarray(c, dtype=float)
        if rotation is not None:
            v = rotation @ v
        out.append((el, tuple(float(t) for t in v + np.asarray(shift))))
    return out


def _nudged(atoms, atom: int, axis: int, delta: float):
    out = [(el, list(c)) for el, c in atoms]
    out[atom][1][axis] += delta
    return [(el, tuple(c)) for el, c in out]


_ROTATION = _rotation((1.0, 2.0, 3.0), 0.9)
_SHIFT = (1.0, -2.0, 0.5)
_BASE = _xyz(_WATER)
_TRANSLATED = _xyz(_moved(_WATER, shift=(1.0, 0.0, 0.0)))
_ROTATED = _xyz(_moved(_WATER, rotation=_ROTATION))
_BOTH = _xyz(_moved(_WATER, rotation=_ROTATION, shift=_SHIFT))


def _post(client, product: str, calcs: dict, links: list[tuple[str, str]]):
    url, payload = _water_payload(product, calcs, links)
    return client.post(url, json=payload)


def _assert_duplicate(resp, product: str, key: str = "sp_calculation_refs") -> dict:
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == f"{product}_role_duplicate", body
    assert len(body["context"][key]) == 2, body
    return body


def _two_sps(client, product, first: str, second: str):
    return _post(client, product, {"s1": _sp(first), "s2": _sp(second)}, [("s1", "sp"), ("s2", "sp")])


# ---------------------------------------------------------------------------
# Water, standalone uploads
# ---------------------------------------------------------------------------


@PRODUCTS
def test_two_sps_on_a_translated_copy_of_water_are_refused(client, product):
    body = _assert_duplicate(_two_sps(client, product, _BASE, _TRANSLATED), product)
    # The wording is #663's: the same code, "geometry" not "atom".
    assert "at most one 'sp' per geometry" in body["detail"], body
    assert "the same geometry" in body["detail"], body


@PRODUCTS
def test_two_sps_on_a_rotated_copy_of_water_are_refused(client, product):
    _assert_duplicate(_two_sps(client, product, _BASE, _ROTATED), product)


@PRODUCTS
def test_two_sps_on_a_translated_and_rotated_copy_are_refused_in_either_order(client, product):
    _assert_duplicate(_two_sps(client, product, _BASE, _BOTH), product)
    _assert_duplicate(_two_sps(client, product, _BOTH, _BASE), product)


@PRODUCTS
def test_copies_written_to_different_precisions_are_still_one_structure(client, product):
    """A depositor's 8-decimal rotated copy beside a 6-decimal original: each carries its own rounding."""
    eight = _xyz(_moved(_WATER, rotation=_ROTATION, shift=_SHIFT), decimals=8)
    _assert_duplicate(_two_sps(client, product, _BASE, eight), product)


@PRODUCTS
def test_the_precision_of_either_deposit_counts_whichever_is_listed_first(client, product):
    """An 8-decimal geometry first and a 6-decimal copy second: the 6-decimal rounding sets the tolerance too."""
    eight = _xyz(_moved(_WATER, rotation=_ROTATION, shift=_SHIFT), decimals=8)
    # The 6-decimal copy is itself moved, so rounding it to six decimals leaves real noise (~3e-7 A)
    # against the 8-decimal one: a tolerance taken from the 8-decimal side alone (~1.7e-8 A) refuses to match.
    six = _xyz(_moved(_WATER, rotation=_rotation((0.0, 1.0, 1.0), 2.1), shift=(-0.3, 0.2, 0.9)), decimals=6)
    _assert_duplicate(_two_sps(client, product, eight, six), product)
    _assert_duplicate(_two_sps(client, product, six, eight), product)


@PRODUCTS
def test_a_moved_copy_at_twelve_decimals_is_one_structure(client, product):
    """The stored precision: the tightest tolerance the rule ever applies."""
    twelve = _xyz(_moved(_WATER, rotation=_ROTATION, shift=_SHIFT), decimals=12)
    _assert_duplicate(_two_sps(client, product, _xyz(_WATER, decimals=12), twelve), product)


@PRODUCTS
def test_three_copies_are_one_duplicate_group(client, product):
    resp = _post(
        client,
        product,
        {"s1": _sp(_BASE), "s2": _sp(_TRANSLATED), "s3": _sp(_ROTATED)},
        [("s1", "sp"), ("s2", "sp"), ("s3", "sp")],
    )
    assert resp.status_code == 422, resp.text[:800]
    assert len(resp.json()["context"]["sp_calculation_refs"]) == 3, resp.json()


@PRODUCTS
def test_a_water_with_a_real_geometry_change_is_not_a_duplicate(client, product):
    """The oxygen moved 0.083 A (the existing _G1/_G2 pair), then translated: still a different geometry."""
    changed = _xyz(_moved(_nudged(_WATER, 0, 2, 0.083), shift=_SHIFT))
    resp = _two_sps(client, product, _BASE, changed)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_a_change_one_decade_above_the_tolerance_is_not_a_duplicate(client, product):
    """6-decimal tolerance is ~1.7e-6 A; one atom moved 1e-5 A is above it (RMSD ~3e-6 A)."""
    changed = _xyz(_moved(_nudged(_WATER, 1, 1, 1e-5), rotation=_ROTATION, shift=_SHIFT))
    resp = _two_sps(client, product, _BASE, changed)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_a_change_inside_the_rounding_is_still_a_duplicate(client, product):
    """One atom one unit of the sixth decimal away: within the rounding of both deposits."""
    changed = _xyz(_moved(_nudged(_WATER, 1, 1, 1e-6), rotation=_ROTATION, shift=_SHIFT))
    _assert_duplicate(_two_sps(client, product, _BASE, changed), product)


@PRODUCTS
def test_the_same_atoms_in_another_order_are_one_structure(client, product):
    """#679: water listed hydrogen first is the same molecule at the same shape, so it is refused as a duplicate.

    #667 pinned the opposite (atoms are compared in the order given). The
    relabelling search lives in ``permuted_rigid_match`` and its API-level
    behaviour is exercised in ``test_api_permuted_duplicate_sp``.
    """
    reordered = _xyz([_WATER[1], _WATER[0], _WATER[2]])
    _assert_duplicate(_two_sps(client, product, _BASE, reordered), product)


@PRODUCTS
def test_a_second_moved_sp_that_is_stored_but_not_linked_is_not_compared(client, product):
    resp = _post(client, product, {"s1": _sp(_BASE), "s2": _sp(_TRANSLATED)}, [("s1", "sp")])
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_a_moved_copy_without_a_declared_geometry_is_not_compared(client, product):
    bare = _sp(_TRANSLATED)
    bare.pop("input_geometries")
    resp = _post(client, product, {"s1": _sp(_BASE), "s2": bare}, [("s1", "sp"), ("s2", "sp")])
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# Composites go through the same loop
# ---------------------------------------------------------------------------


@PRODUCTS
@pytest.mark.parametrize("moved", [_TRANSLATED, _ROTATED, _BOTH], ids=["translated", "rotated", "both"])
def test_two_composites_on_a_moved_copy_are_refused(client, product, moved):
    body = _assert_duplicate(
        _post(client, product, {"c1": _composite(_BASE), "c2": _composite(moved)}, [("c1", "composite"), ("c2", "composite")]),
        product,
        "composite_calculation_refs",
    )
    assert "'composite' links" in body["detail"], body


@PRODUCTS
def test_two_composites_on_a_moved_copy_by_output_geometry_are_refused(client, product):
    """A program-run composite links its geometry as an output only."""
    resp = _post(
        client,
        product,
        {"c1": _composite(_BASE, geometry="output"), "c2": _composite(_BOTH, geometry="output")},
        [("c1", "composite"), ("c2", "composite")],
    )
    _assert_duplicate(resp, product, "composite_calculation_refs")


@PRODUCTS
def test_two_composites_on_different_geometries_are_not_a_duplicate(client, product):
    changed = _xyz(_moved(_nudged(_WATER, 0, 2, 0.083), shift=_SHIFT))
    resp = _post(
        client, product, {"c1": _composite(_BASE), "c2": _composite(changed)}, [("c1", "composite"), ("c2", "composite")]
    )
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# Conformers of one molecule stay distinct
# ---------------------------------------------------------------------------


def _butane(torsion_deg: float, delta_deg: float = 0.0, decimals: int = 6) -> list[tuple[str, tuple[float, float, float]]]:
    """Butane at a given C-C-C-C torsion, atoms in one fixed order (hydrogens added after the carbons)."""
    from rdkit import Chem
    from rdkit.Chem import AllChem, rdMolTransforms

    mol = Chem.AddHs(Chem.MolFromSmiles("CCCC"))
    AllChem.EmbedMolecule(mol, randomSeed=7)
    conf = mol.GetConformer()
    rdMolTransforms.SetDihedralDeg(conf, 0, 1, 2, 3, torsion_deg + delta_deg)
    return [
        (atom.GetSymbol(), tuple(round(float(c), decimals) for c in conf.GetAtomPosition(atom.GetIdx())))
        for atom in mol.GetAtoms()
    ]


def _butane_post(client, product, first, second):
    url, payload = _water_payload(
        product, {"s1": _sp(first), "s2": _sp(second)}, [("s1", "sp"), ("s2", "sp")]
    )
    payload["species_entry"] = {"smiles": "CCCC", "charge": 0, "multiplicity": 1}
    if product == "statmech":
        payload["external_symmetry"] = 2
    return client.post(url, json=payload)


@PRODUCTS
def test_a_butane_with_two_hydrogens_listed_in_another_order_is_one_structure(client, product):
    """#679: the same species with interchangeable atoms of one element swapped in the listing is a duplicate."""
    atoms = _butane(180.0)
    swapped = list(atoms)
    swapped[4], swapped[10] = swapped[10], swapped[4]
    _assert_duplicate(_butane_post(client, product, _xyz(atoms), _xyz(swapped)), product)


@PRODUCTS
def test_two_butane_rotamers_are_not_a_duplicate(client, product):
    anti, gauche = _xyz(_butane(180.0)), _xyz(_butane(60.0))
    resp = _butane_post(client, product, anti, gauche)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_two_copies_of_one_butane_rotamer_are_a_duplicate(client, product):
    atoms = _butane(180.0)
    moved = _xyz(_moved(atoms, rotation=_ROTATION, shift=_SHIFT))
    _assert_duplicate(_butane_post(client, product, _xyz(atoms), moved), product)


@PRODUCTS
def test_a_rotamer_a_hundredth_of_a_degree_apart_is_a_different_conformer(client, product):
    """RMSD ~1e-4 A, a factor of ~100 above the 6-decimal tolerance: close, but not one structure."""
    near = _xyz(_moved(_butane(180.0, 0.01), rotation=_ROTATION, shift=_SHIFT))
    resp = _butane_post(client, product, _xyz(_butane(180.0)), near)
    assert resp.status_code == 201, resp.text[:800]


def _chbrclf(mirror: bool = False) -> list[tuple[str, tuple[float, float, float]]]:
    """Bromochlorofluoromethane (chiral), atoms in one fixed order; ``mirror`` gives the other enantiomer."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles("[C@H](F)(Cl)Br"))
    AllChem.EmbedMolecule(mol, randomSeed=11)
    conf = mol.GetConformer()
    sign = -1.0 if mirror else 1.0
    atoms = []
    for atom in mol.GetAtoms():
        p = conf.GetAtomPosition(atom.GetIdx())
        atoms.append((atom.GetSymbol(), (round(sign * p.x, 6), round(p.y, 6), round(p.z, 6))))
    return atoms


def _chiral_post(client, product, first, second):
    url, payload = _water_payload(product, {"s1": _sp(first), "s2": _sp(second)}, [("s1", "sp"), ("s2", "sp")])
    payload["species_entry"] = {"smiles": "[C@H](F)(Cl)Br", "charge": 0, "multiplicity": 1}
    if product == "statmech":
        payload["external_symmetry"] = 1
    return client.post(url, json=payload)


@PRODUCTS
def test_the_two_enantiomers_of_a_chiral_molecule_are_not_a_duplicate(client, product):
    """The mirror image, then rotated and moved: no rotation superposes it, so it is another structure."""
    first = _chbrclf()
    mirrored = _moved(_chbrclf(mirror=True), rotation=_ROTATION, shift=_SHIFT)
    resp = _chiral_post(client, product, _xyz(first), _xyz(mirrored))
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_one_enantiomer_moved_is_a_duplicate(client, product):
    first = _chbrclf()
    moved = _moved(first, rotation=_ROTATION, shift=_SHIFT)
    _assert_duplicate(_chiral_post(client, product, _xyz(first), _xyz(moved)), product)


# ---------------------------------------------------------------------------
# The service enforces it without the API in front: direct calls on stored rows
# ---------------------------------------------------------------------------


def _direct_call(db_session, geometries, *, isotopes=None, kind: str = "sp"):
    """Run ``assert_role_consistency`` on stored rows, one per ``[(element, (x, y, z)), ...]`` geometry."""
    from app.db.models.calculation import CalculationInputGeometry
    from app.db.models.common import CalculationType
    from app.db.models.geometry import GeometryAtom
    from app.services.calculation_levels import RoleLink, assert_role_consistency
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_geometry,
        make_lot,
        make_species,
        make_species_entry,
    )

    lot_id = make_lot(db_session).id
    entry_id = make_species_entry(db_session, make_species(db_session, smiles="O")).id
    links = []
    for index, atoms in enumerate(geometries):
        geometry = make_geometry(db_session, natoms=len(atoms))
        for atom_index, (element, (x, y, z)) in enumerate(atoms, start=1):
            mass = (isotopes or {}).get((index, atom_index))
            db_session.add(
                GeometryAtom(
                    geometry_id=geometry.id,
                    atom_index=atom_index,
                    element=element,
                    x=x,
                    y=y,
                    z=z,
                    isotope_mass_number=mass,
                )
            )
        db_session.flush()
        calc = make_calculation(
            db_session,
            type=CalculationType.sp if kind == "sp" else CalculationType.composite,
            lot_id=lot_id,
            species_entry_id=entry_id,
        )
        db_session.add(CalculationInputGeometry(calculation_id=calc.id, geometry_id=geometry.id, input_order=1))
        db_session.flush()
        db_session.refresh(calc)
        links.append(RoleLink(role=kind, calculation=calc))
    assert_role_consistency(
        links,
        None,
        duplicate_code="thermo_role_duplicate",
        geometry_mismatch_code="thermo_sp_geometry_mismatch",
        requires_sp_code="thermo_energy_level_requires_sp",
        contradiction_code="thermo_energy_level_contradiction",
        ambiguous_code="thermo_energy_level_ambiguous",
        sp_and_composite_code="thermo_energy_sp_and_composite_linked",
        subject="thermo",
        warnings=None,
    )


def _round(atoms, decimals=6):
    return [(el, tuple(round(v, decimals) for v in c)) for el, c in atoms]


def test_the_service_refuses_two_rows_that_are_one_moved_structure(db_session):
    from app.api.error_contract import CodedValueError

    moved = _round(_moved(_WATER, rotation=_ROTATION, shift=_SHIFT))
    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, [_round(_WATER), moved])
    assert raised.value.code == "thermo_role_duplicate"


def test_the_service_refuses_two_composite_rows_that_are_one_moved_structure(db_session):
    from app.api.error_contract import CodedValueError

    moved = _round(_moved(_WATER, shift=_SHIFT))
    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, [_round(_WATER), moved], kind="composite")
    assert raised.value.code == "thermo_role_duplicate"


def test_the_service_does_not_merge_a_real_change(db_session):
    _direct_call(db_session, [_round(_WATER), _round(_moved(_nudged(_WATER, 0, 2, 0.083), shift=_SHIFT))])


def test_the_service_does_not_merge_different_elements_in_one_place(db_session):
    """Same shape, one atom a different element: not one structure."""
    other = [("O", _WATER[0][1]), ("H", _WATER[1][1]), ("F", _WATER[2][1])]
    _direct_call(db_session, [_round(_WATER), _round(_moved(other, shift=_SHIFT))])


def test_the_service_does_not_merge_two_isotopologues_of_one_shape(db_session):
    """HDO beside H2O: the stated isotope is part of the atom, as on the atom path."""
    _direct_call(db_session, [_round(_WATER), _round(_moved(_WATER, shift=_SHIFT))], isotopes={(1, 2): 2})


def test_the_service_does_not_merge_a_mirror_image(db_session):
    """Alignment allows a proper rotation only: the two enantiomers of a chiral shape are different structures."""
    chiral = [
        ("C", (0.0, 0.0, 0.0)),
        ("F", (1.0, 0.0, 0.0)),
        ("Cl", (0.0, 1.5, 0.0)),
        ("Br", (0.0, 0.0, 1.9)),
        ("H", (-0.6, -0.6, -0.6)),
    ]
    mirror = [(el, (-x, y, z)) for el, (x, y, z) in chiral]
    _direct_call(db_session, [_round(chiral), _round(mirror)])


def test_the_service_does_not_merge_geometries_of_different_size(db_session):
    _direct_call(db_session, [_round(_WATER), _round(_WATER[:2])])


# ---------------------------------------------------------------------------
# The tolerance and its boundary, on the comparison itself
# ---------------------------------------------------------------------------


def _atoms_of(atoms, decimals=6):
    from app.services.calculation_levels import _AtomsOf, _coordinate_decimals

    coordinates = [tuple(round(v, decimals) for v in c) for _, c in atoms]
    return _AtomsOf(tuple((el, None) for el, _ in atoms), coordinates, _coordinate_decimals(coordinates))


def test_the_tolerance_is_the_summed_rounding_of_both_deposits():
    from app.services.calculation_levels import _rigid_motion_tolerance

    assert _rigid_motion_tolerance(6, 6) == pytest.approx(math.sqrt(3) * 1e-6)
    assert _rigid_motion_tolerance(12, 12) == pytest.approx(math.sqrt(3) * 1e-12)
    assert _rigid_motion_tolerance(6, 8) == pytest.approx(math.sqrt(3) / 2 * (1e-6 + 1e-8))
    # The widest the rule ever is: the four-decimal floor on both sides.
    assert _rigid_motion_tolerance(4, 4) == pytest.approx(math.sqrt(3) * 1e-4)


@pytest.mark.parametrize(
    ("coordinates", "decimals"),
    [
        ([(0.0, 0.0, 0.117000), (0.0, 0.757, -0.469)], 4),  # three decimals: held to the floor
        ([(0.123456, 0.0, 1.0)], 6),
        ([(0.12345678, 0.5, 1.0)], 8),
        ([(1 / 3, 2 / 3, 0.1)], 12),  # not written to any short precision: the stored 12
        ([(1.0, 2.0, 3.0)], 4),
        ([(0.0, 0.0, 0.0)], 4),
    ],
)
def test_the_decimals_a_geometry_was_written_to(coordinates, decimals):
    from app.services.calculation_levels import _coordinate_decimals

    assert _coordinate_decimals(coordinates) == decimals


def test_the_boundary_of_the_tolerance_on_the_comparison():
    """RMSD just under the tolerance is one structure; just over, two. Calibrated with the Kabsch RMSD itself."""
    from app.services.calculation_levels import _rigid_motion_tolerance, _same_polyatomic_structure

    base = _atoms_of(_WATER)
    tolerance = _rigid_motion_tolerance(6, 6)
    # RMSD is linear in the size of a small displacement of one atom: measure the slope, then place each side.
    slope = kabsch_rmsd(base.coordinates, _atoms_of(_nudged(_WATER, 1, 1, 1e-3), decimals=12).coordinates) / 1e-3
    for factor, expected in ((0.9, True), (1.1, False)):
        moved = _atoms_of(_moved(_nudged(_WATER, 1, 1, factor * tolerance / slope), rotation=_ROTATION, shift=_SHIFT), decimals=12)
        # ``moved`` is treated as written to six decimals, as the deposit under test would be.
        moved = moved._replace(decimals=6)
        assert _same_polyatomic_structure(base, moved) is expected, factor
    # And the loose side of the same fact: a tolerance 100 times wider would have called 10x-over the same.
    wide = _atoms_of(_moved(_nudged(_WATER, 1, 1, 10 * tolerance / slope), rotation=_ROTATION, shift=_SHIFT), decimals=12)
    assert _same_polyatomic_structure(base, wide._replace(decimals=6)) is False


def test_geometry_atoms_are_not_read_for_a_geometry_with_no_same_size_neighbour(db_session):
    """Cost: an atom list is loaded only for geometries that have a same-size geometry to be compared with."""
    from sqlalchemy import inspect

    from app.db.models.geometry import GeometryAtom
    from app.services.calculation_levels import _merge_rigidly_equal_geometries
    from tests.services.scientific_read._factories import make_geometry

    geometries = []
    for atoms in (_WATER, _WATER[:2], _WATER + _WATER[1:]):
        geometry = make_geometry(db_session, natoms=len(atoms))
        for atom_index, (element, (x, y, z)) in enumerate(atoms, start=1):
            db_session.add(GeometryAtom(geometry_id=geometry.id, atom_index=atom_index, element=element, x=x, y=y, z=z))
        geometries.append(geometry)
    db_session.flush()
    db_session.expire_all()
    roots = _merge_rigidly_equal_geometries(geometries)
    assert roots == {g.id: g.id for g in geometries}
    assert all("atoms" in inspect(g).unloaded for g in geometries)
