"""Merge the composite Gaussian software_release into its decomposed sibling.

Data-repair half of #305; #315 closed the boundary that let this row in
(``SoftwareReleaseRef.normalize_composite_version`` now splits a parsed ESS
banner like ``"Gaussian 16, Revision C.02"`` into ``version="16"``,
``revision="C.02"`` on every new deposit instead of persisting the banner
verbatim). This revision repairs the one row #315 could not reach because it
already existed.

Measured on the deployed archive
---------------------------------
::

    id | software  | version                       | revision | calculations
     5 | Gaussian  | "Gaussian 16, Revision C.02"  | NULL     | 408
     7 | Gaussian  | "16"                          | "C.02"   |   8
     1 | Gaussian  | "09"                          | NULL     |  78

Release 7 is **exactly** the decomposition #315's normaliser would produce
from release 5's ``version``: same ``software_id``, ``version="16"``,
``revision="C.02"``. This is not a guess -- the correct target already
exists in the table, and ``uq_software_release_software_id``
(``(software_id, version, revision, build)`` NULLS NOT DISTINCT) is what
makes 5 and 7 two distinct rows today rather than one.

The repair: repoint every ``calculation`` row on release 5 to release 7,
then delete release 5.

Not touched, and why
---------------------
* **Release 1** (``version="09"``, ``revision`` NULL) -- a legitimate bare
  version with no embedded revision label. The identifying regex below
  requires a trailing ``", Revision <label>"`` suffix, which ``"09"`` does
  not have, so release 1 cannot match it.
* **Releases 2 and 3** -- NULL ``version``. A separate defect tracked
  against #305, out of scope here. ``regexp_match`` against a NULL input
  returns NULL, so they cannot match either.
* **Release 6** (Arkane) -- ``revision`` holds a 40-character hex string and
  it carries zero calculations. Reported, not fixed: it is a different
  program (``software.name <> 'Gaussian'``) so the ``software.name =
  'Gaussian'`` scope excludes it by construction, and it is out of scope
  for this issue regardless.

How the composite row is found
-------------------------------
Not a hardcoded id -- ids are deployment-specific (this database's "5" is
some other id on a freshly migrated test database). Identified the same way
#315 identifies a composite version, scoped to ``software.name =
'Gaussian'`` and ``software_release.revision IS NULL`` (a real revision
value would mean the row was already split)::

    ^Gaussian\\s+(?P<version>.+?),\\s*Revision\\s+(?P<revision>\\S.*)$   (case-insensitive)

exactly ``SoftwareReleaseRef.normalize_composite_version``'s
``_TRAILING_REVISION_LABEL`` pattern, applied after stripping the matching
leading ``name`` token. The decomposed sibling is then looked up by the
*derived* ``version``/``revision`` (and the composite row's own ``build``,
via ``IS NOT DISTINCT FROM``) rather than assumed -- if no such sibling
exists, or more than one composite candidate matches, the migration raises
rather than guessing. Both checks are exercised in
``tests/db/test_merge_composite_gaussian_release_migration.py``.

Why this declares a repair
----------------------------
``calculation`` is an accepted-science root (``trg_as_root_calculation``):
an UPDATE on a row that has ever been approved is refused unconditionally
without a declaration in ``accepted_science_repair`` (``e2c9a4f7b163``).
``declared_columns`` names exactly ``software_release_id`` on
``calculation`` -- the guard enforces that boundary; an UPDATE that touched
anything else on ``calculation`` would be refused by name, repair or no
repair. This is not a correction of a scientific claim: every repointed
calculation keeps its own results untouched, and the software identity it
now cites is the *same* software release, correctly decomposed, that its
own ``version`` string already named.

``software_release`` itself carries no accepted-science guard (no
``trg_as_*`` trigger is attached to it -- confirmed by grep over every
migration in this tree), so declaring a repair against it would be refused
by ``tckdb_validate_accepted_science_repair`` ("carries no accepted-science
guard"), and none is attempted. Deleting release 5, and recreating it on
downgrade, is a plain DML statement against an unguarded table, exactly
like any other identity-table row a repair migration writes.

Declared only when there is confirmed work
--------------------------------------------
``b8e3f1a7c250`` already declares a repair against ``calculation`` (for
``conformer_observation_id``), and ``accepted_science_repair`` permits at
most one declaration per table per transaction (``e2c9a4f7b163``). This
repo's ``alembic/env.py`` runs an entire ``upgrade head`` -- from an empty
database, every revision between base and head -- inside one transaction,
which is exactly what every test in this suite's shared ``db_engine``
fixture does. Declaring unconditionally at the top of ``upgrade()``, the
way ``b8e3f1a7c250`` and ``53b1e7dece9d`` do, would collide with
``b8e3f1a7c250``'s own declaration the moment both run in that one
transaction -- which is always, on a from-scratch bootstrap. So both
``upgrade()`` and ``downgrade()`` here declare only *after* confirming
there is a composite row (respectively, a non-empty ledger) to act on --
which is never true on an empty database, where ``b8e3f1a7c250`` also has
nothing to anchor. The two declarations never actually need to coexist:
either the database is empty (neither has work, neither declares) or it
already has data (in which case every revision through ``53b1e7dece9d``,
this one included, has already been applied in its own earlier,
already-committed transaction, and this migration runs alone).

The ledger, and why the downgrade needs it
--------------------------------------------
After the upgrade, a calculation repointed from 5 to 7 is **structurally
indistinguishable** from one of the 8 that always pointed at 7 --
``b8e3f1a7c250``'s trap verbatim: *"any migration whose downgrade needs
'find the rows that look like the ones I changed' is describing rows it can
no longer identify."* A downgrade that moved "everything on release 7" back
would move all 416, not 408.

So the downgrade reads the ledger by primary key, exactly as
``b8e3f1a7c250`` and ``53b1e7dece9d`` do. Both directions write
non-NULL values for ``software_release_id`` (there is no NULL to test, the
way those two revisions do), so the asymmetry that tells an upgrade's
change row from a downgrade's is different but the same idea: release 7's
id is fixed and never deleted by this revision, so an upgrade's change row
always has ``after_json ->> 'software_release_id' = <release 7's id>`` and a
downgrade's change row never does (a downgrade's ``after`` is the
*recreated* release 5's id, which release 7's id can never equal). No
direction column, same as the precedents.

Recreating release 5: a fresh id, not the original one
---------------------------------------------------------
The deleted row's id is not reused. Two reasons:

1. **It is not portable.** "5" is this deployment's id; a differently
   seeded database (including every test in this suite) will have some
   other id for its composite row, and the migration does not know it in
   advance -- it has to look the row up by content, the same way it looks
   the row up on the way in. Pinning the *recreated* row back to a specific
   numeric id would be pinning it to a number this migration cannot derive
   from anything.
2. **Nothing needs it to be the original id.** The only thing that cites
   ``software_release.id`` is ``calculation.software_release_id``, and the
   downgrade repoints those calculations to whatever id the recreated row
   gets, read back from its own ``INSERT ... RETURNING id`` -- not from a
   value written down in advance.

What the recreated row does **not** restore: ``release_date``, ``notes``,
and ``public_ref``. ``version``, ``revision``, ``software_id`` and
``build`` are fully recoverable -- the first two are the deterministic
inverse of the identifying regex above (``'Gaussian ' || <release 7's
version> || ', Revision ' || <release 7's revision>``, which is exactly how
the original banner read), and the last two are read straight off release
7, which this revision never touches. ``release_date`` and ``notes`` are
not: nothing this revision writes captures what they were, and inventing a
value would misrepresent a repair as more informed than it is. Recreated as
NULL, which is what a row created from nothing but a parsed banner -- no
depositor-supplied release date, no notes -- would have had in the first
place. ``public_ref`` is recreated via ``software_release``'s own
``server_default`` (a fresh opaque ``srel_<uuid>``, the same fallback every
raw-SQL insert against this table gets); the original public ref for
release 5 does not come back. Nothing in this codebase's citation surfaces
(``dataset_release_and_profiles``, ADR 0006/0007) cites a
``software_release`` directly, so this is a fact about the row, not about
anything a consumer could have already cited.

Idempotency
------------
Safe to re-run for real (not just Alembic's own already-applied no-op):
after a successful upgrade, release 5 no longer exists, so the identifying
query matches nothing and the whole body is a no-op on a second pass. A
genuine second execution -- upgrade, downgrade, upgrade again, the same
sequence an operator's rollback-then-reapply performs -- is exercised in
``test_upgrade_downgrade_upgrade_round_trip_repoints_the_same_calculations``
and converges on the same final state both times.

Revision ID: 6141f2d98e78
Revises: 53b1e7dece9d
Create Date: 2026-09-01
"""

from __future__ import annotations

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "6141f2d98e78"
down_revision: Union[str, Sequence[str], None] = "53b1e7dece9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The one column either direction of this revision may write on
#: ``calculation``. The guard compares OLD against NEW and refuses an
#: UPDATE that touches anything else, so this is enforced rather than
#: promised.
_DECLARED_COLUMN = "software_release_id"

#: #315's own pattern, scoped to a software whose leading token already
#: matches (this migration only ever looks inside ``software.name =
#: 'Gaussian'`` rows, so the leading-token check #315 performs at upload
#: time is redundant here -- it is folded into the WHERE clause instead).
_COMPOSITE_VERSION_PATTERN = r"^Gaussian\s+(.+?),\s*Revision\s+(\S.*)$"

_UPGRADE_REASON = (
    "Merge 6141f2d98e78: software_release held a parsed ESS startup banner "
    "in version ('Gaussian 16, Revision C.02') with revision NULL -- the "
    "composite shape #315's normaliser now splits on every new deposit, but "
    "this row predates that fix. Its decomposition (version='16', "
    "revision='C.02', same software_id) already existed as a sibling row. "
    "Repointed every calculation.software_release_id from the composite "
    "row to the decomposed sibling, then deleted the composite row."
)

_DOWNGRADE_REASON = (
    "Downgrade of 6141f2d98e78: recreated the composite software_release "
    "row (fresh id; version/revision/software_id/build reconstructed, "
    "release_date/notes/public_ref not restorable -- see the module "
    "docstring) and repointed back to it exactly the calculations the "
    "upgrade moved off it, read from the repair ledger by primary key."
)

#: Finds the one Gaussian release row whose ``version`` is still a composite
#: banner, and the sibling release that is already its decomposition.
#: ``sr.build IS NOT DISTINCT FROM composite.build`` matches
#: ``uq_software_release_software_id``'s own NULLS-NOT-DISTINCT semantics --
#: if no such sibling exists, this returns no rows and the migration raises
#: rather than fabricating the target.
_FIND_COMPOSITE_AND_TARGET = """
    WITH composite AS (
        SELECT
            sr.id AS composite_id,
            sr.software_id AS software_id,
            sr.build AS build,
            (regexp_match(sr.version, :pattern, 'i'))[1] AS derived_version,
            (regexp_match(sr.version, :pattern, 'i'))[2] AS derived_revision
          FROM software_release sr
          JOIN software s ON s.id = sr.software_id
         WHERE s.name = 'Gaussian'
           AND sr.revision IS NULL
           AND sr.version ~* :pattern
    )
    SELECT
        composite.composite_id,
        composite.software_id,
        target.id AS target_id
      FROM composite
      JOIN software_release AS target
        ON target.software_id = composite.software_id
       AND target.version = composite.derived_version
       AND target.revision = composite.derived_revision
       AND target.build IS NOT DISTINCT FROM composite.build
       AND target.id <> composite.composite_id
"""


def _declare_repair(bind, reason: str) -> None:
    """Stand the accepted-science guard down for one column, on the record.

    Inert wherever no repointed calculation has ever been approved -- which
    is every one of the 408 on the deployed database today. It exists for
    the operator instance where that is not true, so that instance gets a
    recorded repair instead of a failed migration.
    """
    bind.execute(
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


def _find_composite_and_target(bind) -> tuple[int, int, int] | None:
    """Locate the composite Gaussian release and its decomposed sibling.

    Returns ``(composite_id, software_id, target_id)``, or ``None`` when no
    composite Gaussian release exists on this database (the common case on
    a second run, once the first has already deleted it). Raises when a
    composite row exists but no matching decomposed sibling does -- this
    migration does not fabricate the target it is supposed to already find.
    Raises on more than one composite candidate for the same reason
    ``b8e3f1a7c250`` raises on an ambiguous anchor: a repair that cannot
    name a single row does not choose one for itself.
    """
    composite_candidates = bind.execute(
        text(
            """
            SELECT sr.id
              FROM software_release sr
              JOIN software s ON s.id = sr.software_id
             WHERE s.name = 'Gaussian'
               AND sr.revision IS NULL
               AND sr.version ~* :pattern
            """
        ),
        {"pattern": _COMPOSITE_VERSION_PATTERN},
    ).scalars().all()

    if not composite_candidates:
        return None
    if len(composite_candidates) > 1:
        raise RuntimeError(
            "6141f2d98e78: more than one Gaussian software_release row "
            f"looks like a composite ESS banner ({composite_candidates!r}); "
            "this migration merges exactly one known row into its "
            "decomposed sibling and will not guess which of several to "
            "merge."
        )

    row = bind.execute(
        text(_FIND_COMPOSITE_AND_TARGET),
        {"pattern": _COMPOSITE_VERSION_PATTERN},
    ).one_or_none()
    if row is None:
        raise RuntimeError(
            "6141f2d98e78: found a composite Gaussian software_release row "
            f"(id={composite_candidates[0]}) but no decomposed sibling row "
            "matching its derived version/revision/build. The repair this "
            "migration performs assumes the decomposed row already exists; "
            "it does not create one."
        )
    composite_id, software_id, target_id = row
    return composite_id, software_id, target_id


def _repoint_and_record(bind, *, composite_id: int, target_id: int) -> None:
    """Repoint every calculation on ``composite_id`` to ``target_id``.

    Same shape as ``b8e3f1a7c250`` and ``53b1e7dece9d``: the UPDATE and the
    ledger insert are one statement, and the ledger insert is filtered to
    rows the guard itself did *not* already record (``tckdb_repair_permits``
    writes a change row for any accepted calculation as the guard runs; this
    fills in the rest, so every repointed row is recorded exactly once).
    """
    bind.execute(
        text(
            """
            WITH repointed AS (
                UPDATE calculation
                   SET software_release_id = :target_id
                 WHERE software_release_id = :composite_id
             RETURNING calculation.id AS calculation_id
            )
            INSERT INTO accepted_science_repair_change (
                repair_id, record_type, record_id, target_schema, target_table,
                row_identity, changed_columns, before_json, after_json
            )
            SELECT
                declaration.id,
                'calculation',
                repointed.calculation_id,
                'public',
                'calculation',
                jsonb_build_object('id', repointed.calculation_id),
                ARRAY[CAST(:column AS text)],
                jsonb_build_object(CAST(:column AS text), :composite_id),
                jsonb_build_object(CAST(:column AS text), :target_id)
              FROM repointed
              CROSS JOIN (
                    SELECT id
                      FROM accepted_science_repair
                     WHERE target_table = 'calculation'
                       AND xact_id = (pg_current_xact_id())::text::bigint
                   ) AS declaration
             WHERE NOT tckdb_record_is_accepted(
                       CAST('calculation' AS submission_record_type),
                       repointed.calculation_id
                   )
            """
        ),
        {"column": _DECLARED_COLUMN, "composite_id": composite_id, "target_id": target_id},
    )


def upgrade() -> None:
    bind = op.get_bind()

    found = _find_composite_and_target(bind)
    if found is None:
        # Nothing to repair on this database (the common case on a second
        # real run, and on a fresh database bootstrapped straight to head --
        # no Gaussian software_release exists at all yet). Declared only
        # below, once there is something to declare it *for*: this table
        # already carries one repair declaration from b8e3f1a7c250, and
        # ``accepted_science_repair`` permits at most one per table per
        # transaction (e2c9a4f7b163). A fresh-to-head bootstrap runs every
        # revision in one transaction (see alembic/env.py), so an
        # unconditional declare here would collide with b8e3f1a7c250's own
        # the moment both run together -- which they always do on an empty
        # database, where neither has anything to repoint anyway.
        return
    composite_id, _software_id, target_id = found

    _declare_repair(bind, _UPGRADE_REASON)
    _repoint_and_record(bind, composite_id=composite_id, target_id=target_id)

    # Unguarded table: no accepted-science trigger is attached to
    # software_release, so no repair declaration covers this delete (one
    # would be refused -- see the module docstring). Any other table still
    # citing this id (thermo, statmech, kinetics, transport, network,
    # network_solve, molecular_property_observation,
    # execution_environment_manifest) fails this DELETE with an ordinary
    # foreign-key violation, which is the correct outcome: this revision
    # only ever repoints calculation.software_release_id, and a reference
    # from anywhere else means the measured shape this repair assumes does
    # not hold on this database.
    bind.execute(
        text("DELETE FROM software_release WHERE id = :composite_id"),
        {"composite_id": composite_id},
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Declared only once there is confirmed work below (mirrors upgrade()'s
    # same reasoning): a declaration here is only useful to permit an UPDATE
    # on an accepted calculation, and only exists at all past this point if
    # there is a target release and a non-empty ledger to restore from.
    target = bind.execute(
        text(
            """
            SELECT sr.id, sr.software_id, sr.build
              FROM software_release sr
              JOIN software s ON s.id = sr.software_id
             WHERE s.name = 'Gaussian' AND sr.version = '16' AND sr.revision = 'C.02'
            """
        )
    ).one_or_none()
    if target is None:
        # Release 7 is gone by some other route; there is nothing this
        # downgrade can reverse itself out of, and inventing a target would
        # be worse than doing nothing.
        return
    target_id, software_id, build = target

    written = bind.execute(
        text(
            """
            SELECT DISTINCT change.record_id
              FROM accepted_science_repair_change AS change
              JOIN accepted_science_repair AS declaration
                ON declaration.id = change.repair_id
             WHERE declaration.alembic_revision = CAST(:revision AS text)
               AND change.target_table = 'calculation'
               AND change.record_type = 'calculation'
               -- Release 7's id is fixed and this revision never deletes
               -- it, so only an *upgrade* change row can ever carry it as
               -- 'after' -- a downgrade's 'after' is the freshly recreated
               -- composite row's id, which release 7's id can never equal.
               -- Same trick as the NULL test b8e3f1a7c250 and 53b1e7dece9d
               -- use, with release 7's id standing in for NULL.
               AND change.after_json ->> CAST(:column AS text) = CAST(:target_id AS text)
            """
        ),
        {"revision": revision, "column": _DECLARED_COLUMN, "target_id": target_id},
    ).scalars().all()

    if not written:
        # This revision's upgrade never repointed anything on this
        # database (there was no composite row to merge), so there is
        # nothing to recreate and nothing to repoint back.
        return

    _declare_repair(bind, _DOWNGRADE_REASON)

    recreated_id = bind.execute(
        text(
            """
            INSERT INTO software_release (software_id, version, revision, build)
            SELECT :software_id, 'Gaussian ' || sr.version || ', Revision ' || sr.revision, NULL, :build
              FROM software_release sr
             WHERE sr.id = :target_id
            RETURNING id
            """
        ),
        {"software_id": software_id, "build": build, "target_id": target_id},
    ).scalar_one()

    bind.execute(
        text(
            """
            WITH restored AS (
                UPDATE calculation
                   SET software_release_id = :recreated_id
                 WHERE id = ANY(:written)
                   -- Only if the row still carries what the upgrade set. A
                   -- curator who has since repointed it elsewhere owns that
                   -- value now, and this leaves it alone.
                   AND software_release_id = :target_id
             RETURNING calculation.id AS calculation_id
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
                jsonb_build_object(CAST(:column AS text), :target_id),
                jsonb_build_object(CAST(:column AS text), :recreated_id)
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
        {
            "column": _DECLARED_COLUMN,
            "target_id": target_id,
            "recreated_id": recreated_id,
            "written": written,
        },
    )
