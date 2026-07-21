"""add append-only reproducibility assessments

Creates a versioned, per-record reproducibility assessment history.  The
assessment grade is intentionally orthogonal to human ``record_review`` state
and deterministic trust badges.  New evidence or a rubric change appends a new
row; a database trigger rejects UPDATE and DELETE so this remains true even for
direct SQL clients.

The existing ``submission_record_type`` enum is reused.  This revision owns the
new ``reproducibility_grade`` and ``reproducibility_assessor_kind`` enums.

Revision ID: b4e8c1f6a2d9
Revises: a7c9e2f4b6d8
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e8c1f6a2d9"
down_revision: Union[str, Sequence[str], None] = "a7c9e2f4b6d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRADE_VALUES = ("described", "auditable", "rerunnable")
_ASSESSOR_KIND_VALUES = ("system", "curator")
_TRIGGER_NAME = "trg_repro_assessment_append_only"
_TRIGGER_FUNCTION = "reject_reproducibility_assessment_mutation"


def upgrade() -> None:
    """Create the assessment history and enforce append-only storage."""
    grade_enum = postgresql.ENUM(
        *_GRADE_VALUES,
        name="reproducibility_grade",
        create_type=False,
    )
    assessor_kind_enum = postgresql.ENUM(
        *_ASSESSOR_KIND_VALUES,
        name="reproducibility_assessor_kind",
        create_type=False,
    )
    grade_enum.create(op.get_bind(), checkfirst=True)
    assessor_kind_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "record_reproducibility_assessment",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "record_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=False,
        ),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "grade",
            postgresql.ENUM(
                *_GRADE_VALUES,
                name="reproducibility_grade",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("rubric_name", sa.String(length=128), nullable=False),
        sa.Column("rubric_version", sa.String(length=64), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "passed_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "missing_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "assessor_kind",
            postgresql.ENUM(
                *_ASSESSOR_KIND_VALUES,
                name="reproducibility_assessor_kind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("assessor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("source_submission_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "assessed_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_record_reproducibility_assessment"),
        ),
        sa.ForeignKeyConstraint(
            ["assessor_user_id"],
            ["app_user.id"],
            name="fk_repro_assessment_assessor_user",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["source_submission_id"],
            ["submission.id"],
            name="fk_repro_assessment_source_submission",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.CheckConstraint(
            "context_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_record_reproducibility_assessment_context_hash_lower_hex"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(context_json) = 'object'",
            name=op.f("ck_record_reproducibility_assessment_context_json_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(passed_json) = 'array'",
            name=op.f("ck_record_reproducibility_assessment_passed_json_is_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_json) = 'array'",
            name=op.f("ck_record_reproducibility_assessment_missing_json_is_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(warnings_json) = 'array'",
            name=op.f("ck_record_reproducibility_assessment_warnings_json_is_array"),
        ),
        sa.CheckConstraint(
            "(assessor_kind = 'system' AND assessor_user_id IS NULL) OR "
            "(assessor_kind = 'curator' AND assessor_user_id IS NOT NULL)",
            name=op.f("ck_record_reproducibility_assessment_assessor_user_consistent"),
        ),
    )
    op.create_index(
        "ix_repro_assessment_record_latest",
        "record_reproducibility_assessment",
        ["record_type", "record_id", sa.text("assessed_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_repro_assessment_context_hash",
        "record_reproducibility_assessment",
        ["context_hash"],
    )
    op.create_index(
        "ix_repro_assessment_assessor_user_id",
        "record_reproducibility_assessment",
        ["assessor_user_id"],
    )
    op.create_index(
        "ix_repro_assessment_source_submission_id",
        "record_reproducibility_assessment",
        ["source_submission_id"],
    )

    op.execute(
        f"""
        CREATE FUNCTION {_TRIGGER_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'record_reproducibility_assessment is append-only';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER_NAME}
        BEFORE UPDATE OR DELETE ON record_reproducibility_assessment
        FOR EACH ROW
        EXECUTE FUNCTION {_TRIGGER_FUNCTION}()
        """
    )


def downgrade() -> None:
    """Drop the assessment history, its trigger, and revision-owned enums."""
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON record_reproducibility_assessment")
    op.execute(f"DROP FUNCTION IF EXISTS {_TRIGGER_FUNCTION}()")
    op.drop_index(
        "ix_repro_assessment_source_submission_id",
        table_name="record_reproducibility_assessment",
    )
    op.drop_index(
        "ix_repro_assessment_assessor_user_id",
        table_name="record_reproducibility_assessment",
    )
    op.drop_index(
        "ix_repro_assessment_context_hash",
        table_name="record_reproducibility_assessment",
    )
    op.drop_index(
        "ix_repro_assessment_record_latest",
        table_name="record_reproducibility_assessment",
    )
    op.drop_table("record_reproducibility_assessment")

    postgresql.ENUM(
        *_ASSESSOR_KIND_VALUES,
        name="reproducibility_assessor_kind",
    ).drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(
        *_GRADE_VALUES,
        name="reproducibility_grade",
    ).drop(op.get_bind(), checkfirst=True)
