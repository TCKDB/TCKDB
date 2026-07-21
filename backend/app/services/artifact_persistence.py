"""Shared artifact persistence helpers for inline-workflow uploads and the
calculation-targeted artifact upload endpoint.

Two paths are exposed:

- :func:`persist_artifact` — single-artifact decode → validate → store →
  row creation. Used by inline-artifact workflows
  (``computed_reaction``, ``network_pdep``) where the workflow's outer
  transaction already covers atomicity.

- :func:`persist_artifact_batch` — two-pass batch helper for the
  ``POST /calculations/{id}/artifacts`` endpoint. Pass 1 decodes and
  validates every artifact in memory before any storage write. Pass 2
  attempts S3 writes one by one and raises
  :class:`ArtifactStorageUnavailable` on failure so the caller can return
  503. Content-addressed objects written before a failure are retained:
  a key may already be shared by committed rows or concurrent transactions,
  so eager deletion is unsafe. Reference-aware garbage collection is a
  separate maintenance concern.

The two-pass design exists because object-store writes are not part of
the SQL transaction. Looping the single-artifact helper means artifact
#1 lands in S3 even when artifact #3 later fails ESS-signature
validation; the DB rows roll back but the S3 bytes leak.

Trust boundary notes:

- ``ArtifactIn.bytes`` is a *declared* size and is not trusted for
  any allocation decision. The route enforces an encoded-length cap
  against the raw base64 string before pass-1 decode, and the aggregate
  decoded-size cap is computed from real decoded bytes inside pass-1.
- ``ArtifactIn.sha256``, when supplied, is verified against the
  computed hash inside :func:`validate_artifact`.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationArtifact
from app.schemas.fragments.artifact import ArtifactIn
from app.services.artifact_storage import (
    ArtifactStorageUnavailable,
    ArtifactValidationError,
    store_artifact,
    validate_artifact,
    validate_encoded_lengths,
    validate_total_upload_size,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DecodedArtifact:
    """Pass-1 output: decoded + fully validated, not yet stored."""

    artifact_in: ArtifactIn
    content: bytes
    computed_sha256: str


def _strict_b64decode(filename: str, content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArtifactValidationError(
            f"Artifact '{filename}' has invalid base64 content: {exc}"
        ) from exc


def _decode_one(artifact_in: ArtifactIn) -> _DecodedArtifact:
    content = _strict_b64decode(artifact_in.filename, artifact_in.content_base64)
    computed_sha = validate_artifact(
        content,
        artifact_in.kind,
        declared_sha256=artifact_in.sha256,
        declared_bytes=artifact_in.bytes,
    )
    return _DecodedArtifact(
        artifact_in=artifact_in,
        content=content,
        computed_sha256=computed_sha,
    )


def validate_and_decode_all_artifacts(
    artifacts: list[ArtifactIn],
) -> list[_DecodedArtifact]:
    """Pass 1: decode and validate every artifact in memory, no I/O.

    Raises :class:`ArtifactValidationError` on the first failure with no
    partial decoding state retained — callers must map this to 422. No
    object-store writes happen here.

    Aggregate decoded-size enforcement is included in this pass so the
    cap is computed from the real decoded bytes, not from the
    client-declared ``bytes`` value (which is only used for integrity
    cross-checking inside :func:`validate_artifact`).
    """
    # Encoded-length cap first — protects against a client sending a huge
    # base64 string with a tiny declared `bytes` value.
    validate_encoded_lengths([len(a.content_base64) for a in artifacts])

    decoded = [_decode_one(a) for a in artifacts]

    # Aggregate cap from real decoded sizes, not declared values.
    validate_total_upload_size([len(d.content) for d in decoded])
    return decoded


def persist_artifact(
    session: Session,
    *,
    calculation_id: int,
    artifact_in: ArtifactIn,
    created_by: int | None = None,
) -> CalculationArtifact:
    """Decode, validate, store, and record one artifact.

    Single-artifact path — used by inline-artifact workflows whose outer
    transaction already covers atomicity. New batch endpoints must use
    :func:`persist_artifact_batch` instead so that storage writes are
    only attempted for batches that have passed every per-artifact gate.

    ``created_by`` is the uploading user id, recorded on the row for
    audit. Inline workflow callers may pass ``None`` when no user is in
    scope (e.g. system-driven backfills).
    """
    decoded = _decode_one(artifact_in)
    return _store_and_record(session, calculation_id, decoded, created_by)


def persist_artifact_batch(
    session: Session,
    *,
    calculation_id: int,
    artifacts: list[ArtifactIn],
    created_by: int | None = None,
) -> list[CalculationArtifact]:
    """Two-pass: validate-all-then-store-all with safe SQL rollback.

    1. :func:`validate_and_decode_all_artifacts` — any per-artifact
       validation failure or an aggregate-size overflow raises
       :class:`ArtifactValidationError` before any S3 write. The DB is
       untouched.
    2. For each decoded artifact: write content to the content-addressed
       object store, then create a ``CalculationArtifact`` row. After
       all rows are added, ``session.flush()`` is called inside this
       service so SQL-level errors (constraint violations, schema drift,
       FK problems) trigger the same rollback path as storage errors.
       On failure, pending rows are detached while content-addressed
       objects are retained. Eager deletion is unsafe because a digest
       key can be shared by committed rows or a concurrent upload. The
       route maps storage failures to 503; SQL rollback is handled by
       the outer session.

    **Service contract**: if this function returns successfully, the
    rows have been flushed and are present in the session's view of the
    DB (subject only to the outer transaction's commit). Callers do not
    need to call ``session.flush()`` themselves.

    Caveat: if flush succeeds but the outer transaction later rolls
    back, the stored objects become content-addressed orphans. They are
    not reachable through any DB row and the keys are not
    user-discoverable. A future GC pass can sweep them; not handled
    here.

    The caller still owns transaction commit boundaries — this function
    flushes but does not commit.
    """
    decoded_all = validate_and_decode_all_artifacts(artifacts)

    stored_shas: list[str] = []
    rows: list[CalculationArtifact] = []
    try:
        for decoded in decoded_all:
            row = _store_and_record(session, calculation_id, decoded, created_by)
            stored_shas.append(decoded.computed_sha256)
            rows.append(row)
        # Flush inside the service so SQL-layer errors (constraint
        # violations, missing columns from schema drift, FK problems)
        # trigger the same rollback block as storage errors.
        session.flush()
    except ArtifactStorageUnavailable:
        # Real storage outage — keep the explicit type so the route maps
        # it to 503 with `artifact_storage_unavailable`.
        _undo_partial_batch(session, rows, stored_shas)
        raise
    except Exception:
        # Anything else (IntegrityError, ProgrammingError, etc.) is not
        # "storage unavailable" — that label would mislead operators.
        # Detach pending rows, then re-raise the original so the default
        # exception handler maps the HTTP status appropriately (e.g.
        # 500 for DB schema drift, FK violations, constraint conflicts).
        _undo_partial_batch(session, rows, stored_shas)
        raise

    return rows


def _store_and_record(
    session: Session,
    calculation_id: int,
    decoded: _DecodedArtifact,
    created_by: int | None,
) -> CalculationArtifact:
    try:
        uri = store_artifact(decoded.content, decoded.computed_sha256)
    except ArtifactValidationError:
        raise
    except Exception as exc:
        raise ArtifactStorageUnavailable(
            f"Artifact storage write failed: {type(exc).__name__}: {exc}"
        ) from exc
    artifact = CalculationArtifact(
        calculation_id=calculation_id,
        kind=decoded.artifact_in.kind,
        uri=uri,
        sha256=decoded.computed_sha256,
        bytes=len(decoded.content),
        filename=decoded.artifact_in.filename,
        note=None,
        created_by=created_by,
    )
    session.add(artifact)
    return artifact


def _compensate_stored_objects(stored_shas: list[str]) -> None:
    """Retain possible orphans rather than risk deleting shared CAS keys.

    Kept as the cross-workflow failure hook while callers migrate to a
    reference-aware garbage-collection design. A digest in this list does not
    prove the current transaction created the object; ``store_artifact`` may
    have deduplicated against committed or concurrently written content.
    """
    if stored_shas:
        logger.warning(
            "retaining %d content-addressed object(s) after failed upload; "
            "reference-aware garbage collection may reclaim true orphans",
            len(set(stored_shas)),
        )


def _undo_partial_batch(
    session: Session,
    rows: list[CalculationArtifact],
    stored_shas: list[str],
) -> None:
    """Roll back a half-finished pass-2 and retain content-addressed bytes.

    The session-level rollback is the route's job; here we additionally
    expunge the rows we already added so the session does not hand a
    caller stale objects between the failure point and the eventual
    rollback. Object-store keys are deliberately not deleted here because
    they may be shared with committed or concurrent rows.
    """
    for row in rows:
        try:
            session.expunge(row)
        except Exception:
            pass
    _compensate_stored_objects(stored_shas)
