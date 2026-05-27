"""add neutral LLM precheck audit event kind

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-27
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE submission_audit_event_kind "
        "ADD VALUE IF NOT EXISTS 'llm_precheck_recorded'"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade would require rebuilding submission_audit_event_kind and "
        "cannot be run while llm_precheck_recorded audit rows may exist."
    )
