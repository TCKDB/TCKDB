"""Jobs API tests for transport async parity.

Exercises the ``POST /api/v1/jobs/transport`` enqueue endpoint and the
``GET /api/v1/jobs/{job_id}`` status endpoint for transport jobs. The
worker loop is not run here — worker dispatch is covered in
``tests/workers/test_upload_worker.py``.
"""

from __future__ import annotations

from app.db.models.common import UploadJobKind, UploadJobStatus
from app.db.models.upload_job import UploadJob


def _transport_payload(**overrides) -> dict:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
        "dipole_debye": 0.0,
        "polarizability_angstrom3": 0.667,
        "rotational_relaxation": 0.0,
        "note": "H atom transport",
    }
    base.update(overrides)
    return base


class TestEnqueueTransportJob:
    def test_enqueues_transport_job(self, client, db_session):
        resp = client.post("/api/v1/jobs/transport", json=_transport_payload())
        assert resp.status_code == 202
        body = resp.json()
        assert body["kind"] == UploadJobKind.transport.value
        assert body["status"] == UploadJobStatus.queued.value
        job_id = body["job_id"]

        persisted = db_session.get(UploadJob, job_id)
        assert persisted is not None
        assert persisted.kind == UploadJobKind.transport
        assert persisted.status == UploadJobStatus.queued
        assert persisted.payload["sigma_angstrom"] == 2.05

    def test_status_round_trip_for_transport_job(self, client):
        enqueue = client.post(
            "/api/v1/jobs/transport", json=_transport_payload()
        )
        assert enqueue.status_code == 202
        job_id = enqueue.json()["job_id"]

        status = client.get(f"/api/v1/jobs/{job_id}")
        assert status.status_code == 200
        body = status.json()
        assert body["kind"] == UploadJobKind.transport.value
        assert body["status"] == UploadJobStatus.queued.value

    def test_invalid_transport_payload_returns_422(self, client):
        bad = _transport_payload(sigma_angstrom=0.0)  # must be > 0
        resp = client.post("/api/v1/jobs/transport", json=bad)
        assert resp.status_code == 422
