"""Add immutable content-addressed execution environment manifests.

The table starts empty, so no row backfill is required. Upgrade creates the
manifest table, nullable calculation FK, indexes, and immutable-row trigger;
the nullable FK avoids a table rewrite for existing calculations. On large
databases index creation and the foreign-key lock can still block concurrent
writes, so deploy during a low-traffic window. Downgrade removes the FK/table
and irreversibly discards manifests recorded after upgrade.

Revision ID: a8b9c0d1e2f3
Revises: 6a9d2e4c7b1f
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "6a9d2e4c7b1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_environment_manifest",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("platform", sa.String(length=128), nullable=False),
        sa.Column("architecture", sa.String(length=128), nullable=False),
        sa.Column("runtime_kind", sa.String(length=32), nullable=False),
        sa.Column("runtime_locator", sa.Text(), nullable=False),
        sa.Column("executable_locator", sa.Text(), nullable=False),
        sa.Column("software_release_id", sa.BigInteger(), nullable=False),
        sa.Column("workflow_tool_release_id", sa.BigInteger(), nullable=True),
        sa.Column("closure_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("canonical_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_execution_environment_manifest_content_digest_sha256"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(closure_json) = 'array'", name="ck_execution_environment_manifest_closure_json_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(canonical_json) = 'object'", name="ck_execution_environment_manifest_canonical_json_object"
        ),
        sa.UniqueConstraint("content_digest", name="uq_execution_environment_manifest_content_digest"),
        sa.ForeignKeyConstraint(
            ["software_release_id"], ["software_release.id"],
            name="fk_execution_environment_manifest_software_release", deferrable=True, initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_tool_release_id"], ["workflow_tool_release.id"],
            name="fk_execution_environment_manifest_workflow_tool_release", deferrable=True, initially="IMMEDIATE",
        ),
    )
    op.add_column("calculation", sa.Column("execution_environment_manifest_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_calculation_execution_environment_manifest",
        "calculation",
        "execution_environment_manifest",
        ["execution_environment_manifest_id"],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )
    op.create_index(
        "ix_calculation_execution_environment_manifest_id", "calculation", ["execution_environment_manifest_id"]
    )
    op.execute("""
        CREATE FUNCTION reject_execution_environment_manifest_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'execution_environment_manifest is immutable';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_execution_environment_manifest_immutable
        BEFORE UPDATE OR DELETE ON execution_environment_manifest
        FOR EACH ROW EXECUTE FUNCTION reject_execution_environment_manifest_mutation()
    """)
    op.execute("""
        CREATE FUNCTION validate_calculation_execution_environment_binding() RETURNS trigger
        LANGUAGE plpgsql AS $$ DECLARE manifest_software_release_id bigint;
        manifest_workflow_tool_release_id bigint; BEGIN
          IF NEW.execution_environment_manifest_id IS NULL THEN RETURN NEW; END IF;
          SELECT software_release_id, workflow_tool_release_id
          INTO manifest_software_release_id, manifest_workflow_tool_release_id
          FROM execution_environment_manifest WHERE id = NEW.execution_environment_manifest_id;
          IF NOT FOUND
             OR NEW.software_release_id IS DISTINCT FROM manifest_software_release_id
             OR NEW.workflow_tool_release_id IS DISTINCT FROM manifest_workflow_tool_release_id THEN
            RAISE EXCEPTION 'calculation execution environment release bindings must match';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER trg_calculation_execution_environment_binding
        BEFORE INSERT OR UPDATE OF execution_environment_manifest_id, software_release_id, workflow_tool_release_id ON calculation
        FOR EACH ROW EXECUTE FUNCTION validate_calculation_execution_environment_binding()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_calculation_execution_environment_binding ON calculation")
    op.execute("DROP FUNCTION IF EXISTS validate_calculation_execution_environment_binding()")
    op.execute("DROP TRIGGER IF EXISTS trg_execution_environment_manifest_immutable ON execution_environment_manifest")
    op.execute("DROP FUNCTION IF EXISTS reject_execution_environment_manifest_mutation()")
    op.drop_index("ix_calculation_execution_environment_manifest_id", table_name="calculation")
    op.drop_constraint(
        "fk_calculation_execution_environment_manifest",
        "calculation",
        type_="foreignkey",
    )
    op.drop_column("calculation", "execution_environment_manifest_id")
    op.drop_table("execution_environment_manifest")
