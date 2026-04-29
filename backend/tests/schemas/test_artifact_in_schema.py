"""Schema-level tests for the tightened ``ArtifactIn`` validation.

The move from ``backend/app/schemas/workflows/network_pdep_upload.py`` to
``backend/app/schemas/fragments/artifact.py`` is a behavior change, not a
pure refactor: the ``sha256`` constraint is now a lowercase-hex regex
(was length-only) and ``bytes`` must be strictly positive (was
``ge=0``). These tests pin the new contract.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.fragments.artifact import ArtifactIn


def _valid_kwargs(**overrides) -> dict:
    base = {
        "kind": "ancillary",
        "filename": "x.dat",
        "content_base64": "aGVsbG8=",
    }
    base.update(overrides)
    return base


def test_lowercase_sha_accepted() -> None:
    a = ArtifactIn(**_valid_kwargs(sha256="a" * 64))
    assert a.sha256 == "a" * 64


def test_uppercase_sha_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(sha256="A" * 64))


def test_short_sha_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(sha256="a" * 63))


def test_non_hex_sha_rejected() -> None:
    # 64 chars but not all hex
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(sha256="g" * 64))


def test_bytes_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(bytes=0))


def test_bytes_positive_accepted() -> None:
    a = ArtifactIn(**_valid_kwargs(bytes=1))
    assert a.bytes == 1


def test_bytes_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(bytes=-1))


def test_uri_field_rejected() -> None:
    """ArtifactIn is upload-facing; backend-generated ``uri`` must not be
    settable from a client payload (extra='forbid')."""
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(uri="s3://bucket/key"))


def test_re_export_from_network_pdep_upload() -> None:
    """The legacy import path stays valid for back-compat."""
    from app.schemas.workflows.network_pdep_upload import (
        ArtifactIn as LegacyArtifactIn,
    )

    assert LegacyArtifactIn is ArtifactIn
