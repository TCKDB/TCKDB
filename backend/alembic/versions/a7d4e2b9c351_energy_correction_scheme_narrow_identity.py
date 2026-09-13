"""energy_correction_scheme: units and version are not identity

``energy_correction_scheme`` is an already-deployed table, so this is a
new revision rather than an edit to ``c24ce2d9c198``, which is the
revision it partly reverses.

Why this reverses part of c24ce2d9c198
---------------------------------------
``c24ce2d9c198`` added ``units`` to
``uq_energy_correction_scheme_identity``. The stated reason (plan v2
§3.3) was that ``_assert_param_value_compatible`` compares raw floats
against a ``1e-10`` tolerance and is **unit-blind**, so a depositor
re-sending the same correction in a different energy unit resolved onto
the existing row and was told, wrongly, that its numbers conflicted --
with a remedy ("use a distinct identity") the schema could not express.

That bug is real. Putting ``units`` in the identity was the wrong fix
for it, and this repository's own unit policy says so (``CLAUDE.md``):

    Use enum-backed unit fields only when dimensionality genuinely
    varies by scientific context.

An energy correction is always an energy. Hartree and kcal/mol are one
physical fact in two presentations, and the frontend already converts
between them on the fly for Arrhenius parameters. Keying identity on the
unit makes *what a correction is* depend on *how a depositor chose to
write it down*, which is precisely the identity-versus-result confusion
the rest of this schema is careful to avoid: the same library deposited
twice, in two units, became two rows that dedupe against nothing.

The cause is fixed where it lives instead -- ``_assert_param_value_
compatible`` now converts both sides to hartree before comparing (see
``app/services/energy_correction_resolution.py``; ``app/chemistry/
units.py:convert_energy_to_hartree`` already existed for the read
layer). ``units`` stays as a **column**, recording what the depositor
actually sent, because the scheme page promises the parameter table
"exactly as deposited" and rewriting their numbers into a canonical unit
on write would break that promise. It simply stops being part of the
key.

Why ``version`` goes too, and goes entirely
---------------------------------------------
``energy_correction_scheme.version`` was nullable free text, in the
identity, and ``NULL`` on every row of the only deployment that has any
(measured 2026-09-13). It versioned nothing.

The contrast that settles it is in this same schema:
``conformer_assignment_scheme`` carries ``name`` **and** ``version``,
both ``NOT NULL``, because it versions a *methodology* -- when the
torsion-selection rules change you genuinely need to know which revision
produced a given assignment. A parameter library is not that. If a set
of atom energies is refitted, what changed is the fit's provenance (its
citation, its software release), and those are already identity columns.
A counter beside them records nothing a reader can act on and invites
the "v1" label that has no released product behind it.

So the column is dropped, not merely unkeyed. Leaving it in place
unkeyed would be worse than either alternative: an inert field that
looks meaningful, that a future depositor will fill in, and that nothing
would then dedupe on.

The hazard, and the pre-flight check that closes it
-----------------------------------------------------
``c24ce2d9c198``'s hazard was that *replacing* a column could merge two
rows. This revision's hazard is the same one arrived at from the other
direction: **removing** two columns from a unique index can only merge,
never split. Two schemes distinguished today *only* by ``units``, or
*only* by ``version``, collapse into one the instant the narrowed index
is created.

Measured on the deployed Pi 2026-09-13: the two live rows differ by
``kind`` and ``name`` as well as by ``units``, so this deployment
narrows cleanly -- but as before the check is not written for this
deployment. It runs unconditionally and aborts loudly, naming the
colliding public refs, rather than merging or picking a winner.

Revision ID: a7d4e2b9c351
Revises: c24ce2d9c198
Create Date: 2026-09-13

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4e2b9c351"
down_revision: Union[str, None] = "c24ce2d9c198"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The narrowed identity. Any group here with more than one row is two
#: schemes that the deployed shape distinguished only by ``units`` or
#: ``version`` -- exactly the pairs the narrowed index would merge.
_COLLISION_QUERY = sa.text(
    """
    SELECT array_agg(public_ref ORDER BY id) AS refs, count(*) AS n
    FROM energy_correction_scheme
    GROUP BY kind, name, level_of_theory_id,
             source_literature_id, software_release_id,
             workflow_tool_release_id
    HAVING count(*) > 1
    """
)


def _refuse_narrowing_collisions(bind) -> None:
    """Abort rather than let the narrowed index silently merge two
    schemes that are distinguished today only by ``units``/``version``.

    Never merges, never picks a winner: names the colliding public refs
    and says what the operator has to decide first.
    """
    collisions = bind.execute(_COLLISION_QUERY).mappings().all()
    if not collisions:
        return

    details = "; ".join(
        f"{row['n']} schemes sharing (kind, name, level_of_theory_id, "
        f"source_literature_id, software_release_id, "
        f"workflow_tool_release_id): {row['refs']}"
        for row in collisions
    )
    raise RuntimeError(
        "Cannot upgrade to a7d4e2b9c351: this database holds "
        "energy_correction_scheme rows distinguished only by units or by "
        "version, the two columns this revision removes from "
        "uq_energy_correction_scheme_identity. Creating the narrowed "
        f"index would silently merge them into one. Affected: {details}.\n\n"
        "Decide what these rows are before re-running. If they are the "
        "same library written in two units, keep one and repoint its "
        "applied_energy_correction rows at the survivor -- they are one "
        "physical fact and this revision exists so they stop being two "
        "rows. If they are genuinely different libraries, they need a "
        "real discriminator (a differing source_literature_id or "
        "software_release_id), because after this revision units and "
        "version no longer provide one. This migration will never merge "
        "or pick between them itself."
    )


def upgrade() -> None:
    bind = op.get_bind()
    _refuse_narrowing_collisions(bind)

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
            "source_literature_id",
            "software_release_id",
            "workflow_tool_release_id",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # ``units`` is deliberately NOT dropped: it records what the
    # depositor sent, which the scheme page renders verbatim. Only its
    # role in the key changes.
    op.drop_column("energy_correction_scheme", "version")


def downgrade() -> None:
    op.add_column(
        "energy_correction_scheme",
        sa.Column("version", sa.Text(), nullable=True),
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

    # Restores the column, never a value. Every row comes back NULL,
    # which is what every row held on the only deployment that had any.
    # Widening an index can only split, so this direction needs no
    # pre-flight check.
