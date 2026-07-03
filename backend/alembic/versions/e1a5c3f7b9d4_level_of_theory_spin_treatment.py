"""level_of_theory: spin treatment axis (R / U / RO)

DR-0034. Adds ``level_of_theory.spin_treatment`` (restricted /
unrestricted / restricted_open / unknown) as part of the level-of-theory
identity, and folds it into ``lot_hash``. Because the hash formula
changes, existing rows are re-hashed in place using the new formula with
``spin_treatment`` = "unknown" (existing rows carry NULL), so a later
upload of the same level of theory still deduplicates against them.

The recompute is collision-free: two distinct existing rows had distinct
old hashes (unique constraint), so their new hashes differ too.

Revision ID: e1a5c3f7b9d4
Revises: d0f4b2c6e8a3
Create Date: 2026-07-02 11:00:00.000000

"""
import hashlib
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e1a5c3f7b9d4'
down_revision: Union[str, Sequence[str], None] = 'd0f4b2c6e8a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


spin_treatment = postgresql.ENUM(
    'restricted', 'unrestricted', 'restricted_open', 'unknown',
    name='spin_treatment',
    create_type=False,
)

_HASH_FIELDS = (
    'method', 'basis', 'aux_basis', 'cabs_basis',
    'dispersion', 'solvent', 'solvent_model', 'keywords',
)


def _lot_hash(row) -> str:
    payload = {field: row._mapping[field] for field in _HASH_FIELDS}
    # Existing rows carry NULL spin_treatment → folds to "unknown", matching
    # the application hash formula.
    payload['spin_treatment'] = row._mapping['spin_treatment'] or 'unknown'
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def upgrade() -> None:
    """Upgrade schema + re-hash existing rows under the new formula."""
    spin_treatment.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'level_of_theory',
        sa.Column('spin_treatment', spin_treatment, nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, method, basis, aux_basis, cabs_basis, dispersion, "
            "solvent, solvent_model, keywords, spin_treatment "
            "FROM level_of_theory"
        )
    ).all()
    for row in rows:
        bind.execute(
            sa.text("UPDATE level_of_theory SET lot_hash = :h WHERE id = :id"),
            {"h": _lot_hash(row), "id": row._mapping['id']},
        )


def downgrade() -> None:
    """Downgrade schema + revert lot_hash to the spin-treatment-free formula."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, method, basis, aux_basis, cabs_basis, dispersion, "
            "solvent, solvent_model, keywords FROM level_of_theory"
        )
    ).all()
    for row in rows:
        payload = {field: row._mapping[field] for field in _HASH_FIELDS}
        h = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()
        bind.execute(
            sa.text("UPDATE level_of_theory SET lot_hash = :h WHERE id = :id"),
            {"h": h, "id": row._mapping['id']},
        )

    op.drop_column('level_of_theory', 'spin_treatment')
    spin_treatment.drop(bind, checkfirst=True)
