"""Make the calculation dependency graph acyclic in the database.

``calculation_dependency`` is a directed parent -> child edge. The schema
forbade self-edges (``not_self``) and, for four roles, a second parent, but no
write path refused a longer loop: A -> B then B -> A was accepted, as was any
longer ring. Readers defend against that (``dependency_cycle``), which is a
fact about how long the gap has been known, not a guarantee.

This revision adds a ``BEFORE INSERT OR UPDATE`` row trigger that refuses an
edge whose parent is already reachable from its child. Every write path is
covered because the check lives in the table, not in a service.

Lock key (concurrency)
----------------------
Two transactions could each insert one edge of a new cycle and neither would
see the other's uncommitted row. The trigger therefore takes a
transaction-scoped advisory lock before the reachability walk::

    pg_advisory_xact_lock(hashtextextended('calculation_dependency:acyclic', 0))

One global key for the table, on purpose: edge writes are rare and short, and a
cycle can span any set of calculations, so no narrower key is sound. The key is
a 64-bit hash of a namespaced string, the same convention the repository's other
advisory locks use (``conformer_group_resolve:<entry id>``,
``failed-upload:<user>:...``); the ``calculation_dependency:`` prefix keeps it
from colliding with those. The lock is released at commit or rollback. Inside
the function each statement takes a fresh snapshot, so once the lock is held the
walk sees every edge committed by the transaction that held it before, and every
earlier edge of the current transaction. (Under REPEATABLE READ the snapshot is
transaction-wide and would not; the application runs at READ COMMITTED.)

Refusal
-------
SQLSTATE ``23514`` with constraint name ``calculation_dependency_acyclic``. The
API maps that name to the coded 409 ``calculation_dependency_cycle``. The two
calculations are named by ``public_ref`` in the message detail, never by row id.

Ordering against the accepted-science triggers
----------------------------------------------
``trg_as_child_*`` on this table fire first (PostgreSQL runs same-event
triggers in name order and ``trg_as_`` sorts before ``trg_calculation_``). A
write that both targets an accepted calculation and would close a cycle is
therefore refused as immutable-science first; the cycle is reported only once
that refusal is not in the way. Neither masks a *different* kind of write: the
cycle check never relaxes the immutability guard, and the guard never admits a
cycle.

Existing data
-------------
Before the trigger is created the revision looks for cycles already stored and
aborts, listing the calculations by public reference. It never deletes or edits
an edge.

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
LOCK_KEY_TEXT = "calculation_dependency:acyclic"
CONSTRAINT_NAME = "calculation_dependency_acyclic"

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

    PERFORM pg_advisory_xact_lock(hashtextextended('{LOCK_KEY_TEXT}', 0));

    -- UNION (not UNION ALL) drops revisited nodes, so the walk terminates even
    -- if a cycle somehow already exists.
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
    _refuse_existing_cycles(bind)
    op.execute(_FUNCTION_SQL)
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE INSERT OR UPDATE OF parent_calculation_id, child_calculation_id
        ON public.calculation_dependency
        FOR EACH ROW EXECUTE FUNCTION public.{_FUNCTION}()
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON public.calculation_dependency")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}()")
