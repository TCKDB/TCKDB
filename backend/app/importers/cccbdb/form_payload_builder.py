"""Builder: CCCBDB form-result parsed JSON → ``MolecularPropertyObservationCreate``.

Closes the loop from Phase 6/7 (form-resolver) to the same
``MolecularPropertyObservationCreate`` channel the flat property-table
builder already feeds. The builder consumes either:

* an in-memory :class:`CCCBDBFormResultTable` (the parser's return type), or
* the parsed JSON file the form resolver wrote to
  ``parsed/form_<target_kind>_<key>_<sha>.json``

and yields one :class:`MolecularPropertyObservationCreate` per
*workflow-ready* row.

Phase 8 ships exactly one supported ``target_kind`` —
``atomization_energy`` — because it has unambiguous semantics
(scalar enthalpy at a fixed temperature, kJ/mol). Other targets land
in the parsed archive but the builder skips them and emits a warning.

The builder NEVER:

* writes to the database;
* resolves ``species_entry_id`` — identity hints ride along inside
  ``raw_payload_json["identity_hint"]`` for the workflow layer to
  consume;
* invents a temperature — the schema's ``temperature_k`` validator
  requires ``> 0``, so the 0 K condition is encoded as
  ``property_label="atomization_energy_0k"`` with
  ``temperature_k=None``;
* discards the 298 K secondary value — it rides on
  ``raw_payload_json["secondary_values"]["298K"]``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.db.models.common import MolecularPropertyKind, ScientificOriginKind
from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.parsers.form_result import (
    CCCBDBFormResultTable,
    FormResultRow,
)
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)


# Map of supported target_kind → (MolecularPropertyKind, property_label).
# Only targets whose semantics fit a single scalar+unit go here; the
# others stay in the parsed archive without a builder.
_TARGET_KIND_MAP: dict[str, tuple[MolecularPropertyKind, str | None]] = {
    "atomization_energy": (
        MolecularPropertyKind.atomization_energy,
        "atomization_energy_0k",
    ),
}


class CCCBDBFormPayloadBuildResult(BaseModel):
    """One per-row builder outcome.

    ``payload`` is ``None`` for rows the builder intentionally skipped
    (no scalar value, unsupported target kind). The row's identity /
    raw_row content still flows through the ``warnings`` channel so a
    downstream maintainer can spot column drift.
    """

    model_config = ConfigDict(extra="forbid")

    row_index: int
    payload: MolecularPropertyObservationCreate | None
    is_workflow_ready: bool
    warnings: list[str] = Field(default_factory=list)


def build_atomization_energy_payloads_from_form_result(
    parsed: CCCBDBFormResultTable,
    *,
    selection_metadata: dict[str, Any] | None = None,
    resolver_strategy: str | None = None,
    species_key: str | None = None,
) -> list[CCCBDBFormPayloadBuildResult]:
    """Build molecular-property payloads from a parsed CCCBDB
    atomization-energy form result.

    :param parsed: A :class:`CCCBDBFormResultTable` from
        :func:`parse_form_result_page` (``target_kind="atomization_energy"``).
    :param selection_metadata: Optional selection-stage metadata from
        the form resolver (``selection_policy`` / ``selection_status``
        / ``selection_match_basis`` / ``selected_name`` /
        ``selected_cas_number``). Preserved verbatim under
        ``raw_payload_json["selection"]``.
    :param resolver_strategy: Optional resolver-strategy label (e.g.
        ``"requests_session_form_post"``) recorded under
        ``raw_payload_json["resolver_strategy"]``.
    :param species_key: Optional maintainer-supplied stable key. When
        present it becomes ``external_source_record_key``; otherwise
        the parser's content-sha is used.
    :returns: One :class:`CCCBDBFormPayloadBuildResult` per parsed row,
        in row order. Rows with no numeric 0 K value are skipped with
        a warning but never crash the run.
    """

    if parsed.target_kind != "atomization_energy":
        return [
            CCCBDBFormPayloadBuildResult(
                row_index=-1,
                payload=None,
                is_workflow_ready=False,
                warnings=[
                    f"unsupported target_kind {parsed.target_kind!r}; "
                    "atomization_energy is the only kind Phase 8 builds"
                ],
            )
        ]

    property_kind, property_label = _TARGET_KIND_MAP["atomization_energy"]
    record_key = species_key or parsed.content_sha256

    results: list[CCCBDBFormPayloadBuildResult] = []
    for row in parsed.rows:
        results.append(
            _build_row(
                row,
                parsed=parsed,
                property_kind=property_kind,
                property_label=property_label,
                selection_metadata=selection_metadata,
                resolver_strategy=resolver_strategy,
                record_key=record_key,
            )
        )
    return results


def _build_row(
    row: FormResultRow,
    *,
    parsed: CCCBDBFormResultTable,
    property_kind: MolecularPropertyKind,
    property_label: str | None,
    selection_metadata: dict[str, Any] | None,
    resolver_strategy: str | None,
    record_key: str | None,
) -> CCCBDBFormPayloadBuildResult:
    """Convert one parsed row into a ``MolecularPropertyObservationCreate``,
    or yield a skipped/no-payload result when the row lacks a 0 K
    scalar."""

    warnings: list[str] = list(row.warnings)

    if row.value is None:
        warnings.append(
            "no numeric 0 K atomization energy on this row; payload not built"
        )
        return CCCBDBFormPayloadBuildResult(
            row_index=row.row_index,
            payload=None,
            is_workflow_ready=False,
            warnings=warnings,
        )
    if not row.unit:
        warnings.append("missing unit; payload not built")
        return CCCBDBFormPayloadBuildResult(
            row_index=row.row_index,
            payload=None,
            is_workflow_ready=False,
            warnings=warnings,
        )

    raw_payload = _build_raw_payload(
        row=row,
        parsed=parsed,
        selection_metadata=selection_metadata,
        resolver_strategy=resolver_strategy,
    )

    try:
        payload = MolecularPropertyObservationCreate(
            species_entry_id=None,
            scientific_origin=ScientificOriginKind.experimental,
            property_kind=property_kind,
            property_label=property_label,
            scalar_value=row.value,
            scalar_unit=row.unit,
            scalar_uncertainty=row.uncertainty,
            temperature_k=None,
            method_note=None,
            state_label_raw=None,
            external_source_name=SOURCE_NAME,
            external_source_release=SOURCE_RELEASE,
            external_source_doi=SOURCE_DATABASE_DOI,
            external_source_url=parsed.source_url,
            external_source_record_key=record_key,
            external_source_page_kind="experimental_form_result",
            external_source_content_sha256=parsed.content_sha256,
            external_source_parser_version=PARSER_VERSION,
            raw_payload_json=raw_payload,
        )
    except ValidationError as exc:
        warnings.append(
            f"pydantic validation failed: {exc.errors()[0].get('msg', '?')}"
        )
        return CCCBDBFormPayloadBuildResult(
            row_index=row.row_index,
            payload=None,
            is_workflow_ready=False,
            warnings=warnings,
        )

    return CCCBDBFormPayloadBuildResult(
        row_index=row.row_index,
        payload=payload,
        is_workflow_ready=True,
        warnings=warnings,
    )


def _build_raw_payload(
    *,
    row: FormResultRow,
    parsed: CCCBDBFormResultTable,
    selection_metadata: dict[str, Any] | None,
    resolver_strategy: str | None,
) -> dict[str, Any]:
    """Forensic JSONB payload preserving everything the builder
    flattened out of the parser/resolver state. The shape mirrors the
    flat property-table builder's ``raw_payload_json`` for
    downstream-tooling parity."""

    identity_hint = _identity_hint(row)

    payload: dict[str, Any] = {
        "target_kind": parsed.target_kind,
        "row_index": row.row_index,
        "row_formula": row.formula,
        "row_name": row.name,
        "raw_row": dict(row.raw_row),
        "raw_value": row.value,
        "raw_unit": row.unit,
        "raw_uncertainty": row.uncertainty,
        "secondary_values": dict(row.secondary_values),
        "source_url": parsed.source_url,
        "final_url": parsed.final_url,
        "content_sha256": parsed.content_sha256,
        "source_metadata": {
            "source": SOURCE_NAME,
            "source_release": SOURCE_RELEASE,
            "source_database_doi": SOURCE_DATABASE_DOI,
            "parser_version": PARSER_VERSION,
        },
        "row_warnings": list(row.warnings),
    }
    if identity_hint:
        payload["identity_hint"] = identity_hint
    if resolver_strategy is not None:
        payload["resolver_strategy"] = resolver_strategy
    if selection_metadata is not None:
        payload["selection"] = dict(selection_metadata)
    return payload


def _identity_hint(row: FormResultRow) -> dict[str, Any] | None:
    """Surface the row's identity fields verbatim. We deliberately do
    NOT resolve to a ``species_entry`` here — that's the workflow
    layer's job, gated on dedup against existing rows.
    """

    hint: dict[str, Any] = {}
    if row.formula:
        hint["formula"] = row.formula
    if row.name:
        hint["name"] = row.name
    # CCCBDB form results don't currently expose CAS / InChIKey on
    # the result page; the resolver does carry them via the queue
    # record / selection metadata. The workflow layer can join them
    # if needed.
    return hint or None


# ---------------------------------------------------------------------------
# Disk-driven entry point: read a parsed/form_*.json file and build
# payloads from it. Used by the form-payload dry-run script.
# ---------------------------------------------------------------------------


@dataclass
class FormParsedFile:
    """In-memory shape of a parsed/form_*.json file the resolver
    writes."""

    path: Path
    target_kind: str
    table: CCCBDBFormResultTable
    selection_metadata: dict[str, Any] | None
    resolver_strategy: str | None
    species_key: str | None


def load_parsed_form_result(path: Path) -> FormParsedFile:
    """Load one parsed/form_*.json file into the shape the builder
    consumes. Raises :class:`ValueError` if the file's
    ``target_kind`` is missing.

    The on-disk JSON's keys are the ones
    :func:`app.importers.cccbdb.form_resolver._archive_parsed`
    emits — this loader is the inverse.
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    target_kind = data.get("target_kind")
    if not target_kind:
        raise ValueError(
            f"parsed form file {path} has no target_kind"
        )

    rows = [
        FormResultRow(
            row_index=r.get("row_index", i),
            formula=r.get("formula"),
            name=r.get("name"),
            value=r.get("value"),
            unit=r.get("unit"),
            uncertainty=r.get("uncertainty"),
            secondary_values=dict(r.get("secondary_values") or {}),
            raw_row=dict(r.get("raw_row") or {}),
            reference_label=r.get("reference_label"),
            reference_comment=r.get("reference_comment"),
            warnings=list(r.get("warnings") or []),
        )
        for i, r in enumerate(data.get("rows") or [])
    ]

    table = CCCBDBFormResultTable(
        target_kind=target_kind,
        title=data.get("title"),
        column_names=list(data.get("column_names") or []),
        raw_units=data.get("raw_units"),
        rows=rows,
        source_url=data.get("source_url"),
        final_url=data.get("final_url"),
        content_sha256=data.get("content_sha256"),
        warnings=list(data.get("warnings") or []),
    )

    metadata = data.get("source_metadata") or {}
    selection_metadata = data.get("selection")
    resolver_strategy = (
        metadata.get("resolver_strategy")
        if isinstance(metadata, dict) else None
    )
    species_key = (
        metadata.get("species_key")
        if isinstance(metadata, dict) else None
    )

    return FormParsedFile(
        path=path,
        target_kind=target_kind,
        table=table,
        selection_metadata=selection_metadata,
        resolver_strategy=resolver_strategy,
        species_key=species_key,
    )


__all__ = [
    "CCCBDBFormPayloadBuildResult",
    "FormParsedFile",
    "build_atomization_energy_payloads_from_form_result",
    "load_parsed_form_result",
]
