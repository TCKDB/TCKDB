"""Say whether a network's k(T,P) was solved here or read out of a paper.

Adds ``network_solve.kind``, a non-null machine token with two members, and a
check constraint tying the second member to a literature reference:

``computed``
    The master equation was solved here and its inputs were deposited with it
    — a state energy per network state, a barrier per saddle-point path, and a
    collisional energy-transfer model. Every coverage rule that applied before
    this revision still applies, unchanged. This is the preferred form.

``reported``
    The rates were transcribed from a publication. The master-equation inputs
    are not in this database, so the three coverage rules are relaxed and
    ``literature_id`` becomes mandatory instead.

Why this table, and why a token
-------------------------------
Before this revision ``network_kinetics.solve_id`` was NOT NULL and
``channel_kinetics`` was reachable only through a solve, so published k(T,P)
could not be deposited at all: the coverage rules demanded master-equation
inputs that the depositor, by construction, does not have. The alternative fix
— relaxing ``network_kinetics.solve_id`` to nullable and hanging the rates off
the channel — was rejected. It would have duplicated the solve's
literature/software/workflow-tool triple onto ``network_kinetics``, and a NULL
``solve_id`` would have carried two incompatible readings, "transcribed from a
publication" and "the writer never linked the solve", with nothing in the row
to separate them. That is exactly the ambiguity ``b6e1d3a9c740`` removed for
energy-transfer scope and ``d5b1a7c3e9f4`` removed for channel mechanism: a
scientific claim about a record is positively asserted, never inferred from an
absence. ``network_solve`` already carries the provenance triple, including
the nullable ``literature_id`` a reported deposit needs, so the token goes
there and no new nullable FK is introduced.

``ck_network_solve_reported_requires_literature`` makes the second half
structural rather than merely validated at the API boundary. It constrains
only the ``reported`` member; a ``computed`` solve may still cite literature,
and often should.

Backfill design
---------------
``server_default='computed'`` is a statement of fact about existing rows, not
a guess. Before this revision the schema *only* admitted computed solves: a
solve could not be written without state energies covering every network
state, a barrier for every saddle-point path, and an energy-transfer model,
because ``validate_mechanistic_channel_evidence`` refused anything else, and
there was no way to express a reported one. Every stored row is therefore a
master-equation solve by construction. The deployed hydrazine network on the
Pi (network 4, ``net_o6bt63kjeyvhvxx26w6kdi433a``: one solve, 21 channels, 42
kinetics rows, 2 energy-transfer rows) migrates to ``computed`` and keeps
reading exactly as it did, and its 42 k(T,P) stay attributed to a derivation
this database holds.

The new check constraint cannot fail on the backfill for the same reason: it
constrains only ``reported`` rows, and the backfill creates none.

The default is kept on the column so that an ORM client that does not know
about this axis writes the stronger, more informative claim rather than
silently producing a record nobody can re-derive.

Downgrade removes the axis. It refuses to run while any ``reported`` solve
exists, because the pre-``c4d8f1b2a9e6`` schema has no way to say that a set
of k(T,P) was transcribed rather than derived — dropping the column would
present published numbers as this database's own master-equation output,
which is a stronger claim than the record supports and the precise failure
this revision exists to prevent.

Revision ID: c4d8f1b2a9e6
Revises: b6e1d3a9c740
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4d8f1b2a9e6"
down_revision: Union[str, Sequence[str], None] = "b6e1d3a9c740"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


network_solve_kind = postgresql.ENUM(
    "computed",
    "reported",
    name="network_solve_kind",
    create_type=False,
)

_REPORTED_REQUIRES_LITERATURE = "kind <> 'reported' OR literature_id IS NOT NULL"


def upgrade() -> None:
    """Add the origin token; every existing solve is a computed one."""
    bind = op.get_bind()
    network_solve_kind.create(bind, checkfirst=True)

    # Nothing but a master-equation solve was representable before this
    # revision, so 'computed' is derived from the old contract rather than
    # assumed about the data.
    op.add_column(
        "network_solve",
        sa.Column(
            "kind",
            network_solve_kind,
            nullable=False,
            server_default="computed",
        ),
    )

    op.create_check_constraint(
        "reported_requires_literature",
        "network_solve",
        _REPORTED_REQUIRES_LITERATURE,
    )


def downgrade() -> None:
    """Remove the axis, refusing to relabel reported rates as derived ones."""
    bind = op.get_bind()
    reported = bind.execute(
        sa.text("SELECT count(*) FROM network_solve WHERE kind = 'reported'")
    ).scalar_one()
    if reported:
        raise RuntimeError(
            f"Cannot downgrade from c4d8f1b2a9e6: this database holds {reported} "
            "network solve(s) whose k(T,P) were transcribed from a publication "
            "rather than derived here. The pre-c4d8f1b2a9e6 schema has no way "
            "to record that distinction, so dropping the column would present "
            "those rates as this database's own master-equation output — a "
            "claim the record does not support, since it holds none of the "
            "inputs.\n"
            "Operator steps:\n"
            "  1. Back up the database.\n"
            "  2. Export the affected solves "
            "(GET /scientific/network-solves?kind=reported).\n"
            "  3. Delete the reported solves, and the network kinetics that "
            "hang off them.\n"
            "  4. Re-run the downgrade."
        )

    # Bare name: the metadata naming convention expands it back to
    # ``ck_network_solve_reported_requires_literature``.
    op.drop_constraint(
        "reported_requires_literature",
        "network_solve",
        type_="check",
    )
    op.drop_column("network_solve", "kind")
    network_solve_kind.drop(bind, checkfirst=True)
