"""A reaction deposited twice under different families is refused with a coded error.

All three conflict shapes are refused with ``reaction_family_conflict``: seeded against seeded, raw
against raw, and seeded against raw in either order. A refusal names a family only when that side is
in the seeded vocabulary, never depositor text.
"""

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

_SEEDED = ("H_Abstraction", "Disproportionation")


def _post(client, family):
    body = {**_BODY, "reaction_family": family}
    if family not in _SEEDED:
        body["reaction_family_source_note"] = "depositor note"  # required for a non-seeded family
    return client.post("/api/v1/uploads/reactions", json=body)


def test_seeded_family_conflict_is_coded(client):
    assert _post(client, "H_Abstraction").status_code == 201
    resp = _post(client, "Disproportionation")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "reaction_family_conflict"
    assert body["context"] == {"recorded_family": "H_Abstraction", "supplied_family": "Disproportionation"}


def test_raw_family_conflict_does_not_quote_depositor_text(client):
    assert _post(client, "my secret family alpha").status_code == 201
    resp = _post(client, "my secret family beta")
    assert resp.status_code == 422
    assert resp.json()["code"] == "reaction_family_conflict"
    assert resp.json()["context"] == {}
    assert "alpha" not in resp.text and "beta" not in resp.text


def test_recorded_seeded_against_supplied_raw_is_refused(client):
    assert _post(client, "H_Abstraction").status_code == 201
    resp = _post(client, "my secret family beta")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "reaction_family_conflict"
    assert body["context"] == {"recorded_family": "H_Abstraction"}
    assert "beta" not in resp.text


def test_recorded_raw_against_supplied_seeded_is_refused(client):
    assert _post(client, "my secret family alpha").status_code == 201
    resp = _post(client, "H_Abstraction")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "reaction_family_conflict"
    assert body["context"] == {"supplied_family": "H_Abstraction"}
    assert "alpha" not in resp.text


def test_same_family_twice_is_accepted(client):
    assert _post(client, "H_Abstraction").status_code == 201
    assert _post(client, "H_Abstraction").status_code == 201


# ---------------------------------------------------------------------------
# A bundle's family belongs to the bundle's own reaction only
# ---------------------------------------------------------------------------

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_XYZ = {
    "h": ("[H]", 2, "1\nH atom\nH 0.0 0.0 0.0"),
    "h2": ("[H][H]", 1, "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"),
    "oh": ("[OH]", 2, "2\nOH\nO 0.0 0.0 0.0\nH 0.0 0.0 0.97"),
    "h2o": ("O", 1, "3\nH2O\nO 0.0 0.0 0.0\nH 0.0 0.76 0.59\nH 0.0 -0.76 0.59"),
}


def _bundle_species(key):
    smiles, mult, xyz = _XYZ[key]
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": mult},
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle(family):
    return {
        "species": [_bundle_species(k) for k in ("h", "h2", "oh", "h2o")],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
        "reaction_family": family,
        "kinetics": [
            {
                "scientific_origin": "computed",
                "model_kind": "arrhenius",
                "a": 1.0e13,
                "a_units": "cm3_mol_s",
                "n": 0.0,
                "reported_ea": 10.0,
                "reported_ea_units": "kj_mol",
                "tmin_k": 300.0,
                "tmax_k": 2000.0,
                # a fit for a different reaction than the bundle's own
                "reactant_keys": ["h2", "oh"],
                "product_keys": ["h2o", "h"],
            }
        ],
    }


def test_bundle_family_is_not_applied_to_a_kinetics_fit_of_another_reaction(client):
    other = {
        "reversible": True,
        "reactants": [
            {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
            {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
        ],
        "products": [
            {"species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1}},
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
        "reaction_family": "H_Abstraction",
    }
    assert client.post("/api/v1/uploads/reactions", json=other).status_code == 201
    resp = client.post("/api/v1/uploads/computed-reaction", json=_bundle("Disproportionation"))
    assert resp.status_code == 201, resp.text[:600]
