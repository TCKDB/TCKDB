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

**Citation: no backfill.** The two live rows
(``ecs_q5potmkzrmm6ynh2behv5kbfdu`` atom_energy, ``ecs_5dzse4an2emubgyxge2dpj4ae4``
bac_petersson, both at b3lyp/def2tzvp) stay ``NULL`` on
``source_literature_id`` after this migration. Nothing in this archive's
own records states which paper either correction came from — the RMG
correspondence noted in the plan (§1.6) is an external document, not
this database's own data — so there is no derivation to make here, only
a guess, and the archive does not write those in. It requires a real
deposit (the admin attach-provenance route added alongside this
migration, or a corrected re-upload).

**Software / workflow-tool-release: backfilled, narrowly.** This is a
late addition to the plan, added on the owner's explicit ruling after
the plan's own no-backfill recommendation, and it is *not* the same
move the plan warned against. The plan's objection to backfilling
software was that the only available evidence was circumstantial and
external (RMG's dict-key naming, a general "this archive's Gaussian
calculations happen to cluster here" observation). The owner's
derivation is neither: trace the scheme's own
``level_of_theory_id`` to the ``calculation`` rows recorded *in this
database* at that level, and read off their software. That is this
archive's own data restating itself through an FK join, the same kind
of derivation ``_build_software_release_summary`` already performs
read-side for every FSF row — not an inference from an outside source.

The safety condition is what keeps this from becoming the same mistake
under a different name, and it is enforced in the SQL itself, not
merely documented: for a given scheme's level of theory, ``software_id``
is set only when every calculation recorded at that level agrees on
*exactly one* distinct software (``COUNT(DISTINCT software_id) = 1``
over that level's calculations); zero or several, the column is left
``NULL``. No most-common, no first-match, no tie-break — an ambiguous
derivation is not a fact, and this migration never manufactures one.
The same rule, independently, backfills ``workflow_tool_release_id``
from the calculations' own ``workflow_tool_release_id`` where *those
that recorded one at all* agree unanimously (a level of theory where
only a minority of calculations recorded a release, but every one that
did names the same release, still counts as unambiguous — the rows that
recorded nothing are silent, not disagreeing). Both checks are scoped to
the three kinds the software axis applies to
(``atom_energy``/``bac_petersson``/``bac_melius``); the other kinds
carry no ``level_of_theory_id`` in the first place, so the join finds
nothing for them regardless, but the kind filter is written explicitly
rather than relying on that alone.

This backfill only ever *fills a currently-NULL column* — it is
idempotent (a second run finds nothing left to set) and never
overwrites a value already on the row, from an earlier backfill, an
admin attach-provenance call, or an upload. See
``_backfill_software_and_workflow_tool_release`` below for the exact
SQL; it prints how many rows it set and how many it left ``NULL`` (and
why: ambiguous, or no calculation recorded at that level at all) so an
operator applying this migration to a different database sees the same
accounting this revision's own author saw.

Both ``upgrade()`` and ``downgrade()`` are implemented per the
already-deployed-table migration rules. ``downgrade()`` re-creates the
narrower index and **drops the two new columns outright — any value the
backfill (or a later admin attach-provenance call) set is lost with
them.** That is inherent to dropping the columns, not a defect specific
to this revision, but it means a downgrade-then-upgrade cycle does not
restore backfilled software/workflow-tool-release values; it restores
the *columns*, freshly ``NULL``, and the upgrade's backfill step runs
again from the calculation data and reconstructs the same answer only
because that data itself is untouched by this revision. The narrower
index recreation is only lossless today because both live rows are
already distinct under the narrower key; a downgrade attempted after a
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

#: Kinds the software axis applies to (plan §1.6): the only kinds whose
#: numeric parameters are literally computed by a specific program at a
#: specific level of theory. ``atom_hf``/``atom_thermal``/``soc`` are
#: physical/reference constants and are never touched by the backfill.
_SOFTWARE_SCOPED_KINDS: tuple[str, ...] = (
    "atom_energy",
    "bac_petersson",
    "bac_melius",
)


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

    # Data step, after the index exists: if the backfill below ever made
    # two rows identical on the widened identity, the index itself
    # refuses the write rather than silently merging them. See the
    # module docstring for the derivation and its safety condition.
    _backfill_software_and_workflow_tool_release(op.get_bind())

    # Citation: deliberately no backfill. See module docstring -- the two
    # live rows' citation is not derivable from this archive's own data
    # the way software/workflow-tool-release is.


def _backfill_software_and_workflow_tool_release(bind) -> None:
    """Derive ``software_id``/``workflow_tool_release_id`` from this
    archive's own ``calculation`` records, where the derivation is
    unambiguous, and only there.

    Idempotent and non-destructive by construction: every statement below
    is scoped to rows whose target column ``IS NULL``, so re-running this
    against an already-backfilled (or already admin-provenanced, or
    already re-uploaded-with-software) row touches nothing. Called from
    :func:`upgrade`; also called directly, with a plain connection, by
    ``tests/db/test_energy_correction_scheme_backfill.py`` so the
    unambiguous/ambiguous/already-set/idempotent cases are each provable
    against a constructed fixture rather than only against the two real
    rows this repository cannot put in a test.

    :param bind: A SQLAlchemy ``Connection`` (``op.get_bind()`` inside a
        migration, or a plain connection in a test).
    """
    kinds_sql = ", ".join(f"'{k}'" for k in _SOFTWARE_SCOPED_KINDS)

    eligible_software = bind.execute(
        sa.text(
            f"""
            SELECT count(*) FROM energy_correction_scheme
            WHERE software_id IS NULL
              AND level_of_theory_id IS NOT NULL
              AND kind IN ({kinds_sql})
            """
        )
    ).scalar_one()

    software_result = bind.execute(
        sa.text(
            f"""
            UPDATE energy_correction_scheme ecs
            SET software_id = sub.only_software_id
            FROM (
                SELECT c.lot_id AS lot_id,
                       min(sr.software_id) AS only_software_id
                FROM calculation c
                JOIN software_release sr ON sr.id = c.software_release_id
                WHERE c.lot_id IS NOT NULL
                GROUP BY c.lot_id
                HAVING count(DISTINCT sr.software_id) = 1
            ) sub
            WHERE ecs.level_of_theory_id = sub.lot_id
              AND ecs.software_id IS NULL
              AND ecs.kind IN ({kinds_sql})
            """
        )
    )
    software_set = software_result.rowcount
    print(
        "energy_correction_scheme software backfill: "
        f"{software_set} row(s) set, "
        f"{eligible_software - software_set} left NULL "
        "(ambiguous software across calculations at that level of theory, "
        "or no calculation recorded there)."
    )

    eligible_wtr = bind.execute(
        sa.text(
            f"""
            SELECT count(*) FROM energy_correction_scheme
            WHERE workflow_tool_release_id IS NULL
              AND level_of_theory_id IS NOT NULL
              AND kind IN ({kinds_sql})
            """
        )
    ).scalar_one()

    wtr_result = bind.execute(
        sa.text(
            f"""
            UPDATE energy_correction_scheme ecs
            SET workflow_tool_release_id = sub.only_wtr_id
            FROM (
                SELECT c.lot_id AS lot_id,
                       min(c.workflow_tool_release_id) AS only_wtr_id
                FROM calculation c
                WHERE c.lot_id IS NOT NULL
                  AND c.workflow_tool_release_id IS NOT NULL
                GROUP BY c.lot_id
                HAVING count(DISTINCT c.workflow_tool_release_id) = 1
            ) sub
            WHERE ecs.level_of_theory_id = sub.lot_id
              AND ecs.workflow_tool_release_id IS NULL
              AND ecs.kind IN ({kinds_sql})
            """
        )
    )
    wtr_set = wtr_result.rowcount
    print(
        "energy_correction_scheme workflow_tool_release backfill: "
        f"{wtr_set} row(s) set, "
        f"{eligible_wtr - wtr_set} left NULL "
        "(ambiguous among the calculations at that level of theory that "
        "recorded one, none of them recorded one, or no calculation "
        "recorded there)."
    )


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

    # Drops the columns and, with them, any value the backfill or a later
    # admin attach-provenance call set. See module docstring.
    op.drop_column("energy_correction_scheme", "workflow_tool_release_id")
    op.drop_column("energy_correction_scheme", "software_id")
