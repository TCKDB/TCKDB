"""API tests for the standalone transport upload endpoint.

Covers successful uploads, auth handling, request validation, response
shape, and a read-after-write round-trip against the transport read API.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app


def _transport_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
        "dipole_debye": 0.0,
        "polarizability_angstrom3": 0.667,
        "rotational_relaxation": 0.0,
        "note": "H atom transport",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


class TestTransportUpload:
    def test_success(self, client):
        resp = client.post(
            "/api/v1/uploads/transport", json=_transport_payload()
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "transport"
        assert "id" in data
        assert "species_entry_id" in data
        # This payload declares `scientific_origin=computed` but omits
        # software_release and workflow_tool_release; the upload still
        # succeeds but the absence is surfaced via provenance warnings.
        codes = {w["code"] for w in data["warnings"]}
        assert "missing_software_release_provenance" in codes
        assert "missing_workflow_tool_provenance" in codes

    def test_success_with_source_calculations(self, client):
        payload = _transport_payload(
            species_entry={"smiles": "CO", "charge": 0, "multiplicity": 1},
            calculations=[
                {
                    "key": "sp1",
                    "calculation": {
                        "type": "sp",
                        "software_release": {"name": "Gaussian", "version": "16"},
                        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                        "sp_result": {"electronic_energy_hartree": -115.7},
                    },
                },
            ],
            source_calculations=[
                {"calculation_key": "sp1", "role": "full_transport"},
            ],
        )
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "transport"

        # Round-trip via the read endpoint: source calculation is visible.
        read_resp = client.get(f"/api/v1/transport/{data['id']}")
        assert read_resp.status_code == 200
        body = read_resp.json()
        assert body["id"] == data["id"]
        assert len(body["source_calculations"]) == 1
        link = body["source_calculations"][0]
        assert link["role"] == "full_transport"
        assert link["transport_id"] == data["id"]


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


class TestTransportUploadValidation:
    def test_partial_lj_pair_returns_422(self, client):
        payload = _transport_payload()
        payload["epsilon_over_k_k"] = None
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 422

    def test_non_positive_sigma_returns_422(self, client):
        payload = _transport_payload(sigma_angstrom=0.0)
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 422

    def test_negative_rotational_relaxation_returns_422(self, client):
        payload = _transport_payload(rotational_relaxation=-1.0)
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 422

    def test_invalid_smiles_returns_422(self, client):
        payload = _transport_payload()
        payload["species_entry"]["smiles"] = "NOT_A_SMILES"
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 422

    def test_source_calc_with_undefined_key_returns_422(self, client):
        payload = _transport_payload(
            source_calculations=[
                {"calculation_key": "ghost", "role": "full_transport"},
            ],
        )
        resp = client.post("/api/v1/uploads/transport", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Append-only (repeated uploads on the same species entry)
# ---------------------------------------------------------------------------


class TestTransportUploadAppendOnly:
    def test_repeated_transport_creates_separate_records(self, client):
        payload = _transport_payload(
            species_entry={"smiles": "N", "charge": 0, "multiplicity": 1}
        )
        r1 = client.post("/api/v1/uploads/transport", json=payload)
        r2 = client.post("/api/v1/uploads/transport", json=payload)
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["id"] != d2["id"]
        assert d1["species_entry_id"] == d2["species_entry_id"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestTransportUploadAuth:
    def test_missing_api_key_returns_401(self, db_engine, _api_test_user):
        """A TestClient without the auth override must be rejected with 401."""
        app = create_app()
        # No override on get_current_user: the real API-key header check runs.
        with TestClient(app) as c:
            resp = c.post(
                "/api/v1/uploads/transport", json=_transport_payload()
            )
            assert resp.status_code == 401
