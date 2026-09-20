"""Upload payload for a depositor-supplied ThermoML XML document (Phase C-E6).

Unlike every other workflow upload schema, this one carries inline file
bytes rather than already-structured scientific content: a ThermoML
document *is* the payload, and this schema is only the transport for it.
Validation (XSD), parsing and mapping all happen server-side
(``app.importers.thermoml``) and persistence in
``app.services.thermoml_cp_import.import_thermoml_cp_upload`` — nothing
about ThermoML's own shape leaks into this schema. Mirrors
``ArtifactIn.content_base64`` (``tckdb_schemas.fragments.artifact``), the
only other schema in this codebase that carries inline file bytes; base64
*shape* validation (and the size cap) happens in the route, exactly as
``ArtifactIn``'s does in ``app.services.artifact_persistence``.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from tckdb_schemas.rights import DepositRights

from app.schemas.common import SchemaBase


class ThermoMLUploadRequest(SchemaBase):
    """One ThermoML XML document, uploaded directly (not from the NIST
    bulk archive — that path is the ``--archive``/``--doi`` CLI only).

    :param filename: Provenance metadata only — recorded on the custody
        row's warnings/summary text. Never used as a storage path (the
        object store is content-addressed by SHA-256).
    :param content_base64: Base64-encoded ThermoML XML bytes.
    :param doi: The article's own DOI, if the depositor wants to assert
        it explicitly. Optional — when omitted, the DOI is taken from the
        file's own ``Citation/sDOI`` element, if present. Supplying one
        that disagrees with the file's own ``sDOI`` is a refusal
        (``thermoml_doi_conflict``), never a silent override.
    :param rights: The depositor's deposit-time license agreement.
        Required — deliberately **not** optional like ``DepositRights`` on
        every other upload schema. Every other upload route can fall back
        on "attested later, at release time" because the underlying
        science already exists in TCKDB under someone's authority; this
        route's whole content *is* a third-party document the depositor
        is asserting the right to submit, and unlike the CLI's
        ``--archive`` path there is no NIST/TRC ``source_terms`` to stand
        on instead. Omitting it is refused by ordinary Pydantic
        field-requiredness (422, the same mechanism every other required
        field on every other schema in this codebase already uses — not
        a new bespoke refusal). Recorded as an ordinary
        ``depositor_agreement`` attestation, never ``source_terms``.

    No ``dry_run`` field: none of the sibling ``/uploads/*`` routes in
    this router preview a request before committing it (the one preview
    mechanism in this codebase, ``POST /bundles/dry-run``, is a wholly
    separate endpoint from its commit sibling, not a boolean flag shared
    with one). This route follows that precedent and always commits, like
    every other route in ``app.api.routes.uploads``; a caller who wants a
    preview runs the same document through the backend CLI's default
    (no ``--commit``) dry-run mode instead.
    """

    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)
    doi: str | None = Field(default=None, max_length=255)
    rights: DepositRights

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError(
                "filename must not contain path separators or '..'"
            )
        return value

    @field_validator("doi")
    @classmethod
    def _normalize_doi(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


__all__ = ["ThermoMLUploadRequest"]
