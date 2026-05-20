"""Shared helpers for CCCBDB → TCKDB payload builders.

These helpers are intentionally small and dependency-light. The
builders themselves stay in their own modules so each one is easy to
test in isolation.

The ``ExternalSourceMetadata`` model carries CCCBDB-level provenance
in the structured side-channel the existing TCKDB upload schemas do
not expose directly (per-value reference labels, raw page kind,
content hash). Phase 3 / schema-gap work may promote these into a
real ``external_source_record`` table; until then the builder output
preserves them verbatim alongside the validated payload sections.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.importers.cccbdb.models import (
    CCCBDBExperimentalSpeciesRecord,
    CCCBDBValueRef,
)


class ExternalSourceMetadata(BaseModel):
    """CCCBDB provenance preserved alongside a built payload.

    Mirrors the proposed ``external_source_record`` shape from
    ``backend/docs/specs/cccbdb_importer.md`` §7. Until that schema
    lands, the builder hands this dict to its caller; the upload
    workflow can stash it on the relevant ``note`` fields or carry
    it in an out-of-band log.
    """

    model_config = ConfigDict(extra="forbid")

    name: Literal["CCCBDB"] = "CCCBDB"
    release: str
    doi: str
    url: str
    record_key: str
    page_kind: Literal["experimental_species"] = "experimental_species"
    content_sha256: str
    parser_version: str
    per_value_references: dict[str, dict[str, Any]] = Field(
        default_factory=dict
    )
    unparsed: dict[str, Any] = Field(default_factory=dict)


def external_source_from_record(
    record: CCCBDBExperimentalSpeciesRecord,
) -> ExternalSourceMetadata:
    """Project the parser's source metadata onto :class:`ExternalSourceMetadata`."""

    meta = record.source_metadata
    return ExternalSourceMetadata(
        name=meta.source,
        release=meta.source_release,
        doi=meta.source_database_doi,
        url=meta.source_url,
        record_key=meta.source_record_key or meta.content_sha256,
        page_kind=meta.page_kind,
        content_sha256=meta.content_sha256,
        parser_version=meta.parser_version,
    )


def value_ref_to_dict(ref: CCCBDBValueRef | None) -> dict[str, Any] | None:
    """Compact a :class:`CCCBDBValueRef` into a JSON-ready dict.

    Returns ``None`` if the ref is missing or empty after normalization.
    """

    if ref is None:
        return None
    payload = {
        "reference_label": ref.reference_label,
        "reference_comment": ref.reference_comment,
        "raw_reference_text": ref.raw_reference_text,
        "parsed_literature_hint": ref.parsed_literature_hint,
    }
    if not any(v for v in payload.values()):
        return None
    return {k: v for k, v in payload.items() if v is not None}


class BuildResult(BaseModel):
    """Top-level result returned by :func:`build_experimental_species_payload`.

    Each ``*_payload`` field is a dict ready to be wrapped in / fed to
    the corresponding upload-workflow request model, or ``None`` if
    Phase 1 did not capture enough data to populate it. The
    ``warnings`` list records parsed values that the builder
    deliberately chose not to map into the validated payload (e.g.
    experimental vibrational modes, rotational constants).
    """

    model_config = ConfigDict(extra="forbid")

    species_entry_payload: dict[str, Any] | None = None
    species_entry_payload_is_valid: bool = False
    thermo_payload: dict[str, Any] | None = None
    statmech_payload: dict[str, Any] | None = None
    geometry_payload: dict[str, Any] | None = None
    external_source: ExternalSourceMetadata
    warnings: list[str] = Field(default_factory=list)
