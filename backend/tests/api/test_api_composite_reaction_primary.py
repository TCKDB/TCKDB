"""A composite conformer primary on the computed-reaction route (ADR 0021, P3a).

The computed-species bundle and the conformer upload are covered in
``test_api_composite_program_run.py``. The reaction route has its own
``_persist_calculation`` and its own primary-type rule (the shared helper
``require_opt_primary_unless_monatomic``), so it is exercised here with the real
ARC fixture the monatomic-primary tests use: its hydrogen atom, with the
conformer's primary turned into a program-run composite.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationCompositeResult
from app.db.models.common import CalculationType
from app.db.models.statmech import StatmechSourceCalculation
from tests.api.test_api_bundle_monatomic_sp_primary import (
    _REACTION_URL,
    _h_species,
    _reaction_payload,
    _reshape_reaction_atom_to_sp_only,
)


def _composite_atom_payload() -> dict:
    payload = _reshape_reaction_atom_to_sp_only(_reaction_payload("rotor_scan_5"))
    atom = _h_species(payload)
    (conformer,) = atom["conformers"]
    calc = conformer["calculation"]
    energy = calc.pop("sp_electronic_energy_hartree")
    calc["type"] = "composite"
    calc["level_of_theory"] = {"method": "CBS-QB3"}
    calc["composite_result"] = {"assembly": "program_run", "electronic_energy_hartree": energy}
    atom["statmech"]["source_calculations"] = [{"calculation_key": calc["key"], "role": "composite"}]
    return payload


def test_a_composite_conformer_primary_is_accepted_and_carries_the_geometry(client, db_session):
    payload = _composite_atom_payload()
    resp = client.post(_REACTION_URL, json=payload)
    assert resp.status_code == 201, resp.text[:1500]

    composite = db_session.scalars(
        select(Calculation).where(Calculation.type == CalculationType.composite)
    ).one()
    assert db_session.get(CalculationCompositeResult, composite.id) is not None
    assert composite.conformer_observation_id is not None
    # The program run that produced the conformer's geometry carries it as its output.
    assert len(composite.output_geometries) == 1
    linked = db_session.execute(
        select(StatmechSourceCalculation.role).where(StatmechSourceCalculation.calculation_id == composite.id)
    ).scalars().all()
    assert [role.value for role in linked] == ["composite"]


def test_the_same_primary_of_another_type_is_still_refused_on_the_reaction_route(client):
    payload = _composite_atom_payload()
    atom = _h_species(payload)
    (conformer,) = atom["conformers"]
    two_atoms = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"
    conformer["geometry"]["xyz_text"] = two_atoms
    conformer["calculation"] = {
        "key": conformer["calculation"]["key"],
        "type": "freq",
        "software_release": conformer["calculation"]["software_release"],
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
    }
    resp = client.post(_REACTION_URL, json=payload)
    assert resp.status_code == 422, resp.text[:600]
    assert "must be 'opt'" in resp.text
