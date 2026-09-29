"""Lookup routes compare method and basis by identity key (#585).

``/lookup/calculations`` and ``/lookup/species-calculation`` compared the
stored spelling with the *lower-cased* request, which only ever matched a row
stored in lower case. A row uploaded as Gaussian writes it (``wB97XD``,
``Def2TZVP``) was unreachable under any spelling of the request.
"""

from __future__ import annotations

import pytest


def _payload(method: str, basis: str) -> dict:
    return {
        "species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
        "calculation": {
            "type": "opt",
            "software_release": {"name": "gaussian", "version": "09"},
            "level_of_theory": {"method": method, "basis": basis},
            "opt_result": {"converged": True, "n_steps": 3, "final_energy_hartree": -1.17264},
        },
    }


#: (stored method, stored basis, requested method, requested basis)
CASES = [
    ("wB97XD", "Def2TZVP", "wb97xd", "def2-tzvp"),
    ("wb97xd", "def2tzvp", "WB97XD", "Def2-TZVP"),
    ("CCSD(T)", "cc-pVTZ", "ccsd(t)", "cc-pvtz"),
]


@pytest.mark.parametrize(("stored_m", "stored_b", "ask_m", "ask_b"), CASES)
def test_species_calculation_lookup_matches_another_spelling(
    client, stored_m, stored_b, ask_m, ask_b
):
    resp = client.post("/api/v1/uploads/conformers", json=_payload(stored_m, stored_b))
    assert resp.status_code in (200, 201), resp.text
    resp = client.get(
        "/api/v1/lookup/species-calculation",
        params={
            "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
            "type": "opt", "method": ask_m, "basis": ask_b,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    codes = data["match"]["detail_codes"]
    assert data["match"]["status"] == "exact", codes
    assert "calculation_exists" in codes
    assert "lot_method_exact" in codes
    assert "lot_basis_exact" in codes


@pytest.mark.parametrize(("stored_m", "stored_b", "ask_m", "ask_b"), CASES)
def test_calculations_lookup_matches_another_spelling(
    client, stored_m, stored_b, ask_m, ask_b
):
    resp = client.post("/api/v1/uploads/conformers", json=_payload(stored_m, stored_b))
    assert resp.status_code in (200, 201), resp.text
    entry_id = resp.json()["species_entry_id"]
    resp = client.get(
        "/api/v1/lookup/calculations",
        params={"species_entry_id": entry_id, "method": ask_m, "basis": ask_b},
    )
    assert resp.status_code == 200, resp.text
    kinds = [r["resource_type"] for r in resp.json()["results"]]
    assert "calculation" in kinds, resp.text


def test_a_different_method_still_misses(client):
    resp = client.post("/api/v1/uploads/conformers", json=_payload("wB97XD", "Def2TZVP"))
    assert resp.status_code in (200, 201), resp.text
    resp = client.get(
        "/api/v1/lookup/species-calculation",
        params={
            "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
            "type": "opt", "method": "wB97X-D", "basis": "def2-tzvp",
        },
    )
    assert resp.status_code == 200, resp.text
    codes = resp.json()["match"]["detail_codes"]
    assert "lot_method_exact" not in codes
    assert "lot_method_mismatch" in codes or "calculation_none" in codes, codes
