"""Admin deletion of approved scientific records, with an append-only audit.

Adds ``record_deletion``: one row per deleted record holding who, when, why, the record's subject and a JSON copy of
the record and every row removed with it. A trigger refuses UPDATE and DELETE on it, and an insert trigger checks the
named record exists under that public ref and was approved.

The accepted-science guards, ``tckdb_guard_record_review`` and the append-only rejecters of the per-record overlay
tables stand down for a DELETE (never an UPDATE) only when ``tckdb_record_deletion_permits`` holds: the transaction-local
setting ``tckdb.record_deletion`` names the record as ``<record_type>:<id>`` and a ``record_deletion`` row for it was
written in the same transaction. Overlay rows of the record's calculation artifacts are covered through their
calculation. A child row is freed only through the column that owns it (a dependency edge through its child).

The same permit removes the history links that name the record: supersession edges in either direction, structure
evidence findings about a calculation (and adjudications settling them), and structure-determination sources pinning
a calculation. ``tckdb_structure_finding_append_only`` and ``tckdb_structure_source_delete_guard`` stand down for those
rows only.

An archive restore inserts audit rows under the transaction-local setting ``tckdb.record_deletion_restoring``,
which skips the check that the named record exists and was approved and stores ``xact_id`` as NULL.

``downgrade()`` restores the original function bodies, then drops the table with every audit row in it.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c8e2a4f6b1d3"
down_revision: Union[str, Sequence[str], None] = "e3b7c1a9d452"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PERMIT_FUNCTIONS = r"""
CREATE FUNCTION public.tckdb_record_deletion_permits(p_type public.submission_record_type, p_record_id bigint)
    RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        SELECT coalesce(
            p_type IS NOT NULL AND p_record_id IS NOT NULL
            AND coalesce(current_setting('tckdb.record_deletion', true), '') = p_type::text || ':' || p_record_id::text
            AND EXISTS (
                SELECT 1 FROM public.record_deletion d
                WHERE d.record_type = p_type AND d.record_id = p_record_id
                  AND d.xact_id = ((pg_current_xact_id())::text)::bigint
            ),
            false
        )
    $$;

CREATE FUNCTION public.tckdb_record_deletion_covers(p_type public.submission_record_type, p_record_id bigint)
    RETURNS boolean
    LANGUAGE sql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        SELECT coalesce(
            public.tckdb_record_deletion_permits(p_type, p_record_id)
            OR (
                p_type = 'artifact'
                AND EXISTS (
                    SELECT 1 FROM public.calculation_artifact a
                    WHERE a.id = p_record_id
                      AND public.tckdb_record_deletion_permits('calculation', a.calculation_id)
                )
            ),
            false
        )
    $$;

CREATE FUNCTION public.tckdb_record_deletion_covers_row(p_table text, p_row jsonb)
    RETURNS boolean
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            CASE p_table
                WHEN 'record_review', 'record_reproducibility_assessment', 'deposit_warning',
                     'accepted_science_repair_change' THEN
                    RETURN public.tckdb_record_deletion_covers(
                        (p_row ->> 'record_type')::public.submission_record_type,
                        (p_row ->> 'record_id')::bigint
                    );
                WHEN 'record_review_event' THEN
                    RETURN coalesce((
                        SELECT public.tckdb_record_deletion_covers(r.record_type, r.record_id)
                        FROM public.record_review r WHERE r.id = (p_row ->> 'record_review_id')::bigint
                    ), false);
                WHEN 'deposit_warning_retirement' THEN
                    RETURN coalesce((
                        SELECT public.tckdb_record_deletion_covers(w.record_type, w.record_id)
                        FROM public.deposit_warning w WHERE w.id = (p_row ->> 'deposit_warning_id')::bigint
                    ), false);
                WHEN 'software_version_attestation_calculation', 'structure_determination_source' THEN
                    RETURN public.tckdb_record_deletion_permits(
                        'calculation', (p_row ->> 'calculation_id')::bigint
                    );
                WHEN 'scientific_record_supersession' THEN
                    RETURN public.tckdb_record_deletion_permits(
                        (p_row ->> 'record_type')::public.submission_record_type,
                        (p_row ->> 'superseded_record_id')::bigint
                    ) OR public.tckdb_record_deletion_permits(
                        (p_row ->> 'record_type')::public.submission_record_type,
                        (p_row ->> 'superseding_record_id')::bigint
                    );
                WHEN 'structure_evidence_finding' THEN
                    -- A finding about the calculation, or an adjudication settling one (walked up its chain).
                    RETURN coalesce((
                        WITH RECURSIVE chain AS (
                            SELECT (p_row ->> 'subject_calculation_id')::bigint AS subject_id,
                                   (p_row ->> 'source_calculation_id')::bigint AS source_id,
                                   (p_row ->> 'supersedes_finding_id')::bigint AS parent_id,
                                   1 AS depth
                            UNION ALL
                            SELECT f.subject_calculation_id, f.source_calculation_id, f.supersedes_finding_id,
                                   chain.depth + 1
                            FROM public.structure_evidence_finding f
                            JOIN chain ON f.id = chain.parent_id
                            WHERE chain.depth < 64
                        )
                        SELECT bool_or(
                            public.tckdb_record_deletion_permits('calculation', subject_id)
                            OR public.tckdb_record_deletion_permits('calculation', source_id)
                        )
                        FROM chain
                    ), false);
                ELSE
                    RETURN false;
            END CASE;
        END;
    $$;

CREATE FUNCTION public.tckdb_validate_record_deletion() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $_$
        DECLARE found_ref text;
        BEGIN
            IF NOT public.tckdb_is_accepted_science_type(NEW.record_type) THEN
                RAISE EXCEPTION USING ERRCODE = '22023',
                    MESSAGE = 'record_deletion names a record type outside accepted science';
            END IF;
            IF coalesce(current_setting('tckdb.record_deletion_restoring', true), '') = 'on' THEN
                -- An archive restore re-creates the audit of records that are already gone. The transaction id it
                -- came from belongs to another cluster, so it is dropped: a NULL never permits a delete.
                NEW.xact_id := NULL;
                RETURN NEW;
            END IF;
            BEGIN
                EXECUTE format('SELECT public_ref FROM public.%I WHERE id = $1 FOR UPDATE', NEW.record_type::text)
                    INTO found_ref USING NEW.record_id;
            EXCEPTION WHEN undefined_column THEN
                found_ref := NULL;
            END;
            IF found_ref IS NULL OR found_ref <> NEW.record_public_ref THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'record_deletion names no record under that public reference';
            END IF;
            IF NOT public.tckdb_record_is_accepted(NEW.record_type, NEW.record_id) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'record_deletion is only for approved records';
            END IF;
            NEW.xact_id := ((pg_current_xact_id())::text)::bigint;
            RETURN NEW;
        END;
    $_$;

CREATE TRIGGER trg_record_deletion_validate BEFORE INSERT ON public.record_deletion
    FOR EACH ROW EXECUTE FUNCTION public.tckdb_validate_record_deletion();
CREATE TRIGGER trg_append_only_record_deletion BEFORE DELETE OR UPDATE ON public.record_deletion
    FOR EACH ROW EXECUTE FUNCTION public.tckdb_reject_mutation();
CREATE TRIGGER trg_record_deletion_no_truncate BEFORE TRUNCATE ON public.record_deletion
    FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate();
"""

_GUARDS_WITH_DELETION_PERMIT = r"""
CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_root()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
        DECLARE record_id bigint;
        BEGIN
            IF TG_OP = 'INSERT' THEN RETURN NEW; END IF;
            record_id := OLD.id;
            IF TG_OP = 'DELETE' AND public.tckdb_record_deletion_permits(
                TG_ARGV[0]::public.submission_record_type, record_id
            ) THEN
                RETURN OLD;
            END IF;
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
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_child()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
        DECLARE record_ids bigint[] := ARRAY[]::bigint[];
                argument_index integer; record_id bigint; owner_column text;
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
            IF TG_OP = 'DELETE' THEN
                -- The row is freed only by a deletion of the record that owns it: the single guarded column, or for a
                -- dependency edge its child. A permit for the parent leaves the child's edge guarded.
                owner_column := CASE
                    WHEN TG_NARGS = 2 THEN TG_ARGV[1]
                    WHEN TG_TABLE_NAME = 'calculation_dependency' THEN 'child_calculation_id'
                END;
                IF owner_column IS NOT NULL AND public.tckdb_record_deletion_permits(
                    TG_ARGV[0]::public.submission_record_type, (to_jsonb(OLD)->>owner_column)::bigint
                ) THEN
                    RETURN OLD;
                END IF;
            END IF;
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
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_via_child()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
        DECLARE parent_id bigint; record_id bigint;
        BEGIN
            IF TG_OP = 'DELETE' AND public.tckdb_record_deletion_covers_row(TG_TABLE_NAME, to_jsonb(OLD)) THEN
                RETURN OLD;
            END IF;
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
                    IF TG_OP = 'DELETE' AND public.tckdb_record_deletion_permits(
                        TG_ARGV[0]::public.submission_record_type, record_id
                    ) THEN
                        RETURN OLD;
                    END IF;
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
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_guard_record_review()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF public.tckdb_record_deletion_covers(OLD.record_type, OLD.record_id) THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'record_review rows cannot be deleted';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.record_type IS DISTINCT FROM OLD.record_type
                   OR NEW.record_id IS DISTINCT FROM OLD.record_id THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'record_review natural keys are immutable';
                END IF;
                IF OLD.first_approved_at IS NOT NULL
                   AND NEW.first_approved_at IS DISTINCT FROM OLD.first_approved_at THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'first_approved_at is immutable once set';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.first_approved_at IS NULL
               AND NEW.first_approved_at IS NOT NULL
               AND NEW.status <> 'approved' THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'first_approved_at may only be established by approval';
            END IF;
            IF public.tckdb_is_accepted_science_type(NEW.record_type)
               AND (NEW.status = 'approved' OR NEW.first_approved_at IS NOT NULL) THEN
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.record_id
                );
            END IF;
            IF NEW.status = 'approved' THEN
                IF NEW.first_approved_at IS NULL THEN
                    NEW.first_approved_at := coalesce(NEW.reviewed_at, clock_timestamp());
                END IF;
            END IF;
            RETURN NEW;
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_reject_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
        BEGIN
            IF TG_OP = 'DELETE' AND TG_LEVEL = 'ROW'
               AND public.tckdb_record_deletion_covers_row(TG_TABLE_NAME, to_jsonb(OLD)) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = TG_TABLE_NAME || ' is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.reject_deposit_warning_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            IF TG_OP = 'DELETE' AND TG_LEVEL = 'ROW'
               AND public.tckdb_record_deletion_covers_row(TG_TABLE_NAME, to_jsonb(OLD)) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.reject_deposit_warning_retirement_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            IF TG_OP = 'DELETE' AND TG_LEVEL = 'ROW'
               AND public.tckdb_record_deletion_covers_row(TG_TABLE_NAME, to_jsonb(OLD)) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning_retirement is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.reject_reproducibility_assessment_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            IF TG_OP = 'DELETE' AND TG_LEVEL = 'ROW'
               AND public.tckdb_record_deletion_covers_row(TG_TABLE_NAME, to_jsonb(OLD)) THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'record_reproducibility_assessment is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_structure_finding_append_only()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
BEGIN
    IF TG_OP = 'DELETE' AND public.tckdb_record_deletion_covers_row(TG_TABLE_NAME, to_jsonb(OLD)) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION
        'structure_evidence_finding_is_append_only: a finding cannot be %d. Only an authorized adjudication '
        'settles an earlier finding.', lower(TG_OP)
        USING ERRCODE = '23514',
              HINT = 'Append a finding (or, with authority, an adjudication) instead.';
END;
$function$;

CREATE OR REPLACE FUNCTION public.tckdb_structure_source_delete_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
BEGIN
    IF public.tckdb_record_deletion_covers_row(TG_TABLE_NAME, to_jsonb(OLD)) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION
        'structure_determination_sources_are_pinned_at_creation: a source of determination % cannot be removed',
        OLD.determination_id
        USING ERRCODE = '23514';
END;
$function$;
"""

# The bodies these functions had before this revision, verbatim from ``pg_get_functiondef`` at e3b7c1a9d452.
_ORIGINAL_GUARDS = r"""
CREATE OR REPLACE FUNCTION public.reject_deposit_warning_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.reject_deposit_warning_retirement_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'deposit_warning_retirement is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.reject_reproducibility_assessment_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = 'record_reproducibility_assessment is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_child()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
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
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_root()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
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
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_guard_accepted_via_child()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
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
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_guard_record_review()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'record_review rows cannot be deleted';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.record_type IS DISTINCT FROM OLD.record_type
                   OR NEW.record_id IS DISTINCT FROM OLD.record_id THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'record_review natural keys are immutable';
                END IF;
                IF OLD.first_approved_at IS NOT NULL
                   AND NEW.first_approved_at IS DISTINCT FROM OLD.first_approved_at THEN
                    RAISE EXCEPTION USING ERRCODE = '55000',
                        MESSAGE = 'first_approved_at is immutable once set';
                END IF;
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.first_approved_at IS NULL
               AND NEW.first_approved_at IS NOT NULL
               AND NEW.status <> 'approved' THEN
                RAISE EXCEPTION USING ERRCODE = '55000',
                    MESSAGE = 'first_approved_at may only be established by approval';
            END IF;
            IF public.tckdb_is_accepted_science_type(NEW.record_type)
               AND (NEW.status = 'approved' OR NEW.first_approved_at IS NOT NULL) THEN
                PERFORM public.tckdb_lock_scientific_record(
                    NEW.record_type, NEW.record_id
                );
            END IF;
            IF NEW.status = 'approved' THEN
                IF NEW.first_approved_at IS NULL THEN
                    NEW.first_approved_at := coalesce(NEW.reviewed_at, clock_timestamp());
                END IF;
            END IF;
            RETURN NEW;
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_reject_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '55000',
                MESSAGE = TG_TABLE_NAME || ' is append-only';
        END;
        $function$;

CREATE OR REPLACE FUNCTION public.tckdb_structure_finding_append_only()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
BEGIN
    RAISE EXCEPTION
        'structure_evidence_finding_is_append_only: a finding cannot be %d. Only an authorized adjudication '
        'settles an earlier finding.', lower(TG_OP)
        USING ERRCODE = '23514',
              HINT = 'Append a finding (or, with authority, an adjudication) instead.';
END;
$function$;

CREATE OR REPLACE FUNCTION public.tckdb_structure_source_delete_guard()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog', 'public'
AS $function$
BEGIN
    RAISE EXCEPTION
        'structure_determination_sources_are_pinned_at_creation: a source of determination % cannot be removed',
        OLD.determination_id
        USING ERRCODE = '23514';
END;
$function$;
"""

_DROP_PERMIT_FUNCTIONS = r"""
DROP FUNCTION public.tckdb_validate_record_deletion();
DROP FUNCTION public.tckdb_record_deletion_covers_row(text, jsonb);
DROP FUNCTION public.tckdb_record_deletion_covers(public.submission_record_type, bigint);
DROP FUNCTION public.tckdb_record_deletion_permits(public.submission_record_type, bigint);
"""


def _execute(sql: str) -> None:
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(sql)


def upgrade() -> None:
    op.create_table(
        "record_deletion",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "record_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=False,
        ),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("record_public_ref", sa.String(length=40), nullable=False),
        sa.Column(
            "subject_type",
            postgresql.ENUM(name="submission_record_type", create_type=False),
            nullable=True,
        ),
        sa.Column("subject_public_ref", sa.String(length=40), nullable=True),
        sa.Column(
            "subject_chain",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("first_approved_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("deleted_by", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "xact_id",
            sa.BigInteger(),
            server_default=sa.text("((pg_current_xact_id())::text)::bigint"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "record_type IN ('calculation', 'thermo', 'statmech', 'kinetics', "
            "'transport', 'network', 'network_solve', 'applied_energy_correction', "
            "'transition_state_entry', 'conformer_observation')",
            name="supported_type",
        ),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="reason_nonblank"),
        sa.CheckConstraint(
            "(subject_type IS NULL) = (subject_public_ref IS NULL)",
            name="subject_both_or_neither",
        ),
        sa.CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot_is_object"),
        sa.CheckConstraint(
            "jsonb_typeof(subject_chain) = 'array'", name="subject_chain_is_array"
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by"],
            ["app_user.id"],
            name="fk_record_deletion_deleted_by_app_user",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_record_deletion"),
        sa.UniqueConstraint("record_type", "record_id", name="uq_record_deletion_record_type"),
        sa.UniqueConstraint("record_public_ref", name="uq_record_deletion_record_public_ref"),
    )
    op.create_index("ix_record_deletion_deleted_at", "record_deletion", ["deleted_at"])
    _execute(_PERMIT_FUNCTIONS)
    _execute(_GUARDS_WITH_DELETION_PERMIT)


def downgrade() -> None:
    _execute(_ORIGINAL_GUARDS)
    op.drop_table("record_deletion")
    _execute(_DROP_PERMIT_FUNCTIONS)
