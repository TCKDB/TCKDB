"""record an automatic reclaim restore as an integrity detection context

The orphan reclaim moves an unreferenced object to a hold; the purge
deletes from it, re-reading the references first and refusing any digest
a committed row now points at. That refusal was the whole response, and
it left a live wrong state: the row is legitimate, its object is sitting
under ``reclaimed/``, every read of it records ``object_missing``, and
the trust layer hard-fails a correct calculation until somebody happens
to run ``--restore`` by hand. Nothing alerted, and the interval was
unbounded.

The repair is now automatic, because it is unambiguous -- the object
belongs at that key, and ``restore_held_object`` refuses an occupied one
so it cannot clobber anything. What automation must not do is hide that
the reclaim's safety argument lost its race, and ADR 0014 already says
how that is answered: custody changes are recorded, not logged. Moving an
object out of the content-addressed namespace and back is a change of
custody, so it gets a value naming itself as the context, and the row it
carries is a real re-read of the restored bytes rather than an assertion
that the restore worked.

The reclaim *itself* deliberately does not get a value here. A reclaimed
object is by construction referenced by nothing, so a row about it could
not name evidence; and the only honest finding for "no longer at its key"
is ``object_missing``, which is a break, which would hard-fail the next
legitimate upload of those bytes on the strength of a maintenance action.
The restore is different precisely because a committed row does point at
the digest.

Revision ID: c8e2a7d41b96
Revises: b6c1f4a8e703
Create Date: 2026-08-11

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2a7d41b96"
down_revision: Union[str, Sequence[str], None] = "b6c1f4a8e703"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENUM = "artifact_integrity_detection_context"
_NEW_VALUE = "reclaim_restore"
_OLD_VALUES = (
    "download",
    "verification_sweep",
    "store_dedup_verification",
    "parameter_extraction",
    "archive",
    "reproducibility_verification",
)


def upgrade() -> None:
    """Add the reclaim-restore detection context."""
    op.execute(f"ALTER TYPE {_ENUM} ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'")


def downgrade() -> None:
    """Remove the context only when no observation was recorded under it.

    Same refusal as the revision that added ``reproducibility_verification``
    and for the same reason: the table is append-only evidence about
    TCKDB's custody of its own bytes. Rewriting a row to a context it did
    not come from falsifies the record and deleting it destroys the only
    account of what happened, so a downgrade that would have to do either
    fails loudly instead.
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
    op.execute(f"ALTER TYPE {_ENUM} RENAME TO {_ENUM}_with_reclaim_restore")
    op.execute(f"CREATE TYPE {_ENUM} AS ENUM ({values})")
    op.execute(
        "ALTER TABLE artifact_integrity_event "
        f"ALTER COLUMN detected_during TYPE {_ENUM} "
        f"USING detected_during::text::{_ENUM}"
    )
    op.execute(f"DROP TYPE {_ENUM}_with_reclaim_restore")
