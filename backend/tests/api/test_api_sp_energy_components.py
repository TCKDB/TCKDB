"""Single-point energy components, end to end through the upload API (ADR 0021, P4).

``main`` (d22f2918) could not carry the parts of a single point's electronic
energy: ``sp_energy_components`` was an unknown field and was refused as an
extra input, so an SCF/correlation split had nowhere to go.

Rules held here, each over HTTP so the code and context reach the client:

* ``reference + correlation`` must equal the single point's energy within
  1e-6 Eh when all three are present, else ``sp_energy_components_do_not_sum``;
* a duplicate component is ``sp_energy_component_duplicate``;
* components on anything but a single point are ``sp_energy_component_not_on_sp``;
* a deposited ``total`` must equal the energy, else
  ``sp_energy_component_total_mismatch``;
* a refusal writes nothing, and what is stored is what was sent: TCKDB never
  fills in a part it could have computed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models.calculation import Calculation, CalculationSPEnergyComponent

ENERGY = -76.4
REFERENCE = -75.9


def _payload(components, *, energy=ENERGY, calc_type="sp", basis="cc-pCVTZ") -> dict:
    calculation: dict = {
        "type": calc_type,
        "software_release": {"name": "orca", "version": "6.0.1"},
        "level_of_theory": {"method": "CCSD(T)", "basis": basis},
        "sp_energy_components": components,
    }
    if energy is not None and calc_type == "sp":
        calculation["sp_result"] = {"electronic_energy_hartree": energy}
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": calculation,
        "label": "conf-a",
        "note": "test upload",
    }


def _component_rows(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(CalculationSPEnergyComponent))


def _parts(reference: float, correlation: float) -> list[dict]:
    return [
        {"component": "reference", "value_hartree": reference},
        {"component": "correlation", "value_hartree": correlation},
    ]


def test_a_single_point_carries_its_scf_and_correlation_parts_and_reads_them_back(client, db_session):
    correlation = ENERGY - REFERENCE
    resp = client.post("/api/v1/uploads/conformers", json=_payload(_parts(REFERENCE, correlation)))
    assert resp.status_code in (200, 201), resp.text[:800]

    calc = db_session.scalars(select(Calculation).order_by(Calculation.id.desc())).first()
    rows = db_session.execute(
        select(CalculationSPEnergyComponent.component, CalculationSPEnergyComponent.value_hartree).where(
            CalculationSPEnergyComponent.calculation_id == calc.id
        )
    ).all()
    # Stored exactly as sent, in no order the depositor chose.
    assert {c.value: v for c, v in rows} == {"reference": REFERENCE, "correlation": correlation}

    read = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}?include=results")
    assert read.status_code == 200, read.text[:800]
    sp = read.json()["record"]["results"]["sp"]
    assert sp["electronic_energy_hartree"] == ENERGY
    # Reference first: the order of the EnergyComponentKind enum, not the payload's.
    assert sp["energy_components"] == [
        {"component": "reference", "value_hartree": REFERENCE},
        {"component": "correlation", "value_hartree": correlation},
    ]
    # No row id or calculation id travels with a component.
    assert set(sp["energy_components"][0]) == {"component", "value_hartree"}


def test_a_single_point_without_components_reads_an_empty_list(client, db_session):
    resp = client.post("/api/v1/uploads/conformers", json=_payload([]))
    assert resp.status_code in (200, 201), resp.text[:800]
    calc = db_session.scalars(select(Calculation).order_by(Calculation.id.desc())).first()
    sp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}?include=results").json()["record"][
        "results"
    ]["sp"]
    assert sp["energy_components"] == []


# ---------------------------------------------------------------------------
# The sum check at its tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("off", [0.0, 0.9e-6, -0.9e-6])
def test_parts_that_agree_within_one_microhartree_are_accepted(client, db_session, off):
    resp = client.post(
        "/api/v1/uploads/conformers", json=_payload(_parts(REFERENCE, ENERGY - REFERENCE + off))
    )
    assert resp.status_code in (200, 201), resp.text[:800]


@pytest.mark.parametrize("off", [1.1e-6, -1.1e-6, 1e-3])
def test_parts_that_disagree_by_more_than_one_microhartree_are_refused_and_nothing_is_stored(
    client, db_session, off
):
    before = _component_rows(db_session)
    resp = client.post(
        "/api/v1/uploads/conformers", json=_payload(_parts(REFERENCE, ENERGY - REFERENCE + off))
    )
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "sp_energy_components_do_not_sum", body
    assert body["context"]["tolerance_hartree"] == 1e-6
    assert body["context"]["electronic_energy_hartree"] == ENERGY
    assert body["context"]["reference_hartree"] == REFERENCE
    assert _component_rows(db_session) == before


def test_without_the_energy_the_sum_cannot_be_checked_and_is_not_invented(client, db_session):
    resp = client.post("/api/v1/uploads/conformers", json=_payload(_parts(REFERENCE, -0.5), energy=None))
    assert resp.status_code in (200, 201), resp.text[:800]
    calc = db_session.scalars(select(Calculation).order_by(Calculation.id.desc())).first()
    assert calc.sp_result is None  # TCKDB did not store reference + correlation as the energy
    assert len(calc.sp_energy_components) == 2


def test_a_triples_part_makes_the_sum_rule_undecidable_so_it_is_not_applied(client, db_session):
    parts = [*_parts(REFERENCE, -0.4), {"component": "triples", "value_hartree": -0.1}]
    # reference + correlation (-76.3) differs from the energy (-76.4) by the triples part.
    resp = client.post("/api/v1/uploads/conformers", json=_payload(parts))
    assert resp.status_code in (200, 201), resp.text[:800]


# ---------------------------------------------------------------------------
# Duplicates, type, total
# ---------------------------------------------------------------------------


def test_a_duplicate_component_is_refused(client, db_session):
    before = _component_rows(db_session)
    parts = [*_parts(REFERENCE, ENERGY - REFERENCE), {"component": "reference", "value_hartree": REFERENCE}]
    resp = client.post("/api/v1/uploads/conformers", json=_payload(parts))
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "sp_energy_component_duplicate", body
    assert body["context"] == {"component": "reference"}
    assert _component_rows(db_session) == before


@pytest.mark.parametrize("calc_type", ["opt", "freq"])
def test_components_on_a_calculation_that_is_not_a_single_point_are_refused(client, db_session, calc_type):
    before = _component_rows(db_session)
    resp = client.post(
        "/api/v1/uploads/conformers", json=_payload(_parts(REFERENCE, -0.5), calc_type=calc_type)
    )
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "sp_energy_component_not_on_sp", body
    assert body["context"] == {"calculation_type": calc_type}
    assert _component_rows(db_session) == before


def test_a_total_equal_to_the_energy_is_accepted(client, db_session):
    parts = [{"component": "total", "value_hartree": ENERGY + 0.5e-6}]
    resp = client.post("/api/v1/uploads/conformers", json=_payload(parts))
    assert resp.status_code in (200, 201), resp.text[:800]


def test_a_total_that_differs_from_the_energy_is_refused(client, db_session):
    before = _component_rows(db_session)
    parts = [{"component": "total", "value_hartree": ENERGY + 2e-6}]
    resp = client.post("/api/v1/uploads/conformers", json=_payload(parts))
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "sp_energy_component_total_mismatch", body
    assert body["context"]["total_hartree"] == ENERGY + 2e-6
    assert _component_rows(db_session) == before


def test_a_non_finite_value_is_refused(client, db_session):
    parts = [{"component": "reference", "value_hartree": "NaN"}]
    resp = client.post("/api/v1/uploads/conformers", json=_payload(parts, energy=None))
    assert resp.status_code == 422, resp.text[:800]


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def _sp_calculation(client, db_session) -> Calculation:
    resp = client.post("/api/v1/uploads/conformers", json=_payload([], basis="cc-pVDZ"))
    assert resp.status_code in (200, 201), resp.text[:800]
    return db_session.scalars(select(Calculation).order_by(Calculation.id.desc())).first()


def test_the_database_allows_one_row_per_component(client, db_session):
    calc = _sp_calculation(client, db_session)
    statement = text(
        "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
        "VALUES (:c, CAST('reference' AS energy_component_kind), :v)"
    )
    db_session.execute(statement, {"c": calc.id, "v": -1.0})
    with pytest.raises(IntegrityError, match="pk_calc_sp_energy_component"):
        with db_session.begin_nested():
            db_session.execute(statement, {"c": calc.id, "v": -2.0})


@pytest.mark.parametrize("value", ["'NaN'::float8", "'Infinity'::float8", "'-Infinity'::float8"])
def test_the_database_refuses_a_non_finite_value(client, db_session, value):
    calc = _sp_calculation(client, db_session)
    with pytest.raises(IntegrityError, match="value_hartree_finite"):
        with db_session.begin_nested():
            db_session.execute(
                text(
                    "INSERT INTO calc_sp_energy_component (calculation_id, component, value_hartree) "
                    f"VALUES (:c, CAST('correlation' AS energy_component_kind), {value})"
                ),
                {"c": calc.id},
            )
