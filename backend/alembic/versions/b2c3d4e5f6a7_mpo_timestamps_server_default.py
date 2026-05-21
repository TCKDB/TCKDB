"""add server_default=now() to molecular_property_observation timestamps

The original migration (``a1b2c3d4e5f6_add_molecular_property_observation``)
declared ``created_at`` and ``updated_at`` as ``NOT NULL`` with no DDL
default. That worked when nothing wrote to the table — but the
CCCBDB workflow-import service (Phase 9) is the first writer.
SQLAlchemy's ORM TimestampMixin only maps ``created_at`` (no
``updated_at`` on the model), and the ORM-emitted INSERT omits both
columns when they're not explicitly set, leaving Postgres to reject
the row with a ``NOT NULL`` violation.

This migration adds ``server_default=sa.func.now()`` to both columns
so the database fills them on its own when the ORM omits them. The
fix is additive and idempotent — re-applying it on a fresh DB is a
no-op.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-21 (Phase 9)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "molecular_property_observation",
        "created_at",
        server_default=sa.func.now(),
    )
    op.alter_column(
        "molecular_property_observation",
        "updated_at",
        server_default=sa.func.now(),
    )


def downgrade() -> None:
    op.alter_column(
        "molecular_property_observation",
        "updated_at",
        server_default=None,
    )
    op.alter_column(
        "molecular_property_observation",
        "created_at",
        server_default=None,
    )
