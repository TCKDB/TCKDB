"""The kinetics read reports the transition state's own energy calculation.

``provenance.ts_sp_calculation_ref`` used to be the first single point found under
*any* source role, so a reactant's single point (role ``reactant_energy``) was
reported as the transition state's whenever it was listed first -- on the
unmodified ARC ``rotor_scan_1`` bundle too, not only for a composite. It is now
the calculation cited under ``ts_energy``, a single point or a composite (ADR
0021), and ``levels.energy_source`` names which.
"""

from __future__ import annotations

import copy

from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType
from tests.api.test_api_bundle_monatomic_sp_primary import _REACTION_URL, _reaction_payload


def _bundle(*, composite: bool) -> dict:
    payload = copy.deepcopy(_reaction_payload("rotor_scan_1"))
    if composite:
        (ts_sp,) = [c for c in payload["transition_state"]["calculations"] if c["key"] == "ts_sp"]
        energy = ts_sp.pop("sp_electronic_energy_hartree")
        ts_sp["type"] = "composite"
        ts_sp["level_of_theory"] = {"method": "CBS-QB3"}
        ts_sp["composite_result"] = {"assembly": "program_run", "electronic_energy_hartree": energy}
        ts_sp.pop("artifacts", None)
    return payload


def _read_provenance(client, db_session, payload: dict) -> tuple[dict, dict]:
    resp = client.post(_REACTION_URL, json=payload)
    assert resp.status_code == 201, resp.text[:1200]
    entry_id = resp.json()["reaction_entry_id"]
    read = client.get(f"/api/v1/scientific/reaction-entries/{entry_id}/kinetics")
    assert read.status_code == 200, read.text[:800]
    record = read.json()["records"][0]
    return record["provenance"], record["levels"]


def _ref_of(db_session, *types: CalculationType, ts_owned: bool) -> str:
    owner = Calculation.transition_state_entry_id if ts_owned else Calculation.species_entry_id
    (calc,) = db_session.scalars(
        select(Calculation).where(Calculation.type.in_(types), owner.is_not(None))
    ).all()
    return calc.public_ref


def test_an_sp_ts_energy_is_the_one_cited_under_ts_energy_not_a_reactants(client, db_session):
    provenance, levels = _read_provenance(client, db_session, _bundle(composite=False))
    ts_sp_ref = _ref_of(db_session, CalculationType.sp, ts_owned=True)
    assert provenance["ts_sp_calculation_ref"] == ts_sp_ref
    assert levels["energy_source"] == "sp"
    assert levels["energy"]["method"].lower() == "dlpno-ccsd(t)-f12"


def test_a_composite_ts_energy_is_read_back_as_the_composite(client, db_session):
    provenance, levels = _read_provenance(client, db_session, _bundle(composite=True))
    ts_ref = _ref_of(db_session, CalculationType.composite, ts_owned=True)
    assert provenance["ts_sp_calculation_ref"] == ts_ref
    assert levels["energy_source"] == "composite"
    assert levels["energy"]["method"] == "CBS-QB3"
    assert levels["energy"]["composite_scheme"]["name"] == "CBS-QB3"
