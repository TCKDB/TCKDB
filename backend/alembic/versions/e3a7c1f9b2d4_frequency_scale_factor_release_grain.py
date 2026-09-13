"""frequency_scale_factor: key on a software release, not a program

``frequency_scale_factor`` is an already-deployed table (12 live rows on
the hosted Pi), so this is a new revision, never an edit to
``d861dfd60891`` or to ``c24ce2d9c198``. See
``docs/plans/correction-scheme-provenance.md`` (v2, §6) for the full
argument -- it is the ``frequency_scale_factor`` sibling of
``c24ce2d9c198`` (``energy_correction_scheme``'s release-grain
migration); this docstring restates the shape for this table and the one
divergence from that revision (no ``units`` member).

Why release, not program
-------------------------
``frequency_scale_factor.software_id`` has carried a program-only FK
since the initial schema. The owner's ruling (plan v2 §0.1.6 / §0.2.12)
is that a harmonic frequency scale factor is fit against a program's own
*build*-level vibrational frequencies -- the same reasoning §3.1 of the
plan makes for ``energy_correction_scheme``'s atom-energy and
bond-additivity parameters -- so the factor is release-specific, not
merely program-specific. ``frequency_scale_factor`` was, after
``c24ce2d9c198``, the last table in this schema keyed on ``software``
directly instead of ``software_release``.

This revision:

1. Adds ``software_release_id`` (nullable FK to ``software_release.id``).
2. Rebuilds ``uq_frequency_scale_factor_identity`` on
   ``(level_of_theory_id, software_release_id, scale_kind, value,
   source_literature_id, workflow_tool_release_id)``.
3. Drops ``software_id``.

**No ``units`` member, unlike ``c24ce2d9c198``.** A frequency scale
factor is a dimensionless multiplier (``value > 0`` is the only
constraint the column carries) -- there is no unit convention for it to
disagree about, so plan §3.3's argument for widening the identity with
``units`` does not transfer here (plan §6).

**No backfill, on this axis, at all** (owner ruling, v2 §0.2.9: *"I think
NULL as we cannot assume Gaussian"* -- made about the sibling table, and
ruling 12 applies it here too). Every row's ``software_release_id`` is
``NULL`` after this migration and stays that way until someone attests to
a program via a corrected re-deposit (frequency_scale_factor has no admin
attach-provenance route the way ``energy_correction_scheme`` does; see
the plan's §5 for that route's scope, which this revision does not
extend).

The hazard this creates, and the pre-flight check that closes it
-------------------------------------------------------------------
``software_release_id`` is ``NULL`` on every row at the moment the new
index is created (no backfill, see above). Under ``NULLS NOT DISTINCT``,
two rows that differ *only* by the column this revision drops
(``software_id``) would collapse into one the instant the new index
exists, because both would agree (``NULL``) on every column the new
index still checks. Replacing ``software_id`` with an all-``NULL``
``software_release_id`` is the one direction that can *merge* rows, and
with no backfill that risk is not hypothetical on any database this
revision is ever applied to.

Measured on the deployed Pi 2026-09-13 (plan §6): the collision query
below returns zero rows, and the 12 live rows include 10 that share
``(level_of_theory_id, scale_kind, value, source_literature_id)`` and
are distinguished only by ``workflow_tool_release_id`` -- a column this
revision's identity still checks, so those 10 remain 10 distinct rows
after the upgrade. The check still runs unconditionally, against every
database this revision is ever applied to, and aborts loudly naming the
colliding schemes' public refs rather than merging or picking between
them. See ``_refuse_software_only_collisions`` below.

Companion (same PR, not a separate one)
-----------------------------------------
``app/services/energy_correction_resolution.py``'s
``resolve_or_create_freq_scale_factor_ref`` (and its
``_resolve_or_create_fsf_row`` core) widens its lookup to match this
index (``software_release_id`` replaces ``software_id`` in the ``_match``
chain), and ``FreqScaleFactorRef.software``
(``tckdb_schemas.fragments.refs``) changes type from ``SoftwareRef``
(name only) to ``SoftwareReleaseRef`` (name, version, revision, build).
Without both, the widened index sits unused and every upload keeps
resolving against the bare program. The read layer's fabricated
``SoftwareReleaseSummary`` (``software_release_id=0,
software_release_ref=""``) is replaced with a real
``software_release`` -> ``software`` join, matching what ``c24ce2d9c198``
+ its companion PR already did for ``energy_correction_scheme``.

Both ``upgrade()`` and ``downgrade()`` are implemented per the
already-deployed-table migration rules. ``downgrade()`` restores the
*column*, never a value -- re-deriving one is exactly what the no-backfill
ruling forbids, so ``software_id`` comes back ``NULL`` on every row, not
reconstructed. A downgrade attempted after two rows have since been
distinguished only by release fails on a genuine unique violation --
correct and loud, rather than silently re-merging two distinct factors.

Revision ID: e3a7c1f9b2d4
Revises: c24ce2d9c198
Create Date: 2026-09-13 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a7c1f9b2d4"
down_revision: Union[str, None] = "c24ce2d9c198"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The widened identity minus the one column this revision replaces
#: (``software_id``/``software_release_id``) -- neither dropping the
#: former nor adding the latter can itself distinguish a group here, so
#: any group with more than one row is two factors the deployed shape
#: distinguished only by their program, and the new all-``NULL``
#: ``software_release_id`` would merge them.
_COLLISION_QUERY = sa.text(
    """
    SELECT array_agg(public_ref ORDER BY id) AS refs, count(*) AS n
    FROM frequency_scale_factor
    GROUP BY level_of_theory_id, scale_kind, value,
             source_literature_id, workflow_tool_release_id
    HAVING count(*) > 1
    """
)


def _refuse_software_only_collisions(bind) -> None:
    """Abort the upgrade rather than let the widened index silently merge
    two frequency scale factors that are distinguished today only by
    ``software_id``.

    See the module docstring's "hazard" section. Never merges, never
    picks a winner -- names the colliding public refs.
    """
    collisions = bind.execute(_COLLISION_QUERY).mappings().all()
    if not collisions:
        return

    details = "; ".join(
        f"{row['n']} factors sharing (level_of_theory_id, scale_kind, "
        f"value, source_literature_id, workflow_tool_release_id): "
        f"{row['refs']}"
        for row in collisions
    )
    raise RuntimeError(
        "Cannot upgrade to e3a7c1f9b2d4: this database holds "
        "frequency_scale_factor rows distinguished only by software_id "
        "-- the column this revision replaces with software_release_id, "
        "which is NULL on every row after upgrade (no backfill; see the "
        "module docstring). Creating the widened "
        "uq_frequency_scale_factor_identity index would silently merge "
        f"these rows into one. Affected: {details}. This migration will "
        "never merge or pick between them itself.\n\n"
        "To proceed, make the colliding rows distinct on a column the "
        "new identity still checks -- level_of_theory_id, scale_kind, "
        "value, source_literature_id or workflow_tool_release_id -- and "
        "re-run the upgrade. In practice that means either giving one "
        "factor a distinguishing citation or workflow-tool release, or "
        "deleting the redundant row and repointing any statmech/applied "
        "rows that reference it at the survivor. Do this deliberately: "
        "which of two same-valued factors is the real one is a curation "
        "decision, not a mechanical one."
    )


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_software_only_collisions(bind)

    op.add_column(
        "frequency_scale_factor",
        sa.Column(
            "software_release_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "software_release.id",
                deferrable=True,
                initially="IMMEDIATE",
                name="fk_frequency_scale_factor_software_release_id",
            ),
            nullable=True,
        ),
    )

    op.drop_index(
        "uq_frequency_scale_factor_identity",
        table_name="frequency_scale_factor",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_frequency_scale_factor_identity",
        "frequency_scale_factor",
        [
            "level_of_theory_id",
            "software_release_id",
            "scale_kind",
            "value",
            "source_literature_id",
            "workflow_tool_release_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # No data step. No backfill on either the new column or the widened
    # index -- see the module docstring. This revision only makes it
    # *possible* to record a release; it records none.
    op.drop_column("frequency_scale_factor", "software_id")


def downgrade() -> None:
    op.add_column(
        "frequency_scale_factor",
        sa.Column(
            "software_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "software.id", deferrable=True, initially="IMMEDIATE"
            ),
            nullable=True,
        ),
    )

    op.drop_index(
        "uq_frequency_scale_factor_identity",
        table_name="frequency_scale_factor",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_frequency_scale_factor_identity",
        "frequency_scale_factor",
        [
            "level_of_theory_id",
            "software_id",
            "scale_kind",
            "value",
            "source_literature_id",
            "workflow_tool_release_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # Restores the column, never a value -- re-deriving software_id is
    # exactly what the no-backfill ruling forbids. Every row comes back
    # NULL, not reconstructed from software_release_id.
    op.drop_column("frequency_scale_factor", "software_release_id")
