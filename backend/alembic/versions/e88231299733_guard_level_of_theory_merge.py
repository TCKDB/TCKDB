"""level_of_theory_merge: let the database refuse a chain, a loop, or a calculation on a merged row (#591)

``38b06819f099`` added ``level_of_theory_merge`` (``merged_lot_id`` ->
``into_lot_id``) with two rules stated in prose and enforced only by the merge
script: a merge is one hop (``into_lot_id`` is never itself merged), and no
calculation points at a merged row. The table accepted a chain A -> B -> C and
a loop A -> B -> A, and nothing stopped a ``calculation.lot_id`` from naming a
merged row. With a chain, ``canonical_level_of_theory_id`` resolves A to B, which is
itself merged, so a search by A's ref finds nothing.

What this revision writes
-------------------------
* DDL only: two functions and two triggers. No data is changed and no
  existing row is rejected (a trigger checks the rows it is fired for, not
  the rows already there). If a deployed database already holds a chain or a
  calculation on a merged row, the upgrade prints how many, so an operator
  knows; it does not fail, because failing would leave the database
  un-upgradable over a state the new rule exists to stop growing.

``trg_lot_merge_guard`` (BEFORE INSERT OR UPDATE on ``level_of_theory_merge``)
refuses a row when

* ``into_lot_id`` is itself a merged row (a chain, or a loop: A -> B then
  B -> A is a chain at the second insert);
* ``merged_lot_id`` is the target of another merge (the same chain, built from
  the other end: A -> B exists and B is now merged into C);
* a calculation still points at ``merged_lot_id`` (the merge script moves
  calculations first, then inserts the row, so it is unaffected).

``trg_calculation_lot_not_merged`` (BEFORE INSERT, or UPDATE OF ``lot_id``, on
``calculation``) refuses a ``lot_id`` that names a merged row. An update that
leaves ``lot_id`` as it was is not checked, so an existing calculation on a
merged row can still be approved or corrected.

Concurrency
-----------
Both triggers lock the level-of-theory rows they reason about (the merge guard
``FOR UPDATE``, lowest id first; the calculation guard ``FOR KEY SHARE``, the
lock the foreign key takes anyway) before they read. Under READ COMMITTED
(the PostgreSQL default, and the only isolation level this application uses)
each statement after the lock takes a fresh snapshot, so a merge and an
upload racing on one row serialise: the second to take the lock waits, then
sees what the first committed.

**This is not safe under REPEATABLE READ or SERIALIZABLE.** There the
transaction's snapshot predates the lock wait, so the second trigger does not
see the first's commit, and a calculation can be stranded on a merged row (or
the reverse). Nothing in this codebase writes at those levels; a future writer
that does must not rely on these triggers for that race.

Why triggers, not application code
----------------------------------
The rest of this codebase treats the database as the backstop for invariants
that many code paths could break (see the accepted-science guards). The merge
script is the only writer today; the point of the trigger is that a
hand-written row, a later script or a restored dump cannot break the rule
either. The application also follows a merge when an upload's hash meets a
merged row (``resolve_level_of_theory_ref``), so the calculation guard is a
backstop that should not fire in normal use.

Downgrade
---------
Drops both triggers and both functions. It does not touch
``level_of_theory_merge``; downgrading further, past ``38b06819f099``, drops
that table and forgets its merges. This downgrade prints how many merges the
table holds, so the operator sees what the next step down would forget.

Revision ID: e88231299733
Revises: c8424fe82997
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e88231299733"
down_revision: Union[str, Sequence[str], None] = "c8424fe82997"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.tckdb_lot_merge_guard() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            -- Lock both levels of theory, lowest id first, so two merges
            -- that meet in the middle (A -> B and B -> C) serialise, and
            -- so a calculation being inserted on either one has committed
            -- (and is visible below) or waits until this merge has.
            PERFORM 1 FROM public.level_of_theory
             WHERE id IN (NEW.merged_lot_id, NEW.into_lot_id)
             ORDER BY id
               FOR UPDATE;

            IF EXISTS (
                SELECT 1 FROM public.level_of_theory_merge m
                 WHERE m.merged_lot_id = NEW.into_lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a level of theory cannot be merged into one that is itself merged',
                    HINT = 'merge into the row the target was merged into; a merge is one hop';
            END IF;

            IF EXISTS (
                SELECT 1 FROM public.level_of_theory_merge m
                 WHERE m.into_lot_id = NEW.merged_lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a level of theory that other merges point at cannot itself be merged',
                    HINT = 're-aim those merges at the new target first; a merge is one hop';
            END IF;

            IF EXISTS (
                SELECT 1 FROM public.calculation c
                 WHERE c.lot_id = NEW.merged_lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a level of theory that calculations still use cannot be merged',
                    HINT = 'move its calculations to the kept row first';
            END IF;

            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_lot_merge_guard
        BEFORE INSERT OR UPDATE ON public.level_of_theory_merge
        FOR EACH ROW EXECUTE FUNCTION public.tckdb_lot_merge_guard()
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_calculation_lot_not_merged() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NEW.lot_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.lot_id IS NOT DISTINCT FROM OLD.lot_id THEN
                RETURN NEW;
            END IF;

            -- Take the lock the foreign key would take, first, so a merge
            -- committing on this row is either visible below or waits for us.
            PERFORM 1 FROM public.level_of_theory WHERE id = NEW.lot_id FOR KEY SHARE;

            IF EXISTS (
                SELECT 1 FROM public.level_of_theory_merge m
                 WHERE m.merged_lot_id = NEW.lot_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'a calculation cannot use a level of theory that was merged into another',
                    HINT = 'use the level of theory it was merged into';
            END IF;

            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_calculation_lot_not_merged
        BEFORE INSERT OR UPDATE OF lot_id ON public.calculation
        FOR EACH ROW EXECUTE FUNCTION public.tckdb_calculation_lot_not_merged()
        """
    )

    bind = op.get_bind()
    chains = bind.scalar(
        sa.text(
            "SELECT count(*) FROM level_of_theory_merge a "
            "JOIN level_of_theory_merge b ON b.merged_lot_id = a.into_lot_id"
        )
    )
    stranded = bind.scalar(
        sa.text(
            "SELECT count(*) FROM calculation c "
            "JOIN level_of_theory_merge m ON m.merged_lot_id = c.lot_id"
        )
    )
    print(
        "level_of_theory_merge guards installed: "
        f"{chains} existing merge chain link(s), "
        f"{stranded} existing calculation(s) on a merged row "
        "(existing rows are not rejected; both should be 0)."
    )


def downgrade() -> None:
    bind = op.get_bind()
    merges = bind.scalar(sa.text("SELECT count(*) FROM level_of_theory_merge"))
    op.execute("DROP TRIGGER IF EXISTS trg_calculation_lot_not_merged ON public.calculation")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_calculation_lot_not_merged()")
    op.execute("DROP TRIGGER IF EXISTS trg_lot_merge_guard ON public.level_of_theory_merge")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_lot_merge_guard()")
    print(
        f"level_of_theory_merge guards removed. The table holds {merges} merge(s); "
        "downgrading past 38b06819f099 drops the table and forgets them."
    )
