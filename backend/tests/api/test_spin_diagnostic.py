"""Tests for spin-contamination ``<S^2>`` diagnostic evidence on calculations.

Companion to ``test_wavefunction_diagnostic.py``. Covers:

* Pydantic payload validation (``s_squared`` required, non-negativity,
  ``extra="forbid"`` catching a bad field name).
* DB check constraints reject negative values.
* Model round-trip (insert/select).
* Upload through ``/api/v1/uploads/conformers`` persists the row from the
  single shared persistence seam.
* Absence of any value ⇒ no row (the producer contract).
* The scientific detail read endpoint serves the persisted row via
  ``include=spin_diagnostic`` and reports ``has_spin_diagnostic``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models.calculation import CalculationSpinDiagnostic
from app.schemas.fragments.calculation import SpinDiagnosticPayload

KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"


def _conformer_payload(
    *,
    label: str = "spin-diag",
    spin_diagnostic: dict | None = None,
    calc_type: str = "sp",
) -> dict:
    """Build a conformer upload payload for an unrestricted radical calc.

    Multiplicity 2 (a doublet radical) is the realistic setting in which
    an ESS reports a spin-contamination ``<S^2>`` value.
    """
    calc: dict = {
        "type": calc_type,
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {
            "method": "uwb97xd",
            "basis": "def2tzvp",
            "spin_treatment": "unrestricted",
        },
    }
    if calc_type == "sp":
        calc["sp_result"] = {"electronic_energy_hartree": -76.4}
    elif calc_type == "opt":
        calc["opt_result"] = {"converged": True}
    if spin_diagnostic is not None:
        calc["spin_diagnostic"] = spin_diagnostic
    return {
        "species_entry": {
            "smiles": "[OH]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {"xyz_text": "2\nOH radical\nO 0.0 0.0 0.0\nH 0.0 0.0 0.97"},
        "calculation": calc,
        "label": label,
    }


def _latest_calc_id(client) -> int:
    return client.get("/api/v1/calculations").json()["items"][0]["id"]


# ---------------------------------------------------------------------------
# Pydantic payload validation
# ---------------------------------------------------------------------------


class TestPayloadValidation:
    def test_s_squared_required(self):
        with pytest.raises(ValueError):
            SpinDiagnosticPayload()

    def test_note_only_payload_rejected(self):
        """A row without ``s_squared`` is rejected: the observed value is
        the reason the row exists at all."""
        with pytest.raises(ValueError):
            SpinDiagnosticPayload(note="some note")

    def test_s_squared_only_accepted(self):
        payload = SpinDiagnosticPayload(s_squared=2.0104)
        assert payload.s_squared == pytest.approx(2.0104)
        assert payload.s_squared_expected is None
        assert payload.s_squared_annihilated is None

    def test_all_values_accepted(self):
        payload = SpinDiagnosticPayload(
            s_squared=2.0104,
            s_squared_expected=0.75,
            s_squared_annihilated=0.7502,
            note="parsed from Gaussian log",
        )
        assert payload.s_squared == pytest.approx(2.0104)
        assert payload.s_squared_expected == pytest.approx(0.75)
        assert payload.s_squared_annihilated == pytest.approx(0.7502)

    @pytest.mark.parametrize(
        "field",
        ["s_squared", "s_squared_expected", "s_squared_annihilated"],
    )
    def test_negative_value_rejected(self, field):
        base = {"s_squared": 2.0}
        base[field] = -0.01
        with pytest.raises(ValueError):
            SpinDiagnosticPayload(**base)

    def test_unknown_field_rejected(self):
        """``extra="forbid"`` catches producer contract drift early."""
        with pytest.raises(ValueError):
            SpinDiagnosticPayload(s_squared=2.0, s_squared_typo=0.75)


# ---------------------------------------------------------------------------
# DB check constraints (defense-in-depth against direct ORM bypass)
# ---------------------------------------------------------------------------


class TestDBCheckConstraints:
    @pytest.mark.parametrize(
        "field",
        ["s_squared", "s_squared_expected", "s_squared_annihilated"],
    )
    def test_negative_value_rejected_by_db(self, client, db_session, field):
        client.post("/api/v1/uploads/conformers", json=_conformer_payload())
        calc_id = _latest_calc_id(client)
        values = {"s_squared": 2.0}
        values[field] = -0.01
        db_session.add(CalculationSpinDiagnostic(calculation_id=calc_id, **values))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()


# ---------------------------------------------------------------------------
# Model round-trip via direct ORM insert
# ---------------------------------------------------------------------------


class TestModelRoundTrip:
    def test_insert_and_select(self, client, db_session):
        client.post("/api/v1/uploads/conformers", json=_conformer_payload())
        calc_id = _latest_calc_id(client)
        db_session.add(
            CalculationSpinDiagnostic(
                calculation_id=calc_id,
                s_squared=2.0104,
                s_squared_expected=0.75,
                s_squared_annihilated=0.7502,
                note="Gaussian UHF",
            )
        )
        db_session.flush()

        row = db_session.scalar(select(CalculationSpinDiagnostic).where(CalculationSpinDiagnostic.calculation_id == calc_id))
        assert row is not None
        assert row.s_squared == pytest.approx(2.0104)
        assert row.s_squared_expected == pytest.approx(0.75)
        assert row.s_squared_annihilated == pytest.approx(0.7502)
        assert row.note == "Gaussian UHF"


# ---------------------------------------------------------------------------
# Upload persistence (conformer upload → CalculationWithResultsPayload)
# ---------------------------------------------------------------------------


class TestUploadPersistence:
    def test_upload_with_s_squared_persists_row(self, client, db_session):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(spin_diagnostic={"s_squared": 2.0104}),
        )
        assert resp.status_code == 201, resp.text

        rows = db_session.scalars(select(CalculationSpinDiagnostic)).all()
        assert len(rows) == 1
        assert rows[0].s_squared == pytest.approx(2.0104)
        assert rows[0].s_squared_expected is None
        assert rows[0].s_squared_annihilated is None

    def test_upload_with_all_fields_persists_all(self, client, db_session):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                spin_diagnostic={
                    "s_squared": 2.0104,
                    "s_squared_expected": 0.75,
                    "s_squared_annihilated": 0.7502,
                    "note": "parsed",
                }
            ),
        )
        assert resp.status_code == 201, resp.text

        row = db_session.scalar(select(CalculationSpinDiagnostic))
        assert row is not None
        assert row.s_squared == pytest.approx(2.0104)
        assert row.s_squared_expected == pytest.approx(0.75)
        assert row.s_squared_annihilated == pytest.approx(0.7502)
        assert row.note == "parsed"

    def test_upload_without_block_creates_no_row(self, client, db_session):
        resp = client.post("/api/v1/uploads/conformers", json=_conformer_payload())
        assert resp.status_code == 201, resp.text

        count = db_session.scalar(select(func.count()).select_from(CalculationSpinDiagnostic))
        assert count == 0

    def test_upload_missing_s_squared_returns_422(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(spin_diagnostic={"note": "nothing"}),
        )
        assert resp.status_code == 422

    def test_upload_bad_field_name_returns_422(self, client):
        """Proves ``extra="forbid"`` catches contract drift on the block."""
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(spin_diagnostic={"s_squared": 2.0, "s_squared_typo": 0.75}),
        )
        assert resp.status_code == 422

    def test_upload_negative_value_returns_422(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(spin_diagnostic={"s_squared": -0.01}),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency: replay must not duplicate the diagnostic row
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_replay_does_not_duplicate(self, client, db_session):
        key = "spin-diag-idem-key-bbbbbbbbbbb"
        payload = _conformer_payload(spin_diagnostic={"s_squared": 2.01})
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

        count = db_session.scalar(select(func.count()).select_from(CalculationSpinDiagnostic))
        assert count == 1


# ---------------------------------------------------------------------------
# Scientific detail read endpoint: include=spin_diagnostic + available_sections
# ---------------------------------------------------------------------------


class TestScientificReadInclude:
    def test_include_returns_persisted_row(self, client):
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_conformer_payload(
                spin_diagnostic={
                    "s_squared": 2.0104,
                    "s_squared_expected": 0.75,
                    "s_squared_annihilated": 0.7502,
                    "note": "Gaussian",
                }
            ),
        )
        assert resp.status_code == 201, resp.text
        calc_id = _latest_calc_id(client)

        read = client.get(f"/api/v1/scientific/calculations/{calc_id}?include=spin_diagnostic")
        assert read.status_code == 200, read.text
        record = read.json()["record"]
        assert record["available_sections"]["has_spin_diagnostic"] is True

        block = record["spin_diagnostic"]
        assert isinstance(block, list)
        assert len(block) == 1
        assert block[0]["s_squared"] == pytest.approx(2.0104)
        assert block[0]["s_squared_expected"] == pytest.approx(0.75)
        assert block[0]["s_squared_annihilated"] == pytest.approx(0.7502)
        assert block[0]["note"] == "Gaussian"

    def test_include_empty_when_no_row(self, client):
        resp = client.post("/api/v1/uploads/conformers", json=_conformer_payload())
        assert resp.status_code == 201
        calc_id = _latest_calc_id(client)

        read = client.get(f"/api/v1/scientific/calculations/{calc_id}?include=spin_diagnostic")
        assert read.status_code == 200, read.text
        record = read.json()["record"]
        assert record["available_sections"]["has_spin_diagnostic"] is False
        assert record["spin_diagnostic"] == []
