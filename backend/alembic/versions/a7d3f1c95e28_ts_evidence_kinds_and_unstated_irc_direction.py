"""Two more kinds of TS validation evidence, and an IRC whose direction is unstated.

Two widenings of deployed tables, made together because both came from the same
audit of what a producer has and the schema had no place for.

1. Transition-state validation evidence
---------------------------------------
``transition_state_validation_evidence.kind`` admitted one value, ``irc``. It
now also admits ``energy_ordering`` (the saddle point lies above both wells)
and ``imaginary_mode`` (the frequency calculation found the expected imaginary
mode). ``uq_ts_validation_evidence_kind`` is unchanged: one record per kind per
entry.

``reconstruction_calculation_id`` becomes nullable, because an
``energy_ordering`` record is about several calculations and names them per
energy, in the new ``transition_state_validation_energy`` table, rather than
having one source of its own. The shape is enforced, not assumed:
``ck_transition_state_validation_evidence_source_calc_shape`` requires the
column to be NULL exactly for ``energy_ordering``. Every existing row is an
``irc`` row with the column set, so it satisfies the constraint as written and
there is nothing to backfill.

Three columns carry the ``imaginary_mode`` record: the imaginary-mode count,
the reaction-coordinate mode's frequency in cm^-1 (negative, as on a frequency
result) and the producer's displacement-agreement verdict. All three are
nullable, and ``ck_..._mode_cols_imag_only`` refuses them on any other kind. A
fourth check, ``ck_..._mapping_irc_only``, makes the participant mappings an
``irc``-only claim; every existing row with a mapping is an ``irc`` row.

``transition_state_validation_energy`` holds the compared energies. It is owned
by its evidence row, and through it by the transition-state entry, so it takes
the accepted-science freeze the evidence row has lived under since
``a1f6c3e9b527``: ``tckdb_guard_accepted_via_child`` dereferences the evidence
row to find the entry, and TRUNCATE takes the statement-level refusal. Without
it an energy could be rewritten under an accepted record, and the ordering the
evidence row claims would stop following from the numbers recorded beside it.
``source_calculation_id`` is provenance, not ownership, and is not a guarded
column, for the reason ``a1f6c3e9b527`` gives for every other source-calculation
link.

2. IRC direction and branch flags may be unstated
-------------------------------------------------
``calc_irc_result.direction``, ``has_forward`` and ``has_reverse`` were
``NOT NULL`` (the flags with a ``false`` server default), so a producer whose
log does not record which way an IRC ran had to drop the whole result or invent
an answer. All three become nullable and the flags lose their default: NULL is
"not stated", and ``has_forward = false`` is a claim that no forward point
exists. The default has to go with the constraint, not just the ``NOT NULL``:
an ORM insert omits a NULL attribute, so a surviving ``DEFAULT false`` would
turn every unstated flag back into that claim. No row changes: existing values
were required when written, so they were stated.

The guard trigger on ``calc_irc_result`` fires on row writes; ``ALTER TABLE``
does not fire it, so this revision does not touch that registry.

Downgrade
---------
Refuses, with counts, if the data cannot be put back into the narrower shape:
any ``energy_ordering`` or ``imaginary_mode`` evidence row, or any IRC result
with an unstated direction or flag. It does not delete them. Deleting evidence
to make a downgrade succeed would be the silent loss this table's freeze exists
to prevent, and filling ``false`` into an unstated flag would invent a claim.
Remove or complete those rows first, then downgrade.

Revision ID: a7d3f1c95e28
Revises: f1c8a4d7b263
Create Date: 2026-09-30
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a7d3f1c95e28"
down_revision: Union[str, Sequence[str], None] = "f1c8a4d7b263"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EVIDENCE = "transition_state_validation_evidence"
_ENERGY = "transition_state_validation_energy"

#: This revision adds no direct-child guard: the new table reaches its accepted
#: root through its evidence row. Declared empty rather than omitted so
#: ``tests/db/test_accepted_science_trigger_registry.py`` can read the same
#: names off every revision in the regime.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = ()

#: ``(table, record_type, child_column, parent_table, parent_pk, root_column)``.
#: An energy names no transition-state entry of its own; it reaches one through
#: the evidence row it belongs to.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        _ENERGY,
        "transition_state_entry",
        "evidence_id",
        _EVIDENCE,
        "id",
        "transition_state_entry_id",
    ),
)

#: TRUNCATE bypasses row triggers entirely.
_TRUNCATE_TABLES: tuple[str, ...] = (_ENERGY,)


def _trigger_name(prefix: str, table: str) -> str:
    """Mirror ``a1f6c3e9b527``'s naming, truncated to PostgreSQL's 63 bytes."""

    return f"trg_{prefix}_{table}"[:63]


def _child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    """Collapse the direct registry to one trigger per ``(table, record_type)``."""

    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


#: Check constraints added to the evidence table, by their full names.
_EVIDENCE_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "ck_transition_state_validation_evidence_source_calc_shape",
        "(kind = 'energy_ordering') = (reconstruction_calculation_id IS NULL)",
    ),
    (
        "ck_transition_state_validation_evidence_mode_cols_imag_only",
        "kind = 'imaginary_mode' OR (imaginary_frequency_count IS NULL "
        "AND imaginary_frequency_cm1 IS NULL AND mode_displacement_agrees IS NULL)",
    ),
    (
        "ck_transition_state_validation_evidence_imag_count_ge_0",
        "imaginary_frequency_count IS NULL OR imaginary_frequency_count >= 0",
    ),
    (
        "ck_transition_state_validation_evidence_imag_freq_negative",
        "imaginary_frequency_cm1 IS NULL OR (imaginary_frequency_cm1 < 0 "
        "AND imaginary_frequency_cm1 > '-Infinity'::float8)",
    ),
    (
        "ck_transition_state_validation_evidence_mapping_irc_only",
        "kind = 'irc' OR (coalesce(jsonb_typeof(reactant_participant_mapping), 'null') = 'null' "
        "AND coalesce(jsonb_typeof(product_participant_mapping), 'null') = 'null')",
    ),
)

_KIND_CHECK = "ck_transition_state_validation_evidence_ts_validation_kind"


def upgrade() -> None:
    # -- 1. evidence kinds ---------------------------------------------------
    op.execute(f"ALTER TABLE public.{_EVIDENCE} DROP CONSTRAINT {_KIND_CHECK}")
    op.execute(
        f"ALTER TABLE public.{_EVIDENCE} ADD CONSTRAINT {_KIND_CHECK} "
        "CHECK (kind IN ('irc', 'energy_ordering', 'imaginary_mode'))"
    )
    op.execute(f"ALTER TABLE public.{_EVIDENCE} ALTER COLUMN reconstruction_calculation_id DROP NOT NULL")
    op.add_column(_EVIDENCE, sa.Column("imaginary_frequency_count", sa.Integer(), nullable=True))
    op.add_column(_EVIDENCE, sa.Column("imaginary_frequency_cm1", sa.Float(), nullable=True))
    op.add_column(_EVIDENCE, sa.Column("mode_displacement_agrees", sa.Boolean(), nullable=True))
    for name, predicate in _EVIDENCE_CHECKS:
        op.execute(f"ALTER TABLE public.{_EVIDENCE} ADD CONSTRAINT {name} CHECK ({predicate})")

    op.create_table(
        _ENERGY,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("participant", sa.Text(), nullable=False),
        sa.Column("energy_kind", sa.Text(), nullable=False),
        sa.Column("energy_hartree", sa.Float(), nullable=False),
        sa.Column("source_calculation_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "participant ~ '^(ts|reactant:[1-9][0-9]*|product:[1-9][0-9]*)$'",
            name=op.f("ck_transition_state_validation_energy_participant_shape"),
        ),
        sa.CheckConstraint(
            "energy_kind IN ('electronic', 'e0')",
            name=op.f("ck_transition_state_validation_energy_energy_kind"),
        ),
        sa.CheckConstraint(
            "energy_hartree < 0 AND energy_hartree > '-Infinity'::float8",
            name=op.f("ck_transition_state_validation_energy_energy_finite_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            [f"{_EVIDENCE}.id"],
            name="fk_ts_validation_energy_evidence",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["source_calculation_id"],
            ["calculation.id"],
            name="fk_ts_validation_energy_source_calc",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_ENERGY}")),
        sa.UniqueConstraint(
            "evidence_id",
            "participant",
            "energy_kind",
            name="uq_ts_validation_energy_slot",
        ),
    )
    op.create_index(
        "ix_ts_validation_energy_source_calc", _ENERGY, ["source_calculation_id"], unique=False
    )

    for table, record_type, child_column, parent_table, parent_pk, root_column in _VIA_CHILDREN:
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

    # -- 2. IRC direction and flags may be unstated --------------------------
    op.execute("ALTER TABLE public.calc_irc_result ALTER COLUMN direction DROP NOT NULL")
    for column in ("has_forward", "has_reverse"):
        op.execute(f"ALTER TABLE public.calc_irc_result ALTER COLUMN {column} DROP NOT NULL")
        op.execute(f"ALTER TABLE public.calc_irc_result ALTER COLUMN {column} DROP DEFAULT")


def _refuse_if_any(description: str, query: str) -> None:
    count = op.get_bind().execute(sa.text(query)).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot downgrade: {count} {description}. The narrower schema has no "
            "place for them, and deleting evidence or inventing a value to make "
            "the downgrade succeed would be a silent loss. Remove or complete "
            "those rows first."
        )


def downgrade() -> None:
    _refuse_if_any(
        "transition_state_validation_evidence row(s) of kind 'energy_ordering' or 'imaginary_mode'",
        f"SELECT count(*) FROM {_EVIDENCE} WHERE kind <> 'irc'",
    )
    _refuse_if_any(
        "calc_irc_result row(s) with an unstated direction, has_forward or has_reverse",
        "SELECT count(*) FROM calc_irc_result "
        "WHERE direction IS NULL OR has_forward IS NULL OR has_reverse IS NULL",
    )

    for column in ("has_forward", "has_reverse"):
        op.execute(f"ALTER TABLE public.calc_irc_result ALTER COLUMN {column} SET DEFAULT false")
        op.execute(f"ALTER TABLE public.calc_irc_result ALTER COLUMN {column} SET NOT NULL")
    op.execute("ALTER TABLE public.calc_irc_result ALTER COLUMN direction SET NOT NULL")

    for table in _TRUNCATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for item in _VIA_CHILDREN:
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_via', item[0])} ON public.{item[0]}")
    op.drop_index("ix_ts_validation_energy_source_calc", table_name=_ENERGY)
    op.drop_table(_ENERGY)

    for name, _ in reversed(_EVIDENCE_CHECKS):
        op.execute(f"ALTER TABLE public.{_EVIDENCE} DROP CONSTRAINT {name}")
    op.drop_column(_EVIDENCE, "mode_displacement_agrees")
    op.drop_column(_EVIDENCE, "imaginary_frequency_cm1")
    op.drop_column(_EVIDENCE, "imaginary_frequency_count")
    op.execute(f"ALTER TABLE public.{_EVIDENCE} ALTER COLUMN reconstruction_calculation_id SET NOT NULL")
    op.execute(f"ALTER TABLE public.{_EVIDENCE} DROP CONSTRAINT {_KIND_CHECK}")
    op.execute(f"ALTER TABLE public.{_EVIDENCE} ADD CONSTRAINT {_KIND_CHECK} CHECK (kind IN ('irc'))")
