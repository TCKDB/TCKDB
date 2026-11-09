"""A recorded gradient method against an ORCA ``NumFreq`` deck for a method with no analytic gradient is refined, not refused."""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.db.models.calculation import CalculationFreqResult
from tests.api.test_api_assumed_tau_refuses_minimum import _computed_species_with_soft_freq
from tests.api.test_api_input_deck_tau import _recorded

_CODE = "hessian_method_refined"
_CC_NUMFREQ = "! CCSD(T) def2-SVP NumFreq\n* xyz 0 1\nC 0 0 0\n*\n"
_BANNER = (
    "\n                                 *****************\n"
    "                                 * O   R   C   A *\n"
    "                                 *****************\n\n"
)
_CC_ECHO_LOG = (
    _BANNER + "INPUT FILE\n|  1> ! CCSD(T) def2-SVP NumFreq\n|  2> * xyz 0 1\n|  3> C 0 0 0\n|  4> *\n****END OF INPUT****\n"
)


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _payload(deck: str, z: float, *, method: str = "CCSD(T)", parameters=None, kind: str = "input", n_imag: int = 0):
    payload = _computed_species_with_soft_freq("ORCA", z)
    freq = payload["conformers"][0]["additional_calculations"][0]
    freq["level_of_theory"] = {"method": method, "basis": "def2-SVP"}
    freq["freq_result"] = {"n_imag": n_imag, **({"imag_freq_cm1": -40.0} if n_imag else {})}
    freq["artifacts"] = [
        {"kind": kind, "filename": "job.in" if kind == "input" else "job.out",
         "content_base64": base64.b64encode(deck.encode()).decode("ascii")}
    ]
    if parameters is not None:
        freq["parameters"] = parameters
    return payload


def _stored(db_session):
    return db_session.scalars(
        select(CalculationFreqResult).order_by(CalculationFreqResult.calculation_id.desc())
    ).first()


def test_a_recorded_gradient_method_and_a_numfreq_deck_on_coupled_cluster_is_refined_not_refused(client, db_session):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload(_CC_NUMFREQ, 0.21, parameters=_recorded("finite_difference_gradient")),
    )
    assert resp.status_code == 201, resp.text[:600]
    freq = _stored(db_session)
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (80.0, "finite_difference_energy")
    (warning,) = [w for w in resp.json()["warnings"] if w["code"] == _CODE]
    assert warning["context"] == {
        "recorded": "finite_difference_gradient",
        "stored_method": "finite_difference_energy",
        "stored_basis": "finite_difference_energy",
        "refined_because": "no_analytic_gradient",
    }
    assert "finite_difference_gradient" in warning["message"] and "finite_difference_energy" in warning["message"]


def test_the_same_refinement_through_the_input_an_output_log_echoes(client, db_session):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload(_CC_ECHO_LOG, 0.22, parameters=_recorded("finite_difference_gradient"), kind="output_log"),
    )
    assert resp.status_code == 201, resp.text[:600]
    assert (_stored(db_session).imaginary_mode_tau_basis) == "finite_difference_energy"
    assert [w["code"] for w in resp.json()["warnings"] if w["code"] == _CODE] == [_CODE]


def test_a_deck_alone_on_coupled_cluster_needs_no_refinement(client, db_session):
    resp = client.post("/api/v1/uploads/computed-species", json=_payload(_CC_NUMFREQ, 0.23))
    assert resp.status_code == 201, resp.text[:600]
    assert _stored(db_session).imaginary_mode_tau_basis == "finite_difference_energy"
    assert not [w for w in resp.json()["warnings"] if w["code"] == _CODE]


@pytest.mark.parametrize("deck_log", [False, True])
def test_a_recorded_analytic_method_against_numfreq_is_still_refused(client, deck_log):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload(
            _CC_ECHO_LOG if deck_log else _CC_NUMFREQ,
            0.24,
            parameters=_recorded("analytic"),
            kind="output_log" if deck_log else "input",
        ),
    )
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "hessian_method_contradicts_input_deck"


def test_a_method_with_an_analytic_gradient_is_not_refined(client):
    deck = "! B3LYP def2-SVP NumFreq NumGrad\n* xyz 0 1\nC 0 0 0\n*\n"
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload(deck, 0.25, method="B3LYP", parameters=_recorded("finite_difference_gradient")),
    )
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "hessian_method_contradicts_input_deck"
