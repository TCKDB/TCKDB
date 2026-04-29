"""Reusable artifact upload fragment shared across workflows and the
calculation-targeted artifact upload endpoint."""

from __future__ import annotations

from pydantic import Field

from app.db.models.common import ArtifactKind
from app.schemas.common import SchemaBase


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
        storage path).  E.g. ``"input.log"``.
    :param content_base64: Base64-encoded file content.
    :param sha256: Optional SHA-256 hash declared by the uploader. Must be
        64 lowercase hex characters; if provided, the server verifies it
        matches the content.
    :param bytes: Optional declared file size; must be > 0. If provided,
        the server verifies it matches the decoded content length.
    """

    kind: ArtifactKind
    filename: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bytes: int | None = Field(default=None, gt=0)
