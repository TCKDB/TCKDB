"""Frozen-core versus all-electron as part of a level of theory, end to end (ADR 0021, P4).

Reproduction on ``main`` (d22f2918): a frozen-core and an all-electron
CCSD(T)/cc-pCVTZ deposit were one level of theory, because nothing in the
payload could tell them apart, and ``core_treatment`` itself was refused as an
extra input. A focal-point scheme's core-valence term is the difference between
exactly those two, so they must be two rows.

Held here:

* the two treatments, and "not stated", are three rows; the same treatment
  twice is one row;
* an upload that does not state it still lands on the row it always did;
* the field is read back on the level-of-theory detail and on the level summary
  inside a calculation read.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models.calculation import Calculation
from app.db.models.level_of_theory import LevelOfTheory


def _payload(core_treatment: str | None) -> dict:
    level = {"method": "CCSD(T)", "basis": "cc-pCVTZ"}
    if core_treatment is not None:
        level["core_treatment"] = core_treatment
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "orca", "version": "6.0.1"},
            "level_of_theory": level,
        },
        "label": "conf-a",
        "note": "test upload",
    }


def _levels(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(LevelOfTheory))


def _last_calculation(db_session) -> Calculation:
    return db_session.scalars(select(Calculation).order_by(Calculation.id.desc())).first()


def test_frozen_core_all_electron_and_unstated_are_three_levels_and_one_is_one(client, db_session):
    before = _levels(db_session)
    refs = {}
    for core in ("frozen_core", "all_electron", None, "frozen_core", None):
        resp = client.post("/api/v1/uploads/conformers", json=_payload(core))
        assert resp.status_code in (200, 201), resp.text[:800]
        refs.setdefault(core, set()).add(_last_calculation(db_session).lot_id)
    # Three distinct levels from five uploads: each treatment resolves to one row.
    assert _levels(db_session) - before == 3
    assert all(len(ids) == 1 for ids in refs.values()), refs
    assert len({next(iter(ids)) for ids in refs.values()}) == 3


def test_core_treatment_is_stored_on_the_level_and_unstated_stays_null(client, db_session):
    client.post("/api/v1/uploads/conformers", json=_payload("all_electron"))
    stated = db_session.get(LevelOfTheory, _last_calculation(db_session).lot_id)
    client.post("/api/v1/uploads/conformers", json=_payload(None))
    unstated = db_session.get(LevelOfTheory, _last_calculation(db_session).lot_id)
    assert stated.core_treatment.value == "all_electron"
    assert unstated.core_treatment is None


def test_an_unknown_core_treatment_is_refused(client, db_session):
    before = _levels(db_session)
    resp = client.post("/api/v1/uploads/conformers", json=_payload("windowed"))
    assert resp.status_code == 422, resp.text[:800]
    assert _levels(db_session) == before


def test_the_level_detail_and_the_calculation_read_show_it(client, db_session):
    client.post("/api/v1/uploads/conformers", json=_payload("frozen_core"))
    calc = _last_calculation(db_session)
    lot = db_session.get(LevelOfTheory, calc.lot_id)

    detail = client.get(f"/api/v1/scientific/level-of-theories/{lot.public_ref}")
    assert detail.status_code == 200, detail.text[:800]
    assert detail.json()["record"]["level_of_theory"]["core_treatment"] == "frozen_core"

    read = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert read.status_code == 200, read.text[:800]
    assert read.json()["record"]["level_of_theory"]["core_treatment"] == "frozen_core"


def test_an_unstated_level_reads_null_not_a_default(client, db_session):
    client.post("/api/v1/uploads/conformers", json=_payload(None))
    calc = _last_calculation(db_session)
    lot = db_session.get(LevelOfTheory, calc.lot_id)
    detail = client.get(f"/api/v1/scientific/level-of-theories/{lot.public_ref}").json()
    assert "core_treatment" in detail["record"]["level_of_theory"]
    assert detail["record"]["level_of_theory"]["core_treatment"] is None
