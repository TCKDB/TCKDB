"""calculation_input_geometry: record how the geometry was obtained

Two additive changes for `input-geometry extraction from ESS artifacts`
(app/services/input_geometry_extraction.py):

1. Adds a non-nullable ``source`` column (new
   ``calculation_input_geometry_source`` enum: ``deposited`` /
   ``extracted_from_artifact``, server_default ``'deposited'``) to the
   deployed ``calculation_input_geometry`` table.

   Every existing write path (a producer-declared ``input_geometries[]``
   entry, or the freq/sp fallback that reuses the calculation's own
   conformer geometry — see ``app/services/calculation_resolution.py``'s
   ``attach_calculation_input_geometries``) is a depositor-stated value,
   so existing rows backfill to ``'deposited'`` for free via the column
   default; no data migration is required. The one write path that sets
   ``'extracted_from_artifact'`` explicitly is
   ``app/services/input_geometry_extraction.py``: it re-derives a coarse
   ``opt`` calculation's true starting geometry from its own artifacts
   when the depositor recorded none (or recorded the converged output as
   a stand-in for it), and this column is what lets a read distinguish
   that case from an ordinary depositor-stated input.

2. Adds ``'input_geometry_extraction'`` to the
   ``artifact_integrity_detection_context`` enum (mirrors
   ``c8e2a7d41b96``'s addition of ``'reclaim_restore'``), so the
   extraction hook/backfill can record an integrity break the same way
   ``parameter_extraction`` does when a stored artifact's bytes no
   longer match their digest.

Additive changes on already-deployed tables (see migration-rules.md
"already-deployed tables"): both ``upgrade()`` and ``downgrade()`` are
implemented. ``downgrade()`` for the enum value follows the same
append-only-evidence refusal as ``c8e2a7d41b96``: it refuses to remove
the value while any ``artifact_integrity_event`` row uses it.

Revision ID: eb9793f23de6
Revises: c41dfc710b81
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'eb9793f23de6'
down_revision: Union[str, Sequence[str], None] = 'c41dfc710b81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


calculation_input_geometry_source = postgresql.ENUM(
    'deposited',
    'extracted_from_artifact',
    name='calculation_input_geometry_source',
    create_type=False,
)

_DETECTION_CONTEXT_ENUM = 'artifact_integrity_detection_context'
_DETECTION_CONTEXT_NEW_VALUE = 'input_geometry_extraction'
_DETECTION_CONTEXT_OLD_VALUES = (
    'download',
    'verification_sweep',
    'store_dedup_verification',
    'parameter_extraction',
    'archive',
    'reproducibility_verification',
    'reclaim_restore',
)


def upgrade() -> None:
    """Add the source enum type/column and the new detection context value."""
    calculation_input_geometry_source.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'calculation_input_geometry',
        sa.Column(
            'source',
            calculation_input_geometry_source,
            nullable=False,
            server_default='deposited',
        ),
    )
    op.execute(
        f"ALTER TYPE {_DETECTION_CONTEXT_ENUM} ADD VALUE IF NOT EXISTS "
        f"'{_DETECTION_CONTEXT_NEW_VALUE}'"
    )


def downgrade() -> None:
    """Drop the column/type, and remove the detection context value.

    The detection-context removal refuses while any
    ``artifact_integrity_event`` row uses it — same reasoning as
    ``c8e2a7d41b96``: that table is append-only evidence about TCKDB's
    custody of its own bytes, and rewriting or deleting a row to make
    room for a downgrade would falsify or destroy the only account of
    what happened.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM artifact_integrity_event
                WHERE detected_during = '{_DETECTION_CONTEXT_NEW_VALUE}'
            ) THEN
                RAISE EXCEPTION
                    'cannot remove detection context {_DETECTION_CONTEXT_NEW_VALUE} while integrity events use it';
            END IF;
        END;
        $$
        """
    )
    values = ", ".join(f"'{value}'" for value in _DETECTION_CONTEXT_OLD_VALUES)
    op.execute(
        f"ALTER TYPE {_DETECTION_CONTEXT_ENUM} "
        f"RENAME TO {_DETECTION_CONTEXT_ENUM}_with_input_geometry_extraction"
    )
    op.execute(f"CREATE TYPE {_DETECTION_CONTEXT_ENUM} AS ENUM ({values})")
    op.execute(
        "ALTER TABLE artifact_integrity_event "
        f"ALTER COLUMN detected_during TYPE {_DETECTION_CONTEXT_ENUM} "
        f"USING detected_during::text::{_DETECTION_CONTEXT_ENUM}"
    )
    op.execute(f"DROP TYPE {_DETECTION_CONTEXT_ENUM}_with_input_geometry_extraction")

    op.drop_column('calculation_input_geometry', 'source')
    calculation_input_geometry_source.drop(op.get_bind(), checkfirst=True)
