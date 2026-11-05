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


# ---------------------------------------------------------------------------
# the echoed input, several logs, and the computed-reaction adapter
# ---------------------------------------------------------------------------

_ORCA_ECHO_NUMFREQ = (
    _BANNER + "INPUT FILE\n|  1> ! B3LYP def2-SVP NumFreq\n|  2> * xyz 0 1\n|  3> C 0 0 0\n|  4> *\n****END OF INPUT****\n"
)
_DASHES = " " + "-" * 70
_GAUSSIAN_ECHO_NUMER = (
    " Entering Gaussian System, Link 0=g16\n Gaussian 16:  ES64L-G16RevC.01 3-Jul-2019\n"
    f"{_DASHES}\n #p freq=numer b3lyp/def2tzvp\n{_DASHES}\n"
)


def test_a_log_that_only_echoes_a_numerical_request_is_judged_at_the_numerical_tau(client, db_session):
    refused = client.post("/api/v1/uploads/computed-species", json=_payload(0.21, log=_BANNER + "no input echo\n"))
    assert refused.status_code == 422
    resp = client.post("/api/v1/uploads/computed-species", json=_payload(0.22, log=_ORCA_ECHO_NUMFREQ))
    assert resp.status_code == 201, resp.text[:600]
    freq, rows = _stored(db_session)
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (50.0, "finite_difference_gradient")
    assert ("finite_difference_gradient", ParameterStatementKind.requested) in rows


def test_a_gaussian_log_that_only_echoes_freq_numer_is_judged_like_the_same_route_as_a_deck(client, db_session):
    payload = _computed_species_with_soft_freq("Gaussian", 0.23)
    payload["conformers"][0]["additional_calculations"][0]["artifacts"] = [
        _artifact("output_log", _GAUSSIAN_ECHO_NUMER, "job.log")
    ]
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:600]
    freq, _ = _stored(db_session)
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (50.0, "finite_difference_gradient")


def test_a_gaussian_log_that_echoes_two_routes_reads_nothing(client):
    two = _GAUSSIAN_ECHO_NUMER + f"{_DASHES}\n #p sp b3lyp/def2tzvp\n{_DASHES}\n"
    payload = _computed_species_with_soft_freq("Gaussian", 0.24)
    payload["conformers"][0]["additional_calculations"][0]["artifacts"] = [_artifact("output_log", two, "job.log")]
    assert client.post("/api/v1/uploads/computed-species", json=payload).status_code == 422


def test_two_logs_that_report_the_same_method_decide_and_two_that_differ_do_not(client, db_session):
    payload = _payload(0.25, log=_LOG_NUMERICAL)
    payload["conformers"][0]["additional_calculations"][0]["artifacts"].append(
        _artifact("output_log", _LOG_NUMERICAL, "second.out")
    )
    assert client.post("/api/v1/uploads/computed-species", json=payload).status_code == 201
    freq, _ = _stored(db_session)
    assert freq.imaginary_mode_tau_basis == "finite_difference_gradient"

    differ = _payload(0.26, log=_LOG_NUMERICAL)
    differ["conformers"][0]["additional_calculations"][0]["artifacts"].append(
        _artifact("output_log", _LOG_ANALYTIC, "second.out")
    )
    refused = client.post("/api/v1/uploads/computed-species", json=differ)
    assert refused.status_code == 422 and refused.json()["code"] == "n_imag_contradicts_minimum"


def _reaction_bundle_with_log(log: str, label: str) -> dict:
    from tests.api.test_api_bundle_provenance_warnings import _reaction_bundle

    bundle = _reaction_bundle()
    bundle["species"][0]["calculations"].append(
        {
            "key": f"h-freq-{label}",
            "type": "freq",
            "geometry_key": "h-geom",
            "software_release": {"name": "ORCA", "version": "6.1.0"},
            "level_of_theory": {"method": "B3LYP", "basis": "def2-SVP"},
            "freq_n_imag": 1,
            "freq_imag_freq_cm1": -40.0,
            "artifacts": [_artifact("output_log", log, "job.out")],
        }
    )
    return bundle


@pytest.mark.parametrize(("log", "label"), [(_LOG_NUMERICAL, "rep"), (_ORCA_ECHO_NUMFREQ, "echo")])
def test_computed_reaction_stores_the_tau_a_reported_or_echoed_method_was_judged_at(client, db_session, log, label):
    """The reaction bundle adapter carries the report and the echo to persistence."""
    resp = client.post("/api/v1/uploads/computed-reaction", json=_reaction_bundle_with_log(log, label))
    assert resp.status_code == 201, resp.text[:600]
    freq = db_session.scalars(select(CalculationFreqResult).where(CalculationFreqResult.n_imag == 1)).one()
    assert (freq.imaginary_mode_tau_cm1, freq.imaginary_mode_tau_basis) == (50.0, "finite_difference_gradient")
    rows = db_session.execute(
        select(CalculationParameter.canonical_value, CalculationParameter.statement_kind).where(
            CalculationParameter.calculation_id == freq.calculation_id,
            CalculationParameter.canonical_key == "freq.hessian_method",
        )
    ).all()
    kind = ParameterStatementKind.reported if label == "rep" else ParameterStatementKind.requested
    assert ("finite_difference_gradient", kind) in rows


def _typed(value: str) -> list[dict]:
    return [{"raw_key": "freq", "raw_value": value, "canonical_key": "freq.hessian_method", "canonical_value": value}]


def test_a_typed_method_that_contradicts_the_echo_is_refused_and_an_agreeing_one_accepted(client):
    contradicting = _payload(0.31, log=_ORCA_ECHO_NUMFREQ)
    contradicting["conformers"][0]["additional_calculations"][0]["parameters"] = _typed("analytic")
    resp = client.post("/api/v1/uploads/computed-species", json=contradicting)
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "hessian_method_contradicts_input_deck"

    agreeing = _payload(0.32, log=_ORCA_ECHO_NUMFREQ)
    agreeing["conformers"][0]["additional_calculations"][0]["parameters"] = _typed("finite_difference_gradient")
    assert client.post("/api/v1/uploads/computed-species", json=agreeing).status_code == 201


def test_a_typed_method_that_contradicts_only_a_report_is_accepted_and_warned_about_at_read(client):
    payload = _payload(0.33, log=_LOG_NUMERICAL)
    payload["conformers"][0]["additional_calculations"][0]["parameters"] = _typed("analytic")
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:600]
    assert "reported_setting_differs_from_requested" in {w["code"] for w in resp.json()["warnings"]}


def test_computed_reaction_without_a_log_is_refused_at_the_assumed_tau(client):
    resp = client.post("/api/v1/uploads/computed-reaction", json=_reaction_bundle_with_log(_BANNER + "nothing\n", "none"))
    assert resp.status_code == 422, resp.text[:600]
