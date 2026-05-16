"""Tests for wavefunction diagnostic evidence on calculations.

Covers:

* Model round-trip (insert/select).
* DB check constraints reject negative values.
* All-null payload rejected at the schema layer.
* Upload through ``/api/v1/uploads/conformers`` persists the row.
* Idempotency replay does not duplicate the row.
* Read endpoint returns 404 when no row exists, and the row contents
  when one does.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models.calculation import (
    Calculation,
    CalculationWavefunctionDiagnostic,
)
from app.schemas.fragments.calculation import WavefunctionDiagnosticPayload


KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"


def _conformer_payload(
    *,
    label: str = "wfn-diag",
    wavefunction_diagnostic: dict | None = None,
    calc_type: str = "sp",
) -> dict:
    calc: dict = {
        "type": calc_type,
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
    }
    if calc_type == "sp":
        calc["sp_result"] = {"electronic_energy_hartree": -76.4}
    elif calc_type == "opt":
        calc["opt_result"] = {"converged": True}
    if wavefunction_diagnostic is not None:
        calc["wavefunction_diagnostic"] = wavefunction_diagnostic
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": calc,
        "label": label,
    }


# ---------------------------------------------------------------------------
# Pydantic payload validation
# ---------------------------------------------------------------------------


class TestPayloadValidation:
    def test_at_least_one_value_required(self):
        with pytest.raises(ValueError, match="at least one of"):
            WavefunctionDiagnosticPayload()

    def test_note_only_payload_rejected(self):
        """A row with only ``note`` and no diagnostic value is rejected.

        Mirrors the producer contract: omit the block when nothing was
        parsed, instead of a noise row that carries only commentary.
        """
        with pytest.raises(ValueError, match="at least one of"):
            WavefunctionDiagnosticPayload(note="some note")

    def test_t1_only_accepted(self):
        payload = WavefunctionDiagnosticPayload(t1_diagnostic=0.0179)
        assert payload.t1_diagnostic == pytest.approx(0.0179)

    def test_all_values_accepted(self):
        payload = WavefunctionDiagnosticPayload(
            t1_diagnostic=0.018,
            d1_diagnostic=0.045,
            t1_norm=0.4,
            largest_t2_amplitude=0.12,
            note="parsed from ORCA log",
        )
        assert payload.t1_diagnostic == pytest.approx(0.018)
        assert payload.d1_diagnostic == pytest.approx(0.045)
        assert payload.t1_norm == pytest.approx(0.4)
        assert payload.largest_t2_amplitude == pytest.approx(0.12)

    @pytest.mark.parametrize(
        "field",
        ["t1_diagnostic", "d1_diagnostic", "t1_norm", "largest_t2_amplitude"],
    )
    def test_negative_value_rejected(self, field):
        with pytest.raises(ValueError):
            WavefunctionDiagnosticPayload(**{field: -0.01})


# ---------------------------------------------------------------------------
# DB check constraints (defense-in-depth against direct ORM bypass)
# ---------------------------------------------------------------------------


class TestDBCheckConstraints:
    def _make_calc_id(self, client) -> int:
        client.post(
            "/api/v1/uploads/conformers", json=_conformer_payload()
        )
        return client.get("/api/v1/calculations").json()["items"][0]["id"]

    @pytest.mark.parametrize(
        "field",
        ["t1_diagnostic", "d1_diagnostic", "t1_norm", "largest_t2_amplitude"],
    )
    def test_negative_value_rejected_by_db(self, client, db_session, field):
        calc_id = self._make_calc_id(client)
        db_session.add(
            CalculationWavefunctionDiagnostic(
                calculation_id=calc_id,
                **{field: -0.01},
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


# ---------------------------------------------------------------------------
# Model round-trip via direct ORM insert
# ---------------------------------------------------------------------------


class TestModelRoundTrip:
    def test_insert_and_select(self, client, db_session):
        client.post(
            "/api/v1/uploads/conformers", json=_conformer_payload()
        )
        calc_id = client.get("/api/v1/calculations").json()["items"][0]["id"]
        db_session.add(
            CalculationWavefunctionDiagnostic(
                calculation_id=calc_id,
                t1_diagnostic=0.0179,
                d1_diagnostic=0.045,
                t1_norm=0.40,
                largest_t2_amplitude=0.12,
                note="ORCA CCSD(T)",
            )
        )
        db_session.flush()

        row = db_session.scalar(
            select(CalculationWavefunctionDiagnostic).where(
                CalculationWavefunctionDiagnostic.calculation_id == calc_id
            )
        )
        assert row is not None
        assert row.t1_diagnostic == pytest.approx(0.0179)
        assert row.d1_diagnostic == pytest.approx(0.045)
        assert row.t1_norm == pytest.approx(0.40)
        assert row.largest_t2_amplitude == pytest.approx(0.12)
        assert row.note == "ORCA CCSD(T)"


# ---------------------------------------------------------------------------
# Upload persistence (conformer upload → CalculationWithResultsPayload)
# ---------------------------------------------------------------------------


class TestUploadPersistence:
    def test_upload_with_t1_persists_row(self, client, db_session):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                wavefunction_diagnostic={"t1_diagnostic": 0.0179}
            ),
        )
        assert resp.status_code == 201, resp.text

        rows = db_session.scalars(
            select(CalculationWavefunctionDiagnostic)
        ).all()
        assert len(rows) == 1
        assert rows[0].t1_diagnostic == pytest.approx(0.0179)
        assert rows[0].d1_diagnostic is None
        assert rows[0].largest_t2_amplitude is None

    def test_upload_with_multiple_diagnostics_persists_all(
        self, client, db_session
    ):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                wavefunction_diagnostic={
                    "t1_diagnostic": 0.018,
                    "d1_diagnostic": 0.045,
                    "t1_norm": 0.4,
                    "largest_t2_amplitude": 0.12,
                    "note": "parsed",
                }
            ),
        )
        assert resp.status_code == 201, resp.text

        row = db_session.scalar(select(CalculationWavefunctionDiagnostic))
        assert row is not None
        assert row.t1_diagnostic == pytest.approx(0.018)
        assert row.d1_diagnostic == pytest.approx(0.045)
        assert row.t1_norm == pytest.approx(0.4)
        assert row.largest_t2_amplitude == pytest.approx(0.12)
        assert row.note == "parsed"

    def test_upload_without_diagnostic_block_creates_no_row(
        self, client, db_session
    ):
        resp = client.post(
            "/api/v1/uploads/conformers", json=_conformer_payload()
        )
        assert resp.status_code == 201, resp.text

        count = db_session.scalar(
            select(func.count()).select_from(CalculationWavefunctionDiagnostic)
        )
        assert count == 0

    def test_upload_with_all_null_block_returns_422(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                wavefunction_diagnostic={"note": "nothing parsed"}
            ),
        )
        assert resp.status_code == 422
        assert "at least one of" in resp.text

    def test_upload_with_negative_value_returns_422(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                wavefunction_diagnostic={"t1_diagnostic": -0.01}
            ),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency: replay must not duplicate the diagnostic row
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_replay_does_not_duplicate(self, client, db_session):
        key = "wfn-diag-idem-key-aaaaaaaaaaa"
        payload = _conformer_payload(
            wavefunction_diagnostic={"t1_diagnostic": 0.018}
        )
        first = client.post(
            "/api/v1/uploads/conformers",
            json=payload,
            headers={KEY_HEADER: key},
        )
        assert first.status_code == 201
        assert REPLAYED_HEADER not in first.headers

        second = client.post(
            "/api/v1/uploads/conformers",
            json=payload,
            headers={KEY_HEADER: key},
        )
        assert second.status_code == 201
        assert second.headers.get(REPLAYED_HEADER) == "true"

        count = db_session.scalar(
            select(func.count()).select_from(CalculationWavefunctionDiagnostic)
        )
        assert count == 1


# ---------------------------------------------------------------------------
# Read endpoint: missing row → 404, present row → 200
# ---------------------------------------------------------------------------


class TestReadEndpoint:
    def test_missing_row_returns_404(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers", json=_conformer_payload()
        )
        assert resp.status_code == 201
        calc_id = client.get("/api/v1/calculations").json()["items"][0]["id"]

        read = client.get(
            f"/api/v1/calculations/{calc_id}/wavefunction-diagnostic"
        )
        assert read.status_code == 404

    def test_missing_calculation_returns_404(self, client):
        read = client.get(
            "/api/v1/calculations/999999/wavefunction-diagnostic"
        )
        assert read.status_code == 404

    def test_present_row_returns_200(self, client, db_session):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                wavefunction_diagnostic={
                    "t1_diagnostic": 0.0179,
                    "d1_diagnostic": 0.045,
                    "note": "ORCA",
                }
            ),
        )
        assert resp.status_code == 201
        calc_id = client.get("/api/v1/calculations").json()["items"][0]["id"]

        read = client.get(
            f"/api/v1/calculations/{calc_id}/wavefunction-diagnostic"
        )
        assert read.status_code == 200, read.text
        data = read.json()
        assert data["calculation_id"] == calc_id
        assert data["t1_diagnostic"] == pytest.approx(0.0179)
        assert data["d1_diagnostic"] == pytest.approx(0.045)
        assert data["t1_norm"] is None
        assert data["largest_t2_amplitude"] is None
        assert data["note"] == "ORCA"
