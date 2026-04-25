from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import ReactionRole

if TYPE_CHECKING:
    from app.db.models.kinetics import Kinetics
    from app.db.models.species import Species, SpeciesEntry
    from app.db.models.transition_state import TransitionState


class ReactionFamily(Base, TimestampMixin):
    __tablename__ = "reaction_family"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    reactions: Mapped[list["ChemReaction"]] = relationship(
        back_populates="reaction_family"
    )


class ChemReaction(Base, TimestampMixin):
    __tablename__ = "chem_reaction"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stoichiometry_hash: Mapped[Optional[str]] = mapped_column(
        CHAR(64), unique=True, nullable=True
    )
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reaction_family_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("reaction_family.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    reaction_family_raw: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reaction_family_source_note: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    participants: Mapped[list["ReactionParticipant"]] = relationship(
        back_populates="reaction",
        cascade="all, delete-orphan",
    )
    reaction_family: Mapped[Optional["ReactionFamily"]] = relationship(
        back_populates="reactions"
    )
    entries: Mapped[list["ReactionEntry"]] = relationship(
        back_populates="reaction",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "reaction_family_raw IS NULL OR reaction_family_source_note IS NOT NULL",
            name="reaction_family_raw_requires_source_note",
        ),
    )


class ReactionParticipant(Base):
    """Compressed stoichiometric summary for a reaction graph identity.

    This is not an ordered participant-slot table. Repeated species on one side
    of a reaction are represented with `stoichiometry`.
    """

    __tablename__ = "reaction_participant"

    reaction_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chem_reaction.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    species_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    role: Mapped[ReactionRole] = mapped_column(
        SAEnum(ReactionRole, name="reaction_role"),
        primary_key=True,
    )
    stoichiometry: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    reaction: Mapped["ChemReaction"] = relationship(back_populates="participants")
    species: Mapped["Species"] = relationship(back_populates="reaction_participants")

    __table_args__ = (CheckConstraint("stoichiometry >= 1", name="stoichiometry_ge_1"),)


class ReactionEntry(Base, TimestampMixin, CreatedByMixin):
    __tablename__ = "reaction_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reaction_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chem_reaction.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    reaction: Mapped["ChemReaction"] = relationship(back_populates="entries")
    structure_participants: Mapped[list["ReactionEntryStructureParticipant"]] = (
        relationship(
            back_populates="reaction_entry",
            cascade="all, delete-orphan",
            order_by="ReactionEntryStructureParticipant.participant_index",
        )
    )
    transition_states: Mapped[list["TransitionState"]] = relationship(
        back_populates="reaction_entry",
        cascade="all, delete-orphan",
    )
    kinetics_records: Mapped[list["Kinetics"]] = relationship(
        back_populates="reaction_entry",
        cascade="all, delete-orphan",
        foreign_keys="Kinetics.reaction_entry_id",
    )


class ReactionEntryStructureParticipant(Base, TimestampMixin, CreatedByMixin):
    __tablename__ = "reaction_entry_structure_participant"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("reaction_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    role: Mapped[ReactionRole] = mapped_column(
        SAEnum(ReactionRole, name="reaction_role"),
        nullable=False,
    )
    participant_index: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reaction_entry: Mapped["ReactionEntry"] = relationship(
        back_populates="structure_participants"
    )
    species_entry: Mapped["SpeciesEntry"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "participant_index >= 1",
            name="participant_index_ge_1",
        ),
        UniqueConstraint(
            "reaction_entry_id",
            "role",
            "participant_index",
            name="uq_reaction_entry_structure_participant_reaction_entry_id",
        ),
    )
