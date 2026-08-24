"""Give a coarse pre-optimisation back the basin it belongs to.

``calculation.conformer_observation_id`` is the anchor that makes a
calculation count as evidence for a torsional basin. Until the write-path
fix that ships with this revision, the computed-reaction workflow resolved
that anchor from ``geometry_key`` alone and returned silently when the
producer had not supplied one::

    if calc_in.geometry_key is None:
        return                       # silent no-op

The wire schema excuses exactly one calculation type from supplying
``geometry_key`` -- ``opt`` -- because a staged optimisation's earlier stage
genuinely did not run on a declared conformer geometry. So the one type
allowed to omit the key was the one type whose anchor was then dropped, and
nothing anywhere reported it: no constraint, no service validation, no trust
or machine-review rule tests this column against NULL.

What this revision repairs, measured
------------------------------------
On the deployed database before this revision:

======================================================  =====
species-owned ``opt`` rows with a NULL anchor              43
of those, ``optimized_from`` parent of an anchored calc    43
distinct species entries they span                         36
conformer groups those entries own (one each)              36
======================================================  =====

Every one of the 43 is uniform: ``type = 'opt'``, species-owned,
``parameters_json = {"final_settings": {"optimization_stage": "coarse"}}``,
and the ``optimized_from`` parent of exactly one calculation that *is*
anchored. Their target is therefore not a guess -- it is the observation
their own refinement already sits on.

The 124 unanchored transition-state calculations are **not** in scope and
are not a defect. ``conformer_group.species_entry_id`` is NOT NULL and has
no TS counterpart (DR-0004), so a TS calculation has no observation it
*could* be anchored to. The predicate's ``species_entry_id IS NOT NULL``
is what keeps them out.

The predicate, clause by clause
-------------------------------
Narrow on purpose, and narrow in a way that survives being re-run on a
database that does not look like today's:

* ``parent.species_entry_id IS NOT NULL`` -- species-owned. Excludes every
  TS calculation, per above.
* ``parent.type = 'opt'`` -- only an optimisation has an earlier stage.
* ``parent.conformer_observation_id IS NULL`` -- only repair what is
  broken. This is also what makes the revision idempotent: a second run
  selects nothing.
* joined through ``dependency_role = 'optimized_from'`` -- the edge that
  means "this calculation was optimised from that one". Not ``freq_on``,
  ``single_point_on`` or ``scan_parent``: those join two calculations that
  are genuinely different evidence, and inheriting an anchor across one
  would be a scientific error rather than a repair.
* ``refinement.conformer_observation_id IS NOT NULL`` -- the anchor is
  copied from a child that has one. Nothing is invented.
* ``grp.species_entry_id = parent.species_entry_id`` -- the observation
  must belong to a group owned by the parent's *own* species entry. On the
  deployed database no row violates this, and the clause is here for the
  same reason ``c1d8f4a25b30`` kept three null tests that selected nothing
  extra: it encodes the rule, not the data. Without it a malformed edge
  could file a calculation under another species's basin, which is worse
  than leaving it unanchored.
* ``HAVING count(*) = 1`` -- exactly one qualifying anchored child. A
  parent feeding two refinements on two different observations has no
  unambiguous answer, and this revision does not choose one for it. (The
  schema already enforces at most one ``optimized_from`` parent per child;
  it does not bound children per parent, so this is a real check rather
  than a restatement.)

Nothing is hardcoded. The 43 are derived from the dependency edge, so this
revision does the right thing on a lab instance whose rows are different
ones -- or none.

What this does and does not change about what a reader is told
--------------------------------------------------------------
``calculation_count`` rises by 43 and ``geometry_count`` by 37 across those
36 groups (39 of the 43 output geometries are distinct; 37 are new to their
target group). Both are inventories -- what
``include=calculations`` and ``include=geometries`` hand back -- and a row
and a geometry genuinely entered them.

``evidence_coverage`` does **not** move: each value counts *observations*
with at least one calculation of that kind, and every target observation
already carries the anchored ``opt`` refinement this revision copies from.
Measured across the 36 groups: 60 before, 60 after.

``optimization_chain_count`` does **not** move either, and that is the
number that makes this repair safe to make at all. It counts optimisation
*chains*, excluding any ``opt`` row that feeds a refinement on the same
observation. Before this revision a coarse stage was excluded because its
NULL anchor never equalled anything; after it, it is excluded because it
now genuinely feeds a refinement on that very observation. Same answer,
better reason. Measured across the 36 groups: 60 before, 60 after, and zero
groups differ. PR #252's follow-up test
(``test_cg_anchoring_a_superseded_stage_is_evidence_neutral``) asserts this
directly, on a group built for the purpose.

So this revision restores a provenance link and changes no number that
describes how much evidence a basin has. That is what makes it a repair
rather than a correction of a scientific claim -- see the declaration
below.

Why it declares a repair
------------------------
``calculation`` is an accepted-science root: ``trg_as_root_calculation``
refuses UPDATE on any row that has ever been approved. On the deployed
database none of the 43 has been (all 43 carry ``record_review.status =
'not_reviewed'`` with a NULL ``first_approved_at``), so the guard would
permit every one of these writes today and the declaration is inert.

It is here for the database where that is not true. An operator-managed
instance that has approved one of these rows would otherwise take a hard
trigger refusal partway through a migration, and their only mechanical
option would be ``ALTER TABLE ... DISABLE TRIGGER`` -- exactly the
unrecorded capability ``e2c9a4f7b163`` built this mechanism to replace. The
declaration names one column, and the guard enforces that: an UPDATE that
touched anything else on ``calculation`` would be refused by name.

Declaring it is defensible only because of the paragraph above. A repair
may not change a scientific claim, and the measured neutrality of
``evidence_coverage`` and ``optimization_chain_count`` is the evidence that
this one does not -- it restores a link that should never have been
dropped, and every number describing the basin's evidence is unchanged.

The ledger, and why the downgrade needs it
------------------------------------------
``tckdb_repair_permits`` writes an ``accepted_science_repair_change`` row
per changed row -- but only for rows that are *accepted*, because that is
the only path on which the guard runs. With none of the 43 accepted it
would record nothing at all, so this revision appends the change rows for
the rest itself, under the same declaration and in the same transaction.
The insert-time validation covers it exactly (same table, declared column,
owner role), and the result is a complete ledger rather than a partial one.

That ledger is not decoration. It is the only thing that makes the
downgrade exact. After the upgrade, a repaired row is structurally
indistinguishable from a coarse stage that was anchored correctly all
along: species-owned ``opt``, anchored to the same observation as its
refinement. **There are 20 such rows on the deployed database already**
(measured; every one of them within a single observation). A downgrade that
re-derived its target set from the shape of the data would null those 20
too, destroying links this revision never touched. So the downgrade reads
back exactly the rows the upgrade wrote, by primary key, from the ledger.

Selecting on ``before_json -> 'conformer_observation_id' = 'null'`` rather
than on the declaration's prose is deliberate, for the reason
``c1d8f4a25b30`` gives: a structured containment test survives somebody
rewording a sentence. It also separates the two directions without a
direction column -- an upgrade's change row has a null ``before``, a
downgrade's has a null ``after``.

What downgrade() will not do
----------------------------
Restore an anchor a curator has since changed. The reverse UPDATE requires
the row's current anchor to still be the value this revision set; a row
that has moved on is left alone. That is a deliberate refusal, and the same
one ``c1d8f4a25b30`` makes.

It also cannot un-append the ledger: both repair tables are append-only and
un-truncatable. The downgrade declares its own repair and records its own
change rows, so an upgraded-then-downgraded database reads as two recorded
transitions -- which is what happened.

Revision ID: b8e3f1a7c250
Revises: c1d8f4a25b30
Create Date: 2026-08-25

"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "b8e3f1a7c250"
down_revision: Union[str, Sequence[str], None] = "c1d8f4a25b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The one column either direction of this revision may write. The guard
#: compares OLD against NEW and refuses an UPDATE that touches anything
#: else, so this is enforced rather than promised.
_DECLARED_COLUMN = "conformer_observation_id"

_UPGRADE_REASON = (
    "Backfill b8e3f1a7c250: species-owned coarse pre-optimisations were "
    "persisted with no conformer_observation_id because the computed-reaction "
    "workflow resolved the anchor from geometry_key alone and returned "
    "silently when a producer -- correctly -- omitted it. Anchored to the "
    "observation their own optimized_from refinement already sits on. "
    "evidence_coverage and optimization_chain_count are unchanged."
)

_DOWNGRADE_REASON = (
    "Downgrade of b8e3f1a7c250: restoring the NULL conformer_observation_id "
    "this revision had backfilled, for exactly the rows it wrote."
)

#: Rows this revision may anchor, derived from the dependency edge. See the
#: module docstring for the argument behind each clause.
_CANDIDATES = """
    SELECT
        parent.id AS parent_id,
        min(refinement.conformer_observation_id) AS observation_id
      FROM calculation AS parent
      JOIN calculation_dependency AS edge
        ON edge.parent_calculation_id = parent.id
       AND edge.dependency_role = 'optimized_from'
      JOIN calculation AS refinement
        ON refinement.id = edge.child_calculation_id
      JOIN conformer_observation AS obs
        ON obs.id = refinement.conformer_observation_id
      JOIN conformer_group AS grp
        ON grp.id = obs.conformer_group_id
     WHERE parent.conformer_observation_id IS NULL
       AND parent.species_entry_id IS NOT NULL
       AND parent.type = 'opt'
       AND refinement.conformer_observation_id IS NOT NULL
       AND grp.species_entry_id = parent.species_entry_id
     GROUP BY parent.id
    HAVING count(*) = 1
"""


def _declare_repair(reason: str) -> None:
    """Stand the accepted-science guard down for one column, on the record.

    Inert wherever no affected row has ever been approved -- which is every
    row on the deployed database. It exists so that an instance where one
    *has* been approved gets a recorded repair instead of a failed migration.
    """
    op.get_bind().execute(
        text(
            """
            INSERT INTO accepted_science_repair (
                target_table, declared_columns, alembic_revision, reason
            ) VALUES (
                'calculation',
                ARRAY[CAST(:column AS text)],
                CAST(:revision AS text),
                CAST(:reason AS text)
            )
            """
        ),
        {"column": _DECLARED_COLUMN, "revision": revision, "reason": reason},
    )


def upgrade() -> None:
    bind = op.get_bind()
    _declare_repair(_UPGRADE_REASON)
    bind.execute(
        text(
            f"""
            WITH unambiguous AS ({_CANDIDATES}),
            repaired AS (
                UPDATE calculation
                   SET conformer_observation_id = unambiguous.observation_id
                  FROM unambiguous
                 WHERE calculation.id = unambiguous.parent_id
             RETURNING calculation.id AS calculation_id,
                       unambiguous.observation_id AS observation_id
            )
            INSERT INTO accepted_science_repair_change (
                repair_id, record_type, record_id, target_schema, target_table,
                row_identity, changed_columns, before_json, after_json
            )
            SELECT
                declaration.id,
                'calculation',
                repaired.calculation_id,
                'public',
                'calculation',
                jsonb_build_object('id', repaired.calculation_id),
                ARRAY[CAST(:column AS text)],
                jsonb_build_object(CAST(:column AS text), NULL),
                jsonb_build_object(
                    CAST(:column AS text), repaired.observation_id
                )
              FROM repaired
              CROSS JOIN (
                    SELECT id
                      FROM accepted_science_repair
                     WHERE target_table = 'calculation'
                       AND xact_id = (pg_current_xact_id())::text::bigint
                   ) AS declaration
             -- The guard already recorded any row that was accepted; this
             -- fills in the rest, so the ledger covers every row written.
             WHERE NOT tckdb_record_is_accepted(
                       CAST('calculation' AS submission_record_type),
                       repaired.calculation_id
                   )
            """
        ),
        {"column": _DECLARED_COLUMN},
    )


def downgrade() -> None:
    bind = op.get_bind()
    _declare_repair(_DOWNGRADE_REASON)
    bind.execute(
        text(
            """
            WITH written AS (
                SELECT DISTINCT
                       change.record_id AS calculation_id,
                       CAST(
                           change.after_json ->> CAST(:column AS text) AS bigint
                       ) AS observation_id
                  FROM accepted_science_repair_change AS change
                  JOIN accepted_science_repair AS declaration
                    ON declaration.id = change.repair_id
                 WHERE declaration.alembic_revision = CAST(:revision AS text)
                   AND change.target_table = 'calculation'
                   AND change.record_type = 'calculation'
                   -- A null "before" is an upgrade's change row; the
                   -- downgrade's own rows have a null "after". No direction
                   -- column needed, and nothing keys off prose.
                   AND change.before_json -> CAST(:column AS text) = 'null'::jsonb
            ),
            restored AS (
                UPDATE calculation
                   SET conformer_observation_id = NULL
                  FROM written
                 WHERE calculation.id = written.calculation_id
                   -- Only if the row still carries what the upgrade set. A
                   -- curator who has since re-anchored it owns that value
                   -- now, and this leaves it alone.
                   AND calculation.conformer_observation_id
                       = written.observation_id
             RETURNING calculation.id AS calculation_id,
                       written.observation_id AS observation_id
            )
            INSERT INTO accepted_science_repair_change (
                repair_id, record_type, record_id, target_schema, target_table,
                row_identity, changed_columns, before_json, after_json
            )
            SELECT
                declaration.id,
                'calculation',
                restored.calculation_id,
                'public',
                'calculation',
                jsonb_build_object('id', restored.calculation_id),
                ARRAY[CAST(:column AS text)],
                jsonb_build_object(
                    CAST(:column AS text), restored.observation_id
                ),
                jsonb_build_object(CAST(:column AS text), NULL)
              FROM restored
              CROSS JOIN (
                    SELECT id
                      FROM accepted_science_repair
                     WHERE target_table = 'calculation'
                       AND xact_id = (pg_current_xact_id())::text::bigint
                   ) AS declaration
             WHERE NOT tckdb_record_is_accepted(
                       CAST('calculation' AS submission_record_type),
                       restored.calculation_id
                   )
            """
        ),
        {"column": _DECLARED_COLUMN, "revision": revision},
    )
