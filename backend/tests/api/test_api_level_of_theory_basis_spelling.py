"""Two spellings of one basis set upload to one level of theory (#574).

Through the real upload route, because the defect was in what the route
stored: Psi4 writes ``def2-tzvp`` and Gaussian/ARC write ``def2tzvp``, and
before #574 the same method at those two spellings became two
``level_of_theory`` rows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.level_of_theory import LevelOfTheory

_URL = "/api/v1/uploads/conformers"


def _payload(method: str, basis: str, label: str) -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": method, "basis": basis},
        },
        "label": label,
    }


def _upload(client, method: str, basis: str, label: str) -> None:
    response = client.post(_URL, json=_payload(method, basis, label))
    assert response.status_code == 201, response.text


def _rows(db_session, method: str) -> list[LevelOfTheory]:
    return list(
        db_session.scalars(
            select(LevelOfTheory).where(LevelOfTheory.method == method)
        ).all()
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("def2-tzvp", "def2tzvp"),  # Psi4, then ARC/Gaussian (#572)
        ("Def2TZVP", "def2-TZVP"),  # Gaussian, then ORCA
        ("cc-pVTZ", "cc-pvtz"),  # Molpro, then Psi4
    ],
)
def test_two_spellings_upload_to_one_row(client, db_session, first, second):
    method = f"b3lyp-574-{first}"
    _upload(client, method, first, "first")
    [row] = _rows(db_session, method)
    ref_before, hash_before = row.public_ref, row.lot_hash

    _upload(client, method, second, "second")

    [row] = _rows(db_session, method)
    # The first spelling is what the row keeps and shows.
    assert row.basis == first
    assert (row.public_ref, row.lot_hash) == (ref_before, hash_before)
    calcs = db_session.scalars(
        select(Calculation).where(Calculation.lot_id == row.id)
    ).all()
    assert len(calcs) == 2


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("6-31G*", "6-31G**"),
        ("aug-cc-pvtz", "cc-pvtz"),
        ("6-31+G(d)", "6-31G(d)"),
        ("6-31G(d,p)", "6-31G(d)"),
    ],
)
def test_different_basis_sets_stay_two_rows(client, db_session, a, b):
    method = f"b3lyp-574-{a}"
    _upload(client, method, a, "a")
    _upload(client, method, b, "b")
    assert sorted(row.basis for row in _rows(db_session, method)) == sorted([a, b])
