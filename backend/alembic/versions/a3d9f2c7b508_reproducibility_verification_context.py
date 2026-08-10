"""record the reproducibility rubric as an integrity detection context

The rubric has been re-reading graded output logs through
``load_artifact_bytes`` since it was written, and on a digest mismatch it
recorded a warning of its own and nothing else. That made it a second
owner of a fact ``artifact_integrity_event`` already owns, with a
different consequence: the snapshot said the evidence had rotted while
the trust layer, which reads only recorded events, went on grading the
record normally. ADR 0008 puts the fact with the blocking tier and has
the others cite it, so the rubric now writes an event like every other
detector -- and needs a value naming itself as the context.

Recording the context, rather than folding it into ``verification_sweep``,
is the same reason the column exists at all: coverage is uneven by
construction, and "which reads have actually run over this object" is the
only thing that makes an absence of findings interpretable. A rubric
re-read covers exactly the output logs of calculations somebody asked to
grade, which is a different population from what a release sweep covers.

Revision ID: a3d9f2c7b508
Revises: b4e7c1d20f83
Create Date: 2026-08-10

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d9f2c7b508"
down_revision: Union[str, Sequence[str], None] = "b4e7c1d20f83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENUM = "artifact_integrity_detection_context"
_NEW_VALUE = "reproducibility_verification"
_OLD_VALUES = (
    "download",
    "verification_sweep",
    "store_dedup_verification",
    "parameter_extraction",
    "archive",
)


def upgrade() -> None:
    """Add the rubric's detection context."""
    op.execute(f"ALTER TYPE {_ENUM} ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'")


def downgrade() -> None:
    """Remove the context only when no observation was recorded under it.

    The table is append-only evidence about TCKDB's own custody of stored
    bytes. Rewriting such a row to a context it did not come from would be
    falsifying the record, and deleting it would destroy the only account
    of a break, so a downgrade that would have to do either fails loudly
    instead.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM artifact_integrity_event
                WHERE detected_during = '{_NEW_VALUE}'
            ) THEN
                RAISE EXCEPTION
                    'cannot remove detection context {_NEW_VALUE} while integrity events use it';
            END IF;
        END;
        $$
        """
    )
    values = ", ".join(f"'{value}'" for value in _OLD_VALUES)
    op.execute(f"ALTER TYPE {_ENUM} RENAME TO {_ENUM}_with_rubric")
    op.execute(f"CREATE TYPE {_ENUM} AS ENUM ({values})")
    op.execute(
        "ALTER TABLE artifact_integrity_event "
        f"ALTER COLUMN detected_during TYPE {_ENUM} "
        f"USING detected_during::text::{_ENUM}"
    )
    op.execute(f"DROP TYPE {_ENUM}_with_rubric")
