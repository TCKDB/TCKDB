"""name constraints the way the model spells them

Six CHECK constraints on deployed tables carry a name the ORM does not
predict, and one unique index carries a name that describes less than it
enforces. Neither is a live data problem: every one of them enforces the
right rule right now. Both are traps for the next reader, and both have
already been sprung once.

**The six.** ``NAMING_CONVENTION`` in ``app/db/base.py`` spells check
constraints ``ck_%(table_name)s_%(constraint_name)s``. Because that
template interpolates ``%(constraint_name)s``, SQLAlchemy applies it to
constraints that were given an explicit name as well as to anonymous
ones -- unlike the ``uq``/``ix``/``fk`` templates, which key off column
names and so leave an explicit name alone. A migration that hands
``op.create_check_constraint`` a name already carrying the ``ck_<table>_``
prefix therefore gets the prefix twice, and PostgreSQL truncates the
result to 63 characters with a hash suffix. Three revisions did this:

* ``a7c9e2f4b6d8`` -- two on ``calculation_artifact``
* ``a8b9c0d1e2f3`` -- three on ``execution_environment_manifest``
  (via ``sa.CheckConstraint(name=...)`` inside ``create_table``, which
  takes the same path)
* ``c2f7a4e8d1b6`` -- one on ``calc_freq_mode``

leaving names like
``ck_calc_freq_mode_ck_calc_freq_mode_imaginary_dispositi_ddc2``. The
cost is not enforcement, it is drift: ``alembic revision --autogenerate``
compares catalog names against ``Base.metadata`` names, sees six it does
not recognise and six it thinks are missing, and proposes dropping and
recreating constraints that are already correct. Whoever next touches
these tables gets handed that diff and has to work out from scratch
whether it is real. ``e2a7c9d4b615`` and ``f3b7d2c8a419`` pass the short
name and are unaffected, so the codebase currently holds both conventions
at once -- which is worse than holding either.

**The one.** ``statmech_torsion`` has enforced uniqueness of
``(statmech_id, torsion_index)`` since ``d861dfd60891``, the initial
schema, under the name ``uq_statmech_torsion_statmech_id``. That name
mentions only the first of the two columns, so it reads as a uniqueness
rule on ``statmech_id`` alone -- which would be absurd (one torsion per
statmech record) and is therefore easy to skim past as a mistake rather
than as the constraint you were looking for. It was read exactly that
way: a duplicate ``torsion_index`` means one hindered rotor counted twice
in a partition function, and the invariant was reported as unenforced at
the database level on the strength of this name. It is enforced, and
``tests/db/test_statmech_torsion_index_uniqueness.py`` now measures that
rather than trusting either the name or the report. Renamed here to say
both columns.

**Renames, not drop-and-recreate.** ``ALTER TABLE ... RENAME
CONSTRAINT`` and ``ALTER INDEX ... RENAME TO`` touch the catalog only.
No row is read, no row is written, no table is scanned, and each rule
stays enforced continuously -- there is no window in which a writer could
slip past. Dropping and recreating ``ck_calculation_artifact_sha256``
would instead revalidate every artifact row, and would leave the rule off
the table while it did so. Renaming the unique index likewise keeps the
index itself, so no ``REINDEX`` and no period during which duplicate
torsion indices are accepted.

This is also why there is no data-measurement guard of the kind
``e2a7c9d4b615`` needed. That revision installed a *new* rule and had to
ask the database whether the stored rows already satisfied it. This one
installs nothing: every rule here is already in force and already
validated, so the only thing that can differ between hosts is the name.
That is what the guard below measures.

**When the name is already right: no-op, deliberately.** A host built
from scratch after ``e2a7c9d4b615``'s conventions took hold could
conceivably already carry the short name, and a re-run of this revision
certainly will. Refusing in that case would block a deploy on a database
that is already in the state this revision exists to produce, which is
the wrong trade: the goal is "the constraint is present under the model's
name", and if that holds, the goal is met. So a constraint found already
correctly named is skipped with a log line and the migration continues.

The refusals are kept for the two cases that are *not* "already done":

* **Neither name present.** The constraint is gone, not renamed. That is
  real drift -- something dropped a rule this schema depends on -- and
  silently continuing would leave the operator believing this revision
  had put it back. It did not; it cannot; recreating it here would be
  installing an unvalidated rule against unmeasured data, which is the
  thing the paragraph above says this revision does not do.
* **Both names present.** There are two constraints, and a rename cannot
  merge them. Which to keep is a question about how the duplicate got
  there, and only whoever made it can answer.

Revision ID: b7e4d1a9c026
Revises: e2a7c9d4b615
Create Date: 2026-08-13
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4d1a9c026"
down_revision: Union[str, Sequence[str], None] = "e2a7c9d4b615"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

#: ``(table, deployed name, name the model predicts)`` for each CHECK.
#:
#: The deployed names are spelled out rather than discovered by pattern,
#: and the hash suffixes are safe to hard-code: SQLAlchemy derives them
#: deterministically from the over-long name, so every host that ran
#: these three revisions produced the same six strings. Verified against
#: both a from-scratch ``upgrade head`` and the deployed host.
#:
#: The three ``execution_environment_manifest`` entries are truncated to
#: the point where the name no longer says which column each covers, so
#: the pairing was taken from ``pg_get_constraintdef`` rather than read
#: off the names; the definitions are quoted here so the mapping can be
#: checked without a database to hand.
_CHECK_RENAMES: tuple[tuple[str, str, str], ...] = (
    # CHECK (imaginary_disposition IS NULL OR is_imaginary)
    (
        "calc_freq_mode",
        "ck_calc_freq_mode_ck_calc_freq_mode_imaginary_dispositi_ddc2",
        "ck_calc_freq_mode_imaginary_disposition_requires_imaginary_mode",
    ),
    # CHECK (bytes > 0)
    (
        "calculation_artifact",
        "ck_calculation_artifact_ck_calculation_artifact_bytes_gt_0",
        "ck_calculation_artifact_bytes_gt_0",
    ),
    # CHECK (sha256 ~ '^[0-9a-f]{64}$')
    (
        "calculation_artifact",
        "ck_calculation_artifact_ck_calculation_artifact_sha256__8107",
        "ck_calculation_artifact_sha256_lower_hex",
    ),
    # CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$')
    (
        "execution_environment_manifest",
        "ck_execution_environment_manifest_ck_execution_environm_0a7b",
        "ck_execution_environment_manifest_content_digest_sha256",
    ),
    # CHECK (jsonb_typeof(closure_json) = 'array')
    (
        "execution_environment_manifest",
        "ck_execution_environment_manifest_ck_execution_environm_8159",
        "ck_execution_environment_manifest_closure_json_array",
    ),
    # CHECK (jsonb_typeof(canonical_json) = 'object')
    (
        "execution_environment_manifest",
        "ck_execution_environment_manifest_ck_execution_environm_8dbd",
        "ck_execution_environment_manifest_canonical_json_object",
    ),
)

#: ``(table, deployed name, name the model predicts)`` for the unique
#: index. Unlike the CHECKs this one is not the naming-convention bug --
#: ``d861dfd60891`` named it by hand and named it short -- it is just a
#: name that stops one column early.
_INDEX_RENAMES: tuple[tuple[str, str, str], ...] = (
    (
        "statmech_torsion",
        "uq_statmech_torsion_statmech_id",
        "uq_statmech_torsion_statmech_id_torsion_index",
    ),
)

#: PostgreSQL's ``NAMEDATALEN - 1``. An identifier longer than this is
#: silently truncated by ``RENAME``, which would land us back in the
#: situation this revision is undoing -- a name the model does not
#: predict -- without any error to say so.
#: ``ck_calc_freq_mode_imaginary_disposition_requires_imaginary_mode`` is
#: exactly 63 characters, so this is not a hypothetical margin.
_MAX_IDENTIFIER_LENGTH = 63


def _decide(kind: str, table: str, old: str, new: str) -> bool:
    """Return whether to issue the rename, refusing if it is not a rename.

    ``kind`` is ``"constraint"`` or ``"index"`` and appears only in
    messages.
    """
    if len(new) > _MAX_IDENTIFIER_LENGTH:
        raise RuntimeError(
            f"{new!r} is {len(new)} characters; PostgreSQL truncates "
            f"identifiers at {_MAX_IDENTIFIER_LENGTH} and would leave "
            f"{table} carrying a name the ORM still does not predict. "
            "Nothing was changed."
        )

    bind = op.get_bind()
    if kind == "constraint":
        query = sa.text(
            "SELECT conname FROM pg_constraint"
            " WHERE conrelid = CAST(:table AS regclass)"
            " AND contype = 'c'"
            " AND conname = ANY(:names)"
        )
    else:
        query = sa.text(
            "SELECT indexname AS conname FROM pg_indexes"
            " WHERE schemaname = current_schema()"
            " AND tablename = :table"
            " AND indexname = ANY(:names)"
        )
    present = set(
        bind.execute(query, {"table": table, "names": [old, new]}).scalars().all()
    )

    if old in present and new in present:
        raise RuntimeError(
            f"{table} carries both {old!r} and {new!r}. A rename cannot "
            "merge two constraints, and which of them to keep depends on "
            "how the second one got there -- this migration will not "
            "guess. Nothing was changed; drop whichever is the duplicate "
            "and re-run."
        )
    if new in present:
        logger.info(
            "%s.%s is already named as the model spells it; leaving it alone",
            table,
            new,
        )
        return False
    if old not in present:
        raise RuntimeError(
            f"{table} carries neither {old!r} nor {new!r}: the {kind} is "
            "not there to rename. Something dropped a rule this schema "
            "relies on, and recreating it here would install it against "
            "rows this migration has not measured. Nothing was changed; "
            f"restore the missing {kind} and re-run."
        )
    return True


def _rename(kind: str, table: str, old: str, new: str) -> None:
    if not _decide(kind, table, old, new):
        return
    quoted_new = f'"{new}"'
    if kind == "constraint":
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO {quoted_new}')
    else:
        op.execute(f'ALTER INDEX "{old}" RENAME TO {quoted_new}')


def upgrade() -> None:
    """Give each rule the name its model declaration predicts."""
    for table, old, new in _CHECK_RENAMES:
        _rename("constraint", table, old, new)
    for table, old, new in _INDEX_RENAMES:
        _rename("index", table, old, new)


def downgrade() -> None:
    """Put back the names ``d861dfd60891`` and the three revisions left.

    Not cosmetic: ``a7c9e2f4b6d8.downgrade()`` and
    ``d861dfd60891.downgrade()`` drop these by the names they created, so
    a downgrade that stopped here would break the next one down.
    """
    for table, old, new in _INDEX_RENAMES:
        _rename("index", table, new, old)
    for table, old, new in _CHECK_RENAMES:
        _rename("constraint", table, new, old)
