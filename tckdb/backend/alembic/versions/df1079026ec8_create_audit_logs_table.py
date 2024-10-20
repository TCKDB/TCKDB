"""create audit logs table

Revision ID: df1079026ec8
Revises: ec2a22b3c44b
Create Date: 2024-10-20 08:07:18.495911

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from tckdb.backend.app.models.common import MsgpackExt

# revision identifiers, used by Alembic.
revision: str = '738e1beaa271'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=50), nullable=False),
    sa.Column('model_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=50), nullable=False),
    sa.Column('changes', sa.JSON(), nullable=True),
    sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('performed_by', sa.String(length=50), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_id'), 'audit_logs', ['id'], unique=False)
    pass


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_logs_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
    pass
