"""Artifact validation and S3-compatible content-addressed storage.

Validates uploaded calculation artifacts (ESS output logs, input files,
checkpoints) and writes them to an S3-compatible object store (MinIO
locally, AWS S3 in production).

Security measures:
- Content signature validation: output_log artifacts must match a known
  ESS header (Gaussian, ORCA, etc.) or be rejected.
- SHA-256 integrity: declared hash must match computed hash.
- Size limits: per-artifact and per-upload caps.
- Content-addressed keys: the server constructs object keys from the
  content hash, never from user-supplied URIs.  Path traversal is impossible.
- Artifacts are stored as inert blobs, never executed.

Configuration (environment variables):
- ``S3_ENDPOINT_URL``: MinIO/S3 endpoint (default: ``http://localhost:9000``)
- ``S3_ACCESS_KEY``: Access key (default: ``tckdb``)
- ``S3_SECRET_KEY``: Secret key (default: ``tckdb_secret``)
- ``S3_BUCKET``: Bucket name (default: ``tckdb-artifacts``)
- ``S3_REGION``: Region (default: ``us-east-1``)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NoReturn

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.db.models.common import ArtifactIntegrityFinding, ArtifactKind

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _env(key: str, default: str) -> str:
    """Read from environment (populated by dotenv from .env file).

    See ``.env.example`` for all available configuration variables.
    """
    return os.environ.get(key, default)


S3_ENDPOINT_URL = _env("S3_ENDPOINT_URL", "http://localhost:9000")
S3_ACCESS_KEY = _env("S3_ACCESS_KEY", "tckdb")
S3_SECRET_KEY = _env("S3_SECRET_KEY", "tckdb_secret")
S3_BUCKET = _env("S3_BUCKET", "tckdb-artifacts")
S3_REGION = _env("S3_REGION", "us-east-1")

#: Maximum size for a single artifact (bytes).  50 MB.
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024

#: Maximum total upload size across all artifacts in one request (bytes). 200 MB.
MAX_TOTAL_UPLOAD_BYTES = 200 * 1024 * 1024

#: Maximum base64-encoded length for a single artifact.  Rejects oversized
#: payloads BEFORE base64 decoding so a client cannot force the server to
#: allocate 50 MB+ of memory by claiming a small ``bytes`` value while
#: sending a much larger ``content_base64`` string. Sized at the encoded
#: ceiling for ``MAX_ARTIFACT_BYTES`` plus padding slack.
MAX_ENCODED_ARTIFACT_LEN = ((MAX_ARTIFACT_BYTES + 2) // 3) * 4 + 4

#: Aggregate encoded-length cap for one upload request — same intent as
#: above, applied before any artifact in the batch is decoded.
MAX_TOTAL_ENCODED_UPLOAD_LEN = ((MAX_TOTAL_UPLOAD_BYTES + 2) // 3) * 4 + 4

# ---------------------------------------------------------------------------
# ESS output signatures — first ~4 KB of a legitimate log must contain one.
# ---------------------------------------------------------------------------

#: Map of ESS name → byte string that must appear in the first 4 KB.
OUTPUT_LOG_SIGNATURES: dict[str, bytes] = {
    "gaussian": b"Entering Gaussian System",
    "orca": b"O   R   C   A",
    "qchem": b"Q-Chem",
    "molpro": b"MOLPRO",
    "psi4": b"Psi4",
    "nwchem": b"Northwest Computational Chemistry Package",
    "turbomole": b"TURBOMOLE",
    "cfour": b"CFOUR",
}

#: How many bytes to inspect for signature detection.
_SIGNATURE_WINDOW = 4096

#: S3/MinIO error codes that mean "the store answered, and the object is
#: not there" — as opposed to "the store did not answer".
_MISSING_OBJECT_CODES = frozenset({"404", "NoSuchKey", "NotFound"})

#: S3/MinIO error codes that mean "the store answered, and it will not
#: accept this object because it has no room for it".
#:
#: The third fact this module can learn about a write, and the one that
#: had nowhere to go: everything that was not a missing object collapsed
#: into :class:`ArtifactStorageUnavailable` with no discriminator, which
#: the API reports as ``503 ... Retry later.`` A depositor uploading into
#: a full store was therefore told to do the one thing that cannot work —
#: retrying a full disk fails until an *operator* frees space.
#:
#: Provenance of each entry, because a code set assembled from
#: documentation is a code set that has never been seen on the wire:
#:
#: * ``XMinioStorageFull`` — **measured** against MinIO
#:   ``RELEASE.2025-09-07T16-13-09Z`` on a size-capped scratch volume.
#:   MinIO answers ``507`` with "Storage backend has reached its minimum
#:   free drive threshold."
#: * ``XMinioAdminBucketQuotaExceeded`` — **measured** against the same
#:   build with ``mc quota set --size`` on the bucket. MinIO answers
#:   ``400`` here, not 507, and enforces the quota asynchronously from its
#:   data-usage scanner, so the refusal begins a minute or two after the
#:   quota is actually passed.
#: * ``QuotaExceeded``, ``InsufficientStorage``, ``XMinioStorageFullError``
#:   — **not provoked here**; documented spellings from other
#:   S3-compatible implementations (Ceph RGW uses ``QuotaExceeded``) and
#:   from adjacent MinIO releases. Carried because misclassifying a full
#:   store is the defect being repaired and an unrecognised spelling
#:   reproduces it exactly, while a false positive costs only a more
#:   accurate-sounding message on a store that answered something else.
#:
#: ``EntityTooLarge`` is deliberately **not** here, against the first
#: reading of this problem. It means "this object exceeds the store's
#: per-object ceiling", which is a fact about one object rather than about
#: capacity: the store has room, and the identical request will fail on an
#: empty store too. It is also unreachable from every TCKDB write path,
#: because :data:`MAX_ARTIFACT_BYTES` (50 MB) is far below any
#: S3-compatible single-``PUT`` ceiling, so admitting it would add an
#: untriggerable branch whose message ("free space") would be wrong on the
#: one configuration that could trigger it — a store deliberately capped
#: below 50 MB, which is a deployment error and not a full disk.
_STORAGE_FULL_CODES = frozenset(
    {
        "XMinioStorageFull",
        "XMinioStorageFullError",
        "XMinioAdminBucketQuotaExceeded",
        "QuotaExceeded",
        "InsufficientStorage",
    }
)

# ---------------------------------------------------------------------------
# Kinds that must be valid UTF-8 text (no binary allowed).
# ---------------------------------------------------------------------------

_TEXT_KINDS = {
    ArtifactKind.output_log,
    ArtifactKind.input,
    ArtifactKind.formatted_checkpoint,
    # .hess (ORCA) and .fchk (Gaussian) Hessian sidecars are both ASCII text.
    ArtifactKind.hessian,
}

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ArtifactValidationError(ValueError):
    """Raised when an artifact fails validation."""


class ArtifactStorageUnavailable(Exception):
    """Raised when the object store cannot serve or accept a request.

    Distinct from :class:`ArtifactValidationError` so the API layer can
    map it to a 503 Service Unavailable response while validation
    failures stay 422.

    One type, three facts. The type is deliberately not split, so every
    opportunistic ``except ArtifactStorageUnavailable`` in the codebase
    keeps catching all of them; the discriminators below are read by the
    handler in :mod:`app.api.errors`, which is the only place that has to
    tell them apart.

    :param missing: ``True`` when the store answered and said the object
        is *not there*, as opposed to not answering at all. Both were 503
        to a client until the two were split; they are opposite facts for
        custody — an
        unreachable store says nothing about the object, while a store
        that reports ``NoSuchKey`` for a digest a row still references is
        a break TCKDB should record.
    :param full: ``True`` when the store answered and refused the *write*
        for want of room — out of space, or over a bucket quota. See
        :data:`_STORAGE_FULL_CODES`. This is the fact that had no
        spelling: it landed in the residue with genuine outages and was
        reported as ``503 ... Retry later.``, which is advice no depositor
        can act on, because only an operator freeing space can clear it.
        Mutually exclusive with ``missing`` — one is a read fact, the
        other a write fact.
    :param s3_code: The store's own error code, when there was one. Kept
        as a field rather than only inside the message because
        :func:`record_storage_full_observation` reports it to operators on
        ``/status``, and re-parsing it out of an f-string is the fragility
        :class:`ArtifactIntegrityError` already exists to avoid.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: bool = False,
        full: bool = False,
        s3_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.missing = missing
        self.full = full
        self.s3_code = s3_code


class ArtifactIntegrityError(Exception):
    """Raised when stored bytes no longer match their persisted digest/size.

    Carries the comparison as *fields*, not only in the message. The
    caller's job is to write a durable record of what was expected and
    what was found (see :mod:`app.services.artifact_integrity`), and
    re-parsing that out of an f-string would be the same class of
    fragility the scientific check register exists to prevent.

    :param finding: Which of the three observations this is.
    :param sha256: The content-addressed key — what the object should
        hash to.
    :param observed_sha256: What the retrieved bytes actually hash to.
        ``None`` only when nothing was retrieved.
    :param expected_bytes: Persisted byte count, when the caller knew one.
    :param observed_bytes: Length of what was retrieved.
    """

    def __init__(
        self,
        message: str,
        *,
        finding: ArtifactIntegrityFinding,
        sha256: str,
        observed_sha256: str | None = None,
        expected_bytes: int | None = None,
        observed_bytes: int | None = None,
    ) -> None:
        super().__init__(message)
        self.finding = finding
        self.sha256 = sha256
        self.observed_sha256 = observed_sha256
        self.expected_bytes = expected_bytes
        self.observed_bytes = observed_bytes


# ---------------------------------------------------------------------------
# "The store has no room" — classification, and the observation record
# ---------------------------------------------------------------------------


def _error_code(exc: ClientError) -> str:
    """The store's own error code from a botocore ``ClientError``."""
    return str(exc.response.get("Error", {}).get("Code", ""))


@dataclass(frozen=True)
class StorageFullObservation:
    """The last time a write was refused for want of room, in this process.

    Why an observation and not a probe
    ----------------------------------
    ``/status`` cannot find this out by asking. Measured against MinIO
    ``RELEASE.2025-09-07T16-13-09Z`` on a scratch volume filled to its
    free-space threshold:

    * ``head_bucket`` — the probe ``/status`` performs — returns **200**.
    * ``list_objects``, ``head_object`` and ``get_object`` all succeed. A
      full store serves reads perfectly.
    * a **1-byte** ``put_object`` also **succeeds**, on the same store
      that refuses a 4 MiB one. MinIO's threshold check is sized against
      the incoming object, so a cheap synthetic write probe would report
      healthy on a store with no room for any real artifact.

    The last point has a consequence beyond probing, and it is why
    :attr:`attempted_bytes` is recorded: "full" is not all-or-nothing. On a
    store in this state a large ESS log is refused while a small text
    artifact still lands, so *some uploads continuing to work is not
    evidence the store is well*. Verified end to end against a real filled
    MinIO: an 8 MiB artifact answered 507 while a 1-byte write returned 200
    in the same second.

    That last measurement is why there is no write probe here. To answer
    the question a probe would have to write something the size of a real
    artifact (up to :data:`MAX_ARTIFACT_BYTES`) on every health check, and
    the S3 API exposes no capacity or quota query to ask instead —
    MinIO's free space is available only through its admin API, which
    needs administrative credentials this process does not have and should
    not be given, and AWS S3 has no notion of "full" at all.

    So the signal is the real write path's own refusal. TCKDB already
    performs correctly-sized writes for real reasons; recording what they
    are told is strictly better evidence than a synthetic probe, and costs
    nothing when the store is healthy.

    What it does *not* cover, stated here because a health signal whose
    limits are undocumented is the thing that produced this defect:

    * **It needs one upload attempt to fire.** A store that fills while
      TCKDB is idle reads healthy until the next depositor arrives. That
      depositor now gets a correct answer (507) *and* trips this, so at
      most one upload is spent finding out.
    * **It is per-process, and cleared by a restart.** A deployment
      running several API workers can answer ``/status`` from a worker
      that has not attempted a write. Making it durable means a table and
      an append-only record, which is a larger decision than this change.
    """

    #: When the refusal was seen.
    observed_at: datetime

    #: The store's own error code, e.g. ``XMinioStorageFull``.
    s3_code: str

    #: Size of the object the store refused, so an operator can see
    #: whether they are near the threshold or nowhere near it.
    attempted_bytes: int | None


_storage_full_lock = threading.Lock()
_last_storage_full: StorageFullObservation | None = None


def record_storage_full_observation(
    *,
    s3_code: str,
    attempted_bytes: int | None,
    detail: str,
) -> None:
    """Latch "the store refused a write for want of room".

    ``detail`` is what the store said and goes to the log only. It is
    deliberately not kept on the observation: ``/status`` is public, the
    message carries a content digest, and a field nothing reads is a field
    that will be rendered somewhere by accident eventually.
    """
    global _last_storage_full
    with _storage_full_lock:
        _last_storage_full = StorageFullObservation(
            observed_at=datetime.now(timezone.utc),
            s3_code=s3_code,
            attempted_bytes=attempted_bytes,
        )
    logger.error(
        "artifact storage refused a %s-byte write for want of room "
        "(S3 code %s); /status will report degraded until a write succeeds: %s",
        attempted_bytes,
        s3_code,
        detail,
    )


def clear_storage_full_observation() -> None:
    """Forget the latch, because a write has just succeeded.

    Called only after a real ``put_object`` returns. Deliberately **not**
    called on the deduplication path: finding an object already at its
    content-addressed key proves the store can serve a read, and says
    nothing whatever about whether it can accept bytes.

    Nothing else clears it, and in particular no timer does. A store that
    was full ten minutes ago and has accepted nothing since is still a
    store TCKDB has no evidence it can write to, and a latch that decays
    on its own would go quiet while the disk was still full and nobody had
    acted — which is the exact silence this exists to break.
    """
    global _last_storage_full
    with _storage_full_lock:
        if _last_storage_full is None:
            return
        previous = _last_storage_full
        _last_storage_full = None
    logger.info(
        "artifact storage accepted a write; clearing the storage-full "
        "observation recorded at %s (S3 code %s)",
        previous.observed_at.isoformat(),
        previous.s3_code,
    )


def last_storage_full_observation() -> StorageFullObservation | None:
    """The latch, for ``/status``. ``None`` when nothing has been refused."""
    with _storage_full_lock:
        return _last_storage_full


def _raise_write_refusal(
    exc: ClientError,
    *,
    what: str,
    sha256: str,
    attempted_bytes: int | None = None,
) -> NoReturn:
    """Turn a refused *write* into the one typed exception, classified.

    Shared by every arm that attempts to put bytes into the store — the
    upload ``put_object`` and the reclaim/restore ``copy_object`` — so a
    full store is recognised wherever it is met rather than at whichever
    site happened to be edited. Always raises.
    """
    code = _error_code(exc)
    full = code in _STORAGE_FULL_CODES
    if full:
        record_storage_full_observation(
            s3_code=code,
            attempted_bytes=attempted_bytes,
            detail=f"{what} for sha={sha256}: {exc}",
        )
    raise ArtifactStorageUnavailable(
        f"{what} for sha={sha256}: {code or type(exc).__name__}",
        full=full,
        s3_code=code or None,
    ) from exc


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_artifact(
    content: bytes,
    kind: ArtifactKind,
    *,
    declared_sha256: str | None = None,
    declared_bytes: int | None = None,
) -> str:
    """Validate artifact content and return its SHA-256 hash.

    :param content: Raw file content.
    :param kind: Declared artifact kind.
    :param declared_sha256: Optional SHA-256 declared by the uploader.
    :param declared_bytes: Optional file size declared by the uploader.
    :returns: Computed SHA-256 hex digest.
    :raises ArtifactValidationError: On any validation failure.
    """
    # -- Size check --
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ArtifactValidationError(
            f"Artifact exceeds maximum size: {len(content):,} bytes "
            f"(limit: {MAX_ARTIFACT_BYTES:,} bytes)."
        )

    if len(content) == 0:
        raise ArtifactValidationError("Artifact is empty (0 bytes).")

    # -- Integrity check --
    computed_sha = hashlib.sha256(content).hexdigest()

    if declared_sha256 is not None and computed_sha != declared_sha256:
        raise ArtifactValidationError(
            f"SHA-256 mismatch: declared {declared_sha256}, "
            f"computed {computed_sha}."
        )

    if declared_bytes is not None and len(content) != declared_bytes:
        raise ArtifactValidationError(
            f"Size mismatch: declared {declared_bytes:,} bytes, "
            f"actual {len(content):,} bytes."
        )

    # -- Text check for text-expected kinds --
    if kind in _TEXT_KINDS:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactValidationError(
                f"Artifact kind '{kind.value}' must be valid UTF-8 text, "
                f"but the content contains invalid byte sequences."
            ) from exc

    # -- Output log signature check --
    if kind == ArtifactKind.output_log:
        _validate_output_log_signature(content)

    return computed_sha


def _validate_output_log_signature(content: bytes) -> None:
    """Verify that an output_log artifact contains a recognized ESS header."""
    head = content[:_SIGNATURE_WINDOW]
    for _ess_name, signature in OUTPUT_LOG_SIGNATURES.items():
        if signature in head:
            return

    known = ", ".join(sorted(OUTPUT_LOG_SIGNATURES))
    raise ArtifactValidationError(
        f"Output log does not match any known ESS signature in the "
        f"first {_SIGNATURE_WINDOW} bytes. Supported: {known}. "
        f"If this is a valid output file from a supported ESS, the "
        f"signature detection may need updating."
    )


def validate_total_upload_size(artifacts_bytes: list[int]) -> None:
    """Check that the total size of all artifacts in one request is within limits.

    :param artifacts_bytes: List of individual artifact sizes.
    :raises ArtifactValidationError: If total exceeds MAX_TOTAL_UPLOAD_BYTES.
    """
    total = sum(artifacts_bytes)
    if total > MAX_TOTAL_UPLOAD_BYTES:
        raise ArtifactValidationError(
            f"Total artifact upload size {total:,} bytes exceeds limit "
            f"of {MAX_TOTAL_UPLOAD_BYTES:,} bytes."
        )


def validate_encoded_lengths(encoded_lengths: list[int]) -> None:
    """Reject oversized base64 payloads BEFORE any decode allocation.

    Takes the encoded ``len(content_base64)`` for each artifact in the
    batch and enforces both per-artifact and aggregate caps. This runs
    against client-provided strings only, so it is cheap and safe to call
    before allocating decoded buffers — the ``bytes`` field on the
    upload schema is *not* trusted at this stage.
    """
    for i, encoded_len in enumerate(encoded_lengths):
        if encoded_len > MAX_ENCODED_ARTIFACT_LEN:
            raise ArtifactValidationError(
                f"Artifact {i} encoded payload is {encoded_len:,} chars; "
                f"limit is {MAX_ENCODED_ARTIFACT_LEN:,} chars."
            )
    total = sum(encoded_lengths)
    if total > MAX_TOTAL_ENCODED_UPLOAD_LEN:
        raise ArtifactValidationError(
            f"Total encoded artifact payload is {total:,} chars; "
            f"limit is {MAX_TOTAL_ENCODED_UPLOAD_LEN:,} chars."
        )


# ---------------------------------------------------------------------------
# S3 client
# ---------------------------------------------------------------------------


def _get_s3_client(config=None):
    """Create a boto3 S3 client configured for MinIO or AWS S3.

    :param config: Optional :class:`botocore.config.Config`. Callers that
        must not block on a dead endpoint -- the ``/status`` storage probe
        in :mod:`app.api.routes.health` -- pass a short-timeout config here
        instead of building a second client of their own. Endpoint,
        credentials, and region stay owned by this module, so the probe can
        never disagree with the write path about *which* object store it is
        talking to. A health check that reaches a different endpoint than
        the uploads do is worse than no health check at all.
    """
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=config,
    )


def _ensure_bucket(client) -> None:
    """Create the artifacts bucket if it doesn't exist."""
    try:
        client.head_bucket(Bucket=S3_BUCKET)
    except ClientError:
        client.create_bucket(Bucket=S3_BUCKET)


# ---------------------------------------------------------------------------
# Content-addressed S3 storage
# ---------------------------------------------------------------------------


def content_addressed_key(sha256: str) -> str:
    """Compute the S3 object key for an artifact by its SHA-256 hash.

    Layout: ``{sha256[:2]}/{sha256}``

    The two-character prefix prevents hot-partition issues on S3.
    """
    return f"{sha256[:2]}/{sha256}"


def store_artifact(
    content: bytes,
    sha256: str,
    *,
    client=None,
    bucket: str | None = None,
) -> str:
    """Upload artifact content to S3/MinIO.

    If an object with the same SHA-256 key already exists, this is a no-op
    (content-addressed dedup).

    :param content: Validated file content.
    :param sha256: Pre-computed SHA-256 hex digest (from validate_artifact).
    :param client: Optional pre-created boto3 S3 client (for testing).
    :param bucket: Optional bucket name override (for testing).
    :returns: The S3 URI (``s3://bucket/key``).
    """
    if client is None:
        client = _get_s3_client()
    bucket = bucket or S3_BUCKET

    # Every arm below turns a botocore failure into the one typed exception
    # the rest of the code contracts on. ``BotoCoreError`` matters as much as
    # ``ClientError`` and is a separate hierarchy: ``EndpointConnectionError``
    # -- a dead object store, the most ordinary outage there is -- descends
    # only from the former. Most callers reach this through
    # ``_store_and_record``, whose broad ``except`` hid the gap; a *direct*
    # caller such as archive restore saw the raw botocore exception instead,
    # which is the shape that produced an undiagnosable 503 once already.
    try:
        _ensure_bucket(client)
    except BotoCoreError as exc:
        raise ArtifactStorageUnavailable(
            f"Artifact storage is unreachable: {type(exc).__name__}: {exc}"
        ) from exc
    except ClientError as exc:
        # ``_ensure_bucket`` swallows a ``ClientError`` from ``head_bucket``
        # and then calls ``create_bucket``, whose own ``ClientError`` has
        # nothing catching it. Without this arm a raw botocore exception
        # escapes ``store_artifact`` — the same shape the ``BotoCoreError``
        # arm above was added for, and invisible for the same reason: most
        # callers arrive through ``_store_and_record``, whose broad
        # ``except`` relabels anything, while a direct caller such as
        # archive restore sees the raw exception sail past every
        # ``except ArtifactStorageUnavailable`` downstream.
        _raise_write_refusal(
            exc,
            what="Artifact storage bucket check failed",
            sha256=sha256,
            attempted_bytes=len(content),
        )

    key = content_addressed_key(sha256)

    # A HEAD hit is not enough evidence that the stored bytes are sound:
    # verify an existing content-addressed object before attaching another
    # database row to the shared key.
    try:
        client.head_object(Bucket=bucket, Key=key)
        load_artifact_bytes(
            sha256,
            expected_bytes=len(content),
            client=client,
            bucket=bucket,
        )
        return f"s3://{bucket}/{key}"
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in _MISSING_OBJECT_CODES:
            raise ArtifactStorageUnavailable(
                f"Artifact storage HEAD failed for sha={sha256}: "
                f"{code or type(exc).__name__}"
            ) from exc
    except BotoCoreError as exc:
        raise ArtifactStorageUnavailable(
            f"Artifact storage HEAD failed for sha={sha256}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType="application/octet-stream",
        )
    except ClientError as exc:
        # The one arm a full store reaches. Every code that was not a
        # missing object used to collapse into an undiscriminated 503, so
        # "the store has no room" and "the store is down" left here with
        # the same retry advice, of which only one was true.
        _raise_write_refusal(
            exc,
            what="Artifact storage write failed",
            sha256=sha256,
            attempted_bytes=len(content),
        )
    except BotoCoreError as exc:
        raise ArtifactStorageUnavailable(
            f"Artifact storage write failed for sha={sha256}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    # A write landed, so whatever this process last believed about the
    # store having no room is now known to be out of date. Only here: the
    # dedup return above is a read, and proves nothing about capacity.
    clear_storage_full_observation()
    return f"s3://{bucket}/{key}"


def load_artifact_bytes(
    sha256: str,
    *,
    expected_bytes: int | None = None,
    client=None,
    bucket: str | None = None,
) -> bytes:
    """Read and verify previously stored artifact bytes by SHA-256.

    Used by paths that need the file content after it has already left
    the upload pipeline — notably the calculation-parameter backfill and
    approved scientific download. The upload hook never calls this; it works
    from the decoded bytes already in memory to avoid a round-trip.

    :param expected_bytes: Optional persisted byte count to verify on read.
    :raises ArtifactStorageUnavailable: When the object cannot be read
        (missing key, network error, etc.). Callers in opportunistic
        contexts should catch this.
    :raises ArtifactIntegrityError: When retrieved content does not match the
        content-addressed digest or persisted byte count.
    """
    if client is None:
        client = _get_s3_client()
    bucket = bucket or S3_BUCKET
    key = content_addressed_key(sha256)
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read()
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        raise ArtifactStorageUnavailable(
            f"Artifact storage read failed for sha={sha256}: "
            f"{type(exc).__name__}: {exc}",
            missing=code in _MISSING_OBJECT_CODES,
        ) from exc
    except BotoCoreError as exc:
        # Not a ``ClientError``: ``EndpointConnectionError`` and friends
        # descend from ``BotoCoreError`` only. Without this arm a real
        # storage outage escaped this function raw, sailed past every
        # ``except ArtifactStorageUnavailable`` handler downstream, and
        # surfaced as an unexplained 500 on the download route — the
        # opposite of the 503 that route documents.
        raise ArtifactStorageUnavailable(
            f"Artifact storage read failed for sha={sha256}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    computed_sha256 = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(computed_sha256, sha256):
        raise ArtifactIntegrityError(
            f"Artifact digest verification failed for sha={sha256}: "
            f"retrieved sha={computed_sha256}.",
            finding=ArtifactIntegrityFinding.digest_mismatch,
            sha256=sha256,
            observed_sha256=computed_sha256,
            expected_bytes=expected_bytes,
            observed_bytes=len(content),
        )
    if expected_bytes is not None and len(content) != expected_bytes:
        raise ArtifactIntegrityError(
            f"Artifact size verification failed for sha={sha256}: "
            f"expected {expected_bytes} bytes, retrieved {len(content)}.",
            finding=ArtifactIntegrityFinding.size_mismatch,
            sha256=sha256,
            observed_sha256=computed_sha256,
            expected_bytes=expected_bytes,
            observed_bytes=len(content),
        )
    return content


def head_artifact_object(
    sha256: str,
    *,
    client=None,
    bucket: str | None = None,
) -> dict[str, object] | None:
    """Return the store's own metadata for an object, or ``None``.

    A metadata-only probe (``HEAD``), used by the integrity recorder to
    capture what the store says about an object *at the moment a break
    was observed*. Cheap by design: it must not re-download a body on a
    path that has already failed once.

    The three keys that matter are ``LastModified``, ``ETag`` and
    ``ContentLength``, and they are what separates "the object was
    overwritten after we wrote it" from "we never stored what we said we
    did" from "the store handed back the wrong bytes on this read". See
    :class:`~app.db.models.calculation.ArtifactIntegrityEvent`.

    Returns ``None`` rather than raising: the recorder must still write
    its row when the store cannot even be asked for metadata. A missing
    discriminator is a gap in the record; a raised exception here would
    be a missing record.
    """
    if client is None:
        client = _get_s3_client()
    bucket = bucket or S3_BUCKET
    try:
        return client.head_object(Bucket=bucket, Key=content_addressed_key(sha256))
    except ClientError:
        return None
    except Exception:  # pragma: no cover - defensive; probe must never raise
        return None


def delete_artifact_object(
    sha256: str,
    *,
    client=None,
    bucket: str | None = None,
) -> None:
    """Delete an object previously written by :func:`store_artifact`.

    The second half of :func:`hold_artifact_object`, and nothing else may
    call it. Upload failure paths in particular must not: a
    content-addressed key can already be shared by committed rows or by a
    concurrent transaction, so "the upload I was doing failed" is not
    evidence that the object is unreferenced.
    """
    if client is None:
        client = _get_s3_client()
    bucket = bucket or S3_BUCKET
    key = content_addressed_key(sha256)
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except ClientError:
        pass


#: Where reclaimed objects wait. Deliberately outside the two-hex-character
#: content-addressed layout, so a held object can never be found by
#: :func:`content_addressed_key` and therefore can never be deduplicated
#: against or served.
RECLAIM_HOLD_PREFIX = "reclaimed/"


def reclaim_hold_key(sha256: str) -> str:
    """The key an unreferenced object is moved to, never deleted from."""
    return f"{RECLAIM_HOLD_PREFIX}{sha256}"


def hold_artifact_object(
    sha256: str,
    *,
    client=None,
    bucket: str | None = None,
) -> bool:
    """Move an unreferenced object out of the content-addressed namespace.

    Copy first, delete second, and never the other way round. The point is
    that reclaiming an orphan must not be able to destroy bytes: if the
    caller's "nothing references this" turns out to have been wrong -- a
    row committed a moment later, an upload that deduplicated against the
    key between the check and the move -- the object is still there, under
    a name an operator can put back. What the move *does* accomplish is
    that the digest stops being deduplicable, so a later upload of the same
    bytes repopulates the content-addressed key rather than pointing at
    this copy. It does not make the move atomic with respect to an upload
    already in flight: one that deduplicated against the object seconds
    ago can still commit its row afterwards, which is what
    :func:`restore_held_object` exists to undo.

    Returns ``False`` when the object was not there to move, which is the
    normal outcome of two sweeps racing each other.
    """
    if client is None:
        client = _get_s3_client()
    bucket = bucket or S3_BUCKET
    source = content_addressed_key(sha256)
    try:
        client.copy_object(
            Bucket=bucket,
            Key=reclaim_hold_key(sha256),
            CopySource={"Bucket": bucket, "Key": source},
        )
    except ClientError as exc:
        code = _error_code(exc)
        if code in _MISSING_OBJECT_CODES:
            return False
        # A server-side copy is a write, and a full store refuses it —
        # measured, ``XMinioStorageFull`` at 507 on ``copy_object`` as much
        # as on ``put_object``. The reclaim sweep is exactly the tool an
        # operator reaches for when the store is full, so it is the worst
        # place to report "unavailable, retry".
        _raise_write_refusal(
            exc, what="Artifact storage copy failed", sha256=sha256
        )
    except BotoCoreError as exc:
        raise ArtifactStorageUnavailable(
            f"Artifact storage copy failed for sha={sha256}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    delete_artifact_object(sha256, client=client, bucket=bucket)
    return True


def restore_held_object(
    sha256: str,
    *,
    client=None,
    bucket: str | None = None,
) -> bool:
    """Put a held object back at its content-addressed key.

    The inverse of :func:`hold_artifact_object`, and the reason that one
    is a move rather than a delete. The reclaim sweep cannot see an
    upload that deduplicated against an object seconds before it was
    held, so a committed row can end up pointing at a digest whose object
    is in the hold; this is how that is undone, and it is why the cost of
    that race is a manual step rather than lost bytes.

    Callers must check that the content-addressed key is free first --
    an occupied key means somebody re-uploaded the same bytes, and that
    copy is the one every row is reading.

    Returns ``False`` when nothing is held under the digest.
    """
    if client is None:
        client = _get_s3_client()
    bucket = bucket or S3_BUCKET
    try:
        client.copy_object(
            Bucket=bucket,
            Key=content_addressed_key(sha256),
            CopySource={"Bucket": bucket, "Key": reclaim_hold_key(sha256)},
        )
    except ClientError as exc:
        code = _error_code(exc)
        if code in _MISSING_OBJECT_CODES:
            return False
        _raise_write_refusal(
            exc, what="Artifact storage restore failed", sha256=sha256
        )
    except BotoCoreError as exc:
        raise ArtifactStorageUnavailable(
            f"Artifact storage restore failed for sha={sha256}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    purge_held_object(sha256, client=client, bucket=bucket)
    return True


def purge_held_object(
    sha256: str,
    *,
    client=None,
    bucket: str | None = None,
) -> None:
    """Delete an object from the reclaim hold. This is irreversible.

    Safer than deleting a content-addressed object, but not unconditionally
    safe, and the caller carries the rest. A held object is not at its
    content-addressed key, so no upload can deduplicate against it *now*.
    What that does not rule out is an upload that deduplicated against it
    shortly before it was held and committed its row afterwards. The
    caller must therefore re-read the references and never delete a
    digest a row points at -- see ``_purge_hold`` in
    ``backend/scripts/ops/verify_artifact_integrity.py``, the only caller,
    where that check is load-bearing rather than defensive.

    Refusing to delete is where that caller's obligation begins, not
    where it ends: a referenced held digest is a legitimate record
    reading as corrupt, so the sweep restores it and records the restore
    (``_restore_referenced_holds``, ADR 0014). This function stays the
    unconditional deleter it was; the judgement lives in the caller.
    """
    if client is None:
        client = _get_s3_client()
    bucket = bucket or S3_BUCKET
    try:
        client.delete_object(Bucket=bucket, Key=reclaim_hold_key(sha256))
    except ClientError:
        pass
