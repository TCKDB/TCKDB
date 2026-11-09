"""add deposit_warning_retirement

``deposit_warning`` is append-only (triggers refuse UPDATE, DELETE, TRUNCATE), so a warning that stops applying
cannot be marked in place. This revision adds a sibling table, itself append-only: one row per retired warning, saying
when and why it stopped being current. The warning row stays untouched as history; reads show only warnings with no
retirement row.

First user: an output log attached after the deposit satisfies ``converged_opt_no_usable_energy``'s own "energy plus
file" condition, so the warning raised before the file arrived is retired.

Brand-new table, no backfill: every existing warning is current, as before.

Revision ID: b8e3f1a7c524
Revises: d5f2b8c1a704
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b8e3f1a7c524"
down_revision: Union[str, Sequence[str], None] = "d5f2b8c1a704"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROW_TRIGGER = "trg_deposit_warning_retirement_append_only"
_TRUNCATE_TRIGGER = "trg_deposit_warning_retirement_no_truncate"
_FUNCTION = "reject_deposit_warning_retirement_mutation"


def upgrade() -> None:
    op.create_table(
        "deposit_warning_retirement",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("deposit_warning_id", sa.BigInteger(), nullable=False),
        sa.Column("retired_reason_code", sa.Text(), nullable=False),
        sa.Column("retired_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deposit_warning_retirement")),
        sa.ForeignKeyConstraint(
            ["deposit_warning_id"],
            ["deposit_warning.id"],
            name="fk_deposit_warning_retirement_warning",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["retired_by_user_id"],
            ["app_user.id"],
            name="fk_deposit_warning_retirement_by",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.CheckConstraint(
            "retired_reason_code IN ('artifact_attached', 'superseded_by_recheck')",
            name=op.f("ck_deposit_warning_retirement_reason_known"),
        ),
        sa.UniqueConstraint("deposit_warning_id", name="uq_deposit_warning_retirement_warning"),
    )
    op.execute(
        f"""
        CREATE FUNCTION {_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning_retirement is append-only';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_ROW_TRIGGER}
        BEFORE UPDATE OR DELETE ON deposit_warning_retirement
        FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRUNCATE_TRIGGER}
        BEFORE TRUNCATE ON deposit_warning_retirement
        FOR EACH STATEMENT EXECUTE FUNCTION {_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRUNCATE_TRIGGER} ON deposit_warning_retirement")
    op.execute(f"DROP TRIGGER IF EXISTS {_ROW_TRIGGER} ON deposit_warning_retirement")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")
    op.drop_table("deposit_warning_retirement")
