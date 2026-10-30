"""add append-only deposit warnings

Creates ``deposit_warning``: each warn-tier finding of a deposit, stored beside
the record it is about, plus one marker row (``code IS NULL``) per record the
deposit checked, so a read can say ``recorded`` with no items instead of leaving
"nothing fired" and "never checked" looking alike.

Brand-new table; no backfill. Records deposited before this revision have no
marker and read ``not_recorded``.

UPDATE, DELETE and TRUNCATE are refused unconditionally by triggers
(``trg_deposit_warning_append_only`` and ``trg_deposit_warning_no_truncate``),
the regime ``record_reproducibility_assessment`` lives under. INSERT stays
allowed under an approved record: a later artifact upload can add a warning to
an existing calculation. The accepted-science child guard is deliberately not
used.

The existing ``submission_record_type`` enum is reused unchanged.

Revision ID: f7a2c9e4b183
Revises: e5b2c8d1f947
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f7a2c9e4b183"
down_revision: Union[str, Sequence[str], None] = "e5b2c8d1f947"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROW_TRIGGER = "trg_deposit_warning_append_only"
_TRUNCATE_TRIGGER = "trg_deposit_warning_no_truncate"
_FUNCTION = "reject_deposit_warning_mutation"


def upgrade() -> None:
    op.create_table(
        "deposit_warning",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "record_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=True,
        ),
        sa.Column("record_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("register_entry", sa.SmallInteger(), nullable=True),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("check_version", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deposit_warning")),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name="fk_deposit_warning_submission",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.CheckConstraint(
            "(record_type IS NULL) = (record_id IS NULL)",
            name=op.f("ck_deposit_warning_record_both_or_neither"),
        ),
        sa.CheckConstraint(
            "(code IS NULL AND register_entry IS NULL AND field IS NULL AND message IS NULL AND context IS NULL "
            "AND content_hash IS NULL) "
            "OR (code IS NOT NULL AND field IS NOT NULL AND message IS NOT NULL AND content_hash IS NOT NULL AND content_hash ~ '^[0-9a-f]{64}$')",
            name=op.f("ck_deposit_warning_marker_or_complete"),
        ),
        sa.CheckConstraint(
            "submission_id IS NOT NULL OR (record_type IS NOT NULL AND code IS NOT NULL)",
            name=op.f("ck_deposit_warning_submission_or_record_warning"),
        ),
        sa.CheckConstraint(
            "context IS NULL OR jsonb_typeof(context) = 'object'",
            name=op.f("ck_deposit_warning_context_is_object"),
        ),
        sa.UniqueConstraint(
            "submission_id",
            "record_type",
            "record_id",
            "content_hash",
            name="uq_deposit_warning_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_deposit_warning_record", "deposit_warning", ["record_type", "record_id"])

    op.execute(
        f"""
        CREATE FUNCTION {_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning is append-only';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_ROW_TRIGGER}
        BEFORE UPDATE OR DELETE ON deposit_warning
        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRUNCATE_TRIGGER}
        BEFORE TRUNCATE ON deposit_warning
        FOR EACH STATEMENT EXECUTE FUNCTION {_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRUNCATE_TRIGGER} ON deposit_warning")
    op.execute(f"DROP TRIGGER IF EXISTS {_ROW_TRIGGER} ON deposit_warning")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")
    op.drop_index("ix_deposit_warning_record", table_name="deposit_warning")
    op.drop_table("deposit_warning")
