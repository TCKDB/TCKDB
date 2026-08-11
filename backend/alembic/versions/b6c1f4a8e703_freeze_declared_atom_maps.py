"""Bring declared atom maps under the accepted-science immutability regime.

``c6f2a9d4e7b1`` made every accepted scientific record immutable in the
database: once a ``record_review`` row for a supported root has ever reached
``approved``, the root and the rows that carry its scientific meaning can no
longer be updated, deleted, or added to. Corrections go through
``scientific_record_supersession`` — a new record, approved on its own terms,
with an explicit edge from the old one — rather than through mutation.

``d3a7f1c9b284`` added ``reaction_atom_map`` and ``reaction_atom_map_pair``
after that regime was in place and did not join it. Neither table carries a
``trg_as_*`` trigger, so a declared atom map attached to an accepted saddle
point could be edited in place, silently, with no supersession edge and no
review event — while ``calc_freq_mode`` beside it, an ordinary number rather
than a mechanistic claim, could not. That reads as an oversight rather than a
considered exemption: ADR 0011 frames a declared map as a claim the depositor
is answerable for, which is exactly the property this regime enforces.

Which acceptance freezes a map
------------------------------
``reaction_atom_map`` names three things: a ``reaction_entry``, a
``transition_state_entry``, and the ``geometry`` both legs index into. Only one
of the three is the right owner.

**``transition_state_entry``** — chosen. It is an accepted-science root
already (``c6f2a9d4e7b1``'s ``_ROOT_TYPES``), it is a ``NOT NULL`` foreign key
on every map, and one TS entry determines exactly one reaction entry
(``transition_state_entry → transition_state → reaction_entry``), so nothing
unrelated can freeze a map by being approved. Scientifically it is the same
claim: approving a TS entry asserts that this saddle point connects the
declared reactants and products, and ADR 0011 says the atom map is what
"converts that assertion into something a reader can check". The map is not
adjacent to that assertion, it is its content.

**``reaction_entry``** — rejected. It is not an accepted-science root at all:
``tckdb_is_accepted_science_type`` does not admit it and
``tckdb_lock_scientific_record`` has no branch for it, so a guard hung there
would never fire. Making it one is a change to the whole regime, not to atom
maps, and a reaction entry is closer to identity than to result.

**The transition-state ``geometry``, via its calculation** — rejected. A
geometry is reached from a calculation through ``calculation_output_geometry``
and is not private to one calculation, so this would freeze a map when a
calculation that merely shares the geometry is approved: acceptance of
something unrelated. The geometry's own immutability is already handled by
``tckdb_guard_calculation_geometry``, which is a different question — whether
the coordinates may move — from whether the map over them may be rewritten.

Insertion is guarded too, deliberately
--------------------------------------
``tckdb_guard_accepted_child`` fires on INSERT as well as UPDATE and DELETE, so
a map cannot be *added* to an already-accepted TS entry. That is the same rule
``calc_freq_result`` and ``network_state`` live under, and it is the right one
here: attaching a mechanistic claim to a record reviewers already accepted
without one changes what was accepted. ADR 0011 puts retroactive mapping
explicitly out of scope and accepts the resulting split between mapped and
unmapped records rather than solving it; the correction path is to deposit a
new transition-state entry carrying the map, approve it, and record a
``transition_state_entry`` supersession — which
``ck_scientific_record_supersession_supported_type`` and
``app.services.accepted_science.supersession_subject`` both already admit.

What this does to ``e7d1c4b8a3f5``'s advice
-------------------------------------------
``e7d1c4b8a3f5`` froze ``reaction_atom_map.source`` against in-place
relabelling and told the operator, in the exception's ``HINT``, that "to
correct a genuinely mis-declared map, delete it and deposit the real one". That
was true when nothing stopped a delete. It stops being true for a map whose
transition-state entry has been accepted, and an operator who follows a stale
hint into a second refusal learns only that two rules disagree. So this
revision replaces that function's body — same logic, same error, corrected
hint — and ``downgrade()`` puts the original text back, because once the
accepted-science guard is gone the original advice is right again.

No rows are touched
-------------------
This revision is pure DDL. Creating a trigger does not evaluate it against
existing rows, so the migration is safe whatever a deployment holds, and there
is no backfill to design — consistent with ``d3a7f1c9b284``, whose whole
argument is that an atom map cannot be manufactured.

``downgrade()`` drops the four triggers and restores the prior state exactly.
Unlike ``d3a7f1c9b284``'s downgrade it destroys nothing: no map is written,
read, or removed here, only the rule about who may rewrite one.

Revision ID: b6c1f4a8e703
Revises: c8b4e1a7d302
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b6c1f4a8e703"
down_revision: Union[str, Sequence[str], None] = "c8b4e1a7d302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: ``(table, record_type, column)`` — guarded by ``tckdb_guard_accepted_child``
#: against the accepted root named directly on the row.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = (
    ("reaction_atom_map", "transition_state_entry", "transition_state_entry_id"),
)

#: ``(table, record_type, child_column, parent_table, parent_pk, root_column)``
#: — guarded by ``tckdb_guard_accepted_via_child``, which dereferences the
#: parent row to find the root. A pair names no transition-state entry of its
#: own; it reaches one through the map it belongs to.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "reaction_atom_map_pair",
        "transition_state_entry",
        "atom_map_id",
        "reaction_atom_map",
        "id",
        "transition_state_entry_id",
    ),
)

#: TRUNCATE bypasses row triggers entirely, so both tables need the statement
#: level refusal the rest of the regime carries.
_TRUNCATE_TABLES: tuple[str, ...] = (
    "reaction_atom_map",
    "reaction_atom_map_pair",
)


#: ``e7d1c4b8a3f5``'s source-immutability function, with the delete-and-
#: redeposit hint qualified by the freeze this revision installs. Only the
#: ``HINT`` text differs from the original; the condition and the raised error
#: are byte-for-byte the same, so this replaces no rule and relaxes none.
_SOURCE_IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION public.tckdb_reaction_atom_map_source_immutable()
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
                  HINT = 'To correct a mis-declared map whose transition-state '
                         'entry has never been approved, delete it and deposit '
                         'the real one. Once that entry has been approved the '
                         'map is immutable too: deposit a new transition-state '
                         'entry carrying the corrected map, approve it, and '
                         'record a transition_state_entry supersession. Its '
                         'note and equivalent_map_count remain editable in '
                         'place while the entry is unapproved.';
    END IF;
    RETURN NEW;
END;
$$
"""

#: The original text, restored on downgrade. Copied verbatim from
#: ``e7d1c4b8a3f5``: with the accepted-science guard dropped, delete and
#: redeposit is once again the whole of the correction path.
_SOURCE_IMMUTABLE_FUNCTION_BEFORE = """
CREATE OR REPLACE FUNCTION public.tckdb_reaction_atom_map_source_immutable()
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


def _trigger_name(prefix: str, table: str) -> str:
    """Mirror ``c6f2a9d4e7b1``'s naming, truncated to PostgreSQL's 63 bytes.

    The names are suffixed with the table rather than with a positional index,
    because ``c6f2a9d4e7b1`` already owns ``trg_as_child_NN`` /
    ``trg_as_via_NN`` / ``trg_as_truncate_NN`` and appending to a positional
    sequence from a second revision would make both revisions' numbering depend
    on the other's registry.
    """

    return f"trg_{prefix}_{table}"[:63]


def _child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    """Collapse the registry to one trigger per ``(table, record_type)``."""

    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


def upgrade() -> None:
    """Guard atom maps and their pairs with the accepted-science triggers."""

    for table, record_type, columns in _child_groups():
        arguments = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_child", table)}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.tckdb_guard_accepted_child(
                '{record_type}', {arguments}
            )
            """
        )
    for (
        table,
        record_type,
        child_column,
        parent_table,
        parent_pk,
        root_column,
    ) in _VIA_CHILDREN:
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_via", table)}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child(
                '{record_type}', '{child_column}', '{parent_table}',
                '{parent_pk}', '{root_column}'
            )
            """
        )
    for table in _TRUNCATE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_truncate", table)}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )
    op.execute(_SOURCE_IMMUTABLE_FUNCTION)


def downgrade() -> None:
    """Drop the atom-map guards, restoring the prior state exactly."""

    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    for item in _VIA_CHILDREN:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_via', item[0])} ON public.{item[0]}")
    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    op.execute(_SOURCE_IMMUTABLE_FUNCTION_BEFORE)
