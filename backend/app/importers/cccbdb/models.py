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
