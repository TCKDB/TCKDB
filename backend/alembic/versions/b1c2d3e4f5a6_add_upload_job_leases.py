"""Add durable lease and heartbeat fields to async upload jobs.

Revision ID: b1c2d3e4f5a6
Revises: a8b9c0d1e2f3
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_job", sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("upload_job", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_upload_job_status_lease_expires_at",
        "upload_job",
        ["status", "lease_expires_at"],
    )
    # Earlier workers had no lease fields.  Make all recoverable in-flight
    # rows visible to the new claimant rather than silently stranding them.
    op.execute("""
        UPDATE upload_job
        SET status = 'queued', started_at = NULL
        WHERE status = 'processing' AND attempts < max_attempts
    """)
    op.execute("""
        UPDATE upload_job
        SET status = 'failed', completed_at = COALESCE(completed_at, now()),
            error = COALESCE(error, 'Worker stopped after final permitted attempt before lease migration.')
        WHERE status = 'processing' AND attempts >= max_attempts
    """)


def downgrade() -> None:
    op.drop_index("ix_upload_job_status_lease_expires_at", table_name="upload_job")
    op.drop_column("upload_job", "heartbeat_at")
    op.drop_column("upload_job", "lease_expires_at")
