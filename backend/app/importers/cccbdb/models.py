"""Pydantic v2 intermediate models for the CCCBDB importer.

These are **importer-local** records, not TCKDB upload schemas. Phase 2
builder code will translate them into workflow-layer upload payloads.

Design notes:

* ``property_kind`` and unit fields use machine-friendly tokens rather
  than CCCBDB's display labels (e.g. ``hf_298`` instead of
  ``"Hf(298.15 K)"``) so the importer payload stays stable across
  CCCBDB releases that may re-format column headers.
* Every parsed value preserves both the normalized form and the raw
  string it came from. Lossy normalization is reversible by reading
  ``raw_value`` / ``raw_units`` / ``raw_reference_text``.
* Value-level references (``Gurvich``, ``TRC``, ``webbook`` ...) are
  preserved verbatim. We do not attempt to resolve them to TCKDB
  ``literature`` rows here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class CCCBDBValueRef(BaseModel):
    """Row-level reference/citation captured from a CCCBDB value cell.

    Fields are independently optional: CCCBDB sometimes shows only a
    short label (``Gurvich``), sometimes a longer comment, and
    sometimes both. The raw text is always preserved if anything was
    parsed.
    """

    model_config = ConfigDict(extra="forbid")

    reference_label: str | None = None
    reference_comment: str | None = None
    raw_reference_text: str | None = None
    parsed_literature_hint: str | None = None


class CCCBDBSourceMetadata(BaseModel):
    """Database-level + fetch-level provenance for one parsed page."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["CCCBDB"] = "CCCBDB"
    source_release: str = "22"
    source_database_doi: str = "10.18434/T47C7Z"
    source_url: str
    source_record_key: str | None = None
    page_kind: Literal["experimental_species"] = "experimental_species"
    retrieved_at: datetime | None = None
    content_sha256: str
    parser_version: str


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class CCCBDBSpeciesIdentity(BaseModel):
    """Species identity fields extracted from a CCCBDB experimental page.

    ``charge`` and ``multiplicity`` are populated only when CCCBDB
    states them explicitly. ``state_label`` preserves the raw
    electronic-state / conformation string (e.g. ``"X 1A1"``,
    ``"X 2Pi"``) so a downstream curator can infer multiplicity if it
    is not otherwise available.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    other_names: list[str] = Field(default_factory=list)
    formula: str | None = None
    cas: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    smiles: str | None = None
    charge: int | None = None
    multiplicity: int | None = None
    state_label: str | None = None


# ---------------------------------------------------------------------------
# Thermo
# ---------------------------------------------------------------------------


class CCCBDBThermoValue(BaseModel):
    """One experimental thermochemistry datum.

    ``property_kind`` is a machine token (``hf_298``, ``hf_0``,
    ``s_298``, ``cp_298``, ``h_298_minus_h_0``). Display strings live
    in ``raw_property``.
    """

    model_config = ConfigDict(extra="forbid")

    property_kind: str
    raw_property: str
    value: float
    canonical_units: str
    raw_value: str
    raw_units: str
    temperature_k: float | None = None
    uncertainty: float | None = None
    reference: CCCBDBValueRef | None = None


class CCCBDBThermoRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: list[CCCBDBThermoValue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Statmech (point group, frequencies, rotational constants)
# ---------------------------------------------------------------------------


class CCCBDBFrequencyMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode_index: int | None = None
    frequency_cm1: float
    symmetry_label: str | None = None
    raw_value: str
    raw_units: str
    reference: CCCBDBValueRef | None = None


class CCCBDBRotationalConstants(BaseModel):
    """Experimental rotational constants normalized to GHz.

    Linear molecules report only ``b_ghz``; symmetric tops report
    ``a_ghz`` and ``b_ghz`` (with ``c_ghz`` equal to ``b_ghz`` or
    ``a_ghz`` depending on prolate/oblate); asymmetric tops report all
    three. We do not enforce that here — fields are independently
    optional.
    """

    model_config = ConfigDict(extra="forbid")

    a_ghz: float | None = None
    b_ghz: float | None = None
    c_ghz: float | None = None
    raw_values: list[str] = Field(default_factory=list)
    raw_units: str | None = None
    reference: CCCBDBValueRef | None = None


class CCCBDBStatmechRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point_group: str | None = None
    symmetry_number: int | None = None
    frequencies: list[CCCBDBFrequencyMode] = Field(default_factory=list)
    rotational_constants: CCCBDBRotationalConstants | None = None
    zpe_kj_mol: float | None = None
    reference: CCCBDBValueRef | None = None


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


class CCCBDBGeometryAtom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element: str
    x_angstrom: float
    y_angstrom: float
    z_angstrom: float


class CCCBDBGeometryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atoms: list[CCCBDBGeometryAtom] = Field(default_factory=list)
    raw_units: str | None = None
    reference: CCCBDBValueRef | None = None


# ---------------------------------------------------------------------------
# Top-level page record
# ---------------------------------------------------------------------------


class CCCBDBExperimentalSpeciesRecord(BaseModel):
    """Result of parsing one CCCBDB experimental species page.

    ``raw_sections`` captures section-keyed dumps of unparsed-but-seen
    HTML structure for debugging and round-trip reproducibility.
    ``warnings`` accumulates non-fatal parser issues (missing optional
    columns, unrecognized property labels, unparseable cells).
    """

    model_config = ConfigDict(extra="forbid")

    identity: CCCBDBSpeciesIdentity
    thermo: CCCBDBThermoRecord = Field(default_factory=CCCBDBThermoRecord)
    statmech: CCCBDBStatmechRecord = Field(default_factory=CCCBDBStatmechRecord)
    geometry: CCCBDBGeometryRecord | None = None
    source_metadata: CCCBDBSourceMetadata
    raw_sections: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Cross-species experimental property tables (xp1x.asp-family pages)
# ---------------------------------------------------------------------------


class CCCBDBPropertyTableSourceMetadata(BaseModel):
    """Provenance for one CCCBDB cross-species property-table page.

    Parallel to :class:`CCCBDBSourceMetadata` but with
    ``page_kind="experimental_property_table"``. ``property_kind`` is
    a machine token (``hf_0``, ``dipole``, ``diatomic_spectroscopic``,
    …) so a downstream consumer can dispatch on it without parsing
    the raw page title.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["CCCBDB"] = "CCCBDB"
    source_release: str = "22"
    source_database_doi: str = "10.18434/T47C7Z"
    source_url: str
    source_record_key: str | None = None
    page_kind: Literal["experimental_property_table"] = (
        "experimental_property_table"
    )
    property_kind: str
    retrieved_at: datetime | None = None
    content_sha256: str
    parser_version: str


class CCCBDBExperimentalPropertyRow(BaseModel):
    """One row from a CCCBDB cross-species property table.

    Identifier fields are independently optional: CCCBDB rows vary in
    which identifier columns they carry (some have Species formula
    only, some add Name, etc.). Both raw and normalized value/unit
    pairs are preserved so a downstream consumer can confirm
    conversion or fall back to the raw text.
    """

    model_config = ConfigDict(extra="forbid")

    row_index: int
    name: str | None = None
    formula: str | None = None
    cas_number: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    smiles: str | None = None
    state_label_raw: str | None = None

    value: float | None = None
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    uncertainty: float | None = None
    normalized_uncertainty: float | None = None

    reference: CCCBDBValueRef | None = None
    raw_row: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class CCCBDBExperimentalPropertyTable(BaseModel):
    """Result of parsing one CCCBDB cross-species property-table page."""

    model_config = ConfigDict(extra="forbid")

    property_kind: str
    title: str | None = None
    raw_units: str | None = None
    canonical_unit: str | None = None
    column_names: list[str] = Field(default_factory=list)
    rows: list[CCCBDBExperimentalPropertyRow] = Field(default_factory=list)
    source_metadata: CCCBDBPropertyTableSourceMetadata
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Molecule catalog (inchix.asp) — IDENTITY UNIVERSE ONLY
# ---------------------------------------------------------------------------
#
# CRITICAL: The INChI index page is **catalog-only**. Its hyperlinks
# are preserved as raw audit metadata but MUST NOT be trusted as
# data-page URLs:
#
#   * Phase 2b confirmed that `exp1x.asp?casno=...` URL patterns do
#     not resolve.
#   * The actual data path is the cross-species property-table family
#     (Phase 3a, ``xp1x.asp``-style flat tables).
#   * A future search/form resolver may eventually translate a catalog
#     entry into a real data URL via CCCBDB's POST flow — but that
#     resolver does not exist yet.
#
# Therefore ``trusted_property_url`` / ``trusted_species_url`` on
# ``CCCBDBCatalogEntry`` are deliberately typed ``None`` and the
# Phase 3b parser never populates them. Future resolver work will set
# them; until then, ``raw_href`` is debug data only.


class CCCBDBCatalogEntry(BaseModel):
    """One row from CCCBDB's molecule catalog (``inchix.asp``).

    Identifier fields are independently optional. The page may carry
    several formulas with the same name, several names per formula
    (isomers!), or rows with no identifiers at all. The parser
    populates whatever it sees and warns rather than discarding.

    ``raw_href`` is preserved verbatim for audit/debugging but is NOT
    trusted as a data-page URL. ``trusted_property_url`` and
    ``trusted_species_url`` are reserved for a future resolver and
    are always ``None`` in Phase 3b.
    """

    model_config = ConfigDict(extra="forbid")

    catalog_index: int
    formula: str | None = None
    name: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    smiles: str | None = None
    cas_number: str | None = None
    raw_text: str | None = None
    raw_href: str | None = None
    other_names: list[str] = Field(default_factory=list)
    """
    Catalog-supplied synonyms for this species (CCCBDB's
    "other names" column on ``inchix.asp``). Semicolon-separated on
    the live page; the parser splits, trims, and dedupes. Audit-only
    enrichment — the catalog never invents identity from these.
    """
    trusted_property_url: None = None
    trusted_species_url: None = None
    warnings: list[str] = Field(default_factory=list)


class CCCBDBCatalogSourceMetadata(BaseModel):
    """Provenance for a parsed ``inchix.asp`` snapshot."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["CCCBDB"] = "CCCBDB"
    source_release: str = "22"
    source_database_doi: str = "10.18434/T47C7Z"
    source_url: str
    source_record_key: str | None = None
    page_kind: Literal["molecule_catalog_inchi_index"] = (
        "molecule_catalog_inchi_index"
    )
    retrieved_at: datetime | None = None
    content_sha256: str
    parser_version: str


class CCCBDBMoleculeCatalog(BaseModel):
    """Result of parsing one CCCBDB ``inchix.asp`` snapshot."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    column_names: list[str] = Field(default_factory=list)
    entries: list[CCCBDBCatalogEntry] = Field(default_factory=list)
    source_metadata: CCCBDBCatalogSourceMetadata
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Catalog-based identity enrichment for property-table rows
# ---------------------------------------------------------------------------
#
# Enrichment is a *candidate-proposal* layer, not identity resolution.
# A property-table row may match zero, one, or many catalog entries;
# the helper returns all of them with a confidence score and the
# reasons that produced the score, never silently picking one.


class CCCBDBCatalogMatchConfidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


# ---------------------------------------------------------------------------
# Per-species "all data" pages (Phase 5a)
# ---------------------------------------------------------------------------


class CCCBDBSpeciesAllDataSourceMetadata(BaseModel):
    """Provenance for a parsed ``alldata2x.asp?casno=...`` snapshot."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["CCCBDB"] = "CCCBDB"
    source_release: str = "22"
    source_database_doi: str = "10.18434/T47C7Z"
    source_url: str
    source_record_key: str | None = None
    page_kind: Literal["species_all_data"] = "species_all_data"
    cas_number: str | None = None
    retrieved_at: datetime | None = None
    content_sha256: str
    parser_version: str


class CCCBDBSpeciesAllDataRecord(BaseModel):
    """Minimal parsed view of one CCCBDB per-species page.

    Phase 5a deliberately ships a *small* parser: enough to make the
    archive useful for triage (title, section headings, any
    identifiers we can pluck from the body via regex) but not a full
    property extractor. The raw HTML is the durable artifact; richer
    parsing is a later phase.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    detected_name: str | None = None
    detected_formula: str | None = None
    detected_inchi: str | None = None
    detected_inchikey: str | None = None
    detected_smiles: str | None = None
    section_headings: list[str] = Field(default_factory=list)
    source_metadata: CCCBDBSpeciesAllDataSourceMetadata
    warnings: list[str] = Field(default_factory=list)


class CCCBDBCatalogMatch(BaseModel):
    """One scored candidate identity-enrichment for a property row.

    ``is_unambiguous`` is ``True`` only when this is the single
    candidate returned for its source row AND the score is at least
    medium. Formula-only ties are explicitly ambiguous regardless of
    score so isomers do not get silently merged (C2H6O could be
    ethanol or dimethyl ether; C3H6 could be propene or cyclopropane;
    C4H10 could be n-butane or isobutane).
    """

    model_config = ConfigDict(extra="forbid")

    catalog_entry: CCCBDBCatalogEntry
    score: CCCBDBCatalogMatchConfidence
    match_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_unambiguous: bool = False
