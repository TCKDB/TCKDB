"""Stop freezing SCF-stability evidence on the calculation it merely cites.

``c6f2a9d4e7b1`` states the rule its registry follows, immediately above the
registry itself: "Each child is protected by the accepted root that owns its
scientific meaning. Cross-domain provenance FKs are intentionally not treated
as ownership (for example ``thermo_source_calculation.calculation_id``)." It
then registered ``calc_scf_stability`` under two columns:

* ``calculation_id`` -- ``NOT NULL``, the table's primary key, the calculation
  whose wavefunction the row describes. Ownership, correctly guarded.
* ``source_calculation_id`` -- nullable, no part of any key, the *other*
  calculation the stability analysis was read out of. Provenance, and the one
  entry in the whole regime where the stated rule was written down and then
  not applied.

What the extra argument actually did
------------------------------------
``tckdb_guard_accepted_child`` draws no distinction between the columns handed
to it. It collects every non-NULL value from every argument column in OLD and
NEW, and refuses the write if any of those ids has ever been approved. With
both columns registered, ``trg_as_child_19`` therefore refused this:

    INSERT INTO calc_scf_stability (calculation_id, status, source_calculation_id)
    VALUES (<unapproved calculation A>, 'stable', <approved calculation B>);

with ``accepted calculation record B is immutable`` -- while the identical
insert leaving ``source_calculation_id`` NULL succeeded. Nothing about A was
approved; nothing about B was written. The refusal was triggered purely by the
act of *naming* accepted science, on a row belonging to a record no reviewer
had looked at.

That is the incentive inverted. Approving a calculation is the act of saying
"this is a result others may build on", and building on it is exactly what
citing it in the provenance of a later analysis is. A regime that refuses the
citation and permits the uncited copy rewards depositing evidence with its
origin stripped off -- the opposite of what an immutability guard is for. It
also over-covers in the direction that does no good: freezing B's *dependents*
protects nothing about B, whose own rows are already frozen by
``trg_as_root_calculation``.

``a1f6c3e9b527`` had reached this conclusion explicitly, ten revisions later,
for the identically shaped ``network_solve_state_energy.source_calculation_id``
-- "``source_calculation_id`` is provenance, and is excluded for the same
reason ``thermo_source_calculation.calculation_id`` is". This revision brings
``c6f2a9d4e7b1``'s own registry into line with the rule it wrote, so that the
regime says one thing rather than two.

What is *not* relaxed
---------------------
``calculation_id`` stays registered, so the guard is unchanged in every case it
was meant for: under an approved calculation, its SCF-stability row still
cannot be inserted, updated or deleted, and the correction path is still a new
calculation approved on its own terms with a
``scientific_record_supersession`` edge. The trigger keeps its name, keeps
firing ``BEFORE INSERT OR UPDATE OR DELETE``, and keeps its position in the
``trg_as_child_NN`` sequence; only the argument list narrows. The TRUNCATE
refusal on the table is untouched.

Why the trigger is replaced rather than renamed
-----------------------------------------------
``trg_as_child_19`` is the name ``c6f2a9d4e7b1`` gives the twentieth
``(table, record_type)`` group in its registry, which is ``calc_scf_stability``
under ``calculation``. That numbering belongs to that revision and is asserted
against ``pg_trigger`` by
``tests/db/test_accepted_science_trigger_registry.py``; a new name here would
either break that assertion or force the numbering to be recomputed from two
files at once. Dropping and recreating under the same name keeps one revision
in charge of the sequence.

The DROP is deliberately written without ``IF EXISTS``. If the trigger is not
found on ``calc_scf_stability`` this revision must fail loudly rather than
succeed while leaving the defective guard installed, which is the one outcome
that would be indistinguishable from success and would put the wrong argument
list back into service.

No rows are touched
-------------------
Pure DDL. Dropping and creating a trigger evaluates nothing against existing
rows, so this is safe whatever a deployment holds and there is no backfill to
design. Rows that could not be written while the extra argument was installed
were refused, not stored wrong, so there is nothing to repair -- and this is a
guard-registration correction, not a data repair, so ``accepted_science_repair``
is not involved.

``downgrade()`` reinstalls the two-argument form exactly as ``c6f2a9d4e7b1``
created it.

Revision ID: d4e9b1c7a253
Revises: c5a1f8e3d074
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4e9b1c7a253"
down_revision: Union[str, Sequence[str], None] = "c5a1f8e3d074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The trigger ``c6f2a9d4e7b1`` created for the ``calc_scf_stability`` /
#: ``calculation`` group. See the module docstring for why the name is reused.
_TRIGGER = "trg_as_child_19"
_TABLE = "calc_scf_stability"
_RECORD_TYPE = "calculation"

#: ``(table, record_type, column)`` entries this revision removes from the
#: effective accepted-science registry. Read by
#: ``tests/db/test_accepted_science_trigger_registry.py`` so the registry the
#: tests reason about is the one the database actually has, rather than
#: ``c6f2a9d4e7b1``'s frozen text.
_REMOVED_CHILDREN: tuple[tuple[str, str, str], ...] = ((_TABLE, _RECORD_TYPE, "source_calculation_id"),)

#: The argument lists before and after, spelled out rather than derived, so
#: ``upgrade()`` and ``downgrade()`` are each readable on their own.
_OWNERSHIP_ONLY: tuple[str, ...] = ("calculation_id",)
_ORIGINAL: tuple[str, ...] = ("calculation_id", "source_calculation_id")


def _install(columns: tuple[str, ...]) -> None:
    """Replace the guard on ``calc_scf_stability`` with one over ``columns``."""

    arguments = ", ".join(f"'{column}'" for column in columns)
    op.execute(f"DROP TRIGGER {_TRIGGER} ON public.{_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE INSERT OR UPDATE OR DELETE ON public.{_TABLE}
        FOR EACH ROW
        EXECUTE FUNCTION public.tckdb_guard_accepted_child(
            '{_RECORD_TYPE}', {arguments}
        )
        """
    )


def upgrade() -> None:
    """Guard SCF-stability evidence on its own calculation only."""

    _install(_OWNERSHIP_ONLY)


def downgrade() -> None:
    """Restore ``c6f2a9d4e7b1``'s two-argument guard exactly."""

    _install(_ORIGINAL)
