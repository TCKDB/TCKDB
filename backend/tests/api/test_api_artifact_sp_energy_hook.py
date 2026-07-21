"""Tests for the single-point energy reconciliation hook fired by the
artifact upload route (``POST /calculations/{id}/artifacts``).

Hook contract:

- Fires only on ``kind='output_log'`` artifacts of single-point
  (``type='sp'``) calculations.
- The tool omitted the energy -> ``calc_sp_result`` is filled from the log
  and an informational ``sp_energy_filled_from_log`` warning is returned.
- The tool's value disagrees with the log -> a ``sp_energy_payload_log_mismatch``
  warning is returned and the reported value is kept unchanged.
- The values agree -> no warning, no change.
- Anything inside the hook failing must NEVER abort the artifact upload.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationSPResult

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# A real Molpro CCSD(T)-F12 single-point log and its energy (Hartree).
CH4_LOG = (FIXTURES / "molpro" / "ch4_closed_shell" / "input.out").read_bytes()
CH4_ENERGY = -40.457885930635


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[tuple[str, str]]:
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return written


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _molpro_output_log(filename: str = "sp.out") -> dict:
    return {
        "kind": "output_log",
        "filename": filename,
        "content_base64": _b64(CH4_LOG),
    }


def _molpro_log_as_input(filename: str = "sp.in") -> dict:
    """The real energy-bearing Molpro log, but tagged ``input``.

    Used to prove the ``output_log`` kind gate: if the gate were removed,
    the banner + energy here would be reconciled and fill the row.
    """
    return {
        "kind": "input",
        "filename": filename,
        "content_base64": _b64(CH4_LOG),
    }


def _garbage_molpro_log(filename: str = "broken.out") -> dict:
    """A Molpro-bannered but semantically corrupt (valid-UTF-8) log.

    Output logs are validated as UTF-8 before the hook runs, so the bytes
    are well-formed text; the content is nonsense the parser must survive
    by yielding no usable energy rather than raising.
    """
    body = (
        "***  PROGRAM SYSTEM MOLPRO  ***\n"
        "corrupt deck: !RHF STATE 1.1 Energy nan\n"
        "!CCSD(T)-F12 total energy   not-a-number\n"
        "truncated mid-"
    ).encode("utf-8")
    return {"kind": "output_log", "filename": filename, "content_base64": _b64(body)}


def _sp_conformer_payload(
    *,
    sp_energy: float | None,
    calc_type: str = "sp",
    empty_sp_result: bool = False,
) -> dict:
    calc: dict = {
        "type": calc_type,
        "software_release": {"name": "Molpro", "version": "2022.1"},
        "level_of_theory": {"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
    }
    if sp_energy is not None:
        calc["sp_result"] = {"electronic_energy_hartree": sp_energy}
    elif empty_sp_result:
        # Present but valueless -> a calc_sp_result row with NULL energy.
        calc["sp_result"] = {}
    if calc_type == "opt":
        calc["opt_result"] = {"converged": True}
    return {
        "species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": "1\nC atom\nC 0.0 0.0 0.0"},
        "calculation": calc,
        "label": "ch4-sp-energy-hook",
    }


def _create_calc(
    client,
    *,
    sp_energy: float | None,
    calc_type: str = "sp",
    empty_sp_result: bool = False,
) -> int:
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_sp_conformer_payload(
            sp_energy=sp_energy,
            calc_type=calc_type,
            empty_sp_result=empty_sp_result,
        ),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


def _stored_energy(db_session: Session, calc_id: int) -> float | None:
    row = db_session.get(CalculationSPResult, calc_id)
    return row.electronic_energy_hartree if row is not None else None


def _warning_codes(resp) -> list[str]:
    return [w["code"] for w in resp.json().get("warnings", [])]


class TestSpEnergyHook:
    def test_fills_energy_when_payload_omitted_it(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, sp_energy=None)
        assert _stored_energy(db_session, calc_id) is None

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_output_log()]},
        )
        assert resp.status_code == 201, resp.text
        assert _stored_energy(db_session, calc_id) == CH4_ENERGY
        assert "sp_energy_filled_from_log" in _warning_codes(resp)

    def test_mismatch_warns_and_keeps_reported_value(
        self, client, db_session, stub_store_artifact
    ):
        wrong = CH4_ENERGY + 0.01
        calc_id = _create_calc(client, sp_energy=wrong)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_output_log()]},
        )
        assert resp.status_code == 201, resp.text
        assert "sp_energy_payload_log_mismatch" in _warning_codes(resp)
        # The tool's value is kept unchanged — TCKDB flags, never overwrites.
        assert _stored_energy(db_session, calc_id) == wrong

    def test_matching_energy_emits_no_warning(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, sp_energy=CH4_ENERGY)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_output_log()]},
        )
        assert resp.status_code == 201, resp.text
        assert _warning_codes(resp) == []
        assert _stored_energy(db_session, calc_id) == CH4_ENERGY

    def test_input_kind_gate_blocks_energy_bearing_log(
        self, client, db_session, stub_store_artifact
    ):
        # The real energy-bearing Molpro log, but tagged input. Only the
        # kind gate stops it — remove the gate and this would fill.
        calc_id = _create_calc(client, sp_energy=None)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_log_as_input()]},
        )
        assert resp.status_code == 201, resp.text
        assert _stored_energy(db_session, calc_id) is None
        assert "sp_energy_filled_from_log" not in _warning_codes(resp)

    def test_non_sp_calculation_is_skipped(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, sp_energy=None, calc_type="opt")

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_output_log()]},
        )
        assert resp.status_code == 201, resp.text
        # SP-energy reconciliation is scoped to single-point calculations.
        assert _stored_energy(db_session, calc_id) is None
        assert _warning_codes(resp) == []

    def test_fills_pre_existing_null_energy_row(
        self, client, db_session, stub_store_artifact
    ):
        # Tool sent ``sp_result: {}`` -> a row exists with NULL energy. The
        # fill must update it in place, not silently claim to have filled.
        calc_id = _create_calc(client, sp_energy=None, empty_sp_result=True)
        assert db_session.get(CalculationSPResult, calc_id) is not None
        assert _stored_energy(db_session, calc_id) is None

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_output_log()]},
        )
        assert resp.status_code == 201, resp.text
        assert "sp_energy_filled_from_log" in _warning_codes(resp)
        assert _stored_energy(db_session, calc_id) == CH4_ENERGY

    def test_garbage_bannered_log_does_not_raise_or_fill(
        self, client, db_session, stub_store_artifact
    ):
        # A corrupt Molpro-bannered log must never abort the upload; no
        # usable energy means no fill, no warning.
        calc_id = _create_calc(client, sp_energy=None)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_garbage_molpro_log()]},
        )
        assert resp.status_code == 201, resp.text
        assert _stored_energy(db_session, calc_id) is None
        assert _warning_codes(resp) == []

    def test_two_output_logs_one_request_fills_once(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, sp_energy=None)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={
                "artifacts": [
                    _molpro_output_log("a.out"),
                    _molpro_output_log("b.out"),
                ]
            },
        )
        assert resp.status_code == 201, resp.text
        # One fill; the second log sees the in-session row and confirms.
        assert _stored_energy(db_session, calc_id) == CH4_ENERGY
        assert _warning_codes(resp).count("sp_energy_filled_from_log") == 1

    def test_reupload_after_fill_confirms_without_duplicate(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, sp_energy=None)
        r1 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_output_log("first.out")]},
        )
        assert r1.status_code == 201
        assert "sp_energy_filled_from_log" in _warning_codes(r1)

        r2 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_molpro_output_log("second.out")]},
        )
        assert r2.status_code == 201, r2.text
        # The row is already filled with the same value -> confirmed, no warning.
        assert _warning_codes(r2) == []
        assert _stored_energy(db_session, calc_id) == CH4_ENERGY
