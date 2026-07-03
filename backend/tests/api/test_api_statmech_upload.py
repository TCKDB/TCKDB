"""API tests for the standalone statmech upload endpoint.

Covers the happy path, source-calculation persistence (verified via the
statmech read endpoint), append-only behavior, auth enforcement, and
request validation at the API boundary.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _freq_calc_payload() -> dict:
    return {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
        "freq_result": {"n_imag": 0, "zpe_hartree": 0.021},
    }


def _sp_calc_payload() -> dict:
    return {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT_DFT,
        "sp_result": {"electronic_energy_hartree": -76.437},
    }


def _statmech_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
        "note": "statmech for H atom",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


class TestStatmechUpload:
    def test_success(self, client):
        resp = client.post(
            "/api/v1/uploads/statmech", json=_statmech_payload()
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "statmech"
        assert "id" in data
        assert "species_entry_id" in data
        # Computed-origin statmech without software/workflow-tool/freq-scale
        # provenance still succeeds but surfaces the absence as warnings.
        codes = {w["code"] for w in data["warnings"]}
        assert "missing_software_release_provenance" in codes
        assert "missing_workflow_tool_provenance" in codes
        assert "missing_frequency_scale_factor_provenance" in codes

    def test_success_with_source_calculations_round_trip(self, client):
        """Source calcs submitted with local keys round-trip through the
        read endpoint as (statmech, calc, role) links."""
        payload = _statmech_payload(
            species_entry={"smiles": "CO", "charge": 0, "multiplicity": 1},
            calculations=[
                {"key": "freq1", "calculation": _freq_calc_payload()},
                {"key": "sp1", "calculation": _sp_calc_payload()},
            ],
            source_calculations=[
                {"calculation_key": "freq1", "role": "freq"},
                {"calculation_key": "sp1", "role": "sp"},
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["type"] == "statmech"

        read_resp = client.get(f"/api/v1/statmech/{data['id']}")
        assert read_resp.status_code == 200
        body = read_resp.json()
        assert body["id"] == data["id"]
        roles = {sc["role"] for sc in body["source_calculations"]}
        assert roles == {"freq", "sp"}


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


class TestStatmechUploadValidation:
    def test_invalid_smiles_returns_422(self, client):
        payload = _statmech_payload()
        payload["species_entry"]["smiles"] = "NOT_A_SMILES"
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 422

    def test_source_calc_with_undefined_key_returns_422(self, client):
        payload = _statmech_payload(
            source_calculations=[
                {"calculation_key": "ghost", "role": "freq"},
            ],
        )
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 422

    def test_raw_literature_id_returns_422(self, client):
        payload = _statmech_payload(literature_id=42)
        resp = client.post("/api/v1/uploads/statmech", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Append-only
# ---------------------------------------------------------------------------


class TestStatmechUploadAppendOnly:
    def test_repeated_statmech_creates_separate_records(self, client):
        payload = _statmech_payload(
            species_entry={"smiles": "N", "charge": 0, "multiplicity": 1},
        )
        r1 = client.post("/api/v1/uploads/statmech", json=payload)
        r2 = client.post("/api/v1/uploads/statmech", json=payload)
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["id"] != d2["id"]
        assert d1["species_entry_id"] == d2["species_entry_id"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestStatmechUploadAuth:
    def test_missing_api_key_returns_401(self, db_engine, _api_test_user):
        """A TestClient without the auth override must be rejected with 401."""
        app = create_app()
        with TestClient(app) as c:
            resp = c.post(
                "/api/v1/uploads/statmech", json=_statmech_payload()
            )
            assert resp.status_code == 401
