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
    """A raw-but-readable recorded value (NumFreq) and plain deck text are not echoed in TCKDB's words."""
    deck = "! B3LYP def2-SVP Opt AnFreq TightSCF\n* xyz 0 1\nC 0 0 0\n*\n"
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.8, deck, parameters=_recorded("NumFreq")),
    )
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "hessian_method_contradicts_input_deck"
    body = resp.json()
    # The validation envelope also echoes the request ("input"); TCKDB's own words are
    # the message, the context and the code.
    own_words = " ".join(
        [str(body["code"]), str(body.get("context", ""))]
        + [str(item.get("msg", "")) for item in body.get("detail", [])]
    ).lower()
    assert "analytic" in own_words and "numerical" in own_words
    for fragment in ("numfreq", "anfreq", "def2-svp", "tightscf", "b3lyp"):
        assert fragment not in own_words, fragment


def test_two_different_numerical_methods_are_refused_in_tckdb_vocabulary(client):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload(
            "Gaussian",
            0.85,
            "%mem=1GB\n#P B3LYP/6-31G(d) Freq=EnOnly\n\ntitle\n\n0 1\nC 0 0 0\n\n",
            "job.gjf",
            parameters=_recorded("finite_difference_gradient"),
        ),
    )
    assert resp.status_code == 422, resp.text[:600]
    body = resp.json()
    assert body["code"] == "hessian_method_contradicts_input_deck"
    assert body["context"]["recorded"] == "finite_difference_gradient"
    assert body["context"]["input_deck"] == "finite_difference_energy"
    assert "numerical but" not in resp.text


def test_a_recorded_method_that_agrees_with_the_deck_is_accepted(client):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.9, _ORCA_NUMFREQ, parameters=_recorded("finite_difference_gradient")),
    )
    assert resp.status_code == 201, resp.text[:600]


def _reaction_bundle_with_deck(deck: str | None, z_label: str):
    from tests.api.test_api_bundle_provenance_warnings import _reaction_bundle

    bundle = _reaction_bundle()
    calc = {
        "key": f"h-freq-{z_label}",
        "type": "freq",
        "geometry_key": "h-geom",
        "software_release": {"name": "ORCA", "version": "6.0.1"},
        "level_of_theory": {"method": "B3LYP", "basis": "def2-SVP"},
        "freq_n_imag": 1,
        "freq_imag_freq_cm1": -40.0,
    }
    if deck is not None:
        calc["artifacts"] = [_deck(deck, "job.in")]
    bundle["species"][0]["calculations"].append(calc)
    return bundle


def test_computed_reaction_stores_the_tau_the_deck_was_judged_at(client, db_session):
    """The bundle adapter carries the deck reading to persistence."""
    resp = client.post(
        "/api/v1/uploads/computed-reaction", json=_reaction_bundle_with_deck(_ORCA_NUMFREQ, "a")
    )
    assert resp.status_code == 201, resp.text[:600]
    freq = db_session.scalars(
        select(CalculationFreqResult).where(CalculationFreqResult.n_imag == 1)
    ).one()
    rows = db_session.execute(
        select(CalculationParameter.canonical_key, CalculationParameter.canonical_value).where(
            CalculationParameter.calculation_id == freq.calculation_id
        )
    ).all()
    assert freq.imaginary_mode_tau_cm1 == 50.0
    assert freq.imaginary_mode_tau_basis == "finite_difference_gradient"
    assert ("freq.hessian_method", "finite_difference_gradient") in rows
    again = resolve_tau_from_parameters(rows)
    assert (again.tau_cm1, again.basis.value) == (50.0, "finite_difference_gradient")


def test_computed_reaction_without_a_deck_is_refused_at_the_assumed_tau(client):
    resp = client.post("/api/v1/uploads/computed-reaction", json=_reaction_bundle_with_deck(None, "b"))
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "n_imag_contradicts_minimum"


_ORCA_NUMFREQ_NUMGRAD = "! B3LYP def2-SVP NumFreq NumGrad\n* xyz 0 1\nC 0 0 0\n*\n"


def test_numfreq_with_numgrad_is_a_hessian_from_energies_and_agrees_with_a_recorded_energy_method(
    client, db_session
):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.11, _ORCA_NUMFREQ_NUMGRAD, parameters=_recorded("finite_difference_energy")),
    )
    assert resp.status_code == 201, resp.text[:600]
    freq, rows = _stored(db_session)
    assert freq.imaginary_mode_tau_cm1 == 80.0
    assert freq.imaginary_mode_tau_basis == "finite_difference_energy"
    # The workflow's own row from the same deck says the same.
    assert ("freq.hessian_method", "finite_difference_energy") in rows


def test_numfreq_with_numgrad_against_a_recorded_gradient_method_is_refused(client):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_payload("ORCA", 0.12, _ORCA_NUMFREQ_NUMGRAD, parameters=_recorded("finite_difference_gradient")),
    )
    assert resp.status_code == 422, resp.text[:600]
    body = resp.json()
    assert body["code"] == "hessian_method_contradicts_input_deck"
    assert body["context"]["recorded"] == "finite_difference_gradient"
    assert body["context"]["input_deck"] == "finite_difference_energy"


def test_the_deck_alone_with_numgrad_is_judged_at_the_energy_tau(client, db_session):
    resp = client.post(
        "/api/v1/uploads/computed-species", json=_payload("ORCA", 0.13, _ORCA_NUMFREQ_NUMGRAD)
    )
    assert resp.status_code == 201, resp.text[:600]
    freq, _ = _stored(db_session)
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (80.0, "finite_difference_energy")


def test_numfreq_with_anfreq_is_judged_stored_and_recorded_as_one_numerical_method(client, db_session):
    deck = "! B3LYP def2-SVP NumFreq AnFreq\n* xyz 0 1\nC 0 0 0\n*\n"
    resp = client.post("/api/v1/uploads/computed-species", json=_payload("ORCA", 0.14, deck))
    assert resp.status_code == 201, resp.text[:600]
    freq, rows = _stored(db_session)
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (50.0, "finite_difference_gradient")
    # One stored row for the method, and it agrees with the judged tau.
    assert [r for r in rows if r[0] == "freq.hessian_method"] == [("freq.hessian_method", "finite_difference_gradient")]
    again = resolve_tau_from_parameters(rows)
    assert (again.tau_cm1, again.basis.value) == (50.0, "finite_difference_gradient")
