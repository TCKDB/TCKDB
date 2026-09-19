"""Uploader tests against a stub client -- no network (C-Q1)."""

from __future__ import annotations

import json

import pytest

from tckdb_qcschema.errors import E_ALREADY_IMPORTED, E_ARTIFACT_TOO_LARGE, QCSchemaAdapterError
from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import (
    MAX_ARTIFACT_BYTES,
    artifact_idempotency_key,
    conformers_idempotency_key,
    precheck_duplicate,
    sha256_bytes,
    upload_record,
)

from conftest import load_case


class _StubResponse:
    def __init__(self, data, *, replayed=False):
        self.data = data
        self.idempotency_replayed = replayed


class _StubClient:
    """Records every call; never touches the network."""

    def __init__(self, *, search_records=None, conformer_data=None, fail_first_artifact=False):
        self.calls: list[tuple] = []
        self._search_records = search_records or []
        self._conformer_data = conformer_data or {
            "primary_calculation": {"calculation_id": 42}
        }
        self._fail_first_artifact = fail_first_artifact
        self._artifact_call_count = 0
        self._conformer_posted = False

    def request_json(self, method, path, *, json=None, params=None, idempotency_key=None):
        self.calls.append((method, path, idempotency_key, json, params))
        if method == "GET" and path == "/scientific/artifacts/search":
            return _StubResponse({"records": self._search_records})
        if method == "POST" and path == "/uploads/conformers":
            replayed = self._conformer_posted
            self._conformer_posted = True
            return _StubResponse(self._conformer_data, replayed=replayed)
        if method == "POST" and path.endswith("/artifacts"):
            self._artifact_call_count += 1
            if self._fail_first_artifact and self._artifact_call_count == 1:
                raise RuntimeError("simulated network failure on first artifact POST")
            return _StubResponse({"ok": True}, replayed=False)
        raise AssertionError(f"unexpected call: {method} {path}")


@pytest.fixture
def energy_record_and_payload():
    raw, _meta = load_case("energy_v1")
    record = read_document(raw)
    sha = sha256_bytes(raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename="energy_v1.qcschema.json",
        raw_artifact_sha256=sha,
        declared_smiles="O",
    )
    return raw, sha, record, payload


def test_idempotency_keys_are_stable_across_reserialisation():
    raw, meta = load_case("energy_v1")
    document = json.loads(raw)

    reserialised_a = json.dumps(document, indent=2).encode()
    reserialised_b = json.dumps(dict(sorted(document.items(), reverse=True))).encode()

    record_a = read_document(reserialised_a)
    record_b = read_document(reserialised_b)
    record_c = read_document(raw)

    assert record_a.canonical_sha256 == record_b.canonical_sha256 == record_c.canonical_sha256
    assert (
        conformers_idempotency_key(record_a.canonical_sha256)
        == conformers_idempotency_key(record_c.canonical_sha256)
    )
    assert (
        artifact_idempotency_key(record_a.canonical_sha256)
        == artifact_idempotency_key(record_c.canonical_sha256)
    )
    # Two keys derived from the same document must differ from each other.
    assert conformers_idempotency_key(record_a.canonical_sha256) != artifact_idempotency_key(
        record_a.canonical_sha256
    )


def test_keys_change_for_a_logically_different_document():
    raw_a, _ = load_case("energy_v1")
    raw_b, _ = load_case("gradient_v1")
    rec_a = read_document(raw_a)
    rec_b = read_document(raw_b)
    assert conformers_idempotency_key(rec_a.canonical_sha256) != conformers_idempotency_key(
        rec_b.canonical_sha256
    )


def test_precheck_refuses_already_imported(energy_record_and_payload):
    raw, sha, record, payload = energy_record_and_payload
    client = _StubClient(
        search_records=[{"calculation": {"calculation_id": 7}, "artifact": {"sha256": sha}}]
    )
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        precheck_duplicate(client, sha, allow_duplicate=False)
    assert excinfo.value.code == E_ALREADY_IMPORTED


def test_precheck_allows_duplicate_when_flag_set(energy_record_and_payload):
    raw, sha, record, payload = energy_record_and_payload
    client = _StubClient(search_records=[{"calculation": {"calculation_id": 7}}])
    # Must not raise.
    precheck_duplicate(client, sha, allow_duplicate=True)


def test_precheck_passes_with_no_matching_records(energy_record_and_payload):
    raw, sha, record, payload = energy_record_and_payload
    client = _StubClient(search_records=[])
    precheck_duplicate(client, sha, allow_duplicate=False)


def test_upload_record_full_success(energy_record_and_payload):
    raw, sha, record, payload = energy_record_and_payload
    client = _StubClient()
    outcome = upload_record(
        client,
        record,
        payload,
        raw_bytes=raw,
        raw_sha256=sha,
        artifact_filename="energy_v1.qcschema.json",
    )
    assert outcome.calculation_id == 42
    assert outcome.conformer_replayed is False
    assert outcome.artifact_replayed is False

    # Exactly one GET (precheck), one POST conformers, one POST artifacts.
    methods_paths = [(m, p) for (m, p, *_rest) in client.calls]
    assert methods_paths == [
        ("GET", "/scientific/artifacts/search"),
        ("POST", "/uploads/conformers"),
        ("POST", "/calculations/42/artifacts"),
    ]


def test_partial_run_recovery_first_post_replays_second_proceeds(energy_record_and_payload):
    """First run: artifact POST fails after the conformer POST succeeds.

    Second run with the same client (same server-side idempotency state
    simulated by the stub remembering it already saw the conformers POST):
    the conformer POST replays (idempotency_replayed=True) while the
    artifact POST is attempted fresh and this time succeeds.
    """
    raw, sha, record, payload = energy_record_and_payload
    client = _StubClient(fail_first_artifact=True)

    with pytest.raises(RuntimeError):
        upload_record(
            client,
            record,
            payload,
            raw_bytes=raw,
            raw_sha256=sha,
            artifact_filename="energy_v1.qcschema.json",
        )

    # Rerun: same client, same canonical document -> same keys.
    outcome = upload_record(
        client,
        record,
        payload,
        raw_bytes=raw,
        raw_sha256=sha,
        artifact_filename="energy_v1.qcschema.json",
    )
    assert outcome.conformer_replayed is True, "conformer POST should have replayed on rerun"
    assert outcome.artifact_replayed is False, "artifact POST should have proceeded fresh on rerun"
    assert outcome.calculation_id == 42

    conformer_calls = [c for c in client.calls if c[1] == "/uploads/conformers"]
    artifact_calls = [c for c in client.calls if c[1].endswith("/artifacts")]
    assert len(conformer_calls) == 2
    # Both conformer POSTs use the identical idempotency key.
    assert conformer_calls[0][2] == conformer_calls[1][2]
    assert len(artifact_calls) == 2
    assert artifact_calls[0][2] == artifact_calls[1][2]


def test_artifact_over_50mb_is_refused(energy_record_and_payload):
    _raw, sha, record, payload = energy_record_and_payload
    oversized = b"0" * (MAX_ARTIFACT_BYTES + 1)
    client = _StubClient()
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        upload_record(
            client,
            record,
            payload,
            raw_bytes=oversized,
            raw_sha256=sha256_bytes(oversized),
            artifact_filename="huge.qcschema.json",
        )
    assert excinfo.value.code == E_ARTIFACT_TOO_LARGE
    # Refused before any request is made.
    assert client.calls == []


def test_dry_run_sends_nothing(energy_record_and_payload):
    raw, sha, record, payload = energy_record_and_payload
    client = _StubClient()
    plan = upload_record(
        client,
        record,
        payload,
        raw_bytes=raw,
        raw_sha256=sha,
        artifact_filename="energy_v1.qcschema.json",
        dry_run=True,
    )
    assert client.calls == []
    assert plan["conformers_idempotency_key"] == conformers_idempotency_key(record.canonical_sha256)
    assert plan["artifact_idempotency_key"] == artifact_idempotency_key(record.canonical_sha256)
