"""Builder: CCCBDB property-table rows → ``MolecularPropertyObservationCreate``.

Closes the loop from Phase 3a (cross-species property-table parsing)
to Phase 4a (``molecular_property_observation``). Each row of a
:class:`CCCBDBExperimentalPropertyTable` becomes one
:class:`MolecularPropertyObservationCreate` payload.

Identity resolution policy (per Phase 3b prompt + Phase 4a prompt):

* If a catalog is supplied AND the row produces an unambiguous catalog
  match, identity hints (InChI/InChIKey/SMILES) are surfaced in
  ``raw_payload_json["identity_hint"]``. The builder still does NOT
  set ``species_entry_id`` — that's the workflow layer's job, gated
  on whether the catalog identifier resolves to an existing
  ``species_entry`` row.
* If only ambiguous matches exist, the candidate list is preserved in
  ``raw_payload_json["catalog_candidates"]`` with a warning naming
  each isomer.
* Raw CCCBDB hrefs are *never* promoted — see the Phase 3b README and
  the ``CCCBDBCatalogEntry`` docstring.

Property-kind mapping (Phase 3a names → MolecularPropertyKind):

    ``hf_0``                       → ``enthalpy_of_formation``
    ``hf_0_with_uncertainty``      → ``enthalpy_of_formation``
    ``dipole``                     → ``dipole_moment``
    ``diatomic_spectroscopic``     → ``spectroscopic_constant``

Note that ``hf_0`` is enthalpy of formation at 0 K — **not**
atomization energy. Atomization energy is a distinct enum value and
should be assigned only when CCCBDB explicitly reports atomization
energies.

The builder never persists. It returns a list of
:class:`CCCBDBMolecularPropertyBuildResult` whose ``payload`` field
validates against the real
:class:`MolecularPropertyObservationCreate` model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.common import MolecularPropertyKind, ScientificOriginKind
from app.importers.cccbdb.enrichment import propose_catalog_matches
from app.importers.cccbdb.models import (
    CCCBDBCatalogMatch,
    CCCBDBExperimentalPropertyRow,
    CCCBDBExperimentalPropertyTable,
    CCCBDBMoleculeCatalog,
)
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)

# Mapping from Phase 3a property_kind tokens to MolecularPropertyKind +
# an optional property_label override. Unknown kinds fall through to
# MolecularPropertyKind.other with the raw property_kind preserved as
# the label so a downstream maintainer can see what arrived.
_PROPERTY_KIND_MAP: dict[str, MolecularPropertyKind] = {
    "hf_0": MolecularPropertyKind.enthalpy_of_formation,
    "hf_0_with_uncertainty": MolecularPropertyKind.enthalpy_of_formation,
    "dipole": MolecularPropertyKind.dipole_moment,
    "diatomic_spectroscopic": MolecularPropertyKind.spectroscopic_constant,
    "polarizability": MolecularPropertyKind.polarizability,
    "polarizability_iso": MolecularPropertyKind.polarizability_iso,
    "quadrupole": MolecularPropertyKind.quadrupole_moment,
    "ionization_energy": MolecularPropertyKind.ionization_energy,
    "electron_affinity": MolecularPropertyKind.electron_affinity,
    "proton_affinity": MolecularPropertyKind.proton_affinity,
    "atomization_energy": MolecularPropertyKind.atomization_energy,
    "homo_energy": MolecularPropertyKind.homo_energy,
    "lumo_energy": MolecularPropertyKind.lumo_energy,
    "homo_lumo_gap": MolecularPropertyKind.homo_lumo_gap,
    "rotational_constant": MolecularPropertyKind.rotational_constant,
}


class CCCBDBMolecularPropertyBuildResult(BaseModel):
    """One per-row builder outcome.

    ``payload`` is ``None`` when the row could not be promoted to a
    workflow-ready observation (e.g. no scalar value parsed). The
    row's identity / scientific values still flow through the
    ``identity_hint`` and ``warnings`` channels so the next iteration
    has a reproducible signal.
    """

    model_config = ConfigDict(extra="forbid")

    row_index: int
    payload: MolecularPropertyObservationCreate | None
    is_workflow_ready: bool
    catalog_matches: list[CCCBDBCatalogMatch] = Field(default_factory=list)
    identity_hint: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


def build_molecular_property_payloads_from_property_table(
    table: CCCBDBExperimentalPropertyTable,
    *,
    catalog: CCCBDBMoleculeCatalog | None = None,
    scientific_origin: ScientificOriginKind = ScientificOriginKind.experimental,
) -> list[CCCBDBMolecularPropertyBuildResult]:
    """Build payloads for every row of ``table``.

    :param table: A parsed CCCBDB property table.
    :param catalog: Optional molecule catalog for scored identity
        enrichment. When omitted, no identity hints are produced;
        scalar science still flows through unchanged.
    :param scientific_origin: Defaults to ``experimental`` since all
        Phase 3a tables are experimental. Computed CCCBDB pages
        (Phase 5+) will pass ``computed``.
    :returns: One :class:`CCCBDBMolecularPropertyBuildResult` per row,
        in row order. The builder does **not** drop rows that fail
        to produce a payload; failure is reported per-row.
    """

    property_kind, kind_label = _resolve_property_kind(table.property_kind)
    results: list[CCCBDBMolecularPropertyBuildResult] = []

    for row in table.rows:
        results.append(
            _build_row(
                row,
                table=table,
                catalog=catalog,
                scientific_origin=scientific_origin,
                property_kind=property_kind,
                property_label=kind_label,
            )
        )
    return results


def _resolve_property_kind(
    raw_kind: str,
) -> tuple[MolecularPropertyKind, str | None]:
    """Map a Phase 3a ``property_kind`` token onto
    :class:`MolecularPropertyKind`. Unknown kinds fall through to
    ``other`` with the raw token preserved as ``property_label``."""

    mapped = _PROPERTY_KIND_MAP.get(raw_kind)
    if mapped is None:
        return MolecularPropertyKind.other, raw_kind
    # When the mapped kind already encodes the meaning, ``other`` is
    # not in play and the label can stay ``None``.
    return mapped, None


def _build_row(
    row: CCCBDBExperimentalPropertyRow,
    *,
    table: CCCBDBExperimentalPropertyTable,
    catalog: CCCBDBMoleculeCatalog | None,
    scientific_origin: ScientificOriginKind,
    property_kind: MolecularPropertyKind,
    property_label: str | None,
) -> CCCBDBMolecularPropertyBuildResult:
    warnings: list[str] = list(row.warnings)
    catalog_matches: list[CCCBDBCatalogMatch] = []
    identity_hint: dict[str, Any] | None = None

    if catalog is not None:
        catalog_matches = propose_catalog_matches(row, catalog)
        identity_hint = _identity_hint_from_matches(catalog_matches, warnings)

    if row.normalized_value is None and row.value is None:
        warnings.append("no scalar value parsed; payload not built")
        return CCCBDBMolecularPropertyBuildResult(
            row_index=row.row_index,
            payload=None,
            is_workflow_ready=False,
            catalog_matches=catalog_matches,
            identity_hint=identity_hint,
            warnings=warnings,
        )

    scalar_value = (
        row.normalized_value if row.normalized_value is not None else row.value
    )
    scalar_unit = row.normalized_unit or row.unit
    if scalar_unit is None:
        warnings.append("missing unit; payload not built")
        return CCCBDBMolecularPropertyBuildResult(
            row_index=row.row_index,
            payload=None,
            is_workflow_ready=False,
            catalog_matches=catalog_matches,
            identity_hint=identity_hint,
            warnings=warnings,
        )

    scalar_uncertainty = (
        row.normalized_uncertainty
        if row.normalized_uncertainty is not None
        else row.uncertainty
    )

    raw_payload = _build_raw_payload(
        row=row,
        table=table,
        catalog_matches=catalog_matches,
        identity_hint=identity_hint,
    )

    meta = table.source_metadata
    payload = MolecularPropertyObservationCreate(
        species_entry_id=None,
        scientific_origin=scientific_origin,
        property_kind=property_kind,
        property_label=property_label,
        scalar_value=scalar_value,
        scalar_unit=scalar_unit,
        scalar_uncertainty=scalar_uncertainty,
        state_label_raw=row.state_label_raw,
        external_source_name=meta.source,
        external_source_release=meta.source_release,
        external_source_doi=meta.source_database_doi,
        external_source_url=meta.source_url,
        external_source_record_key=meta.source_record_key,
        external_source_page_kind=meta.page_kind,
        external_source_content_sha256=meta.content_sha256,
        external_source_parser_version=meta.parser_version,
        reference_label=(
            row.reference.reference_label if row.reference else None
        ),
        reference_comment=(
            row.reference.reference_comment if row.reference else None
        ),
        raw_reference_text=(
            row.reference.raw_reference_text if row.reference else None
        ),
        raw_payload_json=raw_payload,
    )

    return CCCBDBMolecularPropertyBuildResult(
        row_index=row.row_index,
        payload=payload,
        is_workflow_ready=True,
        catalog_matches=catalog_matches,
        identity_hint=identity_hint,
        warnings=warnings,
    )


def _identity_hint_from_matches(
    matches: list[CCCBDBCatalogMatch],
    warnings: list[str],
) -> dict[str, Any] | None:
    """Return an InChI/InChIKey/SMILES dict iff exactly one match is
    flagged ``is_unambiguous``; otherwise return ``None`` and record
    the ambiguity in ``warnings``."""

    if not matches:
        return None

    unambiguous = [m for m in matches if m.is_unambiguous]
    if len(unambiguous) == 1:
        entry = unambiguous[0].catalog_entry
        return {
            "source": "cccbdb_catalog",
            "score": unambiguous[0].score.value,
            "match_reasons": list(unambiguous[0].match_reasons),
            "formula": entry.formula,
            "name": entry.name,
            "inchi": entry.inchi,
            "inchikey": entry.inchikey,
            "smiles": entry.smiles,
            "cas_number": entry.cas_number,
        }

    # Ambiguous: emit a warning naming every candidate. We never
    # promote any of them to an identity hint.
    candidate_summaries = [
        {
            "formula": m.catalog_entry.formula,
            "name": m.catalog_entry.name,
            "score": m.score.value,
            "match_reasons": list(m.match_reasons),
        }
        for m in matches
    ]
    warnings.append(
        "catalog identity ambiguous: "
        f"{len(candidate_summaries)} candidate(s); "
        f"isomers={[c['name'] for c in candidate_summaries]}"
    )
    return None


def _build_raw_payload(
    *,
    row: CCCBDBExperimentalPropertyRow,
    table: CCCBDBExperimentalPropertyTable,
    catalog_matches: list[CCCBDBCatalogMatch],
    identity_hint: dict[str, Any] | None,
) -> dict[str, Any]:
    """Forensic JSONB payload: enough to regenerate the observation
    later if the parser/builder changes its mind about a column."""

    payload: dict[str, Any] = {
        "property_kind": table.property_kind,
        "row_index": row.row_index,
        "row_formula": row.formula,
        "row_name": row.name,
        "row_state_label_raw": row.state_label_raw,
        "raw_row": dict(row.raw_row),
        "raw_value": row.value,
        "raw_unit": row.unit,
        "raw_uncertainty": row.uncertainty,
        "normalized_value": row.normalized_value,
        "normalized_unit": row.normalized_unit,
        "normalized_uncertainty": row.normalized_uncertainty,
        "row_warnings": list(row.warnings),
    }
    if identity_hint is not None:
        payload["identity_hint"] = identity_hint
    if catalog_matches:
        payload["catalog_candidates"] = [
            {
                "score": m.score.value,
                "is_unambiguous": m.is_unambiguous,
                "match_reasons": list(m.match_reasons),
                "warnings": list(m.warnings),
                "catalog_entry": {
                    "catalog_index": m.catalog_entry.catalog_index,
                    "formula": m.catalog_entry.formula,
                    "name": m.catalog_entry.name,
                    "inchikey": m.catalog_entry.inchikey,
                },
            }
            for m in catalog_matches
        ]
    return payload
