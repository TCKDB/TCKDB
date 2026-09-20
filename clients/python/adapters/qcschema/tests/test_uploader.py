"""Uploader tests against a stub client -- no network (C-Q1)."""

from __future__ import annotations

import hashlib
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


class _StubIdempotencyConflict(Exception):
    """Simulates ``tckdb_client.errors.TCKDBIdempotencyConflictError``.

    The real class is not imported here -- importing anything from
    ``tckdb_client`` (even just ``tckdb_client.errors``) pulls in
    ``tckdb_client/__init__.py``, which imports ``httpx``, and stages 1-4
    (and their tests, this module included) are deliberately network-
    dependency-free; only ``uploader.py`` itself imports ``tckdb_client``,
    and does so lazily. This local stand-in carries the same
    ``status_code``/``code`` shape a caller would actually branch on.
    """

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _canonical_body_hash(body) -> str:
    """Mirrors ``backend/app/services/idempotency.py:canonical_payload_hash``.

    Sort-keys, no-whitespace JSON, sha256'd -- so a payload that is not a
    pure function of the document (a wall-clock ``parameters_extracted_at``,
    for instance) hashes differently between two builds even though every
    other field is identical.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _RealIdempotencyStubClient:
    """A stub that actually enforces the backend's idempotency contract.

    Unlike ``_StubClient`` (which tracks only "has this exact idempotency
    key been POSTed before" and always replays on a repeat key regardless
    of body), this one hashes the request body per key -- same key + same
    body replays (``idempotency_replayed=True``, the stored response),
    same key + a DIFFERENT body raises
    ``tckdb_client.errors.TCKDBIdempotencyConflictError`` (409
    ``idempotency_conflict``), exactly as
    ``backend/app/services/idempotency.py:lookup_or_conflict`` does. This
    is what makes a non-deterministic payload (e.g. a wall-clock
    ``parameters_extracted_at``) observable: rebuilding the payload between
    two runs of the *same* document must still hash identically, or the
    rerun's conformer POST hits the conflict branch instead of replaying.
    """

    def __init__(self, *, search_records=None, fail_first_artifact=False):
        self.calls: list[tuple] = []
        self._search_records = search_records or []
        self._store: dict[str, tuple[str, dict]] = {}
        self._fail_first_artifact = fail_first_artifact
        self._artifact_call_count = 0

    def request_json(self, method, path, *, json=None, params=None, idempotency_key=None):
        self.calls.append((method, path, idempotency_key, json, params))
        if method == "GET" and path == "/scientific/artifacts/search":
            return _StubResponse({"records": self._search_records})

        assert idempotency_key is not None, "expected an idempotency key on every POST"

        if method == "POST" and path.endswith("/artifacts"):
            self._artifact_call_count += 1
            if self._fail_first_artifact and self._artifact_call_count == 1:
                raise RuntimeError("simulated network failure on first artifact POST")

        body_hash = _canonical_body_hash(json)
        if idempotency_key in self._store:
            stored_hash, stored_response = self._store[idempotency_key]
            if stored_hash != body_hash:
                raise _StubIdempotencyConflict(
                    "Idempotency key reused with a different request payload.",
                    status_code=409,
                    code="idempotency_conflict",
                )
            return _StubResponse(stored_response, replayed=True)

        if method == "POST" and path == "/uploads/conformers":
            response_data = {"primary_calculation": {"calculation_id": 42}}
        elif method == "POST" and path.endswith("/artifacts"):
            response_data = {"ok": True}
        else:
            raise AssertionError(f"unexpected call: {method} {path}")

        self._store[idempotency_key] = (body_hash, response_data)
        return _StubResponse(response_data, replayed=False)


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


def test_partial_run_recovery_first_post_replays_second_proceeds():
    """First run: artifact POST fails after the conformer POST succeeds.

    Second run: the payload is REBUILT from scratch (a fresh
    ``build_conformer_upload_payload`` call, as a second real CLI
    invocation would do) rather than reusing the first run's in-memory
    dict. Against a stub that enforces the backend's actual idempotency
    contract (same key + same body replays; same key + a DIFFERENT body
    raises 409 ``idempotency_conflict`` -- see
    ``_RealIdempotencyStubClient``), this is the regression guard for a
    payload that is not a pure function of the document: a wall-clock
    ``parameters_extracted_at`` would make the two builds' bodies differ,
    the rerun's conformer POST would hit the conflict branch instead of
    replaying, and this test would go red with
    ``TCKDBIdempotencyConflictError`` instead of asserting
    ``conformer_replayed is True`` below.
    """
    raw, _meta = load_case("energy_v1")
    record = read_document(raw)
    sha = sha256_bytes(raw)

    def _build_payload() -> dict:
        payload, _report = build_conformer_upload_payload(
            record,
            raw_bytes=raw,
            raw_artifact_filename="energy_v1.qcschema.json",
            raw_artifact_sha256=sha,
            declared_smiles="O",
        )
        return payload

    client = _RealIdempotencyStubClient(fail_first_artifact=True)

    first_payload = _build_payload()
    with pytest.raises(RuntimeError):
        upload_record(
            client,
            record,
            first_payload,
            raw_bytes=raw,
            raw_sha256=sha,
            artifact_filename="energy_v1.qcschema.json",
        )

    # Rerun: rebuild the payload from scratch. Same canonical document ->
    # same idempotency keys, and (once the payload is a pure function of
    # the document) the identical body too.
    second_payload = _build_payload()
    outcome = upload_record(
        client,
        record,
        second_payload,
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


def test_allow_duplicate_mints_a_fresh_key_each_run(energy_record_and_payload):
    """``--allow-duplicate`` must actually create a second deposit.

    Without a fresh nonce, two runs of the identical document would send
    the identical (idempotency_key, body) pair even with the flag set, and
    the backend would replay the first response instead of creating a
    second row -- the flag would then do nothing. With the nonce, the two
    runs' keys differ.
    """
    raw, sha, record, payload = energy_record_and_payload
    client = _RealIdempotencyStubClient()

    outcome_a = upload_record(
        client, record, payload, raw_bytes=raw, raw_sha256=sha,
        artifact_filename="energy_v1.qcschema.json", allow_duplicate=True,
    )
    outcome_b = upload_record(
        client, record, payload, raw_bytes=raw, raw_sha256=sha,
        artifact_filename="energy_v1.qcschema.json", allow_duplicate=True,
    )

    conformer_calls = [c for c in client.calls if c[1] == "/uploads/conformers"]
    assert len(conformer_calls) == 2
    key_a, key_b = conformer_calls[0][2], conformer_calls[1][2]
    assert key_a != key_b, "each --allow-duplicate run must mint its own idempotency key"
    stable_key = conformers_idempotency_key(record.canonical_sha256)
    assert key_a != stable_key and key_b != stable_key, (
        "a --allow-duplicate key must differ from the stable (no-flag) key too, "
        "not just from each other"
    )
    # Neither call should have replayed -- the stub never saw either key before.
    assert outcome_a.conformer_replayed is False
    assert outcome_b.conformer_replayed is False


def test_allow_duplicate_nonce_is_deterministic_when_supplied():
    """``duplicate_nonce`` overrides the random mint, for reproducible tests."""
    raw, _meta = load_case("energy_v1")
    record = read_document(raw)
    assert conformers_idempotency_key(
        record.canonical_sha256, nonce="abc"
    ) == conformers_idempotency_key(record.canonical_sha256, nonce="abc")
    assert conformers_idempotency_key(
        record.canonical_sha256, nonce="abc"
    ) != conformers_idempotency_key(record.canonical_sha256, nonce="def")
    assert conformers_idempotency_key(
        record.canonical_sha256
    ) != conformers_idempotency_key(record.canonical_sha256, nonce="abc")


def test_without_allow_duplicate_keys_stay_stable_across_runs(energy_record_and_payload):
    """The default (no flag) path is unaffected by the nonce mechanism."""
    raw, sha, record, payload = energy_record_and_payload
    client = _StubClient()
    upload_record(
        client, record, payload, raw_bytes=raw, raw_sha256=sha,
        artifact_filename="energy_v1.qcschema.json",
    )
    upload_record(
        client, record, payload, raw_bytes=raw, raw_sha256=sha,
        artifact_filename="energy_v1.qcschema.json",
    )
    conformer_calls = [c for c in client.calls if c[1] == "/uploads/conformers"]
    assert len(conformer_calls) == 2
    assert conformer_calls[0][2] == conformer_calls[1][2] == conformers_idempotency_key(
        record.canonical_sha256
    )


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
