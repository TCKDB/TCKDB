"""Make the calculation dependency graph acyclic in the database.

``calculation_dependency`` is a directed parent -> child edge. The schema
forbade self-edges (``not_self``) and, for four roles, a second parent, but no
write path refused a longer loop: A -> B then B -> A was accepted, as was any
longer ring.

This revision adds a ``BEFORE INSERT OR UPDATE`` row trigger that refuses an
edge whose parent is already reachable from its child. Every write path is
covered because the check lives in the table, not in a service.

Lock key (concurrency)
----------------------
Two transactions could each insert one edge of a new cycle and neither would
see the other's uncommitted row. A *statement-level* ``BEFORE INSERT OR UPDATE``
trigger therefore takes a transaction-scoped advisory lock::

    pg_advisory_xact_lock(hashtextextended('calculation_dependency:acyclic', 0))

before anything else in the statement, and the row trigger then walks the graph.
Statement-level BEFORE triggers run ahead of every row-level trigger, which
matters: the accepted-science guards (``trg_as_child_*``) take ``FOR UPDATE`` row
locks on both endpoint calculations. If the advisory lock were taken from the row
trigger (after those row locks), T1 holding the advisory lock and waiting for a
row T2 locked, while T2 waits for the advisory lock, would deadlock. Taking it
first means a waiting statement holds no row locks from that statement.

One global key for the table, on purpose: edge writes are rare and short, and a
cycle can span any set of calculations, so no narrower key is sound. The key is
a 64-bit hash of a namespaced string, the same convention the repository's other
advisory locks use (``conformer_group_resolve:<entry id>``,
``failed-upload:<user>:...``); the ``calculation_dependency:`` prefix keeps it
from colliding with those. The lock is released at commit or rollback. Inside
the walk each statement takes a fresh snapshot, so once the lock is held the
walk sees every edge committed by the transaction that held it before, and every
earlier edge of the current transaction. (Under REPEATABLE READ the snapshot is
transaction-wide and would not; the application runs at READ COMMITTED.)

Hold time. The lock lasts to the end of the transaction. A deposit that writes
dependency edges early and then keeps working (``computed_reaction`` wires
``depends_on`` and then continues for most of the workflow; other workflows write
edges at different points between autoflushes) makes every other
dependency-writing transaction wait for it to commit. A waiter that exceeds the
connection's ``statement_timeout`` is cancelled and the API answers 503. Moving
the edge writes to the end of each workflow is not a local change, so this
revision documents the consequence instead. A transaction that already holds row
locks from earlier, unrelated statements before it first touches a dependency can
still be chosen as a deadlock victim by PostgreSQL (SQLSTATE 40P01, retryable).

Refusal
-------
SQLSTATE ``23514`` with constraint name ``calculation_dependency_acyclic``. The
API maps that name to the coded 409 ``calculation_dependency_cycle``. The two
calculations are named by ``public_ref`` in the message detail, never by row id.

Ordering against the accepted-science triggers
----------------------------------------------
``trg_as_child_*`` on this table fire before the cycle row trigger (PostgreSQL
runs same-event row triggers in name order and ``trg_as_`` sorts before
``trg_calculation_``). A write that both targets an accepted calculation and
would close a cycle is therefore refused as immutable-science first. Neither
masks a *different* kind of write: the cycle check never relaxes the immutability
guard, and the guard never admits a cycle.

This revision also replaces ``tckdb_raise_if_accepted`` so that its message names
the record by ``public_ref`` instead of by row id (``accepted calculation record
calc_... is immutable``); a type with no ``public_ref`` column reads ``record
without a public reference``. Nothing else about the function changes, and
``downgrade()`` restores the previous body.

Existing data
-------------
The revision first takes ``SHARE ROW EXCLUSIVE`` on the table so no edge can
arrive between the check and the trigger, then looks for cycles already stored
and aborts, listing the calculations by public reference. It never deletes or
edits an edge.

Revision ID: e2f2b3fb6725
Revises: c7d4a9e2b6f1
Create Date: 2026-10-25
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2f2b3fb6725"
down_revision: Union[str, Sequence[str], None] = "c7d4a9e2b6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTION = "tckdb_calculation_dependency_acyclic"
_TRIGGER = "trg_calculation_dependency_acyclic"
_LOCK_FUNCTION = "tckdb_calculation_dependency_acyclic_lock"
_LOCK_TRIGGER = "trg_calculation_dependency_acyclic_lock"
LOCK_KEY_TEXT = "calculation_dependency:acyclic"
CONSTRAINT_NAME = "calculation_dependency_acyclic"

_LOCK_FUNCTION_SQL = f"""
CREATE FUNCTION public.{_LOCK_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('{LOCK_KEY_TEXT}', 0));
    RETURN NULL;
END;
$$
"""

_FUNCTION_SQL = f"""
CREATE FUNCTION public.{_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_ref text;
    child_ref text;
BEGIN
    -- A role-only update keeps the endpoints, so the graph shape is unchanged.
    IF TG_OP = 'UPDATE'
       AND OLD.parent_calculation_id = NEW.parent_calculation_id
       AND OLD.child_calculation_id = NEW.child_calculation_id THEN
        RETURN NEW;
    END IF;

    -- A self-edge is the not_self CHECK's to refuse (BEFORE triggers fire ahead
    -- of CHECK constraints), so it keeps its own, more specific error.
    IF NEW.parent_calculation_id = NEW.child_calculation_id THEN
        RETURN NEW;
    END IF;

    -- The advisory lock is already held: the statement-level trigger took it
    -- before any row lock. UNION (not UNION ALL) drops revisited nodes, so the
    -- walk terminates even if a cycle somehow exists.
    IF EXISTS (
        WITH RECURSIVE reach(id) AS (
            SELECT NEW.child_calculation_id
            UNION
            SELECT d.child_calculation_id
            FROM reach r
            JOIN public.calculation_dependency d ON d.parent_calculation_id = r.id
            WHERE NOT (
                TG_OP = 'UPDATE'
                AND d.parent_calculation_id = OLD.parent_calculation_id
                AND d.child_calculation_id = OLD.child_calculation_id
            )
        )
        SELECT 1 FROM reach WHERE id = NEW.parent_calculation_id
    ) THEN
        SELECT public_ref INTO parent_ref FROM public.calculation
            WHERE id = NEW.parent_calculation_id;
        SELECT public_ref INTO child_ref FROM public.calculation
            WHERE id = NEW.child_calculation_id;
        RAISE EXCEPTION 'calculation_dependency_cycle: the dependency edge would close a cycle'
            USING ERRCODE = '23514',
                  CONSTRAINT = '{CONSTRAINT_NAME}',
                  DETAIL = format('parent=%s;child=%s',
                                  coalesce(parent_ref, ''), coalesce(child_ref, '')),
                  HINT = 'The parent is already reachable from the child through existing dependency edges.';
    END IF;
    RETURN NEW;
END;
$$
"""

_RAISE_IF_ACCEPTED_SIGNATURE = """public.tckdb_raise_if_accepted(
    p_type public.submission_record_type,
    p_record_id bigint,
    p_schema text DEFAULT NULL,
    p_table text DEFAULT NULL,
    p_old jsonb DEFAULT NULL,
    p_new jsonb DEFAULT NULL
)"""

# Body as e2c9a4f7b163 left it (names the record by row id); restored on downgrade.
_RAISE_IF_ACCEPTED_BEFORE = f"""
CREATE OR REPLACE FUNCTION {_RAISE_IF_ACCEPTED_SIGNATURE} RETURNS void
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

# Same body, but the message names the record by public_ref. Every accepted-science
# root type is a table of the same name; one without a public_ref column falls back.
_RAISE_IF_ACCEPTED_AFTER = f"""
CREATE OR REPLACE FUNCTION {_RAISE_IF_ACCEPTED_SIGNATURE} RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    record_ref text;
BEGIN
    IF NOT public.tckdb_record_is_accepted(p_type, p_record_id) THEN
        RETURN;
    END IF;
    IF public.tckdb_repair_permits(
        p_type, p_record_id, p_schema, p_table, p_old, p_new
    ) THEN
        RETURN;
    END IF;
    BEGIN
        EXECUTE format('SELECT public_ref FROM public.%I WHERE id = $1', p_type::text)
            INTO record_ref USING p_record_id;
    EXCEPTION WHEN undefined_column OR undefined_table THEN
        record_ref := NULL;
    END;
    RAISE EXCEPTION USING ERRCODE = '55000',
        MESSAGE = format(
            'accepted %s record %s is immutable', p_type,
            coalesce(record_ref, 'without a public reference')
        ),
        HINT = 'Create and approve a replacement, then record a supersession. '
               'A repair that changes no science must first declare its table '
               'and columns in accepted_science_repair.';
END;
$$
"""


def _cycle_members(edges: list[tuple[int, int]]) -> list[list[int]]:
    """Strongly connected components with more than one node (iterative Tarjan)."""
    graph: dict[int, list[int]] = {}
    for parent, child in edges:
        graph.setdefault(parent, []).append(child)
        graph.setdefault(child, [])
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    components: list[list[int]] = []
    counter = 0
    for root in graph:
        if root in index:
            continue
        work = [(root, iter(graph[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for nxt in children:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(graph[nxt])))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(component)
    return components


def _refuse_existing_cycles(bind) -> None:
    edges = [
        (row[0], row[1])
        for row in bind.execute(
            sa.text("SELECT parent_calculation_id, child_calculation_id FROM calculation_dependency")
        )
    ]
    components = _cycle_members(edges)
    if not components:
        return
    ids = sorted({member for component in components for member in component})
    refs = dict(
        bind.execute(
            sa.text("SELECT id, public_ref FROM calculation WHERE id = ANY(:ids)"),
            {"ids": ids},
        ).all()
    )
    lines = [
        "  - " + ", ".join(sorted(refs[member] for member in component))
        for component in components
    ]
    raise RuntimeError(
        "Cannot make calculation_dependency acyclic: the table already holds "
        f"{len(components)} cycle(s). Each line lists the calculations (by public "
        "reference) that depend on one another in a loop. No edge was changed; "
        "remove or correct the offending edges, then upgrade again.\n" + "\n".join(lines)
    )


def upgrade() -> None:
    bind = op.get_bind()
    # Nothing may slip in between the check and the trigger.
    op.execute("LOCK TABLE public.calculation_dependency IN SHARE ROW EXCLUSIVE MODE")
    _refuse_existing_cycles(bind)
    op.execute(_LOCK_FUNCTION_SQL)
    op.execute(_FUNCTION_SQL)
    op.execute(
        f"""
        CREATE TRIGGER {_LOCK_TRIGGER}
        BEFORE INSERT OR UPDATE OF parent_calculation_id, child_calculation_id
        ON public.calculation_dependency
        FOR EACH STATEMENT EXECUTE FUNCTION public.{_LOCK_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE INSERT OR UPDATE OF parent_calculation_id, child_calculation_id
        ON public.calculation_dependency
        FOR EACH ROW EXECUTE FUNCTION public.{_FUNCTION}()
        """
    )
    op.execute(_RAISE_IF_ACCEPTED_AFTER)


def downgrade() -> None:
    op.execute(_RAISE_IF_ACCEPTED_BEFORE)
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON public.calculation_dependency")
    op.execute(f"DROP TRIGGER IF EXISTS {_LOCK_TRIGGER} ON public.calculation_dependency")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_LOCK_FUNCTION}()")
