from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import CHAR, BigInteger, CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PublicRefMixin, TimestampMixin
from app.db.models.common import CoreTreatment, SpinTreatment

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation


class LevelOfTheory(Base, TimestampMixin, PublicRefMixin):
    """Method/basis provenance used by calculations."""

    __tablename__ = "level_of_theory"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    method: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    aux_basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cabs_basis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispersion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    solvent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    solvent_model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Restricted / unrestricted / restricted-open — part of LOT identity
    # (DR-0034). NULL = unspecified (distinct from an explicit 'unknown').
    spin_treatment: Mapped[Optional[SpinTreatment]] = mapped_column(
        SAEnum(SpinTreatment, name="spin_treatment"),
        nullable=True,
    )

    # Frozen-core vs all-electron (ADR 0021). NULL = not stated, and a NULL
    # row hashes exactly as it did before the column existed: the key joins the
    # hash only when set, so no existing row is re-keyed.
    core_treatment: Mapped[Optional[CoreTreatment]] = mapped_column(
        SAEnum(CoreTreatment, name="core_treatment"),
        nullable=True,
    )

    lot_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)

    calculations: Mapped[list["Calculation"]] = relationship(back_populates="lot")

    __table_args__ = (UniqueConstraint("lot_hash"),)


class LevelOfTheoryMerge(Base, TimestampMixin):
    """A level-of-theory row merged into another spelling of the same level (#574).

    Written only by ``scripts/ops/merge_duplicate_levels_of_theory.py``. The
    merged row is kept, with its ``public_ref``, so a citation of it (a
    published release's frozen provenance) still resolves; the read layer
    resolves that ref to ``into_lot_id``. No calculation points at a merged
    row, and ``into_lot_id`` is never itself merged, so one hop is enough.
    Both rules are enforced by database triggers (revision ``e88231299733``,
    #591), not only by the script: ``trg_lot_merge_guard`` refuses a chain, a
    loop, or a merge of a row calculations still use, and
    ``trg_calculation_lot_not_merged`` refuses a calculation on a merged row.

    A separate table rather than a column on ``level_of_theory``: whole-row
    snapshots of a level of theory (consistency-check inputs, reproducibility
    context hashes) would otherwise change for every row the day the column
    appeared.
    """

    __tablename__ = "level_of_theory_merge"

    merged_lot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("level_of_theory.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    into_lot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("level_of_theory.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        CheckConstraint("merged_lot_id <> into_lot_id", name="not_merged_into_itself"),
    )
