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
import os

import boto3
from botocore.exceptions import ClientError

from app.db.models.common import ArtifactKind

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

# ---------------------------------------------------------------------------
# Kinds that must be valid UTF-8 text (no binary allowed).
# ---------------------------------------------------------------------------

_TEXT_KINDS = {
    ArtifactKind.output_log,
    ArtifactKind.input,
    ArtifactKind.formatted_checkpoint,
}

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ArtifactValidationError(ValueError):
    """Raised when an artifact fails validation."""


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
        except UnicodeDecodeError:
            raise ArtifactValidationError(
                f"Artifact kind '{kind.value}' must be valid UTF-8 text, "
                f"but the content contains invalid byte sequences."
            )

    # -- Output log signature check --
    if kind == ArtifactKind.output_log:
        _validate_output_log_signature(content)

    return computed_sha


def _validate_output_log_signature(content: bytes) -> None:
    """Verify that an output_log artifact contains a recognized ESS header."""
    head = content[:_SIGNATURE_WINDOW]
    for ess_name, signature in OUTPUT_LOG_SIGNATURES.items():
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


# ---------------------------------------------------------------------------
# S3 client
# ---------------------------------------------------------------------------


def _get_s3_client():
    """Create a boto3 S3 client configured for MinIO or AWS S3."""
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
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

    _ensure_bucket(client)

    key = content_addressed_key(sha256)

    # Check if object already exists (content-addressed dedup)
    try:
        client.head_object(Bucket=bucket, Key=key)
        # Already exists — dedup
        return f"s3://{bucket}/{key}"
    except ClientError:
        pass

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="application/octet-stream",
    )

    return f"s3://{bucket}/{key}"
