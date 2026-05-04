from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    PrimaryKeyConstraint,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import (
    ArrheniusAUnits,
    KineticsCalculationRole,
    KineticsModelKind,
    KineticsUncertaintyKind,
    ScientificOriginKind,
)

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.literature import Literature
    from app.db.models.reaction import ReactionEntry
    from app.db.models.software import SoftwareRelease
    from app.db.models.workflow import WorkflowToolRelease


class Kinetics(Base, TimestampMixin, CreatedByMixin):
    """Kinetics records attached to a reaction entry."""

    __tablename__ = "kinetics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("reaction_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    scientific_origin: Mapped[ScientificOriginKind] = mapped_column(
        SAEnum(ScientificOriginKind, name="scientific_origin_kind"),
        nullable=False,
    )
    model_kind: Mapped[KineticsModelKind] = mapped_column(
        SAEnum(KineticsModelKind, name="kinetics_model_kind"),
        nullable=False,
        default=KineticsModelKind.modified_arrhenius,
        server_default=KineticsModelKind.modified_arrhenius.value,
    )

    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    software_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    a: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a_units: Mapped[Optional[ArrheniusAUnits]] = mapped_column(
        SAEnum(ArrheniusAUnits, name="arrhenius_a_units"),
        nullable=True,
    )
    n: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    ea_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    a_uncertainty: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    a_uncertainty_kind: Mapped[Optional[KineticsUncertaintyKind]] = mapped_column(
        SAEnum(KineticsUncertaintyKind, name="kinetics_uncertainty_kind"),
        nullable=True,
    )
    n_uncertainty: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    ea_uncertainty_kj_mol: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    tmin_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    tmax_k: Mapped[Optional[float]] = mapped_column(Double, nullable=True)

    degeneracy: Mapped[Optional[float]] = mapped_column(Double, nullable=True)
    tunneling_model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reaction_entry: Mapped["ReactionEntry"] = relationship(
        back_populates="kinetics_records"
    )
    literature: Mapped[Optional["Literature"]] = relationship()
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship(
        back_populates="kinetics_records"
    )
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship(
        back_populates="kinetics_records"
    )
    source_calculations: Mapped[list["KineticsSourceCalculation"]] = relationship(
        back_populates="kinetics",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("tmin_k IS NULL OR tmin_k > 0", name="tmin_k_gt_0"),
        CheckConstraint("tmax_k IS NULL OR tmax_k > 0", name="tmax_k_gt_0"),
        CheckConstraint(
            "tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k",
            name="tmin_le_tmax",
        ),
        CheckConstraint(
            "(a_uncertainty IS NULL) = (a_uncertainty_kind IS NULL)",
            name="a_uncertainty_kind_required_with_value",
        ),
        CheckConstraint(
            "a_uncertainty_kind <> 'multiplicative' OR a_uncertainty >= 1.0",
            name="a_uncertainty_multiplicative_ge_1",
        ),
    )


class KineticsSourceCalculation(Base):
    """Links kinetics records to supporting calculations by role."""

    __tablename__ = "kinetics_source_calculation"

    kinetics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("kinetics.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    calculation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("calculation.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    role: Mapped[KineticsCalculationRole] = mapped_column(
        SAEnum(KineticsCalculationRole, name="kinetics_calc_role"),
        nullable=False,
    )

    kinetics: Mapped["Kinetics"] = relationship(back_populates="source_calculations")
    calculation: Mapped["Calculation"] = relationship()

    __table_args__ = (PrimaryKeyConstraint("kinetics_id", "calculation_id", "role"),)
