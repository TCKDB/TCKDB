"""Stop claiming a reviewer that does not exist.

Every deposit was stamped ``record_review.status = 'under_review'`` the
moment it landed. Nobody had looked at any of them. Measured on the
deployed database before this revision was written:

===========================================================  ======
``under_review`` rows with no reviewed_by/reviewed_at/first_approved_at  1153
``under_review`` rows carrying any of those three                          0
total ``under_review``                                                  1153
===========================================================  ======

So *every* row in that status was untouched, and the word was simply
false: ``under_review`` asserts a review is in progress. ``not_reviewed``
-- already the enum's own default, already the default of
``ensure_record_review`` -- is the true one, and the transition table has
always permitted ``not_reviewed -> under_review`` for the moment a human
picks a record up.

This is a **data migration**. No column, table, constraint, index or enum
changes; ``record_review_status`` already carries both values.

The predicate, and why it is not "all under_review rows"
--------------------------------------------------------

::

    status = 'under_review'
      AND reviewed_by IS NULL
      AND reviewed_at IS NULL
      AND first_approved_at IS NULL

On this database the three null tests select nothing extra -- all 1153
rows satisfy them. They are here anyway, because the number that makes
them redundant is a fact about today's data, not about the rule. A row
someone really is reviewing carries a ``reviewed_by``, and it must
survive any future run of this revision on any database. Widening to
``WHERE status = 'under_review'`` would be equivalent *now* and wrong the
first time it mattered, silently, on somebody's lab instance.

Why an event row per moved record
---------------------------------

``record_review_event`` is the append-only history of who changed what
when, and ``RecordReviewEventKind.status_change`` is exactly this event.
A migration that moved 1153 rows without writing there would leave the
audit log asserting those records had been ``under_review`` since deposit
and never moved -- the log would be wrong about the very thing it exists
to record. Every application-level status change writes an event; a
migration is not exempt because it is fast.

The event is also what makes the downgrade possible at all. It marks, per
row, that *this* revision moved *this* record, so the reverse can restore
exactly that set rather than guessing from a status that many rows share.
``actor_user_id`` is NULL because no human did this; the actor is named in
``details_json`` and ``reason`` instead.

What downgrade() can and cannot restore
---------------------------------------

**Can**: the status of exactly the rows this revision moved, read back
from the marker events rather than inferred. A row that was
``not_reviewed`` before the upgrade is not touched, because it has no
marker.

**Will not**: a row a curator has since moved *off* ``not_reviewed`` (they
picked it up, approved it, rejected it). Its marker still matches, but its
status is no longer ours to overwrite; the downgrade leaves it alone. That
is a deliberate refusal, not a gap.

**Cannot**: un-append the marker events. ``record_review_event`` is
append-only and the guard trigger refuses DELETE on ``record_review``
outright. The downgrade therefore *adds* a reverse ``status_change`` event
rather than erasing the forward one -- so a database that has been
upgraded and downgraded reads as two recorded transitions, which is what
actually happened.

No trigger is stood down. ``tckdb_guard_record_review`` permits this
update: the natural key is unchanged, ``first_approved_at`` is NULL on
both sides by the predicate, and ``not_reviewed`` is not an accepted-
science status, so no scientific record is locked.

Revision ID: c1d8f4a25b30
Revises: f3c8a1d7b492
Create Date: 2026-08-24

"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "c1d8f4a25b30"
down_revision: Union[str, Sequence[str], None] = "f3c8a1d7b492"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Written into ``record_review_event.details_json`` on every row this
#: revision touches, and matched with ``@>`` on the way back down. The
#: revision id is in the payload rather than only in ``reason`` so the
#: downgrade selects on an indexable containment test against structured
#: data, not on prose somebody may reword.
_MARKER_REVISION = "c1d8f4a25b30"

_UPGRADE_REASON = (
    "Backfill c1d8f4a25b30: deposited records were stamped under_review with "
    "no reviewer, no review timestamp and no approval. Corrected to "
    "not_reviewed, which is what was true."
)

_DOWNGRADE_REASON = (
    "Downgrade of c1d8f4a25b30: restoring the deposit-time under_review stamp "
    "this revision had corrected to not_reviewed."
)


def upgrade() -> None:
    op.get_bind().execute(
        text(
            """
            WITH moved AS (
                UPDATE record_review
                   SET status = 'not_reviewed'
                 WHERE status = 'under_review'
                   AND reviewed_by IS NULL
                   AND reviewed_at IS NULL
                   AND first_approved_at IS NULL
                RETURNING id
            )
            INSERT INTO record_review_event (
                record_review_id,
                event_kind,
                from_status,
                to_status,
                actor_user_id,
                reason,
                details_json
            )
            SELECT
                moved.id,
                'status_change',
                'under_review',
                'not_reviewed',
                NULL,
                CAST(:reason AS text),
                jsonb_build_object(
                    'migration', CAST(:revision AS text),
                    'direction', 'upgrade',
                    'actor', 'alembic'
                )
            FROM moved
            """
        ),
        {"reason": _UPGRADE_REASON, "revision": _MARKER_REVISION},
    )


def downgrade() -> None:
    op.get_bind().execute(
        text(
            """
            WITH restored AS (
                UPDATE record_review rr
                   SET status = 'under_review'
                 WHERE rr.status = 'not_reviewed'
                   AND EXISTS (
                       SELECT 1
                         FROM record_review_event ev
                        WHERE ev.record_review_id = rr.id
                          AND ev.event_kind = 'status_change'
                          AND ev.details_json @> jsonb_build_object(
                                  'migration', CAST(:revision AS text),
                                  'direction', 'upgrade'
                              )
                   )
                RETURNING rr.id
            )
            INSERT INTO record_review_event (
                record_review_id,
                event_kind,
                from_status,
                to_status,
                actor_user_id,
                reason,
                details_json
            )
            SELECT
                restored.id,
                'status_change',
                'not_reviewed',
                'under_review',
                NULL,
                CAST(:reason AS text),
                jsonb_build_object(
                    'migration', CAST(:revision AS text),
                    'direction', 'downgrade',
                    'actor', 'alembic'
                )
            FROM restored
            """
        ),
        {"reason": _DOWNGRADE_REASON, "revision": _MARKER_REVISION},
    )
