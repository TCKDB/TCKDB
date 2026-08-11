"""Declare a repair before making it, and let the database check the claim.

``c6f2a9d4e7b1`` made every accepted scientific record immutable in the
database, and ``b6c1f4a8e703`` and ``a1f6c3e9b527`` extended that regime to
twelve more tables. ADR 0003 is the position it enforces and it is the right
one: a corrected *number* forks identity, because a citation has to keep
resolving to what was cited.

It left one thing with no route through it. A repair that changes no science
-- the letter case of an element symbol in a derived index column, with
``geom_hash``, ``xyz_text``, coordinates, isotopes and atom ordering provably
untouched -- is not a correction of a scientific claim, and forking identity
for it re-keys a citable public ref to fix a shift key. ``b4e7c1d20f83`` is
the worked example: it canonicalises ``geometry_atom.element``, it deliberately
leaves ``trg_as_geometry_atom`` standing, and it therefore fails loudly on any
deployment holding a non-canonical row under an accepted calculation. That is
the correct outcome for a migration that cannot ask anybody. It is not an
answer for the operator who *has* such a row.

The only mechanical option that operator had was ``ALTER TABLE ... DISABLE
TRIGGER``, the work, and ``ENABLE`` -- which the first draft of #118 did and
review rejected, correctly. After the fact that operation is invisible.
Nothing records that the guard was stood down, over which rows, or on what
justification, and the claim "the record did not change scientifically" lived
in a migration docstring, which is prose rather than an assertion.

What this revision adds is not permission. It is a record, and a check.

A repair is declared before it is made
--------------------------------------
``accepted_science_repair`` holds one row per repair: the table it will
change, the exact set of columns it may change, the Alembic revision doing it,
and why. ``accepted_science_repair_change`` holds one row per accepted record
per changed row: which root, which row (by primary key), which columns, and
the values before and after.

While a declaration is in force, ``tckdb_raise_if_accepted`` permits an UPDATE
to the declared table -- **only if the columns that actually changed are a
subset of the declared set**. That is a comparison of OLD against NEW inside a
``BEFORE UPDATE`` trigger, so it is enforced rather than promised: an UPDATE
that touches one undeclared column is refused by name, and the whole
transaction with it. A declaration for ``geometry_atom(element)`` cannot move
an atom.

The check is column-level and not row-level, deliberately: a repair of this
kind is corpus-wide by nature and enumerating rows in advance would be a
worse-founded claim than enumerating columns. What makes that acceptable is
that every row it reaches is recorded individually.

Only UPDATE. INSERT and DELETE under an accepted root stay unconditionally
refused, because adding or removing a row changes what reviewers accepted
rather than how it is spelled. The row count under an accepted root therefore
remains invariant under every repair this mechanism can express.

It cannot be left open
----------------------
Scope is the transaction. ``xact_id`` defaults to ``pg_current_xact_id()`` and
the guard matches on it, so a declaration is inert the instant its transaction
ends -- there is no close step to forget, no flag to reset, and a committed
row can never be reused. A second, independent bound is wall-clock:
``expires_at`` defaults to one hour out and is capped at 24, and an expired
declaration raises rather than silently permitting, so a wedged transaction
cannot hold the door open either. The two timestamps are ``timestamptz``
rather than the naive timestamps used elsewhere in this schema, because an
expiry compared against ``clock_timestamp()`` must not depend on the session's
``TimeZone``.

At most one declaration per table per transaction, enforced on insert. Two
would make "the declaration in force" a question rather than a fact.

It confers no authority anybody lacked
--------------------------------------
Only a role that owns the target table may declare a repair to it
(``pg_has_role(current_user, relowner, 'USAGE')``, which superusers pass). That
is exactly the set of roles that could already have run ``ALTER TABLE ...
DISABLE TRIGGER``. So this adds no power to anyone; it converts an
already-available and unrecorded capability into a recorded one, which is the
entire argument for building it. In the deployment posture of
``backend/docs/deployment/database_roles.md`` the runtime role owns nothing and
is refused here for the same reason it is refused there.

The same owner test guards ``accepted_science_repair_change``, which is what
stops the *record* being forged rather than the repair. The runtime role holds
blanket ``INSERT`` on tables in ``public`` -- granted by the default privileges
in ``configure_database_roles.py``, which run before this table exists and so
cannot exempt it -- and a forged audit row is exactly the failure this table
is built against. So the guarantee is carried entirely by triggers and depends
on no grant: a change row must name a declaration made in the *same*
transaction, for the same table, with columns the declaration covers, and be
inserted by an owner of that table. Every one of those holds by construction
when ``tckdb_repair_permits`` writes it.

Both tables are append-only and un-truncatable, on the same terms as
``record_review_event`` and ``scientific_record_supersession``: a record of a
repair that the repairer can edit afterwards is not a record.

A declaration must name something that is actually guarded
----------------------------------------------------------
The insert-time validation refuses a target that is not a table, that carries
no ``trg_as_*`` guard, that lacks a primary key, or that does not have every
declared column. The first three would each produce a row that looks like a
control and is not: a declaration against an unguarded table permits nothing
because nothing was refusing, and a table with no primary key cannot have its
changed rows named in the record, which is the only reason the permission is
defensible.

It also refuses a declaration that names a primary-key column, for the second
half of that reason: the change record identifies the row by its primary key,
so a repair that rewrote one could not say which row it changed -- and a row
under a new identity is a different row, which is what supersession is for.
Neither ``geometry_atom.element`` nor ``reaction_atom_map_pair.element``, the
motivating columns, is part of its table's key.

What an auditor can reconstruct
-------------------------------
For any repair: the declaring role and login, the transaction, the revision,
the stated reason, the declared columns -- and then, per accepted root and per
row, the primary key of the row, the columns that changed, and their values on
both sides. "The record did not change scientifically" stops being a docstring
and becomes a join. The completeness property that makes it an audit rather
than a sample: under an accepted root, an UPDATE either raises or appends a
change row. There is no third outcome.

Not touched
-----------
``record_review`` and its guard are outside this mechanism; approval history
is not repairable. ``b4e7c1d20f83`` is not retrofitted -- it is deployed, and
its argument for leaving the guard standing is unchanged. Nothing in this
revision repairs anything; it is the path the next repair takes.

Revision ID: e2c9a4f7b163
Revises: a1f6c3e9b527
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e2c9a4f7b163"
down_revision: Union[str, Sequence[str], None] = "a1f6c3e9b527"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The trigger functions that constitute the accepted-science regime. A repair
#: may only be declared against a table one of these is attached to.
_GUARD_FUNCTIONS = (
    "tckdb_guard_accepted_root",
    "tckdb_guard_accepted_child",
    "tckdb_guard_accepted_via_child",
    "tckdb_guard_calculation_geometry",
)


def _create_tables() -> None:
    op.create_table(
        "accepted_science_repair",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "target_schema",
            sa.Text(),
            server_default=sa.text("'public'"),
            nullable=False,
        ),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column(
            "declared_columns",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
        ),
        sa.Column("alembic_revision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "declared_by_role",
            sa.Text(),
            server_default=sa.text("CURRENT_USER"),
            nullable=False,
        ),
        sa.Column(
            "declared_by_login",
            sa.Text(),
            server_default=sa.text("SESSION_USER"),
            nullable=False,
        ),
        sa.Column(
            "xact_id",
            sa.BigInteger(),
            server_default=sa.text("((pg_current_xact_id())::text)::bigint"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp() + interval '1 hour'"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accepted_science_repair")),
        sa.CheckConstraint(
            "length(btrim(target_schema)) > 0 AND length(btrim(target_table)) > 0",
            name=op.f("ck_accepted_science_repair_target_nonblank"),
        ),
        sa.CheckConstraint(
            "cardinality(declared_columns) > 0 AND array_position(declared_columns, NULL) IS NULL",
            name=op.f("ck_accepted_science_repair_columns_declared"),
        ),
        sa.CheckConstraint(
            "length(btrim(alembic_revision)) > 0",
            name=op.f("ck_accepted_science_repair_revision_nonblank"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name=op.f("ck_accepted_science_repair_reason_nonblank"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '24 hours'",
            name=op.f("ck_accepted_science_repair_expiry_window"),
        ),
        schema="public",
    )
    op.create_index(
        "ix_accepted_science_repair_target",
        "accepted_science_repair",
        ["target_schema", "target_table", "xact_id"],
        unique=False,
        schema="public",
    )

    op.create_table(
        "accepted_science_repair_change",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("repair_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "record_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=False,
        ),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("target_schema", sa.Text(), nullable=False),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column("row_identity", postgresql.JSONB(), nullable=False),
        sa.Column("changed_columns", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("before_json", postgresql.JSONB(), nullable=False),
        sa.Column("after_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accepted_science_repair_change")),
        sa.ForeignKeyConstraint(
            ["repair_id"],
            ["public.accepted_science_repair.id"],
            name="fk_repair_change_repair",
        ),
        sa.CheckConstraint(
            "cardinality(changed_columns) > 0",
            name=op.f("ck_accepted_science_repair_change_columns_changed"),
        ),
        schema="public",
    )
    op.create_index(
        "ix_accepted_science_repair_change_repair_id",
        "accepted_science_repair_change",
        ["repair_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_accepted_science_repair_change_record",
        "accepted_science_repair_change",
        ["record_type", "record_id"],
        unique=False,
        schema="public",
    )


def _create_functions() -> None:
    guard_functions = ", ".join(f"'{name}'" for name in _GUARD_FUNCTIONS)

    # Split out of ``tckdb_raise_if_accepted`` so the guards can ask the cheap
    # question first. ``to_jsonb(OLD)`` and ``to_jsonb(NEW)`` are function
    # arguments, which PL/pgSQL evaluates eagerly, so passing them
    # unconditionally would serialise both row versions on *every* update to
    # every guarded table -- including ``calc_hessian``'s packed matrix -- to
    # answer a question that is almost always "no".
    op.execute(
        """
        CREATE FUNCTION public.tckdb_record_is_accepted(
            p_type public.submission_record_type, p_record_id bigint
        ) RETURNS boolean
        LANGUAGE sql STABLE
        SET search_path = pg_catalog, public
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = p_type AND record_id = p_record_id
                  AND first_approved_at IS NOT NULL
            )
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION public.tckdb_validate_accepted_science_repair()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            target_oid oid;
            target_owner oid;
            unknown_columns text[];
        BEGIN
            SELECT relation.oid, relation.relowner
              INTO target_oid, target_owner
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = NEW.target_schema
               AND relation.relname = NEW.target_table
               AND relation.relkind IN ('r', 'p');
            IF target_oid IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair target %I.%I is not a table',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            -- A declaration against a table nothing guards permits nothing,
            -- and would read afterwards as though a control had been stood
            -- down when none was ever in the way.
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_trigger AS guard
                  JOIN pg_proc AS guard_function ON guard_function.oid = guard.tgfoid
                 WHERE guard.tgrelid = target_oid
                   AND NOT guard.tgisinternal
                   AND guard_function.proname IN ({guard_functions})
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair target %I.%I carries no accepted-science guard',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            IF EXISTS (
                SELECT 1
                  FROM unnest(NEW.declared_columns) AS column_name
                 GROUP BY column_name
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'repair declares the same column twice';
            END IF;

            SELECT array_agg(column_name ORDER BY column_name)
              INTO unknown_columns
              FROM unnest(NEW.declared_columns) AS column_name
             WHERE NOT EXISTS (
                SELECT 1
                  FROM pg_attribute AS attribute
                 WHERE attribute.attrelid = target_oid
                   AND attribute.attname = column_name
                   AND attribute.attnum > 0
                   AND NOT attribute.attisdropped
             );
            IF unknown_columns IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '42703',
                    MESSAGE = format(
                        'repair declares column(s) %s that %I.%I does not have',
                        array_to_string(unknown_columns, ', '),
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            -- Every changed row is recorded by primary key. A table without
            -- one cannot be audited, so it cannot be repaired either.
            IF NOT EXISTS (
                SELECT 1 FROM pg_index AS index_
                 WHERE index_.indrelid = target_oid AND index_.indisprimary
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair target %I.%I has no primary key',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            -- ...and for the same reason, the key itself is off limits. The
            -- change record names the row by its primary key, so a repair
            -- that rewrote one could not say which row it changed -- and a
            -- row under a new identity is a different row, which is what
            -- supersession is for.
            SELECT array_agg(attribute.attname ORDER BY attribute.attname)
              INTO unknown_columns
              FROM pg_index AS index_
              JOIN pg_attribute AS attribute
                ON attribute.attrelid = index_.indrelid
               AND attribute.attnum = ANY (index_.indkey)
             WHERE index_.indrelid = target_oid
               AND index_.indisprimary
               AND attribute.attname = ANY (NEW.declared_columns);
            IF unknown_columns IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = format(
                        'repair declares primary-key column(s) %s of %I.%I',
                        array_to_string(unknown_columns, ', '),
                        NEW.target_schema, NEW.target_table
                    ),
                    HINT = 'A row under a new identity is a different row; record a supersession.';
            END IF;

            -- Exactly the roles that could already have run
            -- ALTER TABLE ... DISABLE TRIGGER. This grants nothing new; it
            -- makes an existing capability recordable.
            IF NOT pg_catalog.pg_has_role(current_user, target_owner, 'USAGE') THEN
                RAISE EXCEPTION USING ERRCODE = '42501',
                    MESSAGE = format(
                        'only an owner of %I.%I may declare a repair to it',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;

            IF NEW.xact_id <> (pg_current_xact_id())::text::bigint THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'a repair declaration cannot name another transaction';
            END IF;

            IF EXISTS (
                SELECT 1 FROM public.accepted_science_repair
                 WHERE target_schema = NEW.target_schema
                   AND target_table = NEW.target_table
                   AND xact_id = NEW.xact_id
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'a repair to %I.%I is already declared in this transaction',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION public.tckdb_validate_accepted_science_repair_change()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            declaration public.accepted_science_repair%ROWTYPE;
            target_owner oid;
        BEGIN
            SELECT * INTO declaration
              FROM public.accepted_science_repair
             WHERE id = NEW.repair_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION USING ERRCODE = '23503',
                    MESSAGE = 'repair change names no declaration';
            END IF;
            IF declaration.xact_id <> (pg_current_xact_id())::text::bigint THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'a repair change may only be recorded in the transaction that declared it';
            END IF;
            IF NEW.target_schema IS DISTINCT FROM declaration.target_schema
               OR NEW.target_table IS DISTINCT FROM declaration.target_table THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'repair change names a different table than its declaration';
            END IF;
            IF NOT (NEW.changed_columns <@ declaration.declared_columns) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'repair change names columns its declaration does not cover';
            END IF;

            SELECT relation.relowner
              INTO target_owner
              FROM pg_class AS relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = NEW.target_schema
               AND relation.relname = NEW.target_table;
            IF target_owner IS NULL
               OR NOT pg_catalog.pg_has_role(current_user, target_owner, 'USAGE') THEN
                RAISE EXCEPTION USING ERRCODE = '42501',
                    MESSAGE = format(
                        'only an owner of %I.%I may record a repair change to it',
                        NEW.target_schema, NEW.target_table
                    );
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        """
        CREATE FUNCTION public.tckdb_repair_permits(
            p_type public.submission_record_type,
            p_record_id bigint,
            p_schema text,
            p_table text,
            p_old jsonb,
            p_new jsonb
        ) RETURNS boolean
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            declaration public.accepted_science_repair%ROWTYPE;
            changed_columns text[];
            undeclared_columns text[];
            row_identity jsonb;
        BEGIN
            -- INSERT and DELETE are never repairable: p_old or p_new is NULL
            -- for them, and a repair may not change how many rows an accepted
            -- record is made of.
            IF p_old IS NULL OR p_new IS NULL OR p_table IS NULL THEN
                RETURN false;
            END IF;

            SELECT * INTO declaration
              FROM public.accepted_science_repair
             WHERE target_schema = p_schema
               AND target_table = p_table
               AND xact_id = (pg_current_xact_id())::text::bigint;
            IF NOT FOUND THEN
                RETURN false;
            END IF;
            IF clock_timestamp() >= declaration.expires_at THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'repair declaration %s for %I.%I expired at %s',
                        declaration.id, p_schema, p_table, declaration.expires_at
                    ),
                    HINT = 'A declaration is not a standing permission; declare the repair again.';
            END IF;

            SELECT coalesce(array_agg(entry.key ORDER BY entry.key), ARRAY[]::text[])
              INTO changed_columns
              FROM jsonb_each(p_old) AS entry
             WHERE entry.value IS DISTINCT FROM (p_new -> entry.key);

            SELECT array_agg(column_name ORDER BY column_name)
              INTO undeclared_columns
              FROM unnest(changed_columns) AS column_name
             WHERE NOT (column_name = ANY (declaration.declared_columns));
            IF undeclared_columns IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'repair declaration %s for %I.%I does not declare column(s) %s',
                        declaration.id, p_schema, p_table,
                        array_to_string(undeclared_columns, ', ')
                    ),
                    HINT = 'A repair may only change the columns it declared.';
            END IF;
            IF cardinality(changed_columns) = 0 THEN
                RETURN true;
            END IF;

            SELECT jsonb_object_agg(attribute.attname, p_old -> attribute.attname)
              INTO row_identity
              FROM pg_index AS index_
              JOIN pg_attribute AS attribute
                ON attribute.attrelid = index_.indrelid
               AND attribute.attnum = ANY (index_.indkey)
             WHERE index_.indrelid = format('%I.%I', p_schema, p_table)::regclass
               AND index_.indisprimary;
            IF row_identity IS NULL THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'cannot record a repair to %I.%I: it has no primary key',
                        p_schema, p_table
                    );
            END IF;

            INSERT INTO public.accepted_science_repair_change (
                repair_id, record_type, record_id, target_schema, target_table,
                row_identity, changed_columns, before_json, after_json
            ) VALUES (
                declaration.id, p_type, p_record_id, p_schema, p_table,
                row_identity, changed_columns,
                (
                    SELECT coalesce(jsonb_object_agg(name, p_old -> name), '{}'::jsonb)
                      FROM unnest(changed_columns) AS name
                ),
                (
                    SELECT coalesce(jsonb_object_agg(name, p_new -> name), '{}'::jsonb)
                      FROM unnest(changed_columns) AS name
                )
            );
            RETURN true;
        END;
        $$
        """
    )

    # Replaces c6f2a9d4e7b1's two-argument version. It stays the single owner
    # of both the acceptance question and the immutability message; what it
    # gains is the row context needed to consult a declaration. The extra
    # arguments default to NULL so a caller that supplies none gets exactly the
    # old behaviour.
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_raise_if_accepted(public.submission_record_type, bigint)")
    op.execute(
        """
        CREATE FUNCTION public.tckdb_raise_if_accepted(
            p_type public.submission_record_type,
            p_record_id bigint,
            p_schema text DEFAULT NULL,
            p_table text DEFAULT NULL,
            p_old jsonb DEFAULT NULL,
            p_new jsonb DEFAULT NULL
        ) RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF NOT public.tckdb_record_is_accepted(p_type, p_record_id) THEN
                RETURN;
            END IF;
            IF public.tckdb_repair_permits(
                p_type, p_record_id, p_schema, p_table, p_old, p_new
            ) THEN
                RETURN;
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = format(
                    'accepted %s record %s is immutable', p_type, p_record_id
                ),
                HINT = 'Create and approve a replacement, then record a supersession. '
                       'A repair that changes no science must first declare its table '
                       'and columns in accepted_science_repair.';
        END;
        $$
        """
    )


def _replace_guards() -> None:
    """Hand each guard the row context ``tckdb_raise_if_accepted`` now takes.

    Bodies are otherwise unchanged from ``c6f2a9d4e7b1``. The triggers bind to
    the function by OID, so replacing the body is enough and no trigger is
    recreated here.
    """

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_root()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE record_id bigint;
        BEGIN
            IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
            record_id := OLD.id;
            PERFORM public.tckdb_lock_scientific_record(
                TG_ARGV[0]::public.submission_record_type, record_id
            );
            IF public.tckdb_record_is_accepted(
                TG_ARGV[0]::public.submission_record_type, record_id
            ) THEN
                PERFORM public.tckdb_raise_if_accepted(
                    TG_ARGV[0]::public.submission_record_type, record_id,
                    TG_TABLE_SCHEMA, TG_TABLE_NAME,
                    CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                    CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                );
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_calculation_geometry()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE geometry_ids bigint[] := ARRAY[]::bigint[]; calculation_id bigint;
        BEGIN
            IF TG_TABLE_NAME = 'geometry' THEN
                IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
                IF TG_OP = 'DELETE' THEN
                    geometry_ids := ARRAY[OLD.id];
                ELSE
                    geometry_ids := ARRAY[OLD.id, NEW.id];
                END IF;
            ELSE
                IF TG_OP <> 'INSERT' THEN
                    geometry_ids := array_append(geometry_ids, OLD.geometry_id);
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    geometry_ids := array_append(geometry_ids, NEW.geometry_id);
                END IF;
            END IF;
            FOR calculation_id IN
                SELECT DISTINCT linked.calculation_id
                FROM (
                    SELECT cig.calculation_id
                    FROM public.calculation_input_geometry cig
                    WHERE cig.geometry_id = ANY(geometry_ids)
                    UNION
                    SELECT cog.calculation_id
                    FROM public.calculation_output_geometry cog
                    WHERE cog.geometry_id = ANY(geometry_ids)
                ) AS linked
                ORDER BY linked.calculation_id
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    'calculation', calculation_id
                );
                IF public.tckdb_record_is_accepted('calculation', calculation_id) THEN
                    PERFORM public.tckdb_raise_if_accepted(
                        'calculation', calculation_id,
                        TG_TABLE_SCHEMA, TG_TABLE_NAME,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                    );
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_child()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE record_ids bigint[] := ARRAY[]::bigint[];
                argument_index integer; record_id bigint;
        BEGIN
            FOR argument_index IN 1..TG_NARGS - 1 LOOP
                IF TG_OP <> 'INSERT' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(OLD)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(NEW)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
            END LOOP;
            FOR record_id IN
                SELECT DISTINCT value
                FROM unnest(record_ids) AS value
                WHERE value IS NOT NULL
                ORDER BY value
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    TG_ARGV[0]::public.submission_record_type, record_id
                );
                IF public.tckdb_record_is_accepted(
                    TG_ARGV[0]::public.submission_record_type, record_id
                ) THEN
                    PERFORM public.tckdb_raise_if_accepted(
                        TG_ARGV[0]::public.submission_record_type, record_id,
                        TG_TABLE_SCHEMA, TG_TABLE_NAME,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                        CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                    );
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_via_child()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE parent_id bigint; record_id bigint;
        BEGIN
            FOR parent_id IN
                SELECT DISTINCT value::bigint
                FROM unnest(ARRAY[
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD)->>TG_ARGV[1] END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW)->>TG_ARGV[1] END
                ]) AS value
                WHERE value IS NOT NULL
                ORDER BY value::bigint
            LOOP
                EXECUTE format(
                    'SELECT %I FROM public.%I WHERE %I = $1',
                    TG_ARGV[4], TG_ARGV[2], TG_ARGV[3]
                ) INTO record_id USING parent_id;
                IF record_id IS NOT NULL THEN
                    PERFORM public.tckdb_lock_scientific_record(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    );
                    IF public.tckdb_record_is_accepted(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    ) THEN
                        PERFORM public.tckdb_raise_if_accepted(
                            TG_ARGV[0]::public.submission_record_type, record_id,
                            TG_TABLE_SCHEMA, TG_TABLE_NAME,
                            CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                            CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(NEW) END
                        );
                    END IF;
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )


def _restore_guards() -> None:
    """Restore ``c6f2a9d4e7b1``'s bodies verbatim."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_root()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE record_id bigint;
        BEGIN
            IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
            record_id := OLD.id;
            PERFORM public.tckdb_lock_scientific_record(
                TG_ARGV[0]::public.submission_record_type, record_id
            );
            PERFORM public.tckdb_raise_if_accepted(
                TG_ARGV[0]::public.submission_record_type, record_id
            );
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_calculation_geometry()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE geometry_ids bigint[] := ARRAY[]::bigint[]; calculation_id bigint;
        BEGIN
            IF TG_TABLE_NAME = 'geometry' THEN
                IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
                IF TG_OP = 'DELETE' THEN
                    geometry_ids := ARRAY[OLD.id];
                ELSE
                    geometry_ids := ARRAY[OLD.id, NEW.id];
                END IF;
            ELSE
                IF TG_OP <> 'INSERT' THEN
                    geometry_ids := array_append(geometry_ids, OLD.geometry_id);
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    geometry_ids := array_append(geometry_ids, NEW.geometry_id);
                END IF;
            END IF;
            FOR calculation_id IN
                SELECT DISTINCT linked.calculation_id
                FROM (
                    SELECT cig.calculation_id
                    FROM public.calculation_input_geometry cig
                    WHERE cig.geometry_id = ANY(geometry_ids)
                    UNION
                    SELECT cog.calculation_id
                    FROM public.calculation_output_geometry cog
                    WHERE cog.geometry_id = ANY(geometry_ids)
                ) AS linked
                ORDER BY linked.calculation_id
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    'calculation', calculation_id
                );
                PERFORM public.tckdb_raise_if_accepted(
                    'calculation', calculation_id
                );
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_child()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE record_ids bigint[] := ARRAY[]::bigint[];
                argument_index integer; record_id bigint;
        BEGIN
            FOR argument_index IN 1..TG_NARGS - 1 LOOP
                IF TG_OP <> 'INSERT' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(OLD)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
                IF TG_OP <> 'DELETE' THEN
                    record_ids := array_append(
                        record_ids,
                        (to_jsonb(NEW)->>TG_ARGV[argument_index])::bigint
                    );
                END IF;
            END LOOP;
            FOR record_id IN
                SELECT DISTINCT value
                FROM unnest(record_ids) AS value
                WHERE value IS NOT NULL
                ORDER BY value
            LOOP
                PERFORM public.tckdb_lock_scientific_record(
                    TG_ARGV[0]::public.submission_record_type, record_id
                );
                PERFORM public.tckdb_raise_if_accepted(
                    TG_ARGV[0]::public.submission_record_type, record_id
                );
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_via_child()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE parent_id bigint; record_id bigint;
        BEGIN
            FOR parent_id IN
                SELECT DISTINCT value::bigint
                FROM unnest(ARRAY[
                    CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD)->>TG_ARGV[1] END,
                    CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW)->>TG_ARGV[1] END
                ]) AS value
                WHERE value IS NOT NULL
                ORDER BY value::bigint
            LOOP
                EXECUTE format(
                    'SELECT %I FROM public.%I WHERE %I = $1',
                    TG_ARGV[4], TG_ARGV[2], TG_ARGV[3]
                ) INTO record_id USING parent_id;
                IF record_id IS NOT NULL THEN
                    PERFORM public.tckdb_lock_scientific_record(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    );
                    PERFORM public.tckdb_raise_if_accepted(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    );
                END IF;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END;
        $$
        """
    )


def _create_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_accepted_science_repair_validate
        BEFORE INSERT ON public.accepted_science_repair
        FOR EACH ROW
        EXECUTE FUNCTION public.tckdb_validate_accepted_science_repair()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accepted_science_repair_change_validate
        BEFORE INSERT ON public.accepted_science_repair_change
        FOR EACH ROW
        EXECUTE FUNCTION public.tckdb_validate_accepted_science_repair_change()
        """
    )
    for table in ("accepted_science_repair", "accepted_science_repair_change"):
        op.execute(
            f"""
            CREATE TRIGGER trg_append_only_{table}
            BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation()
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_as_truncate_{table}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )


def upgrade() -> None:
    """Add the declared, checked and recorded repair path."""

    _create_tables()
    _create_functions()
    _replace_guards()
    _create_triggers()


def downgrade() -> None:
    """Remove the repair path and restore the unconditional refusal."""

    for table in ("accepted_science_repair", "accepted_science_repair_change"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_append_only_{table} ON public.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_as_truncate_{table} ON public.{table}")
    op.execute("DROP TRIGGER IF EXISTS trg_accepted_science_repair_validate ON public.accepted_science_repair")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_accepted_science_repair_change_validate ON public.accepted_science_repair_change"
    )
    _restore_guards()
    op.execute(
        "DROP FUNCTION IF EXISTS public.tckdb_raise_if_accepted("
        "public.submission_record_type, bigint, text, text, jsonb, jsonb)"
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_raise_if_accepted(
            p_type public.submission_record_type, p_record_id bigint
        ) RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.record_review
                WHERE record_type = p_type AND record_id = p_record_id
                  AND first_approved_at IS NOT NULL
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = format(
                        'accepted %s record %s is immutable', p_type, p_record_id
                    ),
                    HINT = 'Create and approve a replacement, then record a supersession.';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.tckdb_repair_permits("
        "public.submission_record_type, bigint, text, text, jsonb, jsonb)"
    )
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_record_is_accepted(public.submission_record_type, bigint)")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_validate_accepted_science_repair_change()")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_validate_accepted_science_repair()")
    op.drop_table("accepted_science_repair_change", schema="public")
    op.drop_table("accepted_science_repair", schema="public")
