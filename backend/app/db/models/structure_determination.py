"""Structure determinations and scoped evidence findings (API energy ordering).

Three tables, none of which holds a number:

``structure_determination``
    One source-attributed claim about a defined geometry, conformer basin or saddle, and the quantity
    the claim supplies. **Identity, not provenance**: its identity is the owner (a species entry or a transition
    state entry, and for a basin its observation), the source attribution and a source-scoped
    ``determination_key``, unique (``uq_structure_determination_key``). Stating the key again with the same
    content resolves to the existing row, so a repeat is idempotent and never an additional statistical
    member; stating it with different content is refused. ``content_hash`` digests what the determination
    claims (target kind, quantity, energy convention, recipe, evaluated geometry and the pinned calculations).
    A basin is per observation, and each conformer upload creates a new observation, so a basin claim cannot be
    restated across uploads; a geometry or saddle claim can be, over calculations already deposited.
    **Immutable from creation**: a trigger refuses every UPDATE.

``structure_determination_source``
    One calculation pinned to one role of a determination (energy, geometry optimization, curvature,
    correction, connectivity, alternative characterization). One calculation can fill several roles, each its
    own row; alternative bundles are separate determinations, and nothing here manufactures the Cartesian
    combinations of the attachments an owner happens to have.

``structure_evidence_finding``
    An append-only event: a confirmed identity, state or path incompatibility, a role-specific invalidation, a
    contradictory characterization, or an adjudication, pinned to the geometry, calculation or determination it
    is about, with its author and authority. The heuristic geometry-validation rows stay what they were
    (observations); this table is where a confirmed interpretation of them is recorded without rewriting them.

Nothing is inferred and nothing is backfilled: a calculation or record deposited without a determination has
none, and reads as "not stated".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import (
    StructureDeterminationQuantity,
    StructureDeterminationTargetKind,
    StructureFindingAuthority,
    StructureFindingKind,
    StructureFindingScope,
    StructureFindingVerdict,
    StructureSourceRole,
)

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.geometry import Geometry
    from app.db.models.literature import Literature
    from app.db.models.species import ConformerObservation, SpeciesEntry
    from app.db.models.transition_state import TransitionStateEntry
    from app.db.models.workflow import WorkflowToolRelease


class StructureDetermination(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """One source-attributed claim about a geometry, basin or saddle, immutable from creation."""

    __tablename__ = "structure_determination"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    species_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
        index=True,
    )
    transition_state_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "transition_state_entry.id",
            name="fk_structure_determination_ts_entry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )
    conformer_observation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_observation.id",
            name="fk_structure_determination_conformer_observation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )
    target_kind: Mapped[StructureDeterminationTargetKind] = mapped_column(
        SAEnum(StructureDeterminationTargetKind, name="structure_determination_target_kind"),
        nullable=False,
    )
    #: NULL: an evidence-only determination, which supplies no energy.
    quantity: Mapped[Optional[StructureDeterminationQuantity]] = mapped_column(
        SAEnum(StructureDeterminationQuantity, name="structure_determination_quantity"),
        nullable=True,
    )
    evaluated_geometry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
        index=True,
    )
    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "workflow_tool_release.id",
            name="fk_structure_determination_workflow_tool_release",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    determination_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: Stored as made (``tckdb_schemas.structure_declarations``): a supplied E0's zero-point and
    #: correction convention; the composite recipe actually used. ``none_as_null``: Python ``None`` is SQL NULL
    #: ("not stated"), never the JSON value ``null``.
    energy_convention: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    actual_recipe: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True), nullable=True)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    species_entry: Mapped[Optional["SpeciesEntry"]] = relationship()
    transition_state_entry: Mapped[Optional["TransitionStateEntry"]] = relationship()
    conformer_observation: Mapped[Optional["ConformerObservation"]] = relationship()
    evaluated_geometry: Mapped["Geometry"] = relationship()
    literature: Mapped[Optional["Literature"]] = relationship()
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship()
    sources: Mapped[list["StructureDeterminationSource"]] = relationship(
        back_populates="determination",
        foreign_keys="StructureDeterminationSource.determination_id",
        order_by="StructureDeterminationSource.id",
    )

    __table_args__ = (
        CheckConstraint("num_nonnulls(species_entry_id, transition_state_entry_id) = 1", name="one_owner"),
        # A basin belongs to a species entry and names its observation; a saddle belongs to a transition state
        # entry and names no observation; a bare geometry names none.
        CheckConstraint(
            "(target_kind = 'conformer_basin' AND species_entry_id IS NOT NULL "
            "AND conformer_observation_id IS NOT NULL) "
            "OR (target_kind = 'saddle_point' AND transition_state_entry_id IS NOT NULL "
            "AND conformer_observation_id IS NULL) "
            "OR (target_kind = 'geometry' AND conformer_observation_id IS NULL)",
            name="target_matches_owner",
        ),
        # coalesce: a NULL quantity (evidence only) must read as "not zero_kelvin_energy", not as NULL, and a
        # CHECK passes on NULL.
        CheckConstraint(
            "coalesce(quantity = 'zero_kelvin_energy', false) = (energy_convention IS NOT NULL)",
            name="convention_iff_zero_kelvin",
        ),
        CheckConstraint(
            "literature_id IS NOT NULL OR workflow_tool_release_id IS NOT NULL", name="source_required"
        ),
        CheckConstraint(
            "length(btrim(determination_key)) > 0 AND length(determination_key) <= 128", name="key_bounded"
        ),
        CheckConstraint("identity_hash ~ '^[0-9a-f]{64}$'", name="identity_hash_sha256_hex"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256_hex"),
        CheckConstraint(
            "energy_convention IS NULL OR (jsonb_typeof(energy_convention) = 'object')",
            name="energy_convention_object",
        ),
        CheckConstraint(
            "actual_recipe IS NULL OR (jsonb_typeof(actual_recipe) = 'object' "
            "AND coalesce(jsonb_typeof(actual_recipe -> 'version'), '') = 'number')",
            name="actual_recipe_versioned_object",
        ),
        # The key is an identifier: one owner, one source and one key name one determination. Stating it again
        # either resolves to that determination (the same content) or is refused (a different one).
        UniqueConstraint(
            "species_entry_id",
            "transition_state_entry_id",
            "conformer_observation_id",
            "literature_id",
            "workflow_tool_release_id",
            "determination_key",
            name="uq_structure_determination_key",
            postgresql_nulls_not_distinct=True,
        ),
        # The targets the child's composite foreign keys point at.
        UniqueConstraint("id", "species_entry_id", name="uq_structure_determination_scope_species"),
        UniqueConstraint("id", "transition_state_entry_id", name="uq_structure_determination_scope_ts"),
        UniqueConstraint("id", "conformer_observation_id", name="uq_structure_determination_scope_observation"),
    )


class StructureDeterminationSource(Base):
    """One calculation pinned to one role of a determination.

    The owner columns repeat the determination's owner so composite foreign keys can make "this
    determination's source is a calculation of the determination's own owner (and, for a basin, of its
    observation)" a database fact: whichever of
    the two owner columns is set, both its composite keys are checked (a NULL column skips its keys; the other
    one applies).
    """

    __tablename__ = "structure_determination_source"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    determination_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    role: Mapped[StructureSourceRole] = mapped_column(
        SAEnum(StructureSourceRole, name="structure_source_role"), nullable=False
    )
    calculation_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    # Plain foreign keys as well as the composite ones below: a release resolves a column to the row its own
    # foreign key names, and a column that sat only inside a composite key would resolve to the determination.
    species_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "species_entry.id",
            name="fk_structure_determination_source_species_entry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    transition_state_entry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "transition_state_entry.id",
            name="fk_structure_determination_source_ts_entry",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    #: The basin's observation, repeated from the determination (NULL for any other target). Two composite keys make
    #: "every source of a basin claim is a calculation anchored to that observation" a database fact.
    conformer_observation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_observation.id",
            name="fk_structure_determination_source_observation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    #: The one geometry this role's result describes, when the calculation's own links name exactly one for
    #: that role; NULL when they name none or several (never a guess).
    geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    determination: Mapped["StructureDetermination"] = relationship(
        back_populates="sources", foreign_keys=[determination_id]
    )
    calculation: Mapped["Calculation"] = relationship(foreign_keys=[calculation_id])
    geometry: Mapped[Optional["Geometry"]] = relationship()

    __table_args__ = (
        CheckConstraint("num_nonnulls(species_entry_id, transition_state_entry_id) = 1", name="one_owner"),
        ForeignKeyConstraint(
            ["determination_id"],
            ["structure_determination.id"],
            name="fk_structure_determination_source_determination",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name="fk_structure_determination_source_calculation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["determination_id", "species_entry_id"],
            ["structure_determination.id", "structure_determination.species_entry_id"],
            name="fk_structure_determination_source_scope_det_species",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["determination_id", "transition_state_entry_id"],
            ["structure_determination.id", "structure_determination.transition_state_entry_id"],
            name="fk_structure_determination_source_scope_det_ts",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["calculation_id", "species_entry_id"],
            ["calculation.id", "calculation.species_entry_id"],
            name="fk_structure_determination_source_scope_calc_species",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["calculation_id", "transition_state_entry_id"],
            ["calculation.id", "calculation.transition_state_entry_id"],
            name="fk_structure_determination_source_scope_calc_ts",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["determination_id", "conformer_observation_id"],
            ["structure_determination.id", "structure_determination.conformer_observation_id"],
            name="fk_structure_determination_source_scope_det_observation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["calculation_id", "conformer_observation_id"],
            ["calculation.id", "calculation.conformer_observation_id"],
            name="fk_structure_determination_source_scope_calc_observation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        UniqueConstraint("determination_id", "role", "calculation_id", name="uq_structure_determination_source_role"),
    )


class StructureEvidenceFinding(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """One appended finding about a geometry, calculation or determination.

    Append-only: only an authorized adjudication names (and so settles) an earlier finding; any other finding stands beside it. The subject column matching
    ``scope`` is set and the others are NULL. ``authority`` keeps a depositor's own assertion distinct from an
    authorized adjudication; a finding's kind and verdict are claims, and a kind this release does not read is
    ignored by the assessor, never promoted to a universal failure.
    """

    __tablename__ = "structure_evidence_finding"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[StructureFindingKind] = mapped_column(
        SAEnum(StructureFindingKind, name="structure_finding_kind"), nullable=False
    )
    scope: Mapped[StructureFindingScope] = mapped_column(
        SAEnum(StructureFindingScope, name="structure_finding_scope"), nullable=False
    )
    subject_geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "geometry.id", name="fk_structure_evidence_finding_subject_geometry", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=True,
        index=True,
    )
    subject_calculation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "calculation.id",
            name="fk_structure_evidence_finding_subject_calculation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )
    subject_determination_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "structure_determination.id",
            name="fk_structure_evidence_finding_subject_determination",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )
    #: The role a ``role_invalidation`` invalidates; NULL for every other kind.
    role: Mapped[Optional[StructureSourceRole]] = mapped_column(
        SAEnum(StructureSourceRole, name="structure_source_role", create_type=False), nullable=True
    )
    verdict: Mapped[StructureFindingVerdict] = mapped_column(
        SAEnum(StructureFindingVerdict, name="structure_finding_verdict"), nullable=False
    )
    authority: Mapped[StructureFindingAuthority] = mapped_column(
        SAEnum(StructureFindingAuthority, name="structure_finding_authority"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    semantic_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: The calculation whose evidence supports the finding, and a paper that does.
    source_calculation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "calculation.id",
            name="fk_structure_evidence_finding_source_calculation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    supersedes_finding_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "structure_evidence_finding.id",
            name="fk_structure_evidence_finding_supersedes",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        CheckConstraint(
            "(scope = 'geometry' AND subject_geometry_id IS NOT NULL "
            "AND subject_calculation_id IS NULL AND subject_determination_id IS NULL) "
            "OR (scope = 'calculation' AND subject_calculation_id IS NOT NULL "
            "AND subject_geometry_id IS NULL AND subject_determination_id IS NULL) "
            "OR (scope = 'determination' AND subject_determination_id IS NOT NULL "
            "AND subject_geometry_id IS NULL AND subject_calculation_id IS NULL)",
            name="subject_matches_scope",
        ),
        CheckConstraint("(kind = 'role_invalidation') = (role IS NOT NULL)", name="role_iff_role_invalidation"),
        # An adjudication settles an earlier finding, and only an authorized adjudication can.
        CheckConstraint(
            "kind <> 'adjudication' OR (supersedes_finding_id IS NOT NULL "
            "AND authority = 'authorized_adjudication')",
            name="adjudication_needs_authority",
        ),
        # Only an adjudication supersedes: a finding that merely names an earlier one must not be able to erase it.
        CheckConstraint("supersedes_finding_id IS NULL OR kind = 'adjudication'", name="only_adjudication_supersedes"),
        CheckConstraint(
            "length(btrim(rationale)) > 0 AND length(rationale) <= 2000", name="rationale_bounded"
        ),
        CheckConstraint("semantic_version >= 1", name="semantic_version_positive"),
    )
