"""Tests for artifact validation and S3 content-addressed storage."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.db.models.common import ArtifactKind
from app.services.artifact_storage import (
    ArtifactValidationError,
    content_addressed_key,
    store_artifact,
    validate_artifact,
    validate_total_upload_size,
    _get_s3_client,
    _ensure_bucket,
    MAX_ARTIFACT_BYTES,
    MAX_TOTAL_UPLOAD_BYTES,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GAUSSIAN_OPT_LOG = FIXTURES / "gaussian" / "opt_g09.log"
GAUSSIAN_FREQ_LOG = FIXTURES / "gaussian" / "freq_g09.log"
ORCA_OPT_LOG = FIXTURES / "orca" / "opt_orca.out"

# Dedicated test bucket so tests don't pollute the dev bucket.
TEST_BUCKET = "tckdb-artifacts-test"


# ---------------------------------------------------------------------------
# Signature validation
# ---------------------------------------------------------------------------


class TestOutputLogSignatureValidation:
    """output_log artifacts must match a known ESS header."""

    def test_gaussian_log_accepted(self):
        content = GAUSSIAN_OPT_LOG.read_bytes()
        sha = validate_artifact(content, ArtifactKind.output_log)
        assert len(sha) == 64

    def test_gaussian_freq_log_accepted(self):
        content = GAUSSIAN_FREQ_LOG.read_bytes()
        sha = validate_artifact(content, ArtifactKind.output_log)
        assert len(sha) == 64

    def test_orca_log_accepted(self):
        content = ORCA_OPT_LOG.read_bytes()
        sha = validate_artifact(content, ArtifactKind.output_log)
        assert len(sha) == 64

    def test_unknown_ess_rejected(self):
        content = b"This is not a real ESS output log file.\n" * 10
        with pytest.raises(ArtifactValidationError, match="does not match any known ESS"):
            validate_artifact(content, ArtifactKind.output_log)

    def test_python_script_rejected(self):
        content = b"#!/usr/bin/env python3\nimport os\nos.system('rm -rf /')\n"
        with pytest.raises(ArtifactValidationError, match="does not match any known ESS"):
            validate_artifact(content, ArtifactKind.output_log)


# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------


class TestIntegrityValidation:
    """SHA-256 and size declarations must match content."""

    def test_correct_sha256_accepted(self):
        content = GAUSSIAN_OPT_LOG.read_bytes()
        expected_sha = hashlib.sha256(content).hexdigest()
        sha = validate_artifact(
            content, ArtifactKind.output_log, declared_sha256=expected_sha
        )
        assert sha == expected_sha

    def test_wrong_sha256_rejected(self):
        content = GAUSSIAN_OPT_LOG.read_bytes()
        with pytest.raises(ArtifactValidationError, match="SHA-256 mismatch"):
            validate_artifact(
                content, ArtifactKind.output_log, declared_sha256="a" * 64
            )

    def test_correct_size_accepted(self):
        content = GAUSSIAN_OPT_LOG.read_bytes()
        validate_artifact(
            content, ArtifactKind.output_log, declared_bytes=len(content)
        )

    def test_wrong_size_rejected(self):
        content = GAUSSIAN_OPT_LOG.read_bytes()
        with pytest.raises(ArtifactValidationError, match="Size mismatch"):
            validate_artifact(
                content, ArtifactKind.output_log, declared_bytes=len(content) + 1
            )


# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------


class TestSizeLimits:
    def test_empty_file_rejected(self):
        with pytest.raises(ArtifactValidationError, match="empty"):
            validate_artifact(b"", ArtifactKind.output_log)

    def test_oversized_file_rejected(self):
        content = b"Entering Gaussian System\n" + b"x" * (MAX_ARTIFACT_BYTES + 1 - 25)
        with pytest.raises(ArtifactValidationError, match="exceeds maximum size"):
            validate_artifact(content, ArtifactKind.output_log)

    def test_total_upload_size_accepted(self):
        validate_total_upload_size([1000, 2000, 3000])

    def test_total_upload_size_rejected(self):
        with pytest.raises(ArtifactValidationError, match="Total artifact upload size"):
            validate_total_upload_size([MAX_TOTAL_UPLOAD_BYTES, 1])


# ---------------------------------------------------------------------------
# Text validation
# ---------------------------------------------------------------------------


class TestTextValidation:
    """Text artifact kinds must be valid UTF-8."""

    def test_valid_utf8_accepted(self):
        content = b"Entering Gaussian System\nSCF Done\n"
        validate_artifact(content, ArtifactKind.output_log)

    def test_binary_in_text_kind_rejected(self):
        content = b"Entering Gaussian System\n\xff\xfe\x00\x01"
        with pytest.raises(ArtifactValidationError, match="valid UTF-8"):
            validate_artifact(content, ArtifactKind.output_log)

    def test_binary_checkpoint_accepted(self):
        """checkpoint kind allows binary content (no text check)."""
        content = b"\x00\x01\x02\x03\xff\xfe binary checkpoint data"
        validate_artifact(content, ArtifactKind.checkpoint)


# ---------------------------------------------------------------------------
# Non-log kinds (no signature required)
# ---------------------------------------------------------------------------


class TestNonLogKinds:
    def test_input_file_no_signature_required(self):
        content = b"%mem=32GB\n%nproc=8\n#p wb97xd/def2tzvp opt\n\ntitle\n\n0 1\n"
        sha = validate_artifact(content, ArtifactKind.input)
        assert len(sha) == 64

    def test_ancillary_no_signature_required(self):
        content = b"some ancillary data\n"
        sha = validate_artifact(content, ArtifactKind.ancillary)
        assert len(sha) == 64


# ---------------------------------------------------------------------------
# S3 content-addressed storage (requires MinIO running)
# ---------------------------------------------------------------------------


def _minio_available() -> bool:
    """Check if MinIO is reachable."""
    try:
        client = _get_s3_client()
        client.list_buckets()
        return True
    except Exception:
        return False


@pytest.fixture()
def s3_test_bucket():
    """Create a dedicated test bucket and clean up after."""
    client = _get_s3_client()
    _ensure_bucket(client)
    # Ensure test bucket exists
    try:
        client.head_bucket(Bucket=TEST_BUCKET)
    except Exception:
        client.create_bucket(Bucket=TEST_BUCKET)

    yield client, TEST_BUCKET

    # Cleanup: delete all objects in test bucket
    try:
        response = client.list_objects_v2(Bucket=TEST_BUCKET)
        for obj in response.get("Contents", []):
            client.delete_object(Bucket=TEST_BUCKET, Key=obj["Key"])
        client.delete_bucket(Bucket=TEST_BUCKET)
    except Exception:
        pass


@pytest.mark.skipif(not _minio_available(), reason="MinIO not running")
class TestS3Storage:
    def test_key_layout(self):
        sha = "a577811dc7167bfc1234567890abcdef1234567890abcdef1234567890abcdef"
        key = content_addressed_key(sha)
        assert key == f"a5/{sha}"

    def test_store_returns_s3_uri(self, s3_test_bucket):
        client, bucket = s3_test_bucket
        content = GAUSSIAN_OPT_LOG.read_bytes()
        sha = hashlib.sha256(content).hexdigest()

        uri = store_artifact(content, sha, client=client, bucket=bucket)
        assert uri.startswith(f"s3://{bucket}/")
        assert sha in uri

    def test_stored_content_matches(self, s3_test_bucket):
        client, bucket = s3_test_bucket
        content = GAUSSIAN_OPT_LOG.read_bytes()
        sha = hashlib.sha256(content).hexdigest()

        store_artifact(content, sha, client=client, bucket=bucket)

        # Retrieve and verify
        key = content_addressed_key(sha)
        response = client.get_object(Bucket=bucket, Key=key)
        stored = response["Body"].read()
        assert stored == content

    def test_dedup_same_content(self, s3_test_bucket):
        client, bucket = s3_test_bucket
        content = GAUSSIAN_OPT_LOG.read_bytes()
        sha = hashlib.sha256(content).hexdigest()

        uri1 = store_artifact(content, sha, client=client, bucket=bucket)
        uri2 = store_artifact(content, sha, client=client, bucket=bucket)
        assert uri1 == uri2

    def test_different_files_different_keys(self, s3_test_bucket):
        client, bucket = s3_test_bucket

        content1 = GAUSSIAN_OPT_LOG.read_bytes()
        sha1 = hashlib.sha256(content1).hexdigest()
        uri1 = store_artifact(content1, sha1, client=client, bucket=bucket)

        content2 = GAUSSIAN_FREQ_LOG.read_bytes()
        sha2 = hashlib.sha256(content2).hexdigest()
        uri2 = store_artifact(content2, sha2, client=client, bucket=bucket)

        assert uri1 != uri2
