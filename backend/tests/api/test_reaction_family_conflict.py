"""A reaction deposited twice under different families is refused with a coded error."""

from __future__ import annotations

_BODY = {
    "reversible": True,
    "reactants": [
        {"species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1}},
        {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [
        {"species_entry": {"smiles": "C[CH2]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}},
    ],
}


def _post(client, family):
    body = {**_BODY, "reaction_family": family}
    if family not in ("H_Abstraction", "Disproportionation"):
        body["reaction_family_source_note"] = "depositor note"  # required for a non-seeded family
    return client.post("/api/v1/uploads/reactions", json=body)


def test_seeded_family_conflict_is_coded(client):
    assert _post(client, "H_Abstraction").status_code == 201
    resp = _post(client, "Disproportionation")
    print("SEEDED", resp.status_code, resp.text)
    assert resp.status_code == 422
    assert resp.json()["code"] == "reaction_family_conflict"


def test_raw_family_conflict_does_not_quote_depositor_text(client):
    assert _post(client, "my secret family alpha").status_code == 201
    resp = _post(client, "my secret family beta")
    print("RAW", resp.status_code, resp.text)
    assert resp.status_code == 422
    assert resp.json()["code"] == "reaction_family_conflict"
    assert "alpha" not in resp.text and "beta" not in resp.text
