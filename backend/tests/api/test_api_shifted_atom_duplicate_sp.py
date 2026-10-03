"""The no-optimisation duplicate rule recognises a shifted copy of one atom (#623).

With no ``opt`` linked, two same-level ``sp`` links on one geometry are
refused (#610). That rule grouped the links by ``geometry_id``, so a second
single point on a *shifted* copy of the same atom (a different geometry row)
walked past it, on thermo and statmech alike. For one atom every position is
the same structure, so the links are grouped by element instead.

A polyatomic subject is untouched: a genuinely different geometry is still a
different structure. The polyatomic tests here were run against the rule as it
stood before the change and pass on both sides of it; they pin that.

A rigidly moved copy of a polyatomic geometry is the subject of #667 and is tested in
``test_api_rigid_motion_duplicate_sp.py``; the polyatomic tests here use geometries that differ in shape.
"""

from __future__ import annotations

import pytest

from tests.api.test_api_bundle_monatomic_duplicate_sp import (
    _REACTION_URL,
    _SCENARIO,
    _SPECIES_URL,
    _h_species,
    _reaction_payload,
    _reshape_reaction_atom_to_sp_only,
    _same_level_sp,
    _species_bundle_from_reaction_atom,
)
from tests.api.test_api_composite_role_consistency import (
    _G1,
    _G2,
    _composite,
    _sp,
)
from tests.api.test_api_composite_role_consistency import _payload as _water_payload

_ATOM = "1\nH atom\nH 0.0 0.0 0.0"
_ATOM_SHIFTED = "1\nH atom\nH 1.0 0.0 0.0"
_ATOM_SHIFTED_AGAIN = "1\nH atom\nH -2.5 0.75 0.0"
_HELIUM = "1\nHe atom\nHe 0.0 0.0 0.0"
_HYDROGEN_ATOM = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_OTHER_LEVEL = {"method": "CCSD(T)", "basis": "cc-pVTZ"}

PRODUCTS = pytest.mark.parametrize("product", ["thermo", "statmech"])


def _atom_payload(product: str, calcs: dict[str, dict], links: list[tuple[str, str]]) -> tuple[str, dict]:
    """The standalone upload for one hydrogen atom: the water helper with the atom's identity."""
    url, payload = _water_payload(product, calcs, links)
    payload["species_entry"] = dict(_HYDROGEN_ATOM)
    if product == "statmech":
        payload["external_symmetry"] = 1
    return url, payload


def _post(client, url: str, payload: dict):
    return client.post(url, json=payload)


def _assert_duplicate(resp, product: str, key: str) -> dict:
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == f"{product}_role_duplicate", body
    assert len(body["context"][key]) == 2, body
    return body


# ---------------------------------------------------------------------------
# One atom, standalone uploads
# ---------------------------------------------------------------------------


@PRODUCTS
def test_a_single_sp_on_an_atom_is_accepted(client, product):
    """The control: the shape every refusal below adds exactly one link to."""
    url, payload = _atom_payload(product, {"s1": _sp(_ATOM)}, [("s1", "sp")])
    resp = _post(client, url, payload)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_two_same_level_sps_on_one_atom_geometry_are_refused(client, product):
    """The case #610 already refused; it must keep refusing it."""
    url, payload = _atom_payload(product, {"s1": _sp(_ATOM), "s2": _sp(_ATOM)}, [("s1", "sp"), ("s2", "sp")])
    body = _assert_duplicate(_post(client, url, payload), product, "sp_calculation_refs")
    assert "the same atom" in body["detail"], body


@PRODUCTS
def test_two_same_level_sps_on_a_shifted_copy_of_an_atom_are_refused(client, product):
    """#623: the same atom moved to another coordinate is a different geometry row, not a second structure."""
    url, payload = _atom_payload(
        product, {"s1": _sp(_ATOM), "s2": _sp(_ATOM_SHIFTED)}, [("s1", "sp"), ("s2", "sp")]
    )
    body = _assert_duplicate(_post(client, url, payload), product, "sp_calculation_refs")
    assert "at most one 'sp' per atom" in body["detail"], body


@PRODUCTS
def test_three_sps_on_three_positions_of_an_atom_are_one_duplicate_group(client, product):
    """Every shifted copy joins the one group, not a group of its own."""
    url, payload = _atom_payload(
        product,
        {"s1": _sp(_ATOM), "s2": _sp(_ATOM_SHIFTED), "s3": _sp(_ATOM_SHIFTED_AGAIN)},
        [("s1", "sp"), ("s2", "sp"), ("s3", "sp")],
    )
    resp = _post(client, url, payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == f"{product}_role_duplicate", body
    assert len(body["context"]["sp_calculation_refs"]) == 3, body


@PRODUCTS
def test_two_sps_at_different_levels_on_a_shifted_atom_are_still_refused_as_a_duplicate(client, product):
    """Two energies for one atom were never allowed at two levels; the code now matches the same-geometry case.

    Before the change a shifted copy at a second level was refused as
    ``*_energy_level_ambiguous`` (the level-uniformity rule, R2') because the
    duplicate rule did not see it; the identical geometry at two levels has
    always been ``*_role_duplicate``, since distinctness is checked first.
    """
    url, payload = _atom_payload(
        product,
        {"s1": _sp(_ATOM), "s2": _sp(_ATOM_SHIFTED, _OTHER_LEVEL)},
        [("s1", "sp"), ("s2", "sp")],
    )
    _assert_duplicate(_post(client, url, payload), product, "sp_calculation_refs")
    url, payload = _atom_payload(
        product,
        {"s1": _sp(_ATOM), "s2": _sp(_ATOM, _OTHER_LEVEL)},
        [("s1", "sp"), ("s2", "sp")],
    )
    _assert_duplicate(_post(client, url, payload), product, "sp_calculation_refs")


@pytest.mark.parametrize("symbol", ["D", "T"])
@PRODUCTS
def test_a_shifted_copy_of_the_atom_spelled_d_or_t_is_still_the_same_atom(client, product, symbol):
    """A ``D``/``T`` atom is the same atom wherever it sits.

    Since #672 the spelling declares a nuclide (stored ``H`` + mass number 2/3),
    so the structure key is ``(H, 2)`` / ``(H, 3)``: two sps on a shifted copy
    of one D atom are one structure and refused as a duplicate, exactly like
    two on a shifted protium atom.
    """
    spelled = f"1\n{symbol} atom\n{symbol} 1.0 0.0 0.0"
    shifted = f"1\n{symbol} atom\n{symbol} -2.5 0.75 0.0"
    url, payload = _atom_payload(product, {"s1": _sp(spelled), "s2": _sp(shifted)}, [("s1", "sp"), ("s2", "sp")])
    _assert_duplicate(_post(client, url, payload), product, "sp_calculation_refs")


@pytest.mark.parametrize("symbol", ["D", "T"])
@PRODUCTS
def test_a_d_or_t_atom_is_not_the_same_structure_as_a_protium_atom(client, product, symbol):
    """It used to be, when ``D`` was isotope-silent; it is a different nuclide now.

    The key already told a stated ``H`` + mass 2 apart from plain ``H``
    (#610/#623); a ``D`` spelling is that same statement.
    """
    spelled = f"1\n{symbol} atom\n{symbol} 1.0 0.0 0.0"
    for first, second in ((_ATOM, spelled), (spelled, _ATOM)):
        url, payload = _atom_payload(product, {"s1": _sp(first), "s2": _sp(second)}, [("s1", "sp"), ("s2", "sp")])
        resp = _post(client, url, payload)
        assert resp.json().get("code") != f"{product}_role_duplicate", resp.text[:800]


@PRODUCTS
def test_a_second_sp_that_is_stored_but_not_linked_does_not_trip_the_rule(client, product):
    """Guard the guard: the rule is about links, not about how many sps are uploaded."""
    url, payload = _atom_payload(
        product, {"s1": _sp(_ATOM), "s2": _sp(_ATOM_SHIFTED)}, [("s1", "sp")]
    )
    resp = _post(client, url, payload)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_a_shifted_atom_sp_without_a_declared_geometry_is_not_compared(client, product):
    """Absence of evidence is not a match: an sp with no geometry cannot duplicate anything."""
    bare = _sp(_ATOM)
    bare.pop("input_geometries")
    url, payload = _atom_payload(product, {"s1": _sp(_ATOM), "s2": bare}, [("s1", "sp"), ("s2", "sp")])
    resp = _post(client, url, payload)
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# One atom, composite energies (R2' treats a composite like an sp)
# ---------------------------------------------------------------------------


@PRODUCTS
def test_two_composites_on_a_shifted_copy_of_an_atom_are_refused(client, product):
    url, payload = _atom_payload(
        product,
        {"c1": _composite(_ATOM), "c2": _composite(_ATOM_SHIFTED)},
        [("c1", "composite"), ("c2", "composite")],
    )
    body = _assert_duplicate(_post(client, url, payload), product, "composite_calculation_refs")
    assert "'composite' links" in body["detail"], body
    assert "the same atom" in body["detail"], body


@PRODUCTS
def test_two_composites_on_a_shifted_copy_of_an_atom_by_output_geometry_are_refused(client, product):
    """A program-run composite on an atom links its geometry as an output only."""
    url, payload = _atom_payload(
        product,
        {
            "c1": _composite(_ATOM, geometry="output"),
            "c2": _composite(_ATOM_SHIFTED, geometry="output"),
        },
        [("c1", "composite"), ("c2", "composite")],
    )
    _assert_duplicate(_post(client, url, payload), product, "composite_calculation_refs")


@PRODUCTS
def test_a_single_composite_on_an_atom_is_accepted(client, product):
    url, payload = _atom_payload(product, {"c1": _composite(_ATOM)}, [("c1", "composite")])
    resp = _post(client, url, payload)
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# Polyatomic: unchanged. These pass identically on the rule before #623.
# ---------------------------------------------------------------------------


@PRODUCTS
def test_two_sps_on_different_polyatomic_geometries_with_no_opt_are_not_a_duplicate(client, product):
    """A genuinely different polyatomic geometry is a different structure."""
    url, payload = _water_payload(product, {"s1": _sp(_G1), "s2": _sp(_G2)}, [("s1", "sp"), ("s2", "sp")])
    resp = _post(client, url, payload)
    assert resp.status_code == 201, resp.text[:800]


@PRODUCTS
def test_two_sps_on_one_polyatomic_geometry_with_no_opt_are_a_duplicate(client, product):
    url, payload = _water_payload(product, {"s1": _sp(_G1), "s2": _sp(_G1)}, [("s1", "sp"), ("s2", "sp")])
    body = _assert_duplicate(_post(client, url, payload), product, "sp_calculation_refs")
    assert "the same geometry" in body["detail"], body
    assert "at most one 'sp' per geometry" in body["detail"], body


@PRODUCTS
def test_two_polyatomic_sps_at_different_levels_stay_ambiguous_not_duplicate(client, product):
    """Different polyatomic geometries at two levels: the level rule answers, as before."""
    url, payload = _water_payload(
        product, {"s1": _sp(_G1), "s2": _sp(_G2, _OTHER_LEVEL)}, [("s1", "sp"), ("s2", "sp")]
    )
    resp = _post(client, url, payload)
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == f"{product}_energy_level_ambiguous", resp.json()


@PRODUCTS
def test_two_composites_on_different_polyatomic_geometries_with_no_opt_are_not_a_duplicate(client, product):
    url, payload = _water_payload(
        product, {"c1": _composite(_G1), "c2": _composite(_G2)}, [("c1", "composite"), ("c2", "composite")]
    )
    resp = _post(client, url, payload)
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# One atom, the species bundle and the reaction bundle
# ---------------------------------------------------------------------------


def _shifted(extra: dict) -> dict:
    extra["input_geometries"] = [{"xyz_text": _ATOM_SHIFTED}]
    return extra


def _species_route(product: str) -> tuple[str, dict]:
    bundle = _species_bundle_from_reaction_atom(_reaction_payload(_SCENARIO))
    conformer = bundle["conformers"][0]
    conformer["additional_calculations"] = [_shifted(_same_level_sp(conformer["primary_calculation"]))]
    bundle[product]["source_calculations"] = [
        {"calculation_key": conformer["primary_calculation"]["key"], "role": "sp"},
        {"calculation_key": "r_extra_sp", "role": "sp"},
    ]
    return _SPECIES_URL, bundle


def _reaction_route(product: str) -> tuple[str, dict]:
    payload = _reshape_reaction_atom_to_sp_only(_reaction_payload(_SCENARIO))
    atom = _h_species(payload)
    (conformer,) = atom["conformers"]
    extra = _shifted(_same_level_sp(conformer["calculation"]))
    # The reaction bundle anchors a non-opt calculation to a conformer geometry
    # by key; the explicit input geometry (the shifted atom) is what it ran on.
    extra["geometry_key"] = conformer["geometry"]["key"]
    extra["conformer_key"] = conformer["key"]
    atom["calculations"].append(extra)
    atom[product]["source_calculations"] = [
        {"calculation_key": conformer["calculation"]["key"], "role": "sp"},
        {"calculation_key": "r_extra_sp", "role": "sp"},
    ]
    return _REACTION_URL, payload


_ROUTES = {"species": _species_route, "reaction": _reaction_route}


@pytest.mark.parametrize("route", ["species", "reaction"])
@PRODUCTS
def test_a_shifted_copy_of_the_bundle_atom_is_refused(client, route, product):
    url, payload = _ROUTES[route](product)
    resp = client.post(url, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == f"{product}_role_duplicate", body
    assert "at most one 'sp' per atom" in body["detail"], body
    assert len(body["context"]["sp_calculation_refs"]) == 2, body


@pytest.mark.parametrize("route", ["species", "reaction"])
@PRODUCTS
def test_the_shifted_copy_is_stored_when_only_one_of_the_two_is_linked(client, route, product):
    url, payload = _ROUTES[route](product)
    species = payload if route == "species" else _h_species(payload)
    species[product]["source_calculations"] = species[product]["source_calculations"][:1]
    resp = client.post(url, json=payload)
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# The service enforces it without the API in front: direct calls on stored rows
# ---------------------------------------------------------------------------


def _direct_call(
    db_session,
    geometries: list[tuple[int, list[str]]],
    *,
    isotope: int | None = None,
    kind: str = "sp",
):
    """Run ``assert_role_consistency`` on stored sp rows, one per ``(natoms, elements)`` geometry."""
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
    entry_id = make_species_entry(db_session, make_species(db_session, smiles="[H]", multiplicity=2)).id
    links = []
    for index, (natoms, elements) in enumerate(geometries, start=1):
        geometry = make_geometry(db_session, natoms=natoms)
        for atom_index, element in enumerate(elements, start=1):
            # ``isotope`` applies to the second geometry only, so the pair differs in it.
            mass = isotope if index == 2 else None
            db_session.add(
                GeometryAtom(
                    geometry_id=geometry.id,
                    atom_index=atom_index,
                    element=element,
                    # Geometry ``index`` (1 or 2) puts atom ``k`` at x = index * k: the two polyatomic
                    # geometries differ in bond length, so they are different shapes. They used to sit
                    # on one point, which is one structure under the rigid-motion rule (#667).
                    x=float(index) * atom_index,
                    y=0.0,
                    z=0.0,
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


def test_the_service_refuses_two_sps_on_two_geometry_rows_of_one_atom(db_session):
    from app.api.error_contract import CodedValueError

    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, [(1, ["H"]), (1, ["H"])])
    assert raised.value.code == "thermo_role_duplicate"


def test_the_service_refuses_two_composites_on_two_geometry_rows_of_one_atom(db_session):
    from app.api.error_contract import CodedValueError

    with pytest.raises(CodedValueError) as raised:
        _direct_call(db_session, [(1, ["H"]), (1, ["H"])], kind="composite")
    assert raised.value.code == "thermo_role_duplicate"


def test_the_service_does_not_merge_two_different_elements(db_session):
    """The key is the element: a hydrogen and a helium are two structures, not one."""
    _direct_call(db_session, [(1, ["H"]), (1, ["He"])])


def test_the_service_does_not_merge_two_isotopes_of_one_element(db_session):
    """Hydrogen and deuterium are different atoms, though both are element H."""
    _direct_call(db_session, [(1, ["H"]), (1, ["H"])], isotope=2)


def test_the_service_does_not_merge_two_polyatomic_geometry_rows_of_a_different_shape(db_session):
    _direct_call(db_session, [(2, ["H", "H"]), (2, ["H", "H"])])


def test_the_service_does_not_merge_an_atom_with_a_polyatomic_geometry(db_session):
    _direct_call(db_session, [(1, ["H"]), (2, ["H", "H"])])
