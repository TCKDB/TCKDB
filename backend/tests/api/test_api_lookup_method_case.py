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
    ("wb97xd", "def2tzvp", "wB97X-D", "def2-tzvp"),  # #618
    ("M062X", "def2tzvp", "m06-2x", "def2-tzvp"),  # #618
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


def test_an_alias_match_says_it_matched_by_identity_key_not_exactly(client):
    """The code is unchanged for clients; the message no longer claims the spelling matched."""
    resp = client.post("/api/v1/uploads/conformers", json=_payload("wb97xd", "def2tzvp"))
    assert resp.status_code in (200, 201), resp.text
    base = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1, "type": "opt", "basis": "def2tzvp"}
    aliased = client.get(
        "/api/v1/lookup/species-calculation", params={**base, "method": "wB97X-D"}
    ).json()["match"]
    assert "lot_method_exact" in aliased["detail_codes"]
    assert "method matched exactly" not in aliased["details"]
    assert any("identity key" in d and "wB97X-D" in d for d in aliased["details"])
    same = client.get(
        "/api/v1/lookup/species-calculation", params={**base, "method": "wb97xd"}
    ).json()["match"]
    assert "method matched exactly" in same["details"]


def test_a_different_method_still_misses(client):
    """Gaussian wB97XD is not ORCA wB97X-D3 (#618)."""
    _different_method(client)


def _different_method(client):
    resp = client.post("/api/v1/uploads/conformers", json=_payload("wB97XD", "Def2TZVP"))
    assert resp.status_code in (200, 201), resp.text
    resp = client.get(
        "/api/v1/lookup/species-calculation",
        params={
            "smiles": "[H][H]", "charge": 0, "multiplicity": 1,
            "type": "opt", "method": "wB97X-D3", "basis": "def2-tzvp",
        },
    )
    assert resp.status_code == 200, resp.text
    codes = resp.json()["match"]["detail_codes"]
    assert "lot_method_exact" not in codes
    assert "lot_method_mismatch" in codes or "calculation_none" in codes, codes


def test_a_blank_basis_request_does_not_claim_a_match_with_a_row_that_has_none():
    from types import SimpleNamespace

    from app.api.routes.lookup import _lot_match, _MatchBuilder

    lot = SimpleNamespace(
        method="hf", basis=None, aux_basis=None, dispersion=None, solvent=None, solvent_model=None
    )
    mb = _MatchBuilder()
    _lot_match(lot, None, "", mb)
    assert "lot_basis_exact" not in mb.codes
    assert "lot_basis_mismatch" in mb.codes

    # And a real match still reports as one.
    mb = _MatchBuilder()
    lot.basis = "Def2TZVP"
    _lot_match(lot, None, "def2-tzvp", mb)
    assert "lot_basis_exact" in mb.codes
