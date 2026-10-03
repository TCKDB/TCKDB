"""The upload worker must keep the warnings the direct routes return (#647).

What was wrong
--------------
Every ``POST /uploads/<kind>`` route returns the warnings its record earns. The
background job worker ran the same workflows without a warnings sink, never
assembled the request-derived ones, and wrote a result with no ``warnings`` at
all, so a depositor polling ``GET /jobs/{id}`` never saw what the same record
sent directly would have shown.

How these tests pin it
----------------------
For each job kind: the direct route answers the record (inside a savepoint that
is rolled back), the record is then enqueued and run through ``run_one_job``,
and the stored job result -- read back through ``GET /jobs/{id}`` -- must carry
the same ``warnings`` in the same order. Comparing with the direct route rather
than a written-out list means a warning added to a route tomorrow is expected
here without editing this file. Each case also names one warning it must
contain, so two empty lists cannot pass.
"""

from __future__ import annotations

import pytest

import app.workers.upload_worker as upload_worker
from app.db.models.common import UploadJobKind
from app.db.models.upload_job import UploadJob
from app.services.upload_reconciliation import W_TERM_SYMBOL_CONTRADICTS_MULTIPLICITY
from tests.api.test_api_bundle_upload_warnings import _kinetics_bundle, _thermo_record
from tests.api.test_api_network_reads import _pdep_payload
from tests.api.test_api_scheme_frequency_level import _correction, _payload_with_aec_carriers
from tests.api.test_api_transport_upload import _transport_payload
from tests.api.test_api_uploads import (
    _freq_calc,
    _hydrogen_conformer_payload,
    _reaction_payload,
    _transition_state_payload,
)


@pytest.fixture(autouse=True)
def _doi_metadata(monkeypatch):
    """The DOI's own title, so a depositor title that disagrees earns a warning
    without a network call."""
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Enthalpy of formation of water",
            "container-title": ["J. Phys. Chem. Ref. Data"],
            "issued": 1998,
            "URL": f"https://doi.org/{doi}",
        },
    )


def _conformer() -> dict:
    payload = _hydrogen_conformer_payload()
    payload["species_entry"]["species_entry_kind"] = "vdw_complex"
    payload["additional_calculations"] = [_freq_calc(n_imag=1, imag_freq_cm1=-22.0)]
    return payload


def _reaction() -> dict:
    payload = _reaction_payload()
    # ``3P`` states multiplicity 3 beside a declared multiplicity of 2.
    payload["reactants"][0]["species_entry"]["term_symbol"] = "3P"
    return payload


def _network() -> dict:
    return {
        "name": "warning parity network",
        "literature": {"doi": "10.1063/1.555991", "title": "A Completely Different Paper"},
    }


def _pdep() -> dict:
    payload = _pdep_payload()
    # An sp at a named composite method's level is a misshapen deposit the
    # workflow reports (ADR 0021, decision 7).
    payload["species"][0]["calculations"][0]["level_of_theory"] = {"method": "CBS-QB3"}
    return payload


def _computed_reaction() -> dict:
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [_correction("composite_delta")]
    return payload


#: kind -> (payload, direct route, job route, a warning the record must earn)
CASES: dict[UploadJobKind, tuple] = {
    UploadJobKind.thermo: (
        _thermo_record,
        "/api/v1/uploads/thermo",
        "/api/v1/jobs/thermo",
        "composite_delta_prefer_scheme_terms",
    ),
    UploadJobKind.transition_state: (
        lambda: _transition_state_payload(n_imag=1, imag_freq_cm1=-30.0),
        "/api/v1/uploads/transition-states",
        "/api/v1/jobs/transition-state",
        "transition_state_imaginary_frequency_too_small",
    ),
    UploadJobKind.conformer: (
        _conformer,
        "/api/v1/uploads/conformers",
        "/api/v1/jobs/conformer",
        # One from the request, one the workflow reports on its outcome.
        "dependency_edge_not_inferred",
    ),
    UploadJobKind.reaction: (
        _reaction,
        "/api/v1/uploads/reactions",
        "/api/v1/jobs/reaction",
        W_TERM_SYMBOL_CONTRADICTS_MULTIPLICITY,
    ),
    UploadJobKind.kinetics: (
        lambda: _kinetics_bundle()["records"]["kinetics_uploads"][0],
        "/api/v1/uploads/kinetics",
        "/api/v1/jobs/kinetics",
        "missing_kinetics_interpretation_assignments",
    ),
    UploadJobKind.network: (
        _network,
        "/api/v1/uploads/networks",
        "/api/v1/jobs/network",
        "literature_title_mismatch",
    ),
    UploadJobKind.network_pdep: (
        _pdep,
        "/api/v1/uploads/networks/pdep",
        "/api/v1/jobs/network/pdep",
        "named_composite_deposited_as_sp",
    ),
    UploadJobKind.transport: (
        _transport_payload,
        "/api/v1/uploads/transport",
        "/api/v1/jobs/transport",
        "missing_software_release_provenance",
    ),
    UploadJobKind.computed_reaction: (
        _computed_reaction,
        "/api/v1/uploads/computed-reaction",
        "/api/v1/jobs/computed-reaction",
        "composite_delta_prefer_scheme_terms",
    ),
}


def test_every_job_kind_has_a_case() -> None:
    """A new job kind must say what warning its handler is held to."""
    assert set(CASES) == set(upload_worker._DISPATCH)


def _direct(client, url: str, payload: dict) -> list[dict]:
    """The warnings the direct route answers for ``payload``, with its writes undone."""
    savepoint = client._db_session.begin_nested()
    try:
        resp = client.post(url, json=payload)
    finally:
        savepoint.rollback()
    assert resp.status_code == 201, resp.text[:600]
    return resp.json()["warnings"]


def _enqueue(client, url: str, payload: dict) -> UploadJob:
    resp = client.post(url, json=payload)
    assert resp.status_code == 202, resp.text[:600]
    return client._db_session.get(UploadJob, resp.json()["job_id"])


@pytest.mark.parametrize("kind", list(CASES), ids=lambda k: k.value)
def test_the_job_result_carries_the_warnings_the_direct_route_returns(client, kind: UploadJobKind) -> None:
    build, direct_url, job_url, required = CASES[kind]
    payload = build()
    expected = _direct(client, direct_url, payload)
    assert required in {w["code"] for w in expected}, expected

    job = _enqueue(client, job_url, payload)
    upload_worker.run_one_job(client._db_session, job)

    read = client.get(f"/api/v1/jobs/{job.id}")
    assert read.status_code == 200, read.text[:600]
    result = read.json()["result"]
    assert "result_unavailable" not in result, "the warnings could not be stored in the job result"
    assert result["warnings"] == expected


@pytest.mark.parametrize("kind", list(CASES), ids=lambda k: k.value)
def test_a_retried_attempt_reports_each_warning_once(client, kind: UploadJobKind) -> None:
    """A failed attempt rolls back and the next one runs the handler again; its
    warnings must be its own, not the first attempt's added to its own."""
    build, _direct_url, job_url, required = CASES[kind]
    job = _enqueue(client, job_url, build())
    handler = upload_worker._DISPATCH[kind]
    session = client._db_session

    attempts = []
    for _ in range(2):
        savepoint = session.begin_nested()
        try:
            attempts.append(handler(session, job, upload_worker.ReviewPolicy())["warnings"])
        finally:
            savepoint.rollback()

    first, second = attempts
    assert required in {w["code"] for w in first}
    assert second == first
