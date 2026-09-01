"""Backfill thermo.model_kind='nasa7' for rows that fit but never said so.

Measured on the deployed archive: 65 ``thermo`` rows carry a ``thermo_nasa``
row (a complete NASA-7 fit) and exactly nine ``thermo_point`` rows apiece.
Only 21 of them have ``model_kind = 'nasa7'``. The other 44 have
``model_kind IS NULL`` -- structurally identical to the 21, just never
labelled. This is not random: the 44 fall exactly along the second of two
disjoint thermo populations found while investigating #284 (zero source
calculations, a ``software_release_id``, deposited 2026-07-21 through
2026-08-05) versus the first (21 source calculations, no
``software_release_id``, deposited 2026-07-21 only). Two producers or two
code paths, and ``model_kind`` is a fingerprint of which one wrote the row.

The fit's presence is the evidence
-----------------------------------
The predicate is::

    thermo.model_kind IS NULL
    AND EXISTS (SELECT 1 FROM thermo_nasa WHERE thermo_nasa.thermo_id = thermo.id)

No inference beyond that. A row is only touched because the coefficients
that make it a NASA-7 fit are already sitting in ``thermo_nasa`` -- the
label is being written down, not guessed. ``thermo_wilhoit`` and
``thermo_nasa9_interval`` are both empty on the deployed archive today; if
either ever holds a row whose ``thermo`` parent has a NULL ``model_kind``,
this predicate does not touch it, because there is no ``thermo_nasa`` row to
justify ``'nasa7'`` and no other value is being guessed at. Only
``thermo.model_kind`` moves. ``thermo_nasa``, ``thermo_point``, and every
other column are untouched.

Idempotent: a second run selects nothing, because every row it would have
touched now has ``model_kind IS NOT NULL``.

Why this declares a repair
---------------------------
``thermo`` is an accepted-science root (``c6f2a9d4e7b1``,
``trg_as_root_thermo``): an UPDATE on a row that has ever been approved is
refused unconditionally, without a declaration in ``accepted_science_repair``
(``e2c9a4f7b163``). On the deployed archive none of the 44 has been approved
(machine review on thermo runs on ``model_kind`` itself among other fields,
and human approval has not happened), so today the declaration is inert and
every one of these UPDATEs would have succeeded even without it. It is
declared anyway, for the operator-managed instance where that is not true --
so that instance gets a recorded repair instead of a failed migration
partway through. This is not a correction of a scientific claim: no number a
consumer reads changes, only whether a label the row already earns is
present. ``declared_columns`` names exactly ``model_kind``, and the guard
enforces that boundary: an UPDATE that touched anything else on ``thermo``
would be refused by name, repair or no repair.

The ledger, and why the downgrade needs it
-------------------------------------------
After this upgrade, a repaired row is **structurally indistinguishable**
from one of the 21 that always said ``nasa7`` -- same ``thermo_nasa`` row,
same nine ``thermo_point`` rows, same ``model_kind``. A downgrade that
re-derived its target set from that shape ("null out every ``nasa7`` row
that has a fit") would null all 65, wiping the 21 that were correct from the
day they were written. That is exactly the trap ``b8e3f1a7c250``'s brief
names: *"any migration whose downgrade needs 'find the rows that look like
the ones I changed' is describing rows it can no longer identify."*

So the downgrade does not re-derive anything. ``tckdb_repair_permits``
writes an ``accepted_science_repair_change`` row per changed row when the
guard runs (i.e. for any of the 44 that turns out to be approved); this
revision writes the rest itself, under the same declaration and in the same
transaction, so the ledger is complete regardless of approval state. The
downgrade reads it back by primary key -- rows whose ``before_json ->
'model_kind'`` is JSON null identify the upgrade's own change rows, the same
test ``b8e3f1a7c250`` uses to tell a forward change from a reverse one
without a direction column -- and nulls exactly those, and only if the row
still carries what the upgrade set (a curator who has since set a different
``model_kind`` owns that value now; this leaves it alone).

No schema change. Both tables the repair mechanism uses
(``accepted_science_repair``, ``accepted_science_repair_change``) already
exist as of ``e2c9a4f7b163``; this revision only inserts into them and
updates ``thermo.model_kind``.

Revision ID: 53b1e7dece9d
Revises: a4f7c2e9d651
Create Date: 2026-09-01
"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "53b1e7dece9d"
down_revision: Union[str, Sequence[str], None] = "2c26fb2a75a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The one column either direction of this revision may write. The guard
#: compares OLD against NEW and refuses an UPDATE that touches anything
#: else, so this is enforced rather than promised.
_DECLARED_COLUMN = "model_kind"

_UPGRADE_REASON = (
    "Backfill 53b1e7dece9d: 44 thermo rows carry a complete NASA-7 fit in "
    "thermo_nasa (and nine thermo_point rows apiece) but model_kind was "
    "never written -- a second producer/code path than the 21 rows that do "
    "say nasa7. Set model_kind='nasa7' wherever a thermo_nasa row exists and "
    "model_kind IS NULL. No other column, and no row lacking a thermo_nasa "
    "row, is touched."
)

_DOWNGRADE_REASON = (
    "Downgrade of 53b1e7dece9d: restoring the NULL model_kind this revision "
    "had backfilled, for exactly the rows it wrote."
)


def _declare_repair(reason: str) -> None:
    """Stand the accepted-science guard down for one column, on the record.

    Inert wherever no affected row has ever been approved -- which is every
    row on the deployed database today. It exists so that an instance where
    one *has* been approved gets a recorded repair instead of a failed
    migration.
    """
    op.get_bind().execute(
        text(
            """
            INSERT INTO accepted_science_repair (
                target_table, declared_columns, alembic_revision, reason
            ) VALUES (
                'thermo',
                ARRAY[CAST(:column AS text)],
                CAST(:revision AS text),
                CAST(:reason AS text)
            )
            """
        ),
        {"column": _DECLARED_COLUMN, "revision": revision, "reason": reason},
    )


def upgrade() -> None:
    bind = op.get_bind()
    _declare_repair(_UPGRADE_REASON)
    bind.execute(
        text(
            """
            WITH repaired AS (
                UPDATE thermo
                   SET model_kind = CAST('nasa7' AS thermo_model_kind)
                  FROM thermo_nasa
                 WHERE thermo_nasa.thermo_id = thermo.id
                   AND thermo.model_kind IS NULL
             RETURNING thermo.id AS thermo_id
            )
            INSERT INTO accepted_science_repair_change (
                repair_id, record_type, record_id, target_schema, target_table,
                row_identity, changed_columns, before_json, after_json
            )
            SELECT
                declaration.id,
                'thermo',
                repaired.thermo_id,
                'public',
                'thermo',
                jsonb_build_object('id', repaired.thermo_id),
                ARRAY[CAST(:column AS text)],
                jsonb_build_object(CAST(:column AS text), NULL),
                jsonb_build_object(CAST(:column AS text), 'nasa7')
              FROM repaired
              CROSS JOIN (
                    SELECT id
                      FROM accepted_science_repair
                     WHERE target_table = 'thermo'
                       AND xact_id = (pg_current_xact_id())::text::bigint
                   ) AS declaration
             -- The guard already recorded any row that was accepted; this
             -- fills in the rest, so the ledger covers every row written.
             WHERE NOT tckdb_record_is_accepted(
                       CAST('thermo' AS submission_record_type),
                       repaired.thermo_id
                   )
            """
        ),
        {"column": _DECLARED_COLUMN},
    )


def downgrade() -> None:
    bind = op.get_bind()
    _declare_repair(_DOWNGRADE_REASON)
    bind.execute(
        text(
            """
            WITH written AS (
                SELECT DISTINCT change.record_id AS thermo_id
                  FROM accepted_science_repair_change AS change
                  JOIN accepted_science_repair AS declaration
                    ON declaration.id = change.repair_id
                 WHERE declaration.alembic_revision = CAST(:revision AS text)
                   AND change.target_table = 'thermo'
                   AND change.record_type = 'thermo'
                   -- A null "before" is an upgrade's change row; the
                   -- downgrade's own rows have a null "after". No direction
                   -- column needed, and nothing keys off prose.
                   AND change.before_json -> CAST(:column AS text) = 'null'::jsonb
            ),
            restored AS (
                UPDATE thermo
                   SET model_kind = NULL
                  FROM written
                 WHERE thermo.id = written.thermo_id
                   -- Only if the row still carries what the upgrade set. A
                   -- curator who has since set a different model_kind owns
                   -- that value now, and this leaves it alone.
                   AND thermo.model_kind = CAST('nasa7' AS thermo_model_kind)
             RETURNING thermo.id AS thermo_id
            )
            INSERT INTO accepted_science_repair_change (
                repair_id, record_type, record_id, target_schema, target_table,
                row_identity, changed_columns, before_json, after_json
            )
            SELECT
                declaration.id,
                'thermo',
                restored.thermo_id,
                'public',
                'thermo',
                jsonb_build_object('id', restored.thermo_id),
                ARRAY[CAST(:column AS text)],
                jsonb_build_object(CAST(:column AS text), 'nasa7'),
                jsonb_build_object(CAST(:column AS text), NULL)
              FROM restored
              CROSS JOIN (
                    SELECT id
                      FROM accepted_science_repair
                     WHERE target_table = 'thermo'
                       AND xact_id = (pg_current_xact_id())::text::bigint
                   ) AS declaration
             WHERE NOT tckdb_record_is_accepted(
                       CAST('thermo' AS submission_record_type),
                       restored.thermo_id
                   )
            """
        ),
        {"column": _DECLARED_COLUMN, "revision": revision},
    )
