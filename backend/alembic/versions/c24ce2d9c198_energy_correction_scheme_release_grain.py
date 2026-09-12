"""energy_correction_scheme: key on a software release, not a program

``energy_correction_scheme`` is an already-deployed table (two live rows
on the hosted Pi, 164 dependent ``applied_energy_correction`` rows), so
this is a new revision, never an edit to ``d861dfd60891`` or to
``b6d80e36dcec``. See ``docs/plans/correction-scheme-provenance.md``
(v2, §3-§4) for the full argument; this docstring states the shape and
the one hazard it creates.

Why release, not program
-------------------------
``b6d80e36dcec`` added ``software_id`` (a ``software`` FK -- program
identity only). The owner's ruling (v2 §0.1.6) is that this was too
coarse: an atom-energy or bond-additivity parameter set is the output of
a program's *build*-level numerics (integration grid, SCF thresholds,
a named basis set's internal definition) -- decisions that change
between releases of the same program, not just between programs. Every
other provenance-bearing table in this schema (``calculation``,
``thermo``, ``statmech``, ``kinetics``, ``transport``, ``network``, ...)
already keys on ``software_release``; ``energy_correction_scheme`` and
``frequency_scale_factor`` were the only two exceptions. This revision
closes the exception for the former; ``frequency_scale_factor`` gets its
own sibling revision later (plan §6), out of scope here.

This revision:

1. Adds ``software_release_id`` (nullable FK to ``software_release.id``).
2. Adds ``units`` to ``uq_energy_correction_scheme_identity`` (plan §3.3:
   the resolver's ``_assert_param_value_compatible`` is unit-blind, so a
   depositor re-sending the same correction in a different energy unit
   currently resolves onto the existing row and is told, wrongly, that
   its numbers conflict -- a remedy the schema did not offer because
   nothing distinguished the unit at the identity level).
3. Drops ``software_id``.

**No backfill, on this axis, at all** (owner ruling, v2 §0.2.9: *"I think
NULL as we cannot assume Gaussian"*). ``b6d80e36dcec`` backfilled
``software_id`` from this deployment's own ``calculation`` rows at each
scheme's level of theory; that derivation answered "which program ran
the calculations recorded at this level" -- a different question from
"which program computed this scheme's own parameters" (the two live
correction sets are bit-for-bit copies of an external curated table, not
outputs of any calculation this database ran). The derived value was
wrong, not merely unproven, so dropping ``software_id`` here removes it
rather than converting it, and no equivalent backfill is written for
``software_release_id``. Every row's ``software_release_id`` is ``NULL``
after this migration and stays that way until someone attests to a
program via ``PATCH /admin/energy-correction-schemes/{ref}/provenance``
(which itself gains the release grain as part of this same PR) or a
corrected re-upload.

The hazard this creates, and the pre-flight check that closes it
-------------------------------------------------------------------
``software_release_id`` is ``NULL`` on every row at the moment the new
index is created (no backfill, see above). Under
``NULLS NOT DISTINCT``, two rows that differ *only* by the column this
revision drops (``software_id``) would collapse into one the instant the
new index exists, because both would agree (``NULL``) on every column
the new index still checks. Widening the index with ``units`` can only
*split* rows (a strictly wider key is never less specific); replacing
``software_id`` with an all-``NULL`` ``software_release_id`` is the one
direction that can *merge* them, and with no backfill that risk is not
hypothetical on any database but this one.

Measured on the deployed Pi 2026-09-13: the collision query below
returns zero rows, so this deployment upgrades cleanly -- but the check
is not written for this deployment. It runs unconditionally, against
every database this revision is ever applied to, and aborts loudly
naming the colliding schemes' public refs rather than merging or picking
between them. See ``_refuse_software_only_collisions`` below.

Companion (same PR, not a separate one)
-----------------------------------------
``app/services/energy_correction_resolution.py``'s
``resolve_or_create_scheme`` widens its lookup to match this index
(``software_release_id`` and ``units`` added to the ``_match`` chain),
and ``EnergyCorrectionSchemeRef.software``
(``tckdb_schemas.energy_correction``) changes type from ``SoftwareRef``
(name only) to ``SoftwareReleaseRef`` (name, version, revision, build).
Without both, the widened index sits unused and every upload keeps
collapsing onto the first row. The admin attach-provenance route
(``app/api/routes/admin.py``) is repointed at the renamed column so it
keeps working, but its wire contract is unchanged here -- widening it to
accept a full release (version/revision/build) is PR 3's job, not this
one's.

Both ``upgrade()`` and ``downgrade()`` are implemented per the
already-deployed-table migration rules. ``downgrade()`` restores the
*column*, never a value -- re-deriving one is exactly what the no-backfill
ruling forbids, so ``software_id`` comes back ``NULL`` on every row, not
reconstructed. A downgrade attempted after two schemes have since been
distinguished only by release, or only by units, fails on a genuine
unique violation -- correct and loud, rather than silently re-merging two
libraries the wider index was built to keep apart.

Revision ID: c24ce2d9c198
Revises: b6d80e36dcec
Create Date: 2026-09-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c24ce2d9c198"
down_revision: Union[str, None] = "b6d80e36dcec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The widened identity minus the one column this revision replaces
#: (``software_id``/``software_release_id``) -- neither dropping the
#: former nor adding the latter can itself distinguish a group here, so
#: any group with more than one row is two schemes the deployed shape
#: distinguished only by their program, and the new all-``NULL``
#: ``software_release_id`` would merge them.
_COLLISION_QUERY = sa.text(
    """
    SELECT array_agg(public_ref ORDER BY id) AS refs, count(*) AS n
    FROM energy_correction_scheme
    GROUP BY kind, name, level_of_theory_id, version, units,
             source_literature_id, workflow_tool_release_id
    HAVING count(*) > 1
    """
)


def _refuse_software_only_collisions(bind) -> None:
    """Abort the upgrade rather than let the widened index silently merge
    two schemes that are distinguished today only by ``software_id``.

    See the module docstring's "hazard" section. Never merges, never
    picks a winner -- names the colliding public refs and tells the
    operator how to re-record the distinction before retrying.
    """
    collisions = bind.execute(_COLLISION_QUERY).mappings().all()
    if not collisions:
        return

    details = "; ".join(
        f"{row['n']} schemes sharing (kind, name, level_of_theory_id, "
        f"version, units, source_literature_id, workflow_tool_release_id): "
        f"{row['refs']}"
        for row in collisions
    )
    raise RuntimeError(
        "Cannot upgrade to c24ce2d9c198: this database holds "
        "energy_correction_scheme rows distinguished only by software_id "
        "-- the column this revision replaces with software_release_id, "
        "which is NULL on every row after upgrade (no backfill; see the "
        "module docstring). Creating the widened "
        "uq_energy_correction_scheme_identity index would silently merge "
        f"these rows into one. Affected: {details}. Re-record each "
        "scheme's program as a release via PATCH "
        "/admin/energy-correction-schemes/{ref}/provenance before "
        "re-running this upgrade -- this migration will never merge or "
        "pick between them itself."
    )


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_software_only_collisions(bind)

    op.add_column(
        "energy_correction_scheme",
        sa.Column(
            "software_release_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "software_release.id",
                deferrable=True,
                initially="IMMEDIATE",
                name="fk_energy_correction_scheme_software_release_id",
            ),
            nullable=True,
        ),
    )

    op.drop_index(
        "uq_energy_correction_scheme_identity",
        table_name="energy_correction_scheme",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_energy_correction_scheme_identity",
        "energy_correction_scheme",
        [
            "kind",
            "name",
            "level_of_theory_id",
            "version",
            "units",
            "source_literature_id",
            "software_release_id",
            "workflow_tool_release_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # No data step. No backfill on either the new column or the widened
    # index -- see the module docstring. This revision only makes it
    # *possible* to record a release; it records none.
    op.drop_column("energy_correction_scheme", "software_id")


def downgrade() -> None:
    op.add_column(
        "energy_correction_scheme",
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
        "uq_energy_correction_scheme_identity",
        table_name="energy_correction_scheme",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_energy_correction_scheme_identity",
        "energy_correction_scheme",
        [
            "kind",
            "name",
            "level_of_theory_id",
            "version",
            "source_literature_id",
            "software_id",
            "workflow_tool_release_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # Restores the column, never a value -- re-deriving software_id is
    # exactly what the no-backfill ruling forbids. Every row comes back
    # NULL, not reconstructed from software_release_id.
    op.drop_column("energy_correction_scheme", "software_release_id")
