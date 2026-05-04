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


# ---------------------------------------------------------------------------
# Filename validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,filename",
    [
        ("input", "geom.gjf"),
        ("input", "geom.in"),
        ("output_log", "opt.log"),
        ("output_log", "opt.out"),
        ("output_log", "opt.orca"),
        ("output_log", "OPT.LOG"),  # case-insensitive
        ("checkpoint", "wfn.chk"),
        ("checkpoint", "wfn.gbw"),
        ("formatted_checkpoint", "wfn.fchk"),
        ("ancillary", "note.txt"),
        ("ancillary", "x.dat"),
    ],
)
def test_filename_extension_allowed_per_kind(kind: str, filename: str) -> None:
    a = ArtifactIn(**_valid_kwargs(kind=kind, filename=filename))
    assert a.filename == filename if filename.islower() else a.filename


@pytest.mark.parametrize(
    "kind,filename",
    [
        # Wrong-kind extension
        ("input", "geom.log"),
        ("output_log", "geom.gjf"),
        ("checkpoint", "wfn.fchk"),
        ("ancillary", "note.exe"),
        # Double extension (real ext is .exe)
        ("output_log", "opt.log.exe"),
        # No extension
        ("output_log", "opt"),
    ],
)
def test_filename_extension_rejected(kind: str, filename: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(kind=kind, filename=filename))


@pytest.mark.parametrize(
    "filename",
    [
        "../etc/passwd.log",          # directory traversal
        "sub/dir/opt.log",            # forward slash
        "sub\\dir\\opt.log",          # backslash
        ".hidden.log",                # leading dot
        "-rf.log",                    # leading hyphen (shell flag injection)
        "opt\x00.log",                # NUL
        "opt\n.log",                  # control char
    ],
)
def test_filename_unsafe_chars_rejected(filename: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(kind="output_log", filename=filename))


def test_filename_too_long_rejected() -> None:
    long_name = ("a" * 260) + ".log"
    with pytest.raises(ValidationError):
        ArtifactIn(**_valid_kwargs(kind="output_log", filename=long_name))


def test_filename_nfc_normalized() -> None:
    # "é" as NFD: U+0065 U+0301; should normalize to NFC U+00E9 before
    # length and content checks.
    decomposed = "café.log"
    a = ArtifactIn(**_valid_kwargs(kind="output_log", filename=decomposed))
    assert a.filename == "café.log"
