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

from pydantic import ConfigDict, Field, field_validator
from tckdb_schemas.rights import DepositRights

from app.schemas.common import SchemaBase


class ThermoMLUploadRequest(SchemaBase):
    """One ThermoML XML document, uploaded directly (not from the NIST
    bulk archive — that path is the ``--archive``/``--doi`` CLI only).

    :param filename: Provenance metadata only — recorded verbatim (as
        ``f"upload:{filename}"``) in the custody row's
        ``mapping_report_json["source_label"]``. Never used as a storage
        path (the object store is content-addressed by SHA-256) and never
        used as a fallback ``raw_uri`` the way an archive member path is
        (see ``app.services.thermoml_cp_import._raw_uri_for``'s
        ``allow_member_path_fallback``) — it is a display label, not a
        retrievable location.
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

    # A minimal valid payload. Published as the JSON Schema's ``examples``, in
    # the OpenAPI document, and in the producer contract, which validates it
    # against this model on every generation (generate_producer_contract.py).
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "filename": "benzene_cp_example.xml",
                    # A complete ThermoML 4.0 document: one ideal-gas Cp point for
                    # benzene at 298.15 K, an invented value, no DOI -- modelled on the
                    # hand-authored importer fixtures in app/importers/thermoml/fixtures/.
                    # The route accepts it (tests/api/test_producer_contract_examples.py).
                    "content_base64": (
                        "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiPz4KPERhdGFSZXBvcnQgeG1sbnM9"
                        "Imh0dHA6Ly93d3cuaXVwYWMub3JnL25hbWVzcGFjZXMvVGhlcm1vTUwiPgogIDxWZXJzaW9uPgog"
                        "ICAgPG5WZXJzaW9uTWFqb3I+NDwvblZlcnNpb25NYWpvcj4KICAgIDxuVmVyc2lvbk1pbm9yPjA8"
                        "L25WZXJzaW9uTWlub3I+CiAgPC9WZXJzaW9uPgogIDxDaXRhdGlvbj4KICAgIDxzQXV0aG9yPkRv"
                        "ZSwgSi48L3NBdXRob3I+CiAgICA8c1B1Yk5hbWU+RXhhbXBsZSBKb3VybmFsPC9zUHViTmFtZT4K"
                        "ICAgIDx5clB1YllyPjIwMjY8L3lyUHViWXI+CiAgICA8c1RpdGxlPkV4YW1wbGU6IGFuIGlkZWFs"
                        "LWdhcyBoZWF0IGNhcGFjaXR5IG9mIGJlbnplbmUgKGludmVudGVkIHZhbHVlKTwvc1RpdGxlPgog"
                        "IDwvQ2l0YXRpb24+CiAgPENvbXBvdW5kPgogICAgPFJlZ051bT4KICAgICAgPG5PcmdOdW0+MTwv"
                        "bk9yZ051bT4KICAgIDwvUmVnTnVtPgogICAgPHNTdGFuZGFyZEluQ2hJPkluQ2hJPTFTL0M2SDYv"
                        "YzEtMi00LTYtNS0zLTEvaDEtNkg8L3NTdGFuZGFyZEluQ2hJPgogICAgPHNTdGFuZGFyZEluQ2hJ"
                        "S2V5PlVIT1ZRTlpKWVNPUk5CLVVIRkZGQU9ZU0EtTjwvc1N0YW5kYXJkSW5DaElLZXk+CiAgICA8"
                        "c0NvbW1vbk5hbWU+YmVuemVuZTwvc0NvbW1vbk5hbWU+CiAgICA8c0Zvcm11bGFNb2xlYz5DNkg2"
                        "PC9zRm9ybXVsYU1vbGVjPgogIDwvQ29tcG91bmQ+CiAgPFB1cmVPck1peHR1cmVEYXRhPgogICAg"
                        "PG5QdXJlT3JNaXh0dXJlRGF0YU51bWJlcj4xPC9uUHVyZU9yTWl4dHVyZURhdGFOdW1iZXI+CiAg"
                        "ICA8Q29tcG9uZW50PgogICAgICA8UmVnTnVtPgogICAgICAgIDxuT3JnTnVtPjE8L25PcmdOdW0+"
                        "CiAgICAgIDwvUmVnTnVtPgogICAgPC9Db21wb25lbnQ+CiAgICA8UHJvcGVydHk+CiAgICAgIDxu"
                        "UHJvcE51bWJlcj4xPC9uUHJvcE51bWJlcj4KICAgICAgPFByb3BlcnR5LU1ldGhvZElEPgogICAg"
                        "ICAgIDxQcm9wZXJ0eUdyb3VwPgogICAgICAgICAgPEhlYXRDYXBhY2l0eUFuZERlcml2ZWRQcm9w"
                        "PgogICAgICAgICAgICA8ZVByb3BOYW1lPk1vbGFyIGhlYXQgY2FwYWNpdHkgYXQgY29uc3RhbnQg"
                        "cHJlc3N1cmUsIEovSy9tb2w8L2VQcm9wTmFtZT4KICAgICAgICAgICAgPHNNZXRob2ROYW1lPnN0"
                        "YXRpc3RpY2FsIHRoZXJtb2R5bmFtaWNzPC9zTWV0aG9kTmFtZT4KICAgICAgICAgIDwvSGVhdENh"
                        "cGFjaXR5QW5kRGVyaXZlZFByb3A+CiAgICAgICAgPC9Qcm9wZXJ0eUdyb3VwPgogICAgICA8L1By"
                        "b3BlcnR5LU1ldGhvZElEPgogICAgICA8UHJvcFBoYXNlSUQ+CiAgICAgICAgPGVQcm9wUGhhc2U+"
                        "SWRlYWwgZ2FzPC9lUHJvcFBoYXNlPgogICAgICA8L1Byb3BQaGFzZUlEPgogICAgICA8ZVByZXNl"
                        "bnRhdGlvbj5EaXJlY3QgdmFsdWUsIFg8L2VQcmVzZW50YXRpb24+CiAgICA8L1Byb3BlcnR5Pgog"
                        "ICAgPFBoYXNlSUQ+CiAgICAgIDxlUGhhc2U+SWRlYWwgZ2FzPC9lUGhhc2U+CiAgICA8L1BoYXNl"
                        "SUQ+CiAgICA8Q29uc3RyYWludD4KICAgICAgPG5Db25zdHJhaW50TnVtYmVyPjE8L25Db25zdHJh"
                        "aW50TnVtYmVyPgogICAgICA8Q29uc3RyYWludElEPgogICAgICAgIDxDb25zdHJhaW50VHlwZT4K"
                        "ICAgICAgICAgIDxlUHJlc3N1cmU+UHJlc3N1cmUsIGtQYTwvZVByZXNzdXJlPgogICAgICAgIDwv"
                        "Q29uc3RyYWludFR5cGU+CiAgICAgIDwvQ29uc3RyYWludElEPgogICAgICA8Q29uc3RyYWludFBo"
                        "YXNlSUQ+CiAgICAgICAgPGVDb25zdHJhaW50UGhhc2U+SWRlYWwgZ2FzPC9lQ29uc3RyYWludFBo"
                        "YXNlPgogICAgICA8L0NvbnN0cmFpbnRQaGFzZUlEPgogICAgICA8bkNvbnN0cmFpbnRWYWx1ZT4x"
                        "MDA8L25Db25zdHJhaW50VmFsdWU+CiAgICAgIDxuQ29uc3RyRGlnaXRzPjE8L25Db25zdHJEaWdp"
                        "dHM+CiAgICA8L0NvbnN0cmFpbnQ+CiAgICA8VmFyaWFibGU+CiAgICAgIDxuVmFyTnVtYmVyPjE8"
                        "L25WYXJOdW1iZXI+CiAgICAgIDxWYXJpYWJsZUlEPgogICAgICAgIDxWYXJpYWJsZVR5cGU+CiAg"
                        "ICAgICAgICA8ZVRlbXBlcmF0dXJlPlRlbXBlcmF0dXJlLCBLPC9lVGVtcGVyYXR1cmU+CiAgICAg"
                        "ICAgPC9WYXJpYWJsZVR5cGU+CiAgICAgIDwvVmFyaWFibGVJRD4KICAgIDwvVmFyaWFibGU+CiAg"
                        "ICA8TnVtVmFsdWVzPgogICAgICA8VmFyaWFibGVWYWx1ZT4KICAgICAgICA8blZhck51bWJlcj4x"
                        "PC9uVmFyTnVtYmVyPgogICAgICAgIDxuVmFyVmFsdWU+Mjk4LjE1PC9uVmFyVmFsdWU+CiAgICAg"
                        "ICAgPG5WYXJEaWdpdHM+NTwvblZhckRpZ2l0cz4KICAgICAgPC9WYXJpYWJsZVZhbHVlPgogICAg"
                        "ICA8UHJvcGVydHlWYWx1ZT4KICAgICAgICA8blByb3BOdW1iZXI+MTwvblByb3BOdW1iZXI+CiAg"
                        "ICAgICAgPG5Qcm9wVmFsdWU+ODIuNDQ8L25Qcm9wVmFsdWU+CiAgICAgICAgPG5Qcm9wRGlnaXRz"
                        "PjQ8L25Qcm9wRGlnaXRzPgogICAgICA8L1Byb3BlcnR5VmFsdWU+CiAgICA8L051bVZhbHVlcz4K"
                        "ICA8L1B1cmVPck1peHR1cmVEYXRhPgo8L0RhdGFSZXBvcnQ+Cg=="
                    ),
                    "rights": {
                        "license": "CC-BY-4.0",
                        "depositor_attests_right_to_license": True
                    }
                }
            ]
        },
    )

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
        """Trim ``doi``; a blank DOI is treated as absent."""
        if value is None:
            return None
        value = value.strip()
        return value or None


__all__ = ["ThermoMLUploadRequest"]
