"""Append-only replacement links between accepted scientific records."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.common import SubmissionRecordType

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser


class ScientificRecordSupersession(Base):
    """One immutable, one-to-one edge from an accepted record to its replacement."""

    __tablename__ = "scientific_record_supersession"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    superseded_record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    superseding_record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    actor: Mapped["AppUser"] = relationship(foreign_keys=[created_by])

    __table_args__ = (
        CheckConstraint(
            "record_type IN ('calculation', 'thermo', 'statmech', 'kinetics', "
            "'transport', 'network', 'network_solve', 'applied_energy_correction', "
            "'transition_state_entry', 'conformer_observation')",
            name="supported_type",
        ),
        CheckConstraint(
            "superseded_record_id <> superseding_record_id",
            name="distinct_records",
        ),
        CheckConstraint(
            "length(btrim(reason)) > 0",
            name="reason_nonblank",
        ),
        UniqueConstraint(
            "record_type",
            "superseded_record_id",
            name="uq_scientific_supersession_old_record",
        ),
        UniqueConstraint(
            "record_type",
            "superseding_record_id",
            name="uq_scientific_supersession_new_record",
        ),
    )
