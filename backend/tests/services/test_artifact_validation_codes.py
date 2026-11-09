"""Every artifact validation refusal carries a catalogue code and structured context."""

from __future__ import annotations

import pytest

from app.db.models.common import ArtifactKind
from app.services.artifact_persistence import _strict_b64decode
from app.services.artifact_storage import (
    MAX_ARTIFACT_BYTES,
    MAX_ENCODED_ARTIFACT_LEN,
    MAX_TOTAL_ENCODED_UPLOAD_LEN,
    MAX_TOTAL_UPLOAD_BYTES,
    ArtifactValidationError,
    validate_artifact,
    validate_encoded_lengths,
    validate_total_upload_size,
)


def _refusal(fn, *args, **kwargs) -> ArtifactValidationError:
    with pytest.raises(ArtifactValidationError) as info:
        fn(*args, **kwargs)
    return info.value


def test_unknown_signature() -> None:
    exc = _refusal(validate_artifact, b"not an ess log\n" * 10, ArtifactKind.output_log)
    assert exc.code == "output_log_signature_unrecognized"
    assert exc.context["window_bytes"] == 4096
    assert "xtb" in exc.context["supported"]
    assert str(exc) == exc.detail  # the code is not repeated in the sentence


def test_empty() -> None:
    assert _refusal(validate_artifact, b"", ArtifactKind.ancillary).code == "artifact_empty"


def test_too_large() -> None:
    exc = _refusal(validate_artifact, b"x" * (MAX_ARTIFACT_BYTES + 1), ArtifactKind.ancillary)
    assert exc.code == "artifact_too_large"
    assert exc.context == {"size_bytes": MAX_ARTIFACT_BYTES + 1, "limit_bytes": MAX_ARTIFACT_BYTES}


def test_sha_mismatch() -> None:
    exc = _refusal(validate_artifact, b"abc", ArtifactKind.ancillary, declared_sha256="0" * 64)
    assert exc.code == "artifact_sha256_mismatch"
    assert exc.context["declared_sha256"] == "0" * 64
    assert len(exc.context["computed_sha256"]) == 64


def test_size_mismatch() -> None:
    exc = _refusal(validate_artifact, b"abc", ArtifactKind.ancillary, declared_bytes=5)
    assert exc.code == "artifact_size_mismatch"
    assert exc.context == {"declared_bytes": 5, "actual_bytes": 3}


def test_not_utf8() -> None:
    exc = _refusal(validate_artifact, b"\xff\xfe\x00bad", ArtifactKind.output_log)
    assert exc.code == "artifact_not_utf8_text"
    assert exc.context == {"artifact_kind": "output_log"}


def test_total_size() -> None:
    exc = _refusal(validate_total_upload_size, [MAX_TOTAL_UPLOAD_BYTES, 1])
    assert exc.code == "artifact_total_size_too_large"
    assert exc.context["limit_bytes"] == MAX_TOTAL_UPLOAD_BYTES


def test_encoded_lengths() -> None:
    exc = _refusal(validate_encoded_lengths, [1, MAX_ENCODED_ARTIFACT_LEN + 1])
    assert exc.code == "artifact_encoded_payload_too_large"
    assert exc.context["artifact_index"] == 1
    each = MAX_ENCODED_ARTIFACT_LEN
    exc = _refusal(validate_encoded_lengths, [each] * (MAX_TOTAL_ENCODED_UPLOAD_LEN // each + 1))
    assert exc.code == "artifact_total_encoded_payload_too_large"


def test_invalid_base64_keeps_the_filename_out_of_the_sentence() -> None:
    exc = _refusal(_strict_b64decode, "secret-name.log", "!!not base64!!")
    assert exc.code == "artifact_invalid_base64"
    assert "secret-name" not in str(exc)
    assert exc.context == {"depositor_supplied": {"filename": "secret-name.log"}}
