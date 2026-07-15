"""Group-additivity (Benson) estimation provenance.

Two-layer design mirroring ``energy_correction`` (DR-0003), applied to
``scientific_origin=estimated`` thermochemistry:

* **Reference layer** — :class:`GroupAdditivityScheme` is a reusable,
  literature-sourced description of a group-additivity library / estimator
  (e.g. an RMG group database at a given commit). It is deduped and shared.
* **Application layer** — :class:`AppliedGroupAdditivity` reifies *one*
  estimation: it links a scheme to the ``thermo`` record it produced, and
  :class:`AppliedGroupAdditivityComponent` records the per-Benson-group
  breakdown (which group, how many, and each one's contribution to
  H298 / S298 / Cp298).

See DR-0035 for the full rationale and the thermo-link choice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    Integer,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, PublicRefMixin, TimestampMixin
from app.db.models.common import GroupAdditivityComponentKind

if TYPE_CHECKING:
    from app.db.models.literature import Literature
    from app.db.models.thermo import Thermo


# ---------------------------------------------------------------------------
# Reference layer
# ---------------------------------------------------------------------------


class GroupAdditivityScheme(Base, TimestampMixin, CreatedByMixin, PublicRefMixin):
    """Reusable description of a group-additivity library / estimator.

    Deduped on ``(name, version)``. Examples: ``"RMG-database GAV"`` at a
    given database commit, or a published Benson group-value table. The
    scheme is provenance/reference data — it says *which* estimator was
    used, not the result of any one estimation.
    """

    __tablename__ = "group_additivity_scheme"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    # Commit / revision of the estimator code or group database the values
    # were drawn from (e.g. an RMG-database git sha). Free text; provenance
    # only, never a trust signal.
    code_commit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    source_literature: Mapped[Optional["Literature"]] = relationship()

    applied: Mapped[list["AppliedGroupAdditivity"]] = relationship(
        back_populates="scheme",
    )

    __table_args__ = (
        Index(
            "uq_group_additivity_scheme_name_version",
            "name",
            "version",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


# ---------------------------------------------------------------------------
# Application layer
# ---------------------------------------------------------------------------


class AppliedGroupAdditivity(Base, TimestampMixin, CreatedByMixin):
    """One group-additivity estimation attached to a ``thermo`` record.

    Mirrors ``applied_energy_correction``: the applied row holds the FK to
    its target (here the ``thermo`` result) rather than the target holding a
    FK back. ``thermo_id`` is ``NOT NULL`` and ``UNIQUE`` — an applied-GA row
    is always created attached to a persisted thermo record (the workflow has
    the thermo id in hand before creating it), and an estimated thermo record
    has at most one GA breakdown. This is a brand-new, not-yet-deployed table,
    so the ``NOT NULL`` tightening is legal in-revision per the migration
    rules; the additive/nullable rule governs FKs added to *deployed* tables.
    """

    __tablename__ = "applied_group_additivity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    thermo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("thermo.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
        unique=True,
    )
    scheme_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "group_additivity_scheme.id", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=False,
    )

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    thermo: Mapped["Thermo"] = relationship(
        back_populates="applied_group_additivity",
    )
    scheme: Mapped["GroupAdditivityScheme"] = relationship(back_populates="applied")
    components: Mapped[list["AppliedGroupAdditivityComponent"]] = relationship(
        back_populates="applied_group_additivity",
        cascade="all, delete-orphan",
    )


class AppliedGroupAdditivityComponent(Base):
    """Per-Benson-group contribution within one applied GA estimation.

    Records e.g. "2 x C/C/H3 group, contributing -20.6 kJ/mol to H298".
    Contributions are fixed-unit columns (unit policy): kJ/mol for the
    enthalpy contribution, J/(mol*K) for entropy and heat capacity. Only the
    298 K heat-capacity contribution is stored; the full Cp(T) curve lives on
    the parent ``thermo`` record's NASA / tabulated-points model.
    """

    __tablename__ = "applied_group_additivity_component"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    applied_group_additivity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "applied_group_additivity.id", deferrable=True, initially="IMMEDIATE"
        ),
        nullable=False,
    )
    component_kind: Mapped[GroupAdditivityComponentKind] = mapped_column(
        SAEnum(GroupAdditivityComponentKind, name="group_additivity_component_kind"),
        nullable=False,
    )
    # Benson group descriptor / correction label, e.g. "C/C/H3", "gauche",
    # "6-membered ring". Free text keyed to the scheme's vocabulary.
    group_label: Mapped[str] = mapped_column(Text, nullable=False)
    count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    h298_contribution_kj_mol: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    s298_contribution_j_mol_k: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )
    cp298_contribution_j_mol_k: Mapped[Optional[float]] = mapped_column(
        Double, nullable=True
    )

    applied_group_additivity: Mapped["AppliedGroupAdditivity"] = relationship(
        back_populates="components"
    )

    __table_args__ = (CheckConstraint("count >= 1", name="count_ge_1"),)
