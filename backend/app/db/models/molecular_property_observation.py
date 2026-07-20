"""``molecular_property_observation`` model.

Home for scalar/vector/tensor molecular properties that don't belong
on :class:`Thermo`, :class:`Statmech`, or :class:`Transport`. Closes
CCCBDB Schema Gap 1 (see ``backend/docs/specs/cccbdb_importer.md`` §7).

Design notes
------------

* ``species_entry_id`` is **nullable**. CCCBDB property tables ship
  raw rows where identity is at most ``formula`` + ``name`` — the
  catalog enrichment helper (:func:`app.importers.cccbdb.enrichment.
  propose_catalog_matches`) is often *ambiguous* (isomers). Forcing
  a non-null FK would push the importer into fabricating species
  entries, which would be a worse outcome than carrying an
  identity-unresolved observation with its CCCBDB provenance intact.
  Once an unambiguous match becomes available (manual curation or a
  future resolver), a row's ``species_entry_id`` can be populated
  via an UPDATE.

* Scalars get first-class columns. Vectors and tensors live in
  JSONB for now: in practice CCCBDB ships dipole vectors as
  ``[x, y, z]`` floats and polarizability/quadrupole tensors as 3×3
  matrices; both round-trip through JSONB without losing structure
  or unit metadata. A future migration may promote either to typed
  columns if query needs grow.

* CCCBDB-style external-source provenance lives in dedicated columns
  (``external_source_*``) rather than a side-table. That keeps the
  observation self-describing for replay from a wiped archive.

* The dedupe unique-constraint uses
  ``postgresql_nulls_not_distinct=True`` so unresolved rows still
  dedupe by content + reference + source — without it, every
  ``species_entry_id IS NULL`` row would be treated as distinct.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import MolecularPropertyKind, ScientificOriginKind

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.literature import Literature
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import SpeciesEntry
    from app.db.models.workflow import WorkflowToolRelease


class MolecularPropertyObservation(Base, TimestampMixin, CreatedByMixin):
    """One molecular-property observation with full external provenance.

    See module docstring for the rationale behind nullable
    ``species_entry_id`` and the JSONB vector/tensor fields.
    """

    __tablename__ = "molecular_property_observation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # ---- Identity (nullable: see module docstring) -------------------
    species_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "species_entry.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_molecular_property_observation_species_entry_id",
        ),
        nullable=True,
    )

    # ---- Classification ---------------------------------------------
    scientific_origin: Mapped[ScientificOriginKind] = mapped_column(
        SAEnum(ScientificOriginKind, name="scientific_origin_kind"),
        nullable=False,
    )
    property_kind: Mapped[MolecularPropertyKind] = mapped_column(
        SAEnum(MolecularPropertyKind, name="molecular_property_kind"),
        nullable=False,
    )
    # Free-text refinement of property_kind. Required when
    # property_kind=other so consumers can see what was actually
    # measured (e.g. "Hf(0 K) - Hf(298 K)").
    property_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- Value (at least one representation must be populated) ------
    scalar_value: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    scalar_unit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scalar_uncertainty: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    vector_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    tensor_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # ---- Conditions --------------------------------------------------
    temperature_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    wavelength_nm: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    method_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    state_label_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- TCKDB-internal provenance (optional) ------------------------
    literature_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    software_release_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "software_release.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_molecular_property_observation_software_release_id",
        ),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "workflow_tool_release.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_molecular_property_observation_workflow_tool_release_id",
        ),
        nullable=True,
    )
    source_calculation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "calculation.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_molecular_property_observation_source_calculation_id",
        ),
        nullable=True,
    )

    # ---- External-source provenance (CCCBDB, NIST WebBook, ...) -----
    external_source_name: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    external_source_release: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    external_source_doi: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    external_source_url: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    external_source_record_key: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    external_source_page_kind: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    external_source_content_sha256: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    external_source_parser_version: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    # ---- Row-level reference (CCCBDB row "squib", e.g. "Gurvich") ---
    reference_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reference_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_reference_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- Raw payload for forensic / round-trip uses -----------------
    raw_payload_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )

    # ---- Free-text note ---------------------------------------------
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- Relationships ----------------------------------------------
    species_entry: Mapped[Optional["SpeciesEntry"]] = relationship(
        foreign_keys=[species_entry_id]
    )
    literature: Mapped[Optional["Literature"]] = relationship()
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship()
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship()
    source_calculation: Mapped[Optional["Calculation"]] = relationship()

    __table_args__ = (
        CheckConstraint(
            "scalar_value IS NOT NULL "
            "OR vector_json IS NOT NULL "
            "OR tensor_json IS NOT NULL",
            name="mpo_value_at_least_one",
        ),
        CheckConstraint(
            "scalar_value IS NULL OR scalar_unit IS NOT NULL",
            name="mpo_scalar_value_has_unit",
        ),
        CheckConstraint(
            "scalar_uncertainty IS NULL OR scalar_uncertainty >= 0",
            name="mpo_scalar_uncertainty_ge_0",
        ),
        CheckConstraint(
            "temperature_k IS NULL OR temperature_k > 0",
            name="mpo_temperature_k_gt_0",
        ),
        CheckConstraint(
            "wavelength_nm IS NULL OR wavelength_nm > 0",
            name="mpo_wavelength_nm_gt_0",
        ),
        # Dedupe: same scalar from same external row + source + reference
        # collapses to one observation. NULL-not-distinct so unresolved
        # rows still dedupe rather than multiplying every re-run.
        UniqueConstraint(
            "species_entry_id",
            "property_kind",
            "scientific_origin",
            "external_source_name",
            "external_source_release",
            "external_source_url",
            "external_source_record_key",
            "reference_label",
            "scalar_value",
            "temperature_k",
            name="mpo_dedupe_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_mpo_property_kind_origin",
            "property_kind",
            "scientific_origin",
        ),
        Index(
            "ix_mpo_species_entry_id",
            "species_entry_id",
        ),
        Index(
            "ix_mpo_external_source_release",
            "external_source_name",
            "external_source_release",
        ),
        Index(
            "ix_mpo_external_source_content_sha256",
            "external_source_content_sha256",
        ),
    )
