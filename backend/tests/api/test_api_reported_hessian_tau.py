"""A deposit is judged at the tolerance of the Hessian method its output log REPORTS, ahead of what was requested.

Order: reported, then recorded / deck, then the program's assumed default, then 50 cm-1. A minus 40 cm-1 mode on a
minimum is refused at tau 30 (analytic) and accepted at tau 50 (finite differences), so the judgement shows which
method decided. Each case has a twin with the other method.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.db.models.calculation import CalculationFreqResult, CalculationParameter
from app.db.models.common import ParameterStatementKind
from tests.api.test_api_assumed_tau_refuses_minimum import _computed_species_with_soft_freq

_BANNER = (
    "\n                                 *****************\n"
    "                                 * O   R   C   A *\n"
    "                                 *****************\n\n"
)
_VIB = "-----------------------\nVIBRATIONAL FREQUENCIES\n-----------------------\n"
_LOG_ANALYTIC = _BANNER + "                               ORCA SCF HESSIAN\n-------------------\n" + _VIB
_LOG_NUMERICAL = _BANNER + (
    "                           ORCA NUMERICAL FREQUENCIES\n-------------------\n"
    "\t<< Calculating gradient on displaced geometry  10 (of  52) >>\n" + _VIB
)
_DECK_NUMFREQ = "! B3LYP def2-SVP Opt NumFreq\n* xyz 0 1\nC 0 0 0\n*\n"
_DECK_ANFREQ = "! B3LYP def2-SVP Opt AnFreq\n* xyz 0 1\nC 0 0 0\n*\n"


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _artifact(kind: str, text: str, filename: str) -> dict:
    return {"kind": kind, "filename": filename, "content_base64": base64.b64encode(text.encode()).decode("ascii")}


def _payload(z: float, *, log: str | None = None, deck: str | None = None):
    payload = _computed_species_with_soft_freq("ORCA", z)
    freq = payload["conformers"][0]["additional_calculations"][0]
    freq["artifacts"] = [
        *([_artifact("input", deck, "job.in")] if deck is not None else []),
        *([_artifact("output_log", log, "job.out")] if log is not None else []),
    ]
    return payload


def _stored(db_session):
    freq = db_session.scalars(select(CalculationFreqResult).order_by(CalculationFreqResult.calculation_id.desc())).first()
    rows = db_session.execute(
        select(CalculationParameter.canonical_value, CalculationParameter.statement_kind).where(
            CalculationParameter.calculation_id == freq.calculation_id,
            CalculationParameter.canonical_key == "freq.hessian_method",
        )
    ).all()
    return freq, rows


def test_a_log_reporting_a_numerical_hessian_accepts_what_the_assumed_analytic_default_refuses(client, db_session):
    refused = client.post("/api/v1/uploads/computed-species", json=_payload(0.0))
    assert refused.status_code == 422 and refused.json()["code"] == "n_imag_contradicts_minimum"

    resp = client.post("/api/v1/uploads/computed-species", json=_payload(0.1, log=_LOG_NUMERICAL))
    assert resp.status_code == 201, resp.text[:600]
    freq, rows = _stored(db_session)
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (50.0, "finite_difference_gradient")
    assert ("finite_difference_gradient", ParameterStatementKind.reported) in rows


def test_a_log_reporting_an_analytic_hessian_refuses_what_a_numerical_deck_would_accept(client):
    accepted = client.post("/api/v1/uploads/computed-species", json=_payload(0.2, deck=_DECK_NUMFREQ))
    assert accepted.status_code == 201, accepted.text[:600]

    resp = client.post("/api/v1/uploads/computed-species", json=_payload(0.3, deck=_DECK_NUMFREQ, log=_LOG_ANALYTIC))
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "n_imag_contradicts_minimum"


def test_the_log_outranks_a_deck_that_requested_the_other_method(client, db_session):
    resp = client.post("/api/v1/uploads/computed-species", json=_payload(0.4, deck=_DECK_ANFREQ, log=_LOG_NUMERICAL))
    assert resp.status_code == 201, resp.text[:600]
    freq, _ = _stored(db_session)
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (50.0, "finite_difference_gradient")


def test_a_log_that_reports_nothing_leaves_the_requested_and_assumed_rungs_in_force(client, db_session):
    nothing = _BANNER + "an output log with no frequency listing\n"
    assert client.post("/api/v1/uploads/computed-species", json=_payload(0.5, log=nothing)).status_code == 422
    resp = client.post("/api/v1/uploads/computed-species", json=_payload(0.6, deck=_DECK_NUMFREQ, log=nothing))
    assert resp.status_code == 201, resp.text[:600]
    freq, _ = _stored(db_session)
    assert freq.imaginary_mode_tau_basis == "finite_difference_gradient"


def test_the_refusal_of_a_recorded_method_the_deck_contradicts_is_unchanged(client):
    parameters = [
        {"raw_key": "freq", "raw_value": "analytic", "canonical_key": "freq.hessian_method", "canonical_value": "analytic"}
    ]
    payload = _payload(0.7, deck=_DECK_NUMFREQ, log=_LOG_ANALYTIC)
    payload["conformers"][0]["additional_calculations"][0]["parameters"] = parameters
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "hessian_method_contradicts_input_deck"
