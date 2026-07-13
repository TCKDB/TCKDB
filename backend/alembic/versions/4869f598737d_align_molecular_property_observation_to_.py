"""align molecular_property_observation schema to its ORM model

Resolves model-vs-migration drift on ``molecular_property_observation``.
The table was created by ``a1b2c3d4e5f6`` with a handful of columns that
disagree with the ORM model (``app/db/models/molecular_property_observation``).
In every case the **model** follows TCKDB convention and the original
migration is the outlier, so this revision alters the deployed table to
match the model. The table is empty in every long-lived DB, so the
changes are trivially safe.

Items reconciled (DB -> model):

1-3. ``literature_id`` / ``software_release_id`` /
     ``workflow_tool_release_id``: ``INTEGER`` -> ``BigInteger``. Every
     referenced PK (``literature.id``, ``software_release.id``,
     ``workflow_tool_release.id``) is ``BigInteger`` across the schema,
     and the sibling FKs ``species_entry_id`` / ``source_calculation_id``
     on this same table were already ``BigInteger``. The original three
     were hand-written as ``INTEGER`` by mistake. Integer -> BigInteger
     is a safe widening.

4.   ``created_at``: ``TIMESTAMP(timezone=True)`` -> ``DateTime()``
     (tz-naive). TCKDB stores tz-naive UTC timestamps everywhere: the
     shared ``TimestampMixin`` maps ``DateTime(timezone=False)``, every
     ``created_at`` in the initial migration is naive ``sa.DateTime()``,
     and commit b1c7810 made the app write naive-UTC values "to match
     the tz-naive DateTime(timezone=False) columns". The original
     migration's tz-aware column was the outlier. The DDL
     ``server_default=now()`` (added by ``b2c3d4e5f6a7``) is preserved.

5.   drop ``updated_at``. This is an append-only observation table
     (like the other ``*_observation`` / provenance tables) whose model
     uses ``TimestampMixin`` only (created-only, no ``updated_at``). The
     one table that legitimately carries ``updated_at``
     (``machine_review_curator_task``) declares it explicitly on the
     model because it is a mutable review-queue row; this table is not.
     The original migration added an ``updated_at`` that the model never
     mapped.

Revision ID: 4869f598737d
Revises: f3a9c1d7b2e4
Create Date: 2026-07-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4869f598737d'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1d7b2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'molecular_property_observation', 'literature_id',
        existing_type=sa.INTEGER(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        'molecular_property_observation', 'software_release_id',
        existing_type=sa.INTEGER(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        'molecular_property_observation', 'workflow_tool_release_id',
        existing_type=sa.INTEGER(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )
    op.alter_column(
        'molecular_property_observation', 'created_at',
        existing_type=postgresql.TIMESTAMP(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
        existing_server_default=sa.text('now()'),
    )
    op.drop_column('molecular_property_observation', 'updated_at')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'molecular_property_observation',
        sa.Column(
            'updated_at',
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.alter_column(
        'molecular_property_observation', 'created_at',
        existing_type=sa.DateTime(),
        type_=postgresql.TIMESTAMP(timezone=True),
        existing_nullable=False,
        existing_server_default=sa.text('now()'),
    )
    op.alter_column(
        'molecular_property_observation', 'workflow_tool_release_id',
        existing_type=sa.BigInteger(),
        type_=sa.INTEGER(),
        existing_nullable=True,
    )
    op.alter_column(
        'molecular_property_observation', 'software_release_id',
        existing_type=sa.BigInteger(),
        type_=sa.INTEGER(),
        existing_nullable=True,
    )
    op.alter_column(
        'molecular_property_observation', 'literature_id',
        existing_type=sa.BigInteger(),
        type_=sa.INTEGER(),
        existing_nullable=True,
    )
