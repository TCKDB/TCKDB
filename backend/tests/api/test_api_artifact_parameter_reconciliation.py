"""An output log attached to a calculation is read for parameters and checked against what was typed.

Through the real artifact route: the response carries the warnings, the calculation's read shows them, and the
rows are stored tagged with the artifact kind they came from.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationFreqResult, CalculationParameter
from app.db.models.common import ArtifactKind, ParameterSource
from tests.api.artifact_urls import artifacts_url
from tests.api.test_api_minimum_imaginary_tau import _payload

_DASHES = "-" * 70
_TYPED = [
    {
        "raw_key": "grid",
        "raw_value": "ultrafine",
        "canonical_key": "grid.quality",
        "canonical_value": "ultrafine",
        "section": "integral",
    }
]


def _log(route: str) -> str:
    return f" Entering Gaussian System, Link 0=g16\n Gaussian 16:  ES64L-G16RevC.01 3-Jul-2019\n {_DASHES}\n {route}\n {_DASHES}\n"


@pytest.fixture(autouse=True)
def _stub_store(monkeypatch):
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact",
        lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}",
    )


def _calc(client, db_session, label: str) -> Calculation:
    resp = client.post("/api/v1/uploads/conformers", json=_payload(imag_freq_cm1=-20.0, parameters=_TYPED, label=label))
    assert resp.status_code == 201, resp.text
    freq = db_session.scalars(select(CalculationFreqResult).order_by(CalculationFreqResult.calculation_id.desc())).first()
    return db_session.get(Calculation, freq.calculation_id)


def _attach(client, calc: Calculation, kind: str, text: str, filename: str):
    resp = client.post(
        artifacts_url(client, calc.id),
        json={"artifacts": [{"kind": kind, "filename": filename, "content_base64": base64.b64encode(text.encode()).decode()}]},
    )
    assert resp.status_code == 201, resp.text
    return resp


def test_a_log_that_contradicts_a_typed_parameter_warns_and_its_rows_are_tagged_output_log(client, db_session):
    calc = _calc(client, db_session, "log-disagrees")
    resp = _attach(client, calc, "output_log", _log("#P b3lyp/6-31g(d) freq int=fine"), "job.log")

    [warning] = [w for w in resp.json()["warnings"] if w["code"] == "parameter_disagrees_with_artifact"]
    assert warning["context"]["canonical_key"] == "grid.quality"
    assert warning["context"]["observed_in"] == "output_log"
    assert "fine" not in warning["message"].replace("define", "") and "ultrafine" not in warning["message"]

    db_session.expire_all()
    rows = db_session.scalars(
        select(CalculationParameter).where(
            CalculationParameter.calculation_id == calc.id, CalculationParameter.source == ParameterSource.parser
        )
    ).all()
    assert rows and all(r.artifact_kind is ArtifactKind.output_log for r in rows)

    body = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}?include=parameters").json()
    by_source = {(p["raw_key"], p["source"]) for p in body["record"]["parameters"]}
    assert ("grid", "upload") in by_source and ("fine", "parser") in by_source
    [fragment] = [w for w in body["record_warnings"] if w["record_ref"] == calc.public_ref]
    assert "parameter_disagrees_with_artifact" in {item["code"] for item in fragment["items"]}


def test_a_log_that_agrees_with_the_typed_parameter_warns_nothing(client, db_session):
    calc = _calc(client, db_session, "log-agrees")
    resp = _attach(client, calc, "output_log", _log("#P b3lyp/6-31g(d) freq int=ultrafine"), "job.log")
    assert "parameter_disagrees_with_artifact" not in {w["code"] for w in resp.json()["warnings"]}


def test_a_deck_attached_after_the_log_replaces_only_the_overlap(client, db_session):
    calc = _calc(client, db_session, "log-then-deck")
    _attach(client, calc, "output_log", _log("#P b3lyp/6-31g(d) freq int=ultrafine scf=tight"), "job.log")
    resp = _attach(client, calc, "input", "#P b3lyp/6-31g(d) freq int=ultrafine\n", "job.gjf")
    assert "input_deck_disagrees_with_output_log" not in {w["code"] for w in resp.json()["warnings"]}
    db_session.expire_all()
    kinds = {
        r.canonical_key: r.artifact_kind
        for r in db_session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id,
                CalculationParameter.source == ParameterSource.parser,
                CalculationParameter.canonical_key.is_not(None),
            )
        )
    }
    assert kinds["grid.quality"] is ArtifactKind.input
    assert kinds["scf.convergence"] is ArtifactKind.output_log


def _post(client, calc, artifacts):
    resp = client.post(
        artifacts_url(client, calc.id),
        json={
            "artifacts": [
                {"kind": kind, "filename": name, "content_base64": base64.b64encode(text.encode()).decode()}
                for kind, name, text in artifacts
            ]
        },
    )
    assert resp.status_code == 201, resp.text
    return resp


def test_one_request_with_a_deck_and_a_log_warns_the_same_in_either_order(client, db_session, monkeypatch):
    """The deck is silent on the grid, the log says fine, the depositor typed ultrafine: both orders warn once."""
    stored: dict[str, bytes] = {}

    def fake_store(content, sha256):
        stored[sha256] = content
        return f"s3://test-bucket/{sha256[:2]}/{sha256}"

    monkeypatch.setattr("app.services.artifact_persistence.store_artifact", fake_store)
    monkeypatch.setattr(
        "app.services.calculation_parameter_extraction.load_artifact_bytes", lambda digest, **_: stored[digest]
    )
    deck = ("input", "job.gjf", "#P b3lyp/6-31g(d) freq scf=tight\n")
    log = ("output_log", "job.log", _log("#P b3lyp/6-31g(d) freq int=fine scf=tight"))
    seen = {}
    for name, order in (("deck-first", [deck, log]), ("log-first", [log, deck])):
        calc = _calc(client, db_session, f"order-{name}")
        resp = _post(client, calc, order)
        seen[name] = sorted(w["code"] for w in resp.json()["warnings"] if w["code"] != "software_release_identity_corrected_from_artifact")
    assert seen["deck-first"] == seen["log-first"]
    assert seen["deck-first"].count("parameter_disagrees_with_artifact") == 1
