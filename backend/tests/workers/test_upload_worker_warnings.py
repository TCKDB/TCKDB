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
from tckdb_schemas.deposit_warning_messages import render_message

import app.workers.upload_worker as upload_worker
from app.db.models.common import UploadJobKind
from app.db.models.upload_job import UploadJob
from app.services.upload_reconciliation import W_TERM_SYMBOL_CONTRADICTS_MULTIPLICITY
from tests.api.test_api_bundle_upload_warnings import _kinetics_bundle, _thermo_record
from tests.api.test_api_composite_program_run import _CBS_QB3, _sp
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


def _vdw_freq_calc(geometry_key: str | None, key: str) -> dict:
    """A frequency job reporting one imaginary mode, on a species declared a
    van der Waals complex: a warning the request alone determines."""
    return {
        "key": key,
        "type": "freq",
        "geometry_key": geometry_key,
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        "freq_n_imag": 1,
        "freq_imag_freq_cm1": -22.0,
    }


def _pdep() -> dict:
    payload = _pdep_payload()
    payload["species"][0]["species_entry"]["species_entry_kind"] = "vdw_complex"
    payload["species"][0]["calculations"].append(_vdw_freq_calc("ethyl_geom", "ethyl_freq"))
    # An sp at a named composite method's level is a misshapen deposit the
    # workflow reports (ADR 0021, decision 7).
    payload["species"][0]["calculations"][0]["level_of_theory"] = {"method": "CBS-QB3"}
    return payload


def _computed_reaction() -> dict:
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [_correction("composite_delta")]
    species = payload["species"][0]
    species["species_entry"]["species_entry_kind"] = "vdw_complex"
    species["calculations"].append(_vdw_freq_calc(species["calculations"][0].get("geometry_key"), "x-freq"))
    return payload


def _transport() -> dict:
    payload = _transport_payload()
    # An inline sp at a named composite method's level, as the thermo case has.
    payload["calculations"] = [{"key": "x", "calculation": _sp(_CBS_QB3)}]
    payload["source_calculations"] = [{"calculation_key": "x", "role": "supporting_geometry"}]
    return payload


def _kinetics() -> dict:
    record = _kinetics_bundle()["records"]["kinetics_uploads"][0]
    record["literature"] = {"doi": "10.1063/1.555991", "title": "A Completely Different Paper"}
    return record


#: kind -> (payload, direct route, job route, warnings the record must earn).
#: Where a kind has both, one is derived from the request alone and one is
#: reported by the workflow, so dropping either half is caught.
CASES: dict[UploadJobKind, tuple] = {
    UploadJobKind.thermo: (
        _thermo_record,
        "/api/v1/uploads/thermo",
        "/api/v1/jobs/thermo",
        ("composite_delta_prefer_scheme_terms", "named_composite_deposited_as_sp", "missing_software_release_provenance"),
    ),
    UploadJobKind.transition_state: (
        lambda: _transition_state_payload(n_imag=1, imag_freq_cm1=-30.0),
        "/api/v1/uploads/transition-states",
        "/api/v1/jobs/transition-state",
        ("transition_state_imaginary_frequency_too_small", "reaction_atom_map_absent"),
    ),
    UploadJobKind.conformer: (
        _conformer,
        "/api/v1/uploads/conformers",
        "/api/v1/jobs/conformer",
        # One from the request, one the workflow reports on its outcome.
        ("n_imag_contradicts_minimum", "dependency_edge_not_inferred"),
    ),
    UploadJobKind.reaction: (
        _reaction,
        "/api/v1/uploads/reactions",
        "/api/v1/jobs/reaction",
        (W_TERM_SYMBOL_CONTRADICTS_MULTIPLICITY,),
    ),
    UploadJobKind.kinetics: (
        _kinetics,
        "/api/v1/uploads/kinetics",
        "/api/v1/jobs/kinetics",
        ("missing_kinetics_interpretation_assignments", "literature_title_mismatch"),
    ),
    UploadJobKind.network: (
        _network,
        "/api/v1/uploads/networks",
        "/api/v1/jobs/network",
        ("literature_title_mismatch",),
    ),
    UploadJobKind.network_pdep: (
        _pdep,
        "/api/v1/uploads/networks/pdep",
        "/api/v1/jobs/network/pdep",
        ("n_imag_contradicts_minimum", "named_composite_deposited_as_sp"),
    ),
    UploadJobKind.transport: (
        _transport,
        "/api/v1/uploads/transport",
        "/api/v1/jobs/transport",
        ("missing_software_release_provenance", "named_composite_deposited_as_sp"),
    ),
    UploadJobKind.computed_reaction: (
        _computed_reaction,
        "/api/v1/uploads/computed-reaction",
        "/api/v1/jobs/computed-reaction",
        ("composite_delta_prefer_scheme_terms", "n_imag_contradicts_minimum"),
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
    assert set(required) <= {w["code"] for w in expected}, expected

    job = _enqueue(client, job_url, payload)
    upload_worker.run_one_job(client._db_session, job)

    read = client.get(f"/api/v1/jobs/{job.id}")
    assert read.status_code == 200, read.text[:600]
    result = read.json()["result"]
    assert "result_unavailable" not in result, "the warnings could not be stored in the job result"
    assert result["warnings"] == expected
    # And the sentence is TCKDB's for the code, the same one the direct route, the stored row and the public read
    # carry (#838): never text the depositor typed.
    assert result["warnings"], "nothing to compare"
    for warning in result["warnings"]:
        assert warning["message"] == render_message(warning["code"], warning["context"]), warning["code"]


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
    assert set(required) <= {w["code"] for w in first}
    assert second == first


def test_the_scan_coordinate_warning_is_the_same_on_the_direct_route_and_the_job(client) -> None:
    """A scan whose stored values disagree with its geometries (#835) earns one warning, and the job reports it
    exactly as the direct route does (the check runs after the flush, so the worker has to run it too)."""
    from tests.api.test_api_scan_coordinate_warning import CODE, h2_scan_conformer

    payload = h2_scan_conformer(0.5)
    expected = _direct(client, "/api/v1/uploads/conformers", payload)
    assert [w["code"] for w in expected].count(CODE) == 1, expected

    job = _enqueue(client, "/api/v1/jobs/conformer", payload)
    upload_worker.run_one_job(client._db_session, job)
    result = client.get(f"/api/v1/jobs/{job.id}").json()["result"]
    assert "result_unavailable" not in result
    assert result["warnings"] == expected


def test_a_stale_recorded_warning_is_re_rendered_when_the_job_result_is_assembled(client) -> None:
    """A warning recorded on a job before this wording (#838) carries a message that quoted the depositor.

    The recorded text is data: the job result says the sentence for the code and context, whatever was stored.
    """
    build, _direct_url, job_url, _required = CASES[UploadJobKind.thermo]
    job = _enqueue(client, job_url, build())
    stale_context = {"declared": 0, "logged": -1, "software": "gaussian"}
    job.request_warnings = [
        {
            "field": "ZZQ['stale-key'].charge",
            "code": "charge_mismatch",
            "message": "ZZQ stale quoted 'stale-key'",
            "context": stale_context,
        }
    ]
    client._db_session.flush()
    upload_worker.run_one_job(client._db_session, job)

    result = client.get(f"/api/v1/jobs/{job.id}").json()["result"]
    (rebuilt,) = [w for w in result["warnings"] if w["code"] == "charge_mismatch"]
    assert rebuilt["message"] == render_message("charge_mismatch", stale_context)
    assert "ZZQ" not in rebuilt["message"] and "stale" not in rebuilt["message"]
    assert rebuilt["field"] == "ZZQ['stale-key'].charge"


@pytest.mark.parametrize("route", ["thermo", "conformer", "pdep"])
def test_the_job_result_carries_the_atomic_electronic_warnings_the_direct_route_returns(client, route: str) -> None:
    """A single atom deposited as a job hears the same atomic warnings as the direct route (#836)."""
    from tests.api.test_atomic_warnings_every_route import ATOMIC, ROUTES, _aec

    build, direct_url, job_url = ROUTES[route]
    payload = build("O", levels=[] if route != "thermo" else None, corrections=[_aec()] if route != "pdep" else [])
    expected = _direct(client, direct_url, payload)
    atomic = [w for w in expected if w["code"] in ATOMIC]
    assert atomic, expected

    job = _enqueue(client, job_url, payload)
    upload_worker.run_one_job(client._db_session, job)

    result = client.get(f"/api/v1/jobs/{job.id}").json()["result"]
    assert "result_unavailable" not in result
    assert [w for w in result["warnings"] if w["code"] in ATOMIC] == atomic
    assert result["warnings"] == expected
