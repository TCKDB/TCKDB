"""The no-optimisation duplicate rule recognises the same molecule with its atoms listed in another order (#679).

#667/#676 recognised a rigidly moved copy but compared atoms in the order
given, so listing the same geometry's atoms in another order walked past the
rule. The rule now searches for a relabelling that lays one geometry on the
other (``app.chemistry.permuted_rigid_match``) and then applies the same Kabsch
test and tolerance; it never exchanges atoms of different element or isotope,
never lets a mirror image match, and is bounded: past a cap the pair is
*different* (the behaviour before the search).

API tests drive the standalone thermo and statmech uploads; the service tests
call ``assert_role_consistency`` on stored rows directly, which is the same
code with no schema validation in front.
"""

from __future__ import annotations

import random

import pytest

from app.api.error_contract import CodedValueError
from app.chemistry import permuted_rigid_match as prm
from tests.api.test_api_composite_role_consistency import _composite, _sp
from tests.api.test_api_composite_role_consistency import _payload as _water_payload
from tests.api.test_api_rigid_motion_duplicate_sp import (
    _ROTATION,
    _SHIFT,
    _WATER,
    _assert_duplicate,
    _direct_call,
    _moved,
    _round,
    _xyz,
)
from tests.services.test_permuted_rigid_match import _butane, _chbrclf, _embedded, _staggered_ethane

PRODUCTS = pytest.mark.parametrize("product", ["thermo", "statmech"])


def _reordered(atoms, seed: int, *, moved: bool = True, decimals: int = 6):
    """``atoms`` in a shuffled order, rotated and shifted, rounded as a deposit would be."""
    order = list(range(len(atoms)))
    random.Random(seed).shuffle(order)
    shuffled = [atoms[i] for i in order]
    if moved:
        shuffled = _moved(shuffled, rotation=_ROTATION, shift=_SHIFT)
    return [(el, tuple(round(v, decimals) for v in c)) for el, c in shuffled], order


def _post(client, product, first, second, *, smiles: str, symmetry: int, kind: str = "sp"):
    make = _sp if kind == "sp" else _composite
    url, payload = _water_payload(
        product, {"s1": make(_xyz(first)), "s2": make(_xyz(second))}, [("s1", kind), ("s2", kind)]
    )
    payload["species_entry"] = {"smiles": smiles, "charge": 0, "multiplicity": 1}
    if product == "statmech":
        payload["external_symmetry"] = symmetry
    return client.post(url, json=payload)


# ---------------------------------------------------------------------------
# A relabelled copy is a duplicate
# ---------------------------------------------------------------------------


@PRODUCTS
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_water_in_another_order_moved_and_rotated_is_refused(client, product, seed):
    first = _round(_WATER)
    second, _ = _reordered(_WATER, seed)
    _assert_duplicate(_post(client, product, first, second, smiles="O", symmetry=2), product)


@PRODUCTS
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_ethanol_in_another_order_is_refused(client, product, seed):
    ethanol = _round(_embedded("CCO"))
    second, _ = _reordered(ethanol, seed)
    _assert_duplicate(_post(client, product, ethanol, second, smiles="CCO", symmetry=1), product)


@PRODUCTS
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_methyl_hydrogens_listed_in_another_order_are_refused(client, product, seed):
    """Ethane: the three H of a CH3 are interchangeable, so several relabellings superpose and any one is enough."""
    ethane = _round(_staggered_ethane())
    second, _ = _reordered(ethane, seed)
    _assert_duplicate(_post(client, product, ethane, second, smiles="CC", symmetry=6), product)


@PRODUCTS
def test_two_composites_on_a_relabelled_copy_are_refused(client, product):
    ethanol = _round(_embedded("CCO"))
    second, _ = _reordered(ethanol, 4)
    resp = _post(client, product, ethanol, second, smiles="CCO", symmetry=1, kind="composite")
    _assert_duplicate(resp, product, "composite_calculation_refs")


# ---------------------------------------------------------------------------
# What stays distinct
# ---------------------------------------------------------------------------


@PRODUCTS
def test_an_enantiomer_in_another_order_is_accepted(client, product):
    """The mirror image, shuffled, rotated and moved: a proper rotation never superposes it."""
    first = _round(_chbrclf())
    mirrored, _ = _reordered(_chbrclf(mirror=True), 3)
    resp = _post(client, product, first, mirrored, smiles="[C@H](F)(Cl)Br", symmetry=1)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_the_same_enantiomer_in_another_order_is_refused(client, product):
    """The control for the test above: the very molecule, shuffled, is the duplicate."""
    first = _round(_chbrclf())
    again, _ = _reordered(_chbrclf(), 3)
    _assert_duplicate(_post(client, product, first, again, smiles="[C@H](F)(Cl)Br", symmetry=1), product)


@PRODUCTS
def test_two_butane_rotamers_in_another_order_are_accepted(client, product):
    anti = _round(_butane(180.0))
    gauche, _ = _reordered(_butane(60.0), 5)
    resp = _post(client, product, anti, gauche, smiles="CCCC", symmetry=2)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_the_same_butane_rotamer_in_another_order_is_refused(client, product):
    anti = _round(_butane(180.0))
    again, _ = _reordered(_butane(180.0), 5)
    _assert_duplicate(_post(client, product, anti, again, smiles="CCCC", symmetry=2), product)


@PRODUCTS
def test_a_change_past_the_rounding_in_another_order_is_accepted(client, product):
    ethanol = _embedded("CCO")
    nudged = [(el, (x, y + (1e-4 if i == 3 else 0.0), z)) for i, (el, (x, y, z)) in enumerate(ethanol)]
    second, _ = _reordered(nudged, 2)
    resp = _post(client, product, _round(ethanol), second, smiles="CCO", symmetry=1)
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# The cap: past it a pair is different, the behaviour before the search
# ---------------------------------------------------------------------------


@PRODUCTS
def test_a_search_that_hits_the_cap_falls_back_to_different(client, product, monkeypatch):
    first = _round(_WATER)
    second, _ = _reordered(_WATER, 2)
    _assert_duplicate(_post(client, product, first, second, smiles="O", symmetry=2), product)
    monkeypatch.setattr(prm, "MAX_FITS_PER_PAIR", 0)
    resp = _post(client, product, first, second, smiles="O", symmetry=2)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_the_same_order_path_is_not_behind_the_cap(client, product, monkeypatch):
    """A moved copy in the order given is #676's comparison and needs no search, so the cap cannot reach it."""
    monkeypatch.setattr(prm, "MAX_FITS_PER_PAIR", 0)
    monkeypatch.setattr(prm, "MAX_ATOMS", 0)
    ethanol = _round(_embedded("CCO"))
    second, _ = _reordered(ethanol, 1)
    unshuffled = _round(_moved(ethanol, rotation=_ROTATION, shift=_SHIFT))
    _assert_duplicate(_post(client, product, ethanol, unshuffled, smiles="CCO", symmetry=1), product)
    # whereas the shuffled one is behind it
    assert _post(client, product, ethanol, second, smiles="CCO", symmetry=1).status_code == 201


# ---------------------------------------------------------------------------
# The service, with no schema validation in front: direct calls on stored rows
# ---------------------------------------------------------------------------


def test_the_service_refuses_a_relabelled_copy_of_water(db_session):
    second, _ = _reordered(_WATER, 1)
    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, [_round(_WATER), second])
    assert raised.value.code == "thermo_role_duplicate"


def test_the_service_refuses_a_relabelled_copy_for_composites(db_session):
    second, _ = _reordered(_embedded("CCO"), 1)
    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, [_round(_embedded("CCO")), second], kind="composite")
    assert raised.value.code == "thermo_role_duplicate"


def test_the_service_refuses_exchanged_methyl_hydrogens(db_session):
    ethane = _round(_staggered_ethane())
    cycled = list(ethane)
    cycled[2], cycled[3], cycled[4] = ethane[3], ethane[4], ethane[2]
    with pytest.raises(CodedValueError):
        _direct_call(db_session, [ethane, cycled])


def test_the_service_does_not_merge_a_relabelled_mirror_image(db_session):
    mirrored, _ = _reordered(_chbrclf(mirror=True), 2)
    _direct_call(db_session, [_round(_chbrclf()), mirrored])


def test_the_service_does_not_merge_conformers_in_another_order(db_session):
    second, _ = _reordered(_butane(60.0), 2)
    _direct_call(db_session, [_round(_butane(180.0)), second])


def test_the_service_does_not_merge_two_isotopomers_at_one_geometry(db_session):
    """Ethanol with the deuterium on the oxygen beside ethanol with it on the carbon: one geometry, two molecules."""
    ethanol = _round(_embedded("CCO"))
    hydroxyl_h = max(i for i, (el, _) in enumerate(ethanol) if el == "H")
    methyl_h = min(i for i, (el, _) in enumerate(ethanol) if el == "H")
    second, order = _reordered(ethanol, 3)

    def stated(deuterated: int, positions: list[int], geometry: int):
        return {(geometry, k + 1): 2 for k, source in enumerate(positions) if source == deuterated}

    isotopes = {**stated(hydroxyl_h, list(range(len(ethanol))), 0), **stated(methyl_h, order, 1)}
    _direct_call(db_session, [ethanol, second], isotopes=isotopes)


def test_the_service_refuses_one_isotopomer_listed_in_another_order(db_session):
    """The control for the test above: the same deuterium site, shuffled, is the duplicate."""
    ethanol = _round(_embedded("CCO"))
    hydroxyl_h = max(i for i, (el, _) in enumerate(ethanol) if el == "H")
    second, order = _reordered(ethanol, 3)
    isotopes = {(0, hydroxyl_h + 1): 2, **{(1, k + 1): 2 for k, source in enumerate(order) if source == hydroxyl_h}}
    with pytest.raises(CodedValueError):
        _direct_call(db_session, [ethanol, second], isotopes=isotopes)


def test_the_service_reads_a_d_spelling_as_deuterium_in_another_order(db_session):
    """A row written before the stated-isotope column holds ``D`` with no mass number; it is 2H, never an H."""
    ethanol = _round(_embedded("CCO"))
    hydroxyl_h = max(i for i, (el, _) in enumerate(ethanol) if el == "H")
    second, order = _reordered(ethanol, 3)
    legacy = list(ethanol)
    legacy[hydroxyl_h] = ("D", legacy[hydroxyl_h][1])
    legacy_second = [("D", c) if source == hydroxyl_h else (el, c) for (el, c), source in zip(second, order, strict=True)]
    stated = {(1, k + 1): 2 for k, source in enumerate(order) if source == hydroxyl_h}
    # D beside the same atom stated as 2H, in another order: one structure.
    with pytest.raises(CodedValueError):
        _direct_call(db_session, [legacy, second], isotopes=stated)
    # D on the oxygen against a plain H on the oxygen (and D nowhere else): different molecules.
    _direct_call(db_session, [legacy, second])
    # Both spelt D, in another order: one structure.
    with pytest.raises(CodedValueError):
        _direct_call(db_session, [legacy, legacy_second])


def test_the_service_treats_a_search_past_the_cap_as_different(db_session, monkeypatch):
    second, _ = _reordered(_WATER, 1)
    monkeypatch.setattr(prm, "MAX_FITS_PER_PAIR", 0)
    _direct_call(db_session, [_round(_WATER), second])


def test_the_service_does_not_search_a_geometry_over_the_atom_cap(db_session, monkeypatch):
    monkeypatch.setattr(prm, "MAX_ATOMS", 2)
    second, _ = _reordered(_WATER, 1)
    _direct_call(db_session, [_round(_WATER), second])


def test_the_service_spends_one_budget_across_a_records_geometries(db_session, monkeypatch):
    """Three relabelled copies of one molecule with a one-fit budget: the first pair uses it, the rest are not searched."""
    monkeypatch.setattr(prm, "MAX_FITS_PER_RECORD", 1)
    ethanol = _round(_embedded("CCO"))
    copies = [ethanol, _reordered(ethanol, 1)[0], _reordered(ethanol, 2)[0]]
    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, copies)
    assert raised.value.code == "thermo_role_duplicate"
    # The refusal names the two the one fit could join; with the budget it needs for the third, all three are named.
    assert len(raised.value.context["sp_calculation_refs"]) == 2


def test_the_service_names_all_three_copies_with_the_default_budget(db_session):
    ethanol = _round(_embedded("CCO"))
    copies = [ethanol, _reordered(ethanol, 1)[0], _reordered(ethanol, 2)[0]]
    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, copies)
    assert len(raised.value.context["sp_calculation_refs"]) == 3
