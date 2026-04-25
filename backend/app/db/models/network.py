from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import NetworkSpeciesRole

if TYPE_CHECKING:
    from app.db.models.literature import Literature
    from app.db.models.network_pdep import NetworkChannel, NetworkSolve, NetworkState
    from app.db.models.reaction import ReactionEntry
    from app.db.models.software import SoftwareRelease
    from app.db.models.species import SpeciesEntry
    from app.db.models.workflow import WorkflowToolRelease


class Network(Base, TimestampMixin, CreatedByMixin):
    """Reaction network metadata."""

    __tablename__ = "network"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    literature_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("literature.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    software_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("software_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )
    workflow_tool_release_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("workflow_tool_release.id", deferrable=True, initially="IMMEDIATE"),
        nullable=True,
    )

    literature: Mapped[Optional["Literature"]] = relationship()
    software_release: Mapped[Optional["SoftwareRelease"]] = relationship(
        back_populates="networks"
    )
    workflow_tool_release: Mapped[Optional["WorkflowToolRelease"]] = relationship(
        back_populates="networks"
    )

    reactions: Mapped[list["NetworkReaction"]] = relationship(
        back_populates="network",
        cascade="all, delete-orphan",
    )
    species_links: Mapped[list["NetworkSpecies"]] = relationship(
        back_populates="network",
        cascade="all, delete-orphan",
    )

    states: Mapped[list["NetworkState"]] = relationship(
        back_populates="network",
        cascade="all, delete-orphan",
    )
    channels: Mapped[list["NetworkChannel"]] = relationship(
        back_populates="network",
        cascade="all, delete-orphan",
    )
    solves: Mapped[list["NetworkSolve"]] = relationship(
        back_populates="network",
        cascade="all, delete-orphan",
    )


class NetworkReaction(Base):
    """Links a network to a reaction entry."""

    __tablename__ = "network_reaction"

    network_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    reaction_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("reaction_entry.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )

    network: Mapped["Network"] = relationship(back_populates="reactions")
    reaction_entry: Mapped["ReactionEntry"] = relationship()


class NetworkSpecies(Base):
    """Links a network to a species entry with a role."""

    __tablename__ = "network_species"

    network_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("network.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    species_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("species_entry.id", deferrable=True, initially="IMMEDIATE"),
        primary_key=True,
    )
    role: Mapped[NetworkSpeciesRole] = mapped_column(
        SAEnum(NetworkSpeciesRole, name="network_species_role"),
        primary_key=True,
    )

    network: Mapped["Network"] = relationship(back_populates="species_links")
    species_entry: Mapped["SpeciesEntry"] = relationship()
