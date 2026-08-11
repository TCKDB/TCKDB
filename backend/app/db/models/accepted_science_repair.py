"""Declared, checked and recorded repairs to accepted scientific records.

Both tables are written by the database itself, never by this application.
``accepted_science_repair`` is inserted by a migration running as the schema
owner -- an insert-time trigger refuses any other role, because the owner is
exactly the set that could already have run ``ALTER TABLE ... DISABLE
TRIGGER``. ``accepted_science_repair_change`` is appended by
``tckdb_repair_permits`` from inside the accepted-science guards.

They are mapped here so ``Base.metadata`` stays a complete picture of the
schema and so read paths and tests can query them; there is deliberately no
service that writes either. See ``e2c9a4f7b163`` and
``backend/docs/specs/accepted_science_immutability.md``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.common import SubmissionRecordType


class AcceptedScienceRepair(Base):
    """One declaration: which table, which columns, which revision, and why."""

    __tablename__ = "accepted_science_repair"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    target_schema: Mapped[str] = mapped_column(Text, server_default=text("'public'"), nullable=False)
    target_table: Mapped[str] = mapped_column(Text, nullable=False)
    #: The only columns an UPDATE under this declaration may change. Enforced
    #: by comparing OLD against NEW in the guard, not by convention.
    declared_columns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    alembic_revision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    declared_by_role: Mapped[str] = mapped_column(Text, server_default=text("CURRENT_USER"), nullable=False)
    declared_by_login: Mapped[str] = mapped_column(Text, server_default=text("SESSION_USER"), nullable=False)
    #: The transaction the declaration is valid in, and only in. A committed
    #: declaration can never be reused, so there is no close step to forget.
    xact_id: Mapped[int] = mapped_column(
        BigInteger,
        server_default=text("((pg_current_xact_id())::text)::bigint"),
        nullable=False,
    )
    #: ``timestamptz`` rather than this schema's usual naive timestamps: an
    #: expiry compared against ``clock_timestamp()`` must not depend on the
    #: session's ``TimeZone``.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp() + interval '1 hour'"),
        nullable=False,
    )

    changes: Mapped[list["AcceptedScienceRepairChange"]] = relationship(
        back_populates="repair",
        order_by="AcceptedScienceRepairChange.id",
    )

    __table_args__ = (
        CheckConstraint(
            "length(btrim(target_schema)) > 0 AND length(btrim(target_table)) > 0",
            name="target_nonblank",
        ),
        CheckConstraint(
            "cardinality(declared_columns) > 0 AND array_position(declared_columns, NULL) IS NULL",
            name="columns_declared",
        ),
        CheckConstraint("length(btrim(alembic_revision)) > 0", name="revision_nonblank"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_nonblank"),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '24 hours'",
            name="expiry_window",
        ),
        Index(
            "ix_accepted_science_repair_target",
            "target_schema",
            "target_table",
            "xact_id",
        ),
    )


class AcceptedScienceRepairChange(Base):
    """One accepted record's view of one row a repair rewrote."""

    __tablename__ = "accepted_science_repair_change"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repair_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("accepted_science_repair.id", name="fk_repair_change_repair"),
        nullable=False,
    )
    #: The accepted root whose immutability was stood down for this row.
    record_type: Mapped[SubmissionRecordType] = mapped_column(
        SAEnum(SubmissionRecordType, name="submission_record_type"),
        nullable=False,
    )
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_schema: Mapped[str] = mapped_column(Text, nullable=False)
    target_table: Mapped[str] = mapped_column(Text, nullable=False)
    #: Primary key of the changed row, as ``{column: value}``.
    row_identity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_columns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    before_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    after_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    repair: Mapped["AcceptedScienceRepair"] = relationship(back_populates="changes")

    __table_args__ = (
        CheckConstraint("cardinality(changed_columns) > 0", name="columns_changed"),
        Index("ix_accepted_science_repair_change_repair_id", "repair_id"),
        Index("ix_accepted_science_repair_change_record", "record_type", "record_id"),
    )
