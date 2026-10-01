"""Wire guards on ``level_of_theory.method`` (ADR 0021).

* ``energy//geometry`` in ``method`` is refused with
  ``level_of_theory_method_is_compound``: it is two levels of theory, not one
  method. Reproduction on ``main`` (f22d3a80): the same payloads returned 201
  and stored a level named ``ccsd(t)-f12/cc-pvtz-f12//b3lyp/def2tzvp``.
* A named composite method followed by a correction-table label
  (``cbs-qb3-paraskevas``) or a year (``cbsqb32023``) is accepted with a
  ``level_of_theory_method_names_correction_table`` warning, stored as sent and
  never aliased to the method.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models.level_of_theory import LevelOfTheory


def _payload(method: str, **level) -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "gaussian", "version": "16"},
            "level_of_theory": {"method": method, **level},
        },
        "label": "conf-a",
        "note": "test upload",
    }


def _lot_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(LevelOfTheory))


@pytest.mark.parametrize(
    "method",
    [
        "ccsd(t)-f12/cc-pvtz-f12//b3lyp/def2tzvp",
        "DLPNO-CCSD(T)-F12//wB97XD",
        "b3lyp//",
        "//b3lyp",
        "  cbs-qb3 // b3lyp  ",
    ],
)
def test_a_compound_method_is_refused_with_its_code(client, db_session, method):
    before = _lot_count(db_session)
    resp = client.post("/api/v1/uploads/conformers", json=_payload(method))
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "level_of_theory_method_is_compound", body
    assert body["context"]["field"] == "method"
    assert "separate calculations" in body["detail"][0]["msg"]
    assert _lot_count(db_session) == before  # nothing was written


def test_a_single_slash_is_not_compound(client):
    """Only ``//`` is refused; a lone slash is not an energy//geometry pair."""
    resp = client.post("/api/v1/uploads/conformers", json=_payload("b3lyp/6-31g"))
    assert resp.status_code == 201, resp.text[:800]


@pytest.mark.parametrize("method", ["cbs-qb3-paraskevas", "CBS-QB3-Paraskevas", "cbsqb32023"])
def test_a_correction_table_name_warns_and_is_stored_as_sent(client, db_session, method):
    resp = client.post("/api/v1/uploads/conformers", json=_payload(method))
    assert resp.status_code == 201, resp.text[:800]
    matching = [
        w
        for w in resp.json()["warnings"]
        if w["code"] == "level_of_theory_method_names_correction_table"
    ]
    assert len(matching) == 1, resp.json()["warnings"]
    assert matching[0]["field"] == "calculation.level_of_theory.method"
    assert "energy correction scheme" in matching[0]["message"]

    stored = db_session.scalars(select(LevelOfTheory).where(LevelOfTheory.method == method)).all()
    assert len(stored) == 1  # kept as sent
    cbs = db_session.scalars(select(LevelOfTheory).where(LevelOfTheory.method == "cbs-qb3")).all()
    assert cbs == []  # and not aliased onto the method it is a table for


@pytest.mark.parametrize("method", ["CBS-QB3", "cbsqb3", "G4(MP2)", "w1bd", "W1-BD", "b3lyp"])
def test_ordinary_methods_do_not_warn(client, method):
    resp = client.post("/api/v1/uploads/conformers", json=_payload(method))
    assert resp.status_code == 201, resp.text[:800]
    assert [
        w for w in resp.json()["warnings"] if w["code"].startswith("level_of_theory_method")
    ] == []

