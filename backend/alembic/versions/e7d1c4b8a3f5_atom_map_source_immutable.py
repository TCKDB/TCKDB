"""Stop an inferred atom map from being relabelled as a declared one.

``d3a7f1c9b284`` made half of ADR 0011's inference contract structural:
``ck_reaction_atom_map_inferred_requires_note`` holds
``inferred ⇒ note IS NOT NULL``, so an inferred map cannot be anonymous about
the algorithm that produced it. The other half — that an inferred map is never
*presented as though a human asserted it* — was not held by anything.

A ``CHECK`` constrains one row against itself, and it can only see the row it
is handed. It therefore has nothing to say about a row *changing*::

    UPDATE reaction_atom_map SET source = 'declared', note = NULL WHERE id = 1;

Both halves of that statement satisfy the check as written — ``declared`` does
not require a note — so it commits, and the row now reads back as a
depositor's own statement about a mechanism that was in fact produced by an
algorithm. The map's atom indices are unchanged; only the claim about who
stands behind them has moved, and it has moved in the one direction ADR 0011
says it must never move. Nothing in the record afterwards distinguishes it from
a map somebody followed an IRC to obtain.

Why this matters when there is no API route that does it
--------------------------------------------------------
There is no update path: the atom map is written once by
``app.services.reaction_atom_map.persist_reaction_atom_map`` and never edited,
so the statement above is reachable only from a ``psql`` prompt or a future
write path nobody has written yet. That is precisely the audience the
accepted-science immutability triggers in ``c6f2a9d4e7b1`` were installed for,
and the argument ADR 0010 makes for the trigger in ``f9b2e6c4a1d7``: a
guarantee that holds only because the one wired write path happens not to
exercise the hole is not a guarantee about the record, it is a guarantee about
today's code. ADR 0011 is explicit that inference is permitted **only as a
labelled and separable thing**, and a label that can be quietly removed is not
a label.

What is made immutable, and what is not
---------------------------------------
``source`` alone. Once a map's provenance token is written it cannot change in
either direction, because both directions are misattributions: ``inferred →
declared`` manufactures human authority, and ``declared → inferred`` withdraws
an attribution the depositor did make. ADR 0011 does not offer a re-labelling
procedure for either, and correcting a genuinely mis-declared map means
deleting it and depositing the real one, which stays available — this trigger
constrains ``UPDATE`` only.

``note`` deliberately stays mutable, so a depositor can correct a typo in an
algorithm name or add detail later. It cannot be *removed* from an inferred
map, because ``source`` can no longer move away from ``inferred`` and
``ck_reaction_atom_map_inferred_requires_note`` still refuses a NULL note
there. Between the frozen token and the existing check, the laundering path is
closed without freezing the whole row.

``equivalent_map_count`` likewise stays mutable: it is a symmetry count the
depositor may legitimately learn later, and ADR 0011 says ``NULL`` is "no claim
made" rather than a claim of 1, so filling it in is completing a record rather
than changing what it says.

Why a trigger rather than a ``CHECK``
--------------------------------------
A ``CHECK`` cannot see ``OLD``. Comparing a row against its previous value is
what a ``BEFORE UPDATE`` row trigger is for, and this one is not deferred: it
needs no other table, so there is nothing to wait for, and failing at the
statement gives an operator the offending id immediately.

Backfill design: none is possible or needed. The rule constrains transitions,
not states, so no stored row can violate it — every ``reaction_atom_map`` row
that exists is, trivially, still carrying the ``source`` it was written with.
The tables were introduced in ``d3a7f1c9b284``, which is not yet applied to any
deployed database, so there are no rows anywhere in any case.

``downgrade()`` drops the trigger and its function. It needs no guard:
removing a rule about future updates cannot invalidate a stored row.

Revision ID: e7d1c4b8a3f5
Revises: d3a7f1c9b284
Create Date: 2026-08-05
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e7d1c4b8a3f5"
down_revision: Union[str, Sequence[str], None] = "d3a7f1c9b284"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FUNCTION_NAME = "tckdb_reaction_atom_map_source_immutable"
TRIGGER_NAME = "trg_reaction_atom_map_source_immutable"

_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION public.{FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.source IS DISTINCT FROM OLD.source THEN
        RAISE EXCEPTION
            'atom_map_source_is_immutable: reaction_atom_map % was written '
            'with source=''%'' and cannot be changed to ''%''. An atom map '
            'records who stands behind a mechanism claim: relabelling an '
            'inferred map as declared manufactures human attribution, and '
            'relabelling a declared map as inferred withdraws one the '
            'depositor made (ADR 0011).',
            OLD.id, OLD.source, NEW.source
            USING ERRCODE = '23514',
                  HINT = 'To correct a genuinely mis-declared map, delete it '
                         'and deposit the real one. Its note and '
                         'equivalent_map_count remain editable in place.';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    """Freeze ``reaction_atom_map.source`` against in-place relabelling."""
    op.execute(_FUNCTION_SQL)
    op.execute(
        f"CREATE TRIGGER {TRIGGER_NAME} "
        "BEFORE UPDATE ON public.reaction_atom_map "
        f"FOR EACH ROW EXECUTE FUNCTION public.{FUNCTION_NAME}()"
    )


def downgrade() -> None:
    """Drop the trigger; no stored row can be invalidated by removing it."""
    op.execute(
        f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON public.reaction_atom_map"
    )
    op.execute(f"DROP FUNCTION IF EXISTS public.{FUNCTION_NAME}()")
