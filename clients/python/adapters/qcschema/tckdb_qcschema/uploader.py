"""Idempotent upload of a mapped QCSchema record via ``tckdb-client`` (C-Q1).

The only stage that touches the network. ``tckdb_client`` is imported
lazily so stages 1-4 (and their tests) never require it.

Idempotency
-----------
Both request keys derive from the canonical-JSON SHA-256 of the *parsed*
document (``record.canonical_sha256``, from :mod:`tckdb_qcschema.reader`),
not from the raw file bytes -- so a byte-different but logically identical
re-serialisation of the same document (different key order, different
whitespace) produces the same keys and replays rather than duplicating:

* ``qcschema:<canonical_sha[:32]>:conformers``
* ``qcschema:<canonical_sha[:32]>:artifact``

Pre-check and recovery
-----------------------
Before posting, the *raw file bytes'* SHA-256 (not the canonical-document
hash -- this is checking "have these exact bytes been deposited before",
which is what the artifact-search route indexes) is looked up via the
scientific artifact search route's ``sha256`` filter. A hit refuses
``already_imported`` unless ``--allow-duplicate``. This is a best-effort
courtesy, not a guarantee: two concurrent importers race past it, keys are
per-user and time-limited, and TCKDB has no server-side content dedupe --
see the plan's stated limits.

A partial prior run (conformer posted, artifact never reached the server)
recovers on rerun because both POSTs are attempted every time with the
same deterministic keys: the conformer POST replays (the server returns
the stored 201 body with an idempotency-replayed marker) while the
artifact POST proceeds for the first time.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from .errors import E_ALREADY_IMPORTED, E_ARTIFACT_TOO_LARGE, QCSchemaAdapterError
from .reader import QCRecord

#: Refused above this size, per the plan's stated artifact cap.
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024

#: Search endpoint queried for a raw-bytes duplicate before posting.
_ARTIFACT_SEARCH_PATH = "/scientific/artifacts/search"

#: Both posts go through ``client.request_json`` directly rather than the
#: ``client.upload`` / ``client.upload_artifact`` convenience wrappers.
#: Those wrappers call ``post_json``, which unwraps straight to the
#: response body and discards the ``Idempotency-Replayed`` header --
#: exactly the signal ``upload_record`` needs to tell a fresh 201 apart
#: from a replayed one for the partial-run-recovery contract.
_CONFORMERS_PATH = "/uploads/conformers"


def _artifact_path(calculation_id: int) -> str:
    return f"/calculations/{calculation_id}/artifacts"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def conformers_idempotency_key(canonical_sha256: str) -> str:
    return f"qcschema:{canonical_sha256[:32]}:conformers"


def artifact_idempotency_key(canonical_sha256: str) -> str:
    return f"qcschema:{canonical_sha256[:32]}:artifact"


@dataclass
class UploadOutcome:
    """What happened for one imported QCSchema document."""

    calculation_id: int
    conformer_replayed: bool
    artifact_replayed: bool
    conformer_response: dict
    artifact_response: dict


def precheck_duplicate(client, raw_sha256: str, *, allow_duplicate: bool) -> None:
    """Refuse ``already_imported`` if a matching-sha256 artifact already exists.

    :param client: A ``tckdb_client.TCKDBClient`` (or test stub exposing
        ``request_json``).
    """
    response = client.request_json(
        "GET", _ARTIFACT_SEARCH_PATH, params={"sha256": raw_sha256, "limit": 1}
    )
    data = response.data if hasattr(response, "data") else response
    records = (data or {}).get("records", []) if isinstance(data, dict) else []
    if records and not allow_duplicate:
        existing = records[0]
        existing_calc_id = (existing.get("calculation") or {}).get("calculation_id")
        raise QCSchemaAdapterError(
            E_ALREADY_IMPORTED,
            f"an artifact with sha256={raw_sha256} already exists "
            f"(calculation_id={existing_calc_id!r}); pass --allow-duplicate "
            f"to import anyway.",
            existing=existing,
        )


def upload_record(
    client,
    record: QCRecord,
    payload: dict,
    *,
    raw_bytes: bytes,
    raw_sha256: str,
    artifact_filename: str,
    allow_duplicate: bool = False,
    dry_run: bool = False,
) -> UploadOutcome | dict:
    """Precheck, then POST the conformer and its raw-artifact sidecar.

    :param raw_bytes: The exact bytes of the original ``.json`` file --
        uploaded verbatim as the artifact, never a re-serialisation.
    :param dry_run: When True, build and validate keys without sending
        any request; returns a plan dict instead of an
        :class:`UploadOutcome`.
    :raises QCSchemaAdapterError: ``artifact_too_large`` or
        ``already_imported``.
    """
    raw_bytes_len = len(raw_bytes)
    if raw_bytes_len > MAX_ARTIFACT_BYTES:
        raise QCSchemaAdapterError(
            E_ARTIFACT_TOO_LARGE,
            f"raw artifact is {raw_bytes_len} bytes, exceeding the "
            f"{MAX_ARTIFACT_BYTES}-byte cap.",
            bytes=raw_bytes_len,
        )

    conformers_key = conformers_idempotency_key(record.canonical_sha256)
    artifact_key = artifact_idempotency_key(record.canonical_sha256)

    if dry_run:
        return {
            "conformers_idempotency_key": conformers_key,
            "artifact_idempotency_key": artifact_key,
            "calculation_type": payload["calculation"]["type"],
        }

    precheck_duplicate(client, raw_sha256, allow_duplicate=allow_duplicate)

    conformer_response = client.request_json(
        "POST", _CONFORMERS_PATH, json=payload, idempotency_key=conformers_key
    )
    conformer_data = conformer_response.data
    conformer_replayed = bool(conformer_response.idempotency_replayed)

    calculation_id = conformer_data["primary_calculation"]["calculation_id"]

    artifact_payload = {
        "artifacts": [
            {
                "kind": "ancillary",
                "filename": artifact_filename,
                "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
                "sha256": raw_sha256,
                "bytes": raw_bytes_len,
            }
        ]
    }
    artifact_response = client.request_json(
        "POST",
        _artifact_path(calculation_id),
        json=artifact_payload,
        idempotency_key=artifact_key,
    )
    artifact_data = artifact_response.data
    artifact_replayed = bool(artifact_response.idempotency_replayed)

    return UploadOutcome(
        calculation_id=calculation_id,
        conformer_replayed=conformer_replayed,
        artifact_replayed=artifact_replayed,
        conformer_response=conformer_data,
        artifact_response=artifact_data,
    )


__all__ = [
    "MAX_ARTIFACT_BYTES",
    "UploadOutcome",
    "sha256_bytes",
    "conformers_idempotency_key",
    "artifact_idempotency_key",
    "precheck_duplicate",
    "upload_record",
]
