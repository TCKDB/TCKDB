from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CHAR, BigInteger, Date, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.calculation import Calculation
    from app.db.models.kinetics import Kinetics
    from app.db.models.network import Network
    from app.db.models.network_pdep import NetworkSolve
    from app.db.models.statmech import Statmech
    from app.db.models.thermo import Thermo
    from app.db.models.transport import Transport


class WorkflowTool(Base, TimestampMixin):
    """Stable identity for a workflow or orchestration tool."""

    __tablename__ = "workflow_tool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    releases: Mapped[list["WorkflowToolRelease"]] = relationship(
        back_populates="workflow_tool",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("name"),)


class WorkflowToolRelease(Base, TimestampMixin):
    """Exact provenance for a workflow tool code state.

    Dedupe uses `(workflow_tool_id, version, git_commit)` with PostgreSQL
    `NULLS NOT DISTINCT`.
    """

    __tablename__ = "workflow_tool_release"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    workflow_tool_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workflow_tool.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )

    version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    git_commit: Mapped[Optional[str]] = mapped_column(CHAR(40), nullable=True)
    release_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    workflow_tool: Mapped["WorkflowTool"] = relationship(back_populates="releases")

    calculations: Mapped[list["Calculation"]] = relationship(
        back_populates="workflow_tool_release"
    )
    thermo_records: Mapped[list["Thermo"]] = relationship(
        back_populates="workflow_tool_release"
    )
    statmech_records: Mapped[list["Statmech"]] = relationship(
        back_populates="workflow_tool_release"
    )
    transport_records: Mapped[list["Transport"]] = relationship(
        back_populates="workflow_tool_release"
    )
    kinetics_records: Mapped[list["Kinetics"]] = relationship(
        back_populates="workflow_tool_release"
    )
    networks: Mapped[list["Network"]] = relationship(
        back_populates="workflow_tool_release"
    )
    network_solves: Mapped[list["NetworkSolve"]] = relationship(
        back_populates="workflow_tool_release"
    )

    __table_args__ = (
        Index(
            "uq_workflow_tool_release_workflow_tool_id",
            "workflow_tool_id",
            "version",
            "git_commit",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )
