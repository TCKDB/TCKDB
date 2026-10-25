"""A declared statmech model's basin can be re-pointed by a declared repair, and nothing else about it can.

``e6b3a9d1f742`` made ``statmech_model_declaration``, ``statmech_model_member`` and ``statmech_model_source`` immutable by
an unconditional ``BEFORE UPDATE`` trigger (``tckdb_statmech_model_immutable``): "supersede the statmech record instead
of editing". That is right for every value a declaration states, and it is why a correction there is a new statmech
record.

It also stood in the way of one thing that is not a correction of a stated value. When the rule that decides which
conformer *basin* an observation belongs to changes (``torsion_basin`` version 2), the basin a declaration or a member
names can be filed under a different ``conformer_group`` row. Which row a basin is filed under is identity, not a
scientific value: it does not change the declared recipe, the members' energies or multiplicities, or the sources.
Owner decision (2026-10-26): extend the declared-repair mechanism of ``e2c9a4f7b163`` to that column on exactly those
two tables, and let a declaration's checksum follow it.

What this revision changes
--------------------------
Only the body of ``tckdb_statmech_model_immutable()``; its three triggers are untouched (they bind to the function by
OID). An UPDATE is permitted when **all** of these hold:

* the table is ``statmech_model_declaration`` or ``statmech_model_member`` (``statmech_model_source`` is refused
  outright, unconditionally);
* the changed columns are within ``{conformer_group_id}`` (a member) or ``{conformer_group_id, content_hash}`` (a
  declaration), and nothing else changed;
* if ``conformer_group_id`` changed, both the old and the new value are set and the new group belongs to the same
  species entry as the declaring statmech record (the test the insert guards of ``e6b3a9d1f742`` apply): a repair
  re-points a basin, it does not create, remove or move one across entries;
* if ``content_hash`` changed, this same transaction has already moved that declaration's basin or the basin of one of
  its members (read from the repair ledger, so the move is on the record next to the checksum that follows it): the
  checksum covers the declaration's basin and each member's basin, and may be rewritten only because one of them moved;
* an ``accepted_science_repair`` declaration for that table is in force in this transaction and covers the changed
  columns. For a row under an accepted statmech record the accepted-science guard that fires before this trigger has
  already required that and written the old -> new ledger row; for a row that was never accepted this trigger asks
  ``tckdb_repair_permits``, which checks the declaration and writes the same ledger row. Either way every change is
  recorded, with the old and new value of each changed column.

Anything else raises the same message as before. No trigger is disabled and none is weakened for any other column.

Downgrade restores the unconditional refusal.

Revision ID: a5d9c3e7b1f2
Revises: e2f2b3fb6725
Create Date: 2026-10-26
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a5d9c3e7b1f2"
down_revision: Union[str, Sequence[str], None] = "e2f2b3fb6725"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REPAIRABLE_SQL = """
CREATE OR REPLACE FUNCTION public.tckdb_statmech_model_immutable()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    changed_columns text[];
    root_statmech_id bigint;
    host_species bigint;
    allowed_columns text[];
BEGIN
    IF TG_OP = 'UPDATE' AND TG_TABLE_NAME IN ('statmech_model_declaration', 'statmech_model_member') THEN
        SELECT coalesce(array_agg(entry.key ORDER BY entry.key), ARRAY[]::text[])
          INTO changed_columns
          FROM jsonb_each(to_jsonb(OLD)) AS entry
         WHERE entry.value IS DISTINCT FROM (to_jsonb(NEW) -> entry.key);

        IF TG_TABLE_NAME = 'statmech_model_declaration' THEN
            allowed_columns := ARRAY['conformer_group_id', 'content_hash'];
            root_statmech_id := OLD.statmech_id;
        ELSE
            allowed_columns := ARRAY['conformer_group_id'];
            SELECT statmech_id INTO root_statmech_id
              FROM public.statmech_model_declaration WHERE id = OLD.declaration_id;
        END IF;

        IF cardinality(changed_columns) > 0 AND changed_columns <@ allowed_columns THEN
            SELECT species_entry_id INTO host_species FROM public.statmech WHERE id = root_statmech_id;

            -- A repair re-points an existing basin at a group of the record's own species entry.
            IF 'conformer_group_id' = ANY (changed_columns) AND (
                OLD.conformer_group_id IS NULL
                OR NEW.conformer_group_id IS NULL
                OR host_species IS NULL
                OR NOT EXISTS (
                    SELECT 1 FROM public.conformer_group g
                     WHERE g.id = NEW.conformer_group_id AND g.species_entry_id = host_species
                )
            ) THEN
                RAISE EXCEPTION '% is an immutable declared model: a basin repair must name a conformer group of the declaring record''s own species entry', TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            -- The checksum may follow a basin that moved in this transaction, and nothing else.
            IF 'content_hash' = ANY (changed_columns) AND NOT ('conformer_group_id' = ANY (changed_columns)) AND NOT EXISTS (
                SELECT 1
                  FROM public.accepted_science_repair_change c
                  JOIN public.accepted_science_repair r ON r.id = c.repair_id
                 WHERE r.xact_id = (pg_current_xact_id())::text::bigint
                   AND 'conformer_group_id' = ANY (c.changed_columns)
                   AND (
                        (c.target_table = 'statmech_model_declaration' AND (c.row_identity ->> 'id')::bigint = OLD.id)
                     OR (c.target_table = 'statmech_model_member' AND (c.row_identity ->> 'id')::bigint IN (
                            SELECT m.id FROM public.statmech_model_member m WHERE m.declaration_id = OLD.id))
                   )
            ) THEN
                RAISE EXCEPTION 'statmech_model_declaration is an immutable declared model: content_hash may only follow a basin moved in the same repair'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            -- Under an accepted statmech record the accepted-science guard (trg_as_*, which fires first) has already
            -- required a declaration covering these columns and recorded the change.
            IF public.tckdb_record_is_accepted(CAST('statmech' AS public.submission_record_type), root_statmech_id) THEN
                RETURN NEW;
            END IF;
            -- Otherwise ask the repair mechanism directly: it checks the declaration and records old -> new.
            IF public.tckdb_repair_permits(
                CAST('statmech' AS public.submission_record_type), root_statmech_id,
                'public', TG_TABLE_NAME, to_jsonb(OLD), to_jsonb(NEW)
            ) THEN
                RETURN NEW;
            END IF;
        END IF;
    END IF;
    RAISE EXCEPTION '% is an immutable declared model: supersede the statmech record instead of editing %', TG_TABLE_NAME, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
"""

_ORIGINAL_SQL = """
CREATE OR REPLACE FUNCTION public.tckdb_statmech_model_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is an immutable declared model: supersede the statmech record instead of editing %', TG_TABLE_NAME, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
"""


def upgrade() -> None:
    op.execute(_REPAIRABLE_SQL)


def downgrade() -> None:
    op.execute(_ORIGINAL_SQL)
