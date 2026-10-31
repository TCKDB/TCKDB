"""A deck uploaded after the deposit that disagrees with the stored tolerance basis warns.

The stored tau is append-only and is not relabelled; the warning is stored on the
calculation (the #837 machinery) and shown under ``record_warnings`` on its read.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationFreqResult
from tests.api.artifact_urls import artifacts_url
from tests.api.test_api_minimum_imaginary_tau import _ANALYTIC_TIGHT, _payload

_CODE = "hessian_method_disagrees_with_stored_tau_basis"
_NUMER = "%mem=1GB\n#P B3LYP/6-31G(d) Freq=Numer\n\ntitle\n\n0 2\nH 0 0 0\n\n"
_PLAIN = "%mem=1GB\n#P B3LYP/6-31G(d) Freq\n\ntitle\n\n0 2\nH 0 0 0\n\n"
_FD = [
    {
        "raw_key": "freq",
        "raw_value": "numer",
        "canonical_key": "freq.hessian_method",
        "canonical_value": "finite_difference_gradient",
        "section": "freq",
    }
]


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _freq_calc(client, db_session, **kwargs) -> Calculation:
    resp = client.post("/api/v1/uploads/conformers", json=_payload(**kwargs))
    assert resp.status_code == 201, resp.text
    freq = db_session.scalars(
        select(CalculationFreqResult).order_by(CalculationFreqResult.calculation_id.desc())
    ).first()
    return db_session.get(Calculation, freq.calculation_id)


def _upload_deck(client, calc: Calculation, text: str, filename: str = "late.gjf"):
    resp = client.post(
        artifacts_url(client, calc.id),
        json={
            "artifacts": [
                {
                    "kind": "input",
                    "filename": filename,
                    "content_base64": base64.b64encode(text.encode()).decode(),
                }
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    return resp


def _read_codes(client, calc: Calculation) -> list[dict]:
    body = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json()
    (fragment,) = [w for w in body["record_warnings"] if w["record_ref"] == calc.public_ref]
    return [item for item in fragment["items"] if item["code"] == _CODE]


def test_a_numerical_deck_against_an_assumed_analytic_basis_warns_and_relabels_nothing(client, db_session):
    calc = _freq_calc(client, db_session, imag_freq_cm1=-20.0, label="late-a")
    before = (calc.freq_result.imaginary_mode_tau_cm1, calc.freq_result.imaginary_mode_tau_basis)
    assert before == (30.0, "assumed_analytic_default")

    resp = _upload_deck(client, calc, _NUMER)

    assert [w["code"] for w in resp.json()["warnings"] if w["code"] == _CODE] == [_CODE]
    db_session.expire_all()
    stored = db_session.get(CalculationFreqResult, calc.id)
    assert (stored.imaginary_mode_tau_cm1, stored.imaginary_mode_tau_basis) == before

    (item,) = _read_codes(client, calc)
    assert "analytic" in item["message"] and "finite_difference_gradient" in item["message"]
    assert "Freq=Numer" not in item["message"] and "B3LYP" not in item["message"]


def test_a_numerical_deck_against_a_recorded_analytic_basis_warns(client, db_session):
    calc = _freq_calc(
        client, db_session, imag_freq_cm1=-10.0, parameters=_ANALYTIC_TIGHT, label="late-recorded"
    )
    assert calc.freq_result.imaginary_mode_tau_basis == "analytic_tight"
    resp = _upload_deck(client, calc, _NUMER)
    assert _CODE in {w["code"] for w in resp.json()["warnings"]}
    assert len(_read_codes(client, calc)) == 1


def test_an_agreeing_deck_warns_nothing(client, db_session):
    calc = _freq_calc(client, db_session, imag_freq_cm1=-40.0, parameters=_FD, label="late-agree")
    assert calc.freq_result.imaginary_mode_tau_basis == "finite_difference_gradient"
    resp = _upload_deck(client, calc, _NUMER)
    assert _CODE not in {w["code"] for w in resp.json()["warnings"]}
    assert _read_codes(client, calc) == []


def test_a_deck_with_no_frequency_directive_warns_nothing(client, db_session):
    calc = _freq_calc(client, db_session, imag_freq_cm1=-20.0, label="late-plain")
    resp = _upload_deck(client, calc, _PLAIN)
    assert _CODE not in {w["code"] for w in resp.json()["warnings"]}
    assert _read_codes(client, calc) == []


def test_a_basis_that_rests_on_no_method_warns_nothing(client, db_session):
    calc = _freq_calc(
        client,
        db_session,
        imag_freq_cm1=-40.0,
        software={"name": "Molpro", "version": "2022.1"},
        label="late-unrecorded",
    )
    assert calc.freq_result.imaginary_mode_tau_basis == "protocol_not_recorded"
    resp = _upload_deck(client, calc, _NUMER)
    assert _CODE not in {w["code"] for w in resp.json()["warnings"]}


_ORCA_NUMFREQ_NUMGRAD = "! CCSD def2-SVP NumFreq NumGrad\n* xyz 0 2\nH 0 0 0\n*\n"


def test_gradient_against_energy_names_both_methods(client, db_session):
    """A stored finite-difference-from-gradients basis and a NumFreq+NumGrad deck."""
    payload = _payload(imag_freq_cm1=-40.0, label="late-grad-energy", software={"name": "ORCA", "version": "6.0.1"})
    payload["additional_calculations"][0]["level_of_theory"] = {"method": "CCSD", "basis": "def2-SVP"}
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text
    freq = db_session.scalars(
        select(CalculationFreqResult).order_by(CalculationFreqResult.calculation_id.desc())
    ).first()
    calc = db_session.get(Calculation, freq.calculation_id)
    assert freq.imaginary_mode_tau_basis == "assumed_finite_difference_gradient"

    resp = _upload_deck(client, calc, _ORCA_NUMFREQ_NUMGRAD, "late.in")
    (warning,) = [w for w in resp.json()["warnings"] if w["code"] == _CODE]
    assert warning["context"] == {
        "stored_basis": "assumed_finite_difference_gradient",
        "stored_method": "finite_difference_gradient",
        "deck_method": "finite_difference_energy",
    }
    (item,) = _read_codes(client, calc)
    assert "finite_difference_gradient" in item["message"] and "finite_difference_energy" in item["message"]
    assert " a analytic" not in item["message"]


def test_a_failing_check_cannot_fail_the_artifact_upload(client, db_session, monkeypatch):
    calc = _freq_calc(client, db_session, imag_freq_cm1=-20.0, label="late-boom")

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure inside the check")

    monkeypatch.setattr("app.services.hessian_method_deck_check._disagreement", boom)
    resp = _upload_deck(client, calc, _NUMER)
    body = resp.json()
    assert len(body["artifacts"]) == 1
    assert _CODE not in {w["code"] for w in body["warnings"]}
    # The session is still usable for the rest of the request / test.
    assert db_session.get(Calculation, calc.id) is not None
