from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import TransitionStateEntryStatus
from app.db.types import RDKitMol

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.reaction import ReactionEntry


class TransitionState(Base, TimestampMixin, CreatedByMixin):
    """Reaction-channel-level transition-state concept.

    This groups candidate saddle-point structures that belong to the same
    reaction-channel interpretation.
    """

    __tablename__ = "transition_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("reaction_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reaction_entry: Mapped["ReactionEntry"] = relationship(
        back_populates="transition_states"
    )
    entries: Mapped[list["TransitionStateEntry"]] = relationship(
        back_populates="transition_state",
        cascade="all, delete-orphan",
    )


class TransitionStateEntry(Base, TimestampMixin, CreatedByMixin):
    """One candidate transition-state geometry family member under a TS concept.

    Calculations refine or validate this candidate.
    """

    __tablename__ = "transition_state_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transition_state_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("transition_state.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    charge: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    multiplicity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mol: Mapped[Optional[str]] = mapped_column(RDKitMol(), nullable=True)
    unmapped_smiles: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[TransitionStateEntryStatus] = mapped_column(
        SAEnum(TransitionStateEntryStatus, name="transition_state_entry_status"),
        nullable=False,
        default=TransitionStateEntryStatus.optimized,
        server_default=TransitionStateEntryStatus.optimized.value,
    )

    transition_state: Mapped["TransitionState"] = relationship(back_populates="entries")
    calculations: Mapped[list["Calculation"]] = relationship(
        back_populates="transition_state_entry",
        foreign_keys="Calculation.transition_state_entry_id",
    )

    __table_args__ = (
        CheckConstraint("multiplicity >= 1", name="multiplicity_ge_1"),
    )
