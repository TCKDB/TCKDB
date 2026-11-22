"""Audit of approved scientific records an admin deleted.

One row per deleted record: who, when, why, what it was about, and a JSON copy of the record and every row removed
with it. Append-only: a trigger refuses UPDATE and DELETE. The row is also what lets the accepted-science guards stand
down for one delete, in the transaction that wrote it (see ``tckdb_record_deletion_permits``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.common import SubmissionRecordType

if TYPE_CHECKING:
    from app.db.models.app_user import AppUser


class RecordDeletion(Base):
    """One approved record removed by an admin, with a copy of everything removed."""

    __tablename__ = "record_deletion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    #: The deleted row's id. Nothing references it any more; kept so the copy can be matched to logs.
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    record_public_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The record's immediate container (a reaction entry for kinetics, a species entry for thermo), by public ref.
    subject_type: Mapped[Optional[SubmissionRecordType]] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=True,
    )
    subject_public_ref: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    #: Every container up to the identity root, nearest first: ``[{"record_type": ..., "public_ref": ...}, ...]``.
    subject_chain: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    first_approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("app_user.id", deferrable=True, initially="IMMEDIATE"),
        nullable=False,
    )
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), server_default=func.now(), nullable=False)
    #: ``{"record": {...}, "owned": {table: [rows]}, "overlays": {table: [rows]}, "release_selections": [rows]}``.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: The transaction that wrote the row; the guards honour it only there.
    xact_id: Mapped[int] = mapped_column(
        BigInteger,
        server_default=text("((pg_current_xact_id())::text)::bigint"),
        nullable=False,
    )

    actor: Mapped["AppUser"] = relationship(foreign_keys=[deleted_by])

    __table_args__ = (
        CheckConstraint(
            "record_type IN ('calculation', 'thermo', 'statmech', 'kinetics', "
            "'transport', 'network', 'network_solve', 'applied_energy_correction', "
            "'transition_state_entry', 'conformer_observation')",
            name="supported_type",
        ),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_nonblank"),
        CheckConstraint("(subject_type IS NULL) = (subject_public_ref IS NULL)", name="subject_both_or_neither"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot_is_object"),
        CheckConstraint("jsonb_typeof(subject_chain) = 'array'", name="subject_chain_is_array"),
        UniqueConstraint("record_type", "record_id"),
        UniqueConstraint("record_public_ref"),
        Index("ix_record_deletion_deleted_at", "deleted_at"),
    )
