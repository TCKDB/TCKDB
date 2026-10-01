"""Kinetics energy-level auto-resolution finds composites (ADR 0021, decision 4).

``energy_level_of_theory`` on a kinetics upload names the level the reactants'
and products' energies were computed at, and the server links the calculation
found there to the rate. It used to find only an ``sp``; a level bound to a
composite scheme (``CBS-QB3``) carries ``composite`` calculations, so a
CBS-QB3 rate could not be deposited at all. The priority is R1's: a composite at
the level wins, and only when there is none is the single point looked for.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, KineticsCalculationRole
from app.db.models.kinetics import KineticsSourceCalculation

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_CBS_QB3 = {"method": "CBS-QB3"}
_OTHER = {"method": "B3LYP", "basis": "6-31G(d)"}

_METHYL = ("[CH3]", 2, "4\nmethyl\nC 0.0 0.0 0.0\nH 1.08 0.0 0.0\nH -0.54 0.935 0.0\nH -0.54 -0.935 0.0")
_H_ATOM = ("[H]", 2, "1\nH atom\nH 0.0 0.0 0.0")
_METHANE = (
    "C",
    1,
    "5\nmethane\nC 0.0 0.0 0.0\nH 0.629 0.629 0.629\nH -0.629 -0.629 0.629\n"
    "H -0.629 0.629 -0.629\nH 0.629 -0.629 -0.629",
)


def _composite_calc(lot: dict | None = None) -> dict:
    return {
        "type": "composite",
        "software_release": _SOFTWARE,
        "level_of_theory": lot or _CBS_QB3,
        "composite_result": {"assembly": "program_run", "electronic_energy_hartree": -40.4},
    }


def _sp_calc(lot: dict) -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "sp_result": {"electronic_energy_hartree": -40.5},
    }


def _deposit(client, species, *, primary: dict, additional: list[dict] | None = None) -> dict:
    smiles, multiplicity, xyz = species
    payload: dict = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "geometry": {"xyz_text": xyz},
        "calculation": primary,
    }
    if additional:
        payload["additional_calculations"] = additional
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text[:500]
    return resp.json()


def _kinetics(client, *, energy_level: dict):
    return client.post(
        "/api/v1/uploads/kinetics",
        json={
            "reaction": {
                "reversible": False,
                "reactants": [
                    {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
                    {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
                ],
                "products": [{"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}}],
            },
            "scientific_origin": "computed",
            "energy_level_of_theory": energy_level,
            "a": 1.0e13,
            "a_units": "cm3_mol_s",
            "n": 0.0,
            "reported_ea": 0.0,
            "reported_ea_units": "kj_mol",
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "software_release": {"name": "Arkane", "version": "3.0"},
        },
    )


def _linked(db_session, kinetics_id: int) -> list[tuple[str, str, str]]:
    rows = db_session.execute(
        select(KineticsSourceCalculation.role, Calculation.type)
        .join(Calculation, Calculation.id == KineticsSourceCalculation.calculation_id)
        .where(KineticsSourceCalculation.kinetics_id == kinetics_id)
    ).all()
    return sorted((role.value, calc_type.value) for role, calc_type in rows)


def test_the_energy_level_resolves_to_the_composite_calculation_of_each_species(client, db_session):
    for species in (_METHYL, _H_ATOM, _METHANE):
        _deposit(client, species, primary=_composite_calc())
    resp = _kinetics(client, energy_level=_CBS_QB3)
    assert resp.status_code == 201, resp.text[:600]
    assert _linked(db_session, resp.json()["id"]) == [
        (KineticsCalculationRole.product_energy.value, CalculationType.composite.value),
        (KineticsCalculationRole.reactant_energy.value, CalculationType.composite.value),
        (KineticsCalculationRole.reactant_energy.value, CalculationType.composite.value),
    ]


def test_a_composite_wins_over_an_sp_at_the_same_level(client, db_session):
    """R1's priority: composite > sp. The sp stays stored; only the composite is linked."""
    for species in (_METHYL, _H_ATOM, _METHANE):
        _deposit(
            client,
            species,
            primary=_composite_calc(),
            additional=[_sp_calc(_CBS_QB3)],
        )
    resp = _kinetics(client, energy_level=_CBS_QB3)
    assert resp.status_code == 201, resp.text[:600]
    assert {calc_type for _role, calc_type in _linked(db_session, resp.json()["id"])} == {"composite"}


def test_without_a_composite_the_sp_at_the_level_is_still_found(client, db_session):
    """The pre-existing behaviour, unchanged: a single point at the declared level."""
    for species in (_METHYL, _H_ATOM, _METHANE):
        _deposit(client, species, primary=_sp_calc(_OTHER))
    resp = _kinetics(client, energy_level=_OTHER)
    assert resp.status_code == 201, resp.text[:600]
    assert {calc_type for _role, calc_type in _linked(db_session, resp.json()["id"])} == {"sp"}


def test_two_composites_at_the_level_cannot_be_auto_resolved(client):
    _deposit(client, _METHYL, primary=_composite_calc())
    _deposit(client, _METHYL, primary=_composite_calc())
    _deposit(client, _H_ATOM, primary=_composite_calc())
    _deposit(client, _METHANE, primary=_composite_calc())
    resp = _kinetics(client, energy_level=_CBS_QB3)
    assert resp.status_code == 422, resp.text[:600]
    assert "Multiple composite calculations" in resp.text


def test_nothing_at_the_level_is_still_the_old_refusal(client):
    for species in (_METHYL, _H_ATOM, _METHANE):
        _deposit(client, species, primary=_sp_calc(_OTHER))
    resp = _kinetics(client, energy_level=_CBS_QB3)
    assert resp.status_code == 422, resp.text[:600]
    assert "No SP calculation found" in resp.text


def test_an_explicit_energy_role_may_cite_a_composite_calculation(client):
    """The same widening on the explicit path: the role accepts sp or composite."""
    from app.services.kinetics_resolution import _KINETICS_ROLE_COMPATIBILITY

    for role in (
        KineticsCalculationRole.reactant_energy,
        KineticsCalculationRole.product_energy,
        KineticsCalculationRole.ts_energy,
    ):
        assert _KINETICS_ROLE_COMPATIBILITY[role]["calculation_types"] == {
            CalculationType.sp,
            CalculationType.composite,
        }
    # And nothing else widened.
    assert _KINETICS_ROLE_COMPATIBILITY[KineticsCalculationRole.freq]["calculation_types"] == {CalculationType.freq}
