"""Declared statistical models: what a statmech record actually represents (statmech selection).

Three tables, children of ``statmech``:

``statmech_model_declaration``
    At most one per statmech record. It says which model the record supplies: the scope (an atom, one conformer
    basin, an explicit equilibrium ensemble, or a transition-state determination), who stands behind the claim, and a
    validated typed ``recipe`` (``tckdb_schemas.statmech_declarations``) stored as made. It is *reviewed scientific
    content of its statmech record*, not a separately approved annotation: the accepted-science triggers freeze it, its
    members and its sources with the statmech row, so adding or changing a declaration on an accepted record means a
    new, superseding statmech record. A statmech row with no declaration reads as exactly what it always was; nothing is
    inferred, and nothing is backfilled.

``statmech_model_member``
    One member of an explicit ensemble: an existing statmech record of the same species entry (its own statistical
    interpretation), optionally assigned to a conformer basin, with an attributed counting multiplicity and a relative
    energy in kJ/mol that states its convention (electronic only, zero-point corrected, ...). A record can be a member once per declaration, so a repeated observation is evidence and never
    another state. ``counting_multiplicity`` NULL means "not stated", never one.

``statmech_model_source``
    What one part of the model rests on, by role: a calculation (optionally the one geometry its result describes),
    imported literature parameters, or a frequency scale factor. Imported parameters need no fabricated calculation.

The owner rules (a basin of the same species entry, a determination of the same transition-state entry, a source
calculation of the same subject, a member of the same species entry) are database facts, enforced by triggers in
revision ``e6b3a9d1f742``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
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
from app.db.models.common import EnergyCorrectionConvention, StatmechModelScopeKind, StatmechModelSourceRole

if TYPE_CHECKING:
    from app.db.models.statmech import Statmech


class StatmechModelDeclaration(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """The model one statmech record supplies. Frozen with its statmech record once that is accepted."""

    __tablename__ = "statmech_model_declaration"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    statmech_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("statmech.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
        unique=True,
    )
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    scope_kind: Mapped[StatmechModelScopeKind] = mapped_column(
        SAEnum(StatmechModelScopeKind, name="statmech_model_scope_kind"), nullable=False
    )
    #: The basin, for ``basin`` scope. Always a group of the statmech record's own species entry.
    conformer_group_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_group.id",
            name="fk_statmech_model_declaration_conformer_group",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )
    #: The saddle determination, for ``ts_determination`` scope. Always a saddle of the record's own TS entry.
    structure_determination_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "structure_determination.id",
            name="fk_statmech_model_declaration_determination",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
        index=True,
    )
    #: Who stands behind the declaration (separate from the statmech row's own attribution).
    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", name="fk_statmech_model_declaration_literature", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "workflow_tool_release.id",
            name="fk_statmech_model_declaration_workflow_tool_release",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    #: The recipe as made (``ModelRecipe``, ``None`` fields left out). ``none_as_null``: Python ``None`` is SQL NULL.
    recipe: Mapped[dict] = mapped_column(JSONB(none_as_null=True), nullable=False)
    #: sha256 over the canonical declaration, its members and its sources by public ref: a checksum, recomputable.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    statmech: Mapped["Statmech"] = relationship()
    members: Mapped[list["StatmechModelMember"]] = relationship(
        back_populates="declaration", order_by="StatmechModelMember.id"
    )
    sources: Mapped[list["StatmechModelSource"]] = relationship(
        back_populates="declaration", order_by="StatmechModelSource.id"
    )

    __table_args__ = (
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(
            "(scope_kind = 'basin') = (conformer_group_id IS NOT NULL) "
            "AND (scope_kind = 'ts_determination') = (structure_determination_id IS NOT NULL)",
            name="scope_matches_pointers",
        ),
        CheckConstraint("literature_id IS NOT NULL OR workflow_tool_release_id IS NOT NULL", name="attribution_required"),
        CheckConstraint(
            "jsonb_typeof(recipe) = 'object'",
            name="recipe_object",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256_hex"),
    )


class StatmechModelMember(Base):
    """One member of an explicit equilibrium ensemble."""

    __tablename__ = "statmech_model_member"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "statmech_model_declaration.id",
            name="fk_statmech_model_member_declaration",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
        index=True,
    )
    member_key: Mapped[str] = mapped_column(Text, nullable=False)
    member_statmech_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "statmech.id",
            name="fk_statmech_model_member_statmech",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=False,
        index=True,
    )
    conformer_group_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "conformer_group.id",
            name="fk_statmech_model_member_conformer_group",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    #: The depositor's attributed multiplicity. NULL: not stated (never one).
    counting_multiplicity: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    relative_energy_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    #: What is already folded into ``relative_energy_kj_mol`` (the vocabulary network state energies use). Present if and
    #: only if the energy is; never assumed. ``other`` is not allowed here: a member carries no note to explain it.
    relative_energy_convention: Mapped[Optional[EnergyCorrectionConvention]] = mapped_column(
        SAEnum(EnergyCorrectionConvention, name="energy_correction_convention", create_type=False), nullable=True
    )
    region_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    declaration: Mapped["StatmechModelDeclaration"] = relationship(back_populates="members")
    member_statmech: Mapped["Statmech"] = relationship(foreign_keys=[member_statmech_id])

    __table_args__ = (
        CheckConstraint(
            "length(btrim(member_key)) > 0 AND length(member_key) <= 64", name="member_key_bounded"
        ),
        CheckConstraint(
            "counting_multiplicity IS NULL OR counting_multiplicity >= 1", name="counting_multiplicity_ge_1"
        ),
        CheckConstraint(
            "relative_energy_kj_mol IS NULL OR "
            "(relative_energy_kj_mol <> 'NaN' AND abs(relative_energy_kj_mol) < 'Infinity')",
            name="relative_energy_finite",
        ),
        CheckConstraint(
            "(relative_energy_kj_mol IS NULL) = (relative_energy_convention IS NULL)",
            name="relative_energy_convention_with_energy",
        ),
        CheckConstraint(
            "relative_energy_convention IS DISTINCT FROM 'other'",
            name="relative_energy_convention_not_other",
        ),
        # A key names one member and a statmech record is a member once. Two members may sit in one basin: nothing here
        # asserts one observation per basin as a chemical uniqueness rule.
        UniqueConstraint("declaration_id", "member_key", name="uq_statmech_model_member_key"),
        UniqueConstraint("declaration_id", "member_statmech_id", name="uq_statmech_model_member_statmech"),
    )


class StatmechModelSource(Base):
    """What one part of a declared model rests on."""

    __tablename__ = "statmech_model_source"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    declaration_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[StatmechModelSourceRole] = mapped_column(
        SAEnum(StatmechModelSourceRole, name="statmech_model_source_role"), nullable=False
    )
    calculation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "calculation.id", name="fk_statmech_model_source_calculation", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=True,
        index=True,
    )
    #: The one geometry the calculation's result describes for this role (NULL when not stated).
    geometry_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("geometry.id", name="fk_statmech_model_source_geometry", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", name="fk_statmech_model_source_literature", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    frequency_scale_factor_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey(
            "frequency_scale_factor.id",
            name="fk_statmech_model_source_frequency_scale_factor",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        nullable=True,
    )
    #: The ensemble member this source is for; NULL for the model as a whole.
    member_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    declaration: Mapped["StatmechModelDeclaration"] = relationship(back_populates="sources")

    __table_args__ = (
        ForeignKeyConstraint(
            ["declaration_id"],
            ["statmech_model_declaration.id"],
            name="fk_statmech_model_source_declaration",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        ForeignKeyConstraint(
            ["declaration_id", "member_key"],
            ["statmech_model_member.declaration_id", "statmech_model_member.member_key"],
            name="fk_statmech_model_source_member",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        CheckConstraint(
            "length(btrim(source_key)) > 0 AND length(source_key) <= 64", name="source_key_bounded"
        ),
        CheckConstraint(
            "num_nonnulls(calculation_id, literature_id, frequency_scale_factor_id) = 1", name="exactly_one_locator"
        ),
        CheckConstraint(
            "(role = 'scale_factor') = (frequency_scale_factor_id IS NOT NULL)", name="scale_factor_role_matches_locator"
        ),
        CheckConstraint("geometry_id IS NULL OR calculation_id IS NOT NULL", name="geometry_needs_calculation"),
        UniqueConstraint("declaration_id", "source_key", name="uq_statmech_model_source_key"),
    )
