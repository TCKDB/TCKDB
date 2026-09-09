"""energy_correction_scheme: widen identity to citation + software

``energy_correction_scheme`` is an already-deployed table (two live rows
on the hosted Pi), so this is a new revision, never an edit to
``d861dfd60891``. Two gaps, one root cause, one revision — see
``docs/plans/correction-scheme-provenance.md`` (§3):

1. **Citations were silently discarded.** ``resolve_or_create_scheme``
   dedups on ``(kind, name, level_of_theory_id, version)`` only;
   ``source_literature_id`` was never part of the lookup or the unique
   index, even though ``app/services/public_refs.py``'s own docstring for
   ``_canonical_energy_correction_scheme`` already claimed it was part of
   the row's identity. A depositor supplying a citation for a scheme
   identity that already existed got it dropped with no error.

2. **No software dimension existed at all.** ``FrequencyScaleFactor``
   (same file) already carries ``software_id`` + ``workflow_tool_release_id``
   with the comment "same LOT in Gaussian vs QChem can yield different
   factors" — true of atom-energy/BAC corrections too, and
   ``energy_correction_scheme`` had no equivalent column.

Both gaps are closed the same way: add the two columns (mirroring
``FrequencyScaleFactor`` exactly — nullable, same FK shape, same
explicit constraint name for the workflow-tool-release FK because the
naming-convention-derived name exceeds PostgreSQL's 63-byte identifier
limit) and widen the unique index to
``(kind, name, level_of_theory_id, version, source_literature_id,
software_id, workflow_tool_release_id)``, still ``NULLS NOT DISTINCT``.

**What the widened index newly permits:** two schemes with the same
``(kind, name, lot, version)`` and a different, non-null
``source_literature_id`` and/or ``software_id`` — previously impossible,
now two legitimately distinct, coexisting rows (a Gaussian scheme and an
ORCA scheme at the same LOT are not a collision at all).

**What it still rejects:** two schemes identical on every widened field,
including all three new/adjusted dimensions left ``NULL`` on both —
``NULLS NOT DISTINCT`` still treats those as duplicates, so two fully
uncited, fully software-less schemes of otherwise-identical identity
still collapse into one row, exactly as today. This is intentional
(plan §2.4): a real, un-discriminable ambiguity is stated, not
manufactured into two indistinguishable rows.

**Companion (same PR, not a separate one):**
``app/services/energy_correction_resolution.py``'s
``resolve_or_create_scheme`` widens its lookup to match this index, and
``EnergyCorrectionSchemeRef`` (``tckdb_schemas.energy_correction``) gains
``software``/``workflow_tool_release`` upload fields, mirroring
``FreqScaleFactorRef`` exactly. Without both, the widened index sits
unused.

**Backfill: deliberately none.** The two live rows
(``ecs_q5potmkzrmm6ynh2behv5kbfdu`` atom_energy, ``ecs_5dzse4an2emubgyxge2dpj4ae4``
bac_petersson, both at b3lyp/def2tzvp) stay ``NULL`` on both
``source_literature_id`` and ``software_id`` after this migration. The
plan's own investigation (docs/plans/correction-scheme-provenance.md §1.6)
found real, suggestive but *circumstantial* evidence for both — the
atom-energy values are a bit-for-bit match to RMG's own
``b3lyp2023/def2tzvp`` table, whose dict key names
``software='gaussian'``, and every one of the 416 calculations this
archive has ever recorded at that LOT ran Gaussian 16 — but neither fact
is something either row's actual depositor (whoever ran the Arkane
export that produced these two rows, which is not any ingestion script
currently in this repository) ever recorded in TCKDB. Writing either
field onto these rows via migration would assert a provenance link the
archive itself never received, which is the "assert from absence"
failure this archive's own design exists to avoid. **Flagging, not
silently deciding**, per the brief for this change: an operator applying
this migration to a database with rows in the same shape should expect
the same two columns to land ``NULL`` and require a real deposit (the
admin attach-provenance route added alongside this migration, or a
corrected re-upload) to fill them — not a migration-time guess.

Both ``upgrade()`` and ``downgrade()`` are implemented per the
already-deployed-table migration rules. ``downgrade()`` re-creates the
narrower index; this is only lossless today because both live rows are
already distinct under the narrower key. A downgrade attempted after a
real citation/software-only-distinguished duplicate has been deposited
would fail on a genuine constraint violation — the correct, loud
behavior for a downgrade that would otherwise silently re-merge two
rows the wider index was built to keep apart.

Revision ID: b6d80e36dcec
Revises: eb9793f23de6
Create Date: 2026-09-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6d80e36dcec"
down_revision: Union[str, None] = "eb9793f23de6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.add_column(
        "energy_correction_scheme",
        sa.Column(
            "workflow_tool_release_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "workflow_tool_release.id",
                deferrable=True,
                initially="IMMEDIATE",
                name="fk_energy_correction_scheme_workflow_tool_release_id",
            ),
            nullable=True,
        ),
    )

    op.drop_index(
        "uq_energy_correction_scheme_kind_name_lot_version",
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

    # No backfill. See module docstring: the two live rows' software and
    # literature are circumstantially inferable but not recorded facts,
    # and this migration deliberately leaves them NULL rather than
    # writing a guess into deployed scientific data.


def downgrade() -> None:
    op.drop_index(
        "uq_energy_correction_scheme_identity",
        table_name="energy_correction_scheme",
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_energy_correction_scheme_kind_name_lot_version",
        "energy_correction_scheme",
        ["kind", "name", "level_of_theory_id", "version"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.drop_column("energy_correction_scheme", "workflow_tool_release_id")
    op.drop_column("energy_correction_scheme", "software_id")
