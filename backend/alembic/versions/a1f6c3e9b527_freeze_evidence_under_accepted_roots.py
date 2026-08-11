"""Close the accepted-science immutability gap on five evidence tables.

``c6f2a9d4e7b1`` made every accepted scientific record immutable in the
database, and ``b6c1f4a8e703`` extended that regime to the atom-map tables
``d3a7f1c9b284`` had added after it. Both left an asymmetry standing.

An atom map *elaborates* the claim that a saddle point connects its declared
endpoints; ``transition_state_validation_evidence`` is the IRC record that
*backs* the same claim, and
``app.services.reaction_atom_map.validate_atom_map_agrees_with_irc_evidence``
holds the two against each other. After ``b6c1f4a8e703`` the elaboration was
frozen once its transition-state entry was approved while the evidence stayed
editable in place — so the check comparing them could be satisfied at deposit
and then quietly falsified afterwards by rewriting the half that was still
writable, with no supersession edge and no review event. Two surfaces bearing
one claim under two mutability regimes is not a position either can defend.

Four more tables are in the same class: an ownership foreign key to an
accepted-science root, carrying result-grade content, and no ``trg_as_*``
guard. They are frozen here as one batch because the argument is identical for
all five and splitting it would leave the same asymmetry behind under a
different name.

Which acceptance freezes each table
-----------------------------------
The rule ``c6f2a9d4e7b1`` set is that a child is guarded by the accepted root
that owns its *scientific meaning*, and that cross-domain provenance foreign
keys are not ownership. Applying it:

``transition_state_validation_evidence`` → **transition_state_entry**, via
``transition_state_entry_id``. It is ``NOT NULL``, and the row is exactly the
evidence for that entry's saddle point: ``uq_ts_validation_evidence_kind``
makes it at most one IRC record *per entry*. Its other root reference,
``reconstruction_calculation_id``, is provenance — the calculation the path
was reconstructed by. Guarding on that would freeze the evidence when an IRC
calculation is approved, which is acceptance of the *input*, and would leave
it thawed when the entry the evidence is about is approved, which is the case
that matters. Approving a TS entry is precisely the act of accepting "this
saddle point connects these endpoints"; this table holds what that acceptance
rested on.

``network_solve_channel_barrier`` and ``network_solve_state_energy`` →
**network_solve**, via ``solve_id``. Both are ``NOT NULL`` and part of each
table's primary key. These are the barriers and state energies a solve was
*run with*: change one after the fact and the stored ``network_kinetics``
k(T,P) no longer follows from the inputs recorded beside it, while
``network_kinetics`` and its Chebyshev/PLOG children are already frozen by
this same root. Freezing the outputs of a solve and not its inputs makes the
solve unreproducible in exactly the way the regime exists to prevent. Their
``transition_state_entry_id``, ``channel_id``, ``reaction_entry_id`` and
``state_id`` references identify *which* path or well a number belongs to and
are not ownership; ``source_calculation_id`` is provenance, and is excluded
for the same reason ``thermo_source_calculation.calculation_id`` is.

``kinetics_interpretation_assignment`` and
``kinetics_tunneling_application`` → **kinetics**, via ``kinetics_id``. Both
are ``NOT NULL`` and part of the primary key, and both sit beside
``kinetics_arrhenius_entry`` and ``kinetics_plog``, which ``c6f2a9d4e7b1``
already froze under this root. The assignment names the exact statistical-
mechanics interpretation and conventions a rate was computed under, and the
tunneling row names the correction applied to it — rewrite either and the
stored rate constant stops being the number those inputs produce. Their
``statmech_id``, ``conformer_selection_id`` and ``transition_state_entry_id``
references say *what was used*, not who owns the row.

No root is added and no root type changes, so
``tckdb_lock_scientific_record`` needs no new branch: all three record types
named here are already in ``c6f2a9d4e7b1``'s ``_ROOT_TYPES``.

Insertion is guarded too
------------------------
``tckdb_guard_accepted_child`` fires on INSERT as well as UPDATE and DELETE,
so none of these rows can be *added* to an already-accepted root. That is the
rule ``calc_freq_result`` and ``network_state`` already live under, and it is
the right one here for the same reason ``b6c1f4a8e703`` gave: attaching
evidence, an input, or an interpretation to a record reviewers accepted
without it changes what was accepted. The correction path is a new record
approved on its own terms with a ``scientific_record_supersession`` edge —
supported for ``transition_state_entry``, ``kinetics`` and ``network_solve``
alike by ``ck_scientific_record_supersession_supported_type``.

TRUNCATE bypasses row triggers entirely, so all five tables take the
statement-level refusal the rest of the regime carries.

No rows are touched
-------------------
Pure DDL. Creating a trigger does not evaluate it against existing rows, so
this is safe whatever a deployment holds and there is no backfill to design.
``f3b7d2c8a419``'s backfill of ``transition_state_validation_evidence`` runs
*before* this revision, and must: the guard installed here would refuse those
UPDATEs on any row under an approved entry. The two must not be reordered.

``downgrade()`` drops the fifteen triggers and destroys nothing.

Revision ID: a1f6c3e9b527
Revises: f3b7d2c8a419
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1f6c3e9b527"
down_revision: Union[str, Sequence[str], None] = "f3b7d2c8a419"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: ``(table, record_type, column)`` — guarded by ``tckdb_guard_accepted_child``
#: against the accepted root named directly on the row. Every entry names an
#: ownership foreign key, never a provenance one; see the module docstring for
#: the per-table argument.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = (
    (
        "transition_state_validation_evidence",
        "transition_state_entry",
        "transition_state_entry_id",
    ),
    ("network_solve_channel_barrier", "network_solve", "solve_id"),
    ("network_solve_state_energy", "network_solve", "solve_id"),
    ("kinetics_interpretation_assignment", "kinetics", "kinetics_id"),
    ("kinetics_tunneling_application", "kinetics", "kinetics_id"),
)

#: No table here reaches its root through a parent row, so this revision adds
#: no ``tckdb_guard_accepted_via_child`` trigger. Declared empty rather than
#: omitted so ``tests/db/test_accepted_science_trigger_registry.py`` can read
#: the same three names off every revision in the regime.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = ()

#: TRUNCATE bypasses row triggers entirely.
_TRUNCATE_TABLES: tuple[str, ...] = tuple(table for table, _, _ in _DIRECT_CHILDREN)


def _trigger_name(prefix: str, table: str) -> str:
    """Mirror ``b6c1f4a8e703``'s naming, truncated to PostgreSQL's 63 bytes.

    Suffixed with the table rather than with a positional index, for the
    reason that revision gives: ``c6f2a9d4e7b1`` owns the ``trg_as_child_NN``
    sequence, and appending to it from a third revision would make every
    revision's numbering depend on the others'.
    """

    return f"trg_{prefix}_{table}"[:63]


def _child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    """Collapse the registry to one trigger per ``(table, record_type)``."""

    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


def upgrade() -> None:
    """Guard the five evidence tables with the accepted-science triggers."""

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
    for table in _TRUNCATE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_truncate", table)}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )


def downgrade() -> None:
    """Drop the guards, restoring the prior state exactly."""

    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
