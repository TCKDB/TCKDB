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
from sqlalchemy.sql.elements import conv

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    MolecularPropertyKind,
    ObservedStateBasis,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
    ScientificOriginKind,
)

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.external_source import ExternalSourceRecord
    from app.db.models.literature import Literature
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import SpeciesEntry
    from app.db.models.workflow import WorkflowToolRelease


class MolecularPropertyObservation(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """One molecular-property observation with full external provenance.

    See module docstring for the rationale behind nullable
    ``species_entry_id`` and the JSONB vector/tensor fields.

    ``public_ref`` (prefix ``mpo_``, Phase C-E5) was added by migration
    ``d2f4a7c1b8e6`` — an already-deployed table, so the column was added
    and backfilled in place rather than folded into the initial schema.
    See that revision and ``app/services/public_refs.py`` for the
    opaque-ref rationale (two rows with identical scalar bytes from two
    distinct import events must stay separately citable).
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

    # ---- Uncertainty meaning (Phase C-E1) -----------------------------
    # A typed uncertainty *meaning* for ``scalar_uncertainty``, versioned
    # as ``uncertainty.precedence.v1`` (docs/research/tckdb-phase-c-
    # implementation-plan.md C2): the first present of combined expanded,
    # combined standard, expanded, standard. No "unspecified" member
    # (DR-0007) -- an uncertainty without a stated kind is NULL, enforced
    # by ``ck_mpo_uncertainty_kind_iff_value`` below.
    uncertainty_kind: Mapped[Optional[ObservedUncertaintyKind]] = mapped_column(
        SAEnum(ObservedUncertaintyKind, name="observed_uncertainty_kind"),
        nullable=True,
    )
    uncertainty_coverage_factor: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    uncertainty_level_of_confidence_pct: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    uncertainty_assessor: Mapped[Optional[ObservedUncertaintyAssessor]] = mapped_column(
        SAEnum(ObservedUncertaintyAssessor, name="observed_uncertainty_assessor"),
        nullable=True,
    )

    # ---- Conditions --------------------------------------------------
    temperature_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    pressure_bar: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    state_basis: Mapped[Optional[ObservedStateBasis]] = mapped_column(
        SAEnum(ObservedStateBasis, name="observed_state_basis"),
        nullable=True,
    )
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

    # ---- Source custody (Phase C-E1, new rows only) -------------------
    # Points at the exact fetched-document snapshot an importer derived
    # this row from. Existing CCCBDB rows keep their flattened
    # ``external_source_*`` columns above and are NOT migrated to this FK
    # (see backend/docs/specs/cccbdb_importer.md §7 Gap 4).
    external_source_record_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "external_source_record.id",
            deferrable=True,
            initially="IMMEDIATE",
            name="fk_molecular_property_observation_external_source_record_id",
        ),
        nullable=True,
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
    external_source_record: Mapped[Optional["ExternalSourceRecord"]] = relationship(
        back_populates="observations"
    )

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
        # Phase C-E1 CHECKs below are named with ``conv(...)`` rather than a
        # bare string, unlike the pre-existing ``mpo_*`` CHECKs above. A bare
        # name is treated as *input* to ``NAMING_CONVENTION["ck"]`` and gets
        # ``ck_molecular_property_observation_`` glued on -- harmless for the
        # pre-existing short names (they stay under Postgres's 63-char
        # identifier limit and got realigned to the expanded form by
        # ``b7e4d1a9c026``), but several of these names would then exceed 63
        # chars and get silently hash-truncated (measured: two land on
        # "ck_molecular_property_observation_ck_mpo_uncertainty_co_XXXX",
        # distinguished only by an opaque hash). ``conv()`` marks the name as
        # already final, matching exactly what the migration
        # (``0b4a3afabfd3``) creates via ``op.f(...)`` -- the same fix
        # ``execution_environment_manifest`` uses for its FK names, for the
        # same reason.
        CheckConstraint(
            "pressure_bar IS NULL OR pressure_bar > 0",
            name=conv("ck_mpo_pressure_bar_gt_0"),
        ),
        CheckConstraint(
            "uncertainty_coverage_factor IS NULL OR uncertainty_coverage_factor >= 1",
            name=conv("ck_mpo_uncertainty_coverage_factor_ge_1"),
        ),
        CheckConstraint(
            "uncertainty_level_of_confidence_pct IS NULL "
            "OR (uncertainty_level_of_confidence_pct > 0 "
            "AND uncertainty_level_of_confidence_pct <= 100)",
            name=conv("ck_mpo_uncertainty_confidence_pct_range"),
        ),
        # Fixed-unit CHECK for the heat-capacity kind (unit policy: no new
        # free-text unit column for a quantity with a canonical unit).
        CheckConstraint(
            "property_kind <> 'heat_capacity_cp' OR scalar_unit = 'J/mol/K'",
            name=conv("ck_mpo_heat_capacity_cp_unit_j_mol_k"),
        ),
        CheckConstraint(
            "property_kind <> 'heat_capacity_cp' OR temperature_k IS NOT NULL",
            name=conv("ck_mpo_heat_capacity_cp_requires_temperature"),
        ),
        # Scoped to heat_capacity_cp rows only, NOT every row: existing
        # (and future) CCCBDB-imported rows set scalar_uncertainty without
        # any uncertainty_kind (CCCBDB has no per-value uncertainty-kind
        # concept), so a global iff-CHECK would be un-migratable against
        # live data and would reject the current CCCBDB importer's writes.
        # Scoping to the new Cp contract keeps the invariant meaningful
        # exactly where it is new (see
        # tests/db/test_observation_contract_migration.py::
        # test_cccbdb_style_row_with_uncertainty_and_no_kind_survives).
        CheckConstraint(
            "property_kind <> 'heat_capacity_cp' "
            "OR (scalar_uncertainty IS NULL) = (uncertainty_kind IS NULL)",
            name=conv("ck_mpo_uncertainty_kind_iff_value"),
        ),
        CheckConstraint(
            "uncertainty_coverage_factor IS NULL "
            "OR uncertainty_kind IN ('expanded', 'combined_expanded')",
            name=conv("ck_mpo_coverage_factor_only_expanded"),
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
