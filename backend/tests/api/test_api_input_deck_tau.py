"""An inline input deck is read before a deposit is judged (owner decision 2026-10-31).

ARC ships input decks as ``input`` artifacts and no ``parameters``. The
backend parsed ``freq.hessian_method`` out of them only after validation, so a
deck saying ``NumFreq`` was judged as if it said nothing. Order of evidence:
recorded parameter, then the deck, then the assumed default, then 50.

Paths that cannot see a deck at validation time, and keep the behaviour
they had: the standalone calculation / conformer / transition-state payloads
carry no ``artifacts`` field (a deck reaches them through the artifact route
after the deposit), and a deck attached later is read only by the stored-row
resolution used by the ops lister.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select
from tckdb_schemas.stationary_point import resolve_tau_from_parameters

from app.db.models.calculation import CalculationFreqResult, CalculationParameter
from tests.api.test_api_assumed_tau_refuses_minimum import _computed_species_with_soft_freq

_ORCA_NUMFREQ = "! B3LYP def2-SVP Opt NumFreq\n* xyz 0 1\nC 0 0 0\n*\n"
_ORCA_ANFREQ = "! B3LYP def2-SVP AnFreq\n* xyz 0 1\nC 0 0 0\n*\n"
_ORCA_NO_DIRECTIVE = "! B3LYP def2-SVP Opt Freq\n* xyz 0 1\nC 0 0 0\n*\n"
_GAUSSIAN_NUMER = "%mem=1GB\n#P B3LYP/6-31G(d) Freq=Numer\n\ntitle\n\n0 1\nC 0 0 0\n\n"
_GAUSSIAN_PLAIN = "%mem=1GB\n#P B3LYP/6-31G(d) Freq\n\ntitle\n\n0 1\nC 0 0 0\n\n"


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _deck(text: str, filename: str) -> dict:
    return {
        "kind": "input",
        "filename": filename,
        "content_base64": base64.b64encode(text.encode()).decode("ascii"),
    }


def _payload(software: str, z: float, deck: str | None = None, filename: str = "job.in", parameters=None):
    payload = _computed_species_with_soft_freq(software, z)
    freq = payload["conformers"][0]["additional_calculations"][0]
    if deck is not None:
        freq["artifacts"] = [_deck(deck, filename)]
    if parameters is not None:
        freq["parameters"] = parameters
    return payload


def _stored(db_session):
    freq = db_session.scalars(
        select(CalculationFreqResult).order_by(CalculationFreqResult.calculation_id.desc())
    ).first()
    rows = db_session.execute(
        select(CalculationParameter.canonical_key, CalculationParameter.canonical_value).where(
            CalculationParameter.calculation_id == freq.calculation_id
        )
    ).all()
    return freq, rows


def test_orca_numfreq_deck_accepts_a_minus_40_minimum_at_the_recorded_tau(client, db_session):
    resp = client.post(
        "/api/v1/uploads/computed-species", json=_payload("ORCA", 0.0, _ORCA_NUMFREQ)
    )
    assert resp.status_code == 201, resp.text[:600]
    freq, rows = _stored(db_session)
    assert freq.imaginary_mode_tau_cm1 == 50.0
    assert freq.imaginary_mode_tau_basis == "finite_difference_gradient"
    # The workflow stored the deck's own parameter row, and it says the same.
    assert ("freq.hessian_method", "finite_difference_gradient") in rows
    again = resolve_tau_from_parameters(rows)
    assert (again.tau_cm1, again.basis.value) == (
        freq.imaginary_mode_tau_cm1,
        freq.imaginary_mode_tau_basis,
    )


def test_the_same_orca_minimum_without_a_deck_is_refused(client):
    resp = client.post("/api/v1/uploads/computed-species", json=_payload("ORCA", 0.1))
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "n_imag_contradicts_minimum"


def test_a_gaussian_numer_deck_is_judged_at_the_recorded_basis(client, db_session):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("Gaussian", 0.2, _GAUSSIAN_NUMER, "job.gjf"),
    )
    assert resp.status_code == 201, resp.text[:600]
    freq, rows = _stored(db_session)
    assert freq.imaginary_mode_tau_basis == "finite_difference_gradient"
    assert ("freq.hessian_method", "finite_difference_gradient") in rows


def test_a_deck_with_no_frequency_directive_leaves_the_assumption_in_force(client):
    gaussian = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("Gaussian", 0.3, _GAUSSIAN_PLAIN, "job.gjf"),
    )
    assert gaussian.status_code == 422, gaussian.text[:600]
    orca = client.post(
        "/api/v1/uploads/computed-species", json=_payload("ORCA", 0.4, _ORCA_NO_DIRECTIVE)
    )
    assert orca.status_code == 422, orca.text[:600]


def _recorded(value: str) -> list[dict]:
    return [
        {
            "raw_key": "freq",
            "raw_value": value,
            "canonical_key": "freq.hessian_method",
            "canonical_value": value,
            "section": "freq",
        }
    ]


def test_a_recorded_method_that_contradicts_the_deck_is_refused_both_ways(client):
    numerical_vs_analytic = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.5, _ORCA_ANFREQ, parameters=_recorded("numerical")),
    )
    assert numerical_vs_analytic.status_code == 422, numerical_vs_analytic.text[:600]
    assert numerical_vs_analytic.json()["code"] == "hessian_method_contradicts_input_deck"

    analytic_vs_numerical = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.6, _ORCA_NUMFREQ, parameters=_recorded("analytic")),
    )
    assert analytic_vs_numerical.status_code == 422, analytic_vs_numerical.text[:600]
    assert analytic_vs_numerical.json()["code"] == "hessian_method_contradicts_input_deck"


def test_the_refusal_echoes_neither_the_recorded_string_nor_the_deck(client):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.8, _ORCA_ANFREQ, parameters=_recorded("numerical")),
    )
    assert resp.status_code == 422
    assert "AnFreq" not in resp.text and "def2-SVP" not in resp.text


def test_a_recorded_method_that_agrees_with_the_deck_is_accepted(client):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.9, _ORCA_NUMFREQ, parameters=_recorded("finite_difference_gradient")),
    )
    assert resp.status_code == 201, resp.text[:600]
