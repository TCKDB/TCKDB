"""Reusable artifact upload fragment shared across workflows and the
calculation-targeted artifact upload endpoint."""

from __future__ import annotations

import os
import unicodedata

from pydantic import Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import ArtifactKind

#: Per-component limit on most filesystems (ext4, NTFS, APFS).
MAX_FILENAME_LENGTH = 255

#: Per-kind filename extension allowlist. Compared case-insensitively
#: against the single trailing extension — ``foo.log.exe`` is rejected
#: because ``.exe`` is not in any kind's set. Content validation
#: (ESS-header signature, UTF-8 check) lives in ``artifact_storage``;
#: this set is the coarse first-pass label check.
KIND_ALLOWED_EXTENSIONS: dict[ArtifactKind, frozenset[str]] = {
    ArtifactKind.input: frozenset({".gjf", ".in"}),
    ArtifactKind.output_log: frozenset({".out", ".log", ".orca"}),
    ArtifactKind.checkpoint: frozenset({".chk", ".gbw"}),
    ArtifactKind.formatted_checkpoint: frozenset({".fchk"}),
    # Raw Hessian sidecars: ORCA ``.hess``, Gaussian formatted checkpoint
    # ``.fchk`` (Cartesian Force Constants block). The parsed matrix in
    # ``calc_hessian`` is the queryable source of truth; this artifact is
    # the byte-exact audit trail. See DR-0030.
    ArtifactKind.hessian: frozenset({".hess", ".fchk"}),
    ArtifactKind.ancillary: frozenset(
        {".txt", ".dat", ".csv", ".json", ".yml", ".yaml"}
    ),
}


def _validate_filename(value: str, kind: ArtifactKind) -> str:
    """Reject filenames that would be unsafe to echo back, log, or
    surface in UIs, and enforce a per-kind extension allowlist.

    Storage paths never use this value (artifacts are content-addressed
    by sha256 in :func:`artifact_storage.content_addressed_key`), so
    these rules are about defending downstream consumers of the audit
    trail and constraining filenames to a per-kind allowlist of safe
    extensions for an ESS workflow.
    """
    value = unicodedata.normalize("NFC", value)

    if len(value) > MAX_FILENAME_LENGTH:
        raise ValueError(
            f"filename exceeds {MAX_FILENAME_LENGTH}-character limit"
        )

    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError("filename contains control or NUL characters")

    if "/" in value or "\\" in value:
        raise ValueError("filename must not contain path separators")

    if value.startswith(".") or value.startswith("-"):
        raise ValueError("filename must not start with '.' or '-'")

    if ".." in value:
        raise ValueError("filename must not contain '..'")

    _, ext = os.path.splitext(value)
    allowed = KIND_ALLOWED_EXTENSIONS.get(kind, frozenset())
    if ext.lower() not in allowed:
        permitted = ", ".join(sorted(allowed)) if allowed else "(none)"
        raise ValueError(
            f"filename extension '{ext}' is not allowed for kind "
            f"'{kind.value}'; permitted: {permitted}"
        )

    return value


class ArtifactIn(SchemaBase):
    """An artifact (file) attached to a calculation — upload transport only.

    Provide ``content_base64`` to upload file content inline.  The server
    validates the content (ESS signature, size limits, SHA-256 integrity),
    writes it to a content-addressed store, and creates a
    ``CalculationArtifact`` row with the final URI.

    This schema is strictly upload-facing.  The DB model
    (``CalculationArtifact``) stores ``uri``, ``sha256``, ``bytes``, and
    ``kind`` — no inline content. The ``uri`` is backend-generated only
    and is intentionally not exposed on this schema.

    :param kind: Artifact type (input, output_log, checkpoint, etc.).
    :param filename: Original filename (provenance metadata, not used for
        storage path).  E.g. ``"input.log"``. Extension must be in
        :data:`KIND_ALLOWED_EXTENSIONS` for the supplied ``kind`` and
        must pass the character / length / leading-character rules in
        :func:`_validate_filename`.
    :param content_base64: Base64-encoded file content.
    :param sha256: Optional SHA-256 hash declared by the uploader. Must be
        64 lowercase hex characters; if provided, the server verifies it
        matches the content.
    :param bytes: Optional declared file size; must be > 0. If provided,
        the server verifies it matches the decoded content length.
    """

    kind: ArtifactKind
    filename: str = Field(min_length=1, max_length=MAX_FILENAME_LENGTH)
    content_base64: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bytes: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_filename(self) -> "ArtifactIn":
        # Re-assigns the normalized form back onto the model so callers
        # see the NFC version, not whatever decomposed form arrived.
        object.__setattr__(
            self, "filename", _validate_filename(self.filename, self.kind)
        )
        return self
