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
``computed`` is a strong claim -- it says this database holds the derivation --
so the backfill checks rather than assumes.

An earlier draft of this revision argued that ``server_default='computed'``
was a statement of fact, because ``validate_mechanistic_channel_evidence``
refused a solve without state energies, barriers and an energy-transfer model.
**That argument is backwards-projected and does not hold.** Those rules, and
the ``network_solve_state_energy`` / ``network_solve_channel_barrier`` tables
themselves, arrived on 2026-07-31 in ``c1d2e3f4a5b6``. ``network_solve`` ships
in the initial schema and the PDep write path landed on 2026-07-14, so for the
whole life of the table before Stage 2 a solve carrying no master-equation
inputs was not merely possible -- it was the ordinary shape. Reasoning from
today's validator to yesterday's rows is exactly the circularity ADR 0010
warns about: a validator on one write path was never a property of the record.

What does protect a *real* deployment is ``c1d2e3f4a5b6._refuse_legacy_pdep_data``,
which hard-refuses the Stage 2 upgrade while ``network_channel`` or
``network_solve_energy_transfer`` hold any rows, forcing delete-and-re-upload
under the v2 contract. But that gate counts only those two tables: a
pre-Stage-2 solve on a network with no channels and no energy-transfer rows
passes it untouched, and ``channels`` was ``default_factory=list`` in the
pre-Stage-2 upload schema, so such a payload was accepted.

So ``upgrade()`` asks the database instead. A solve with no state energies
cannot be honestly labelled: ``computed`` would assert a derivation that is
absent, and ``reported`` demands a literature link this revision cannot
invent. Rather than mint a false claim, the upgrade refuses and names the
rows, leaving a human to classify them -- the same stance
``_refuse_legacy_pdep_data`` and this revision's own ``downgrade()`` take.

The deployed hydrazine network on the Pi (network 4,
``net_o6bt63kjeyvhvxx26w6kdi433a``) was verified to carry state energies and
barriers for its single solve, so it passes the check and migrates to
``computed`` unchanged.

The new check constraint cannot fail on the backfill: it constrains only
``reported`` rows, and the backfill creates none.

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

    # Derive the backfill instead of asserting it. A solve holding no state
    # energies cannot honestly take either token, so refuse and name it rather
    # than stamp 'computed' on a row that has no derivation behind it. See the
    # module docstring for why the old contract does not prove what it looks
    # like it proves.
    unbacked = bind.execute(
        sa.text(
            "SELECT s.id FROM network_solve s WHERE NOT EXISTS ("
            "  SELECT 1 FROM network_solve_state_energy e WHERE e.solve_id = s.id"
            ") ORDER BY s.id"
        )
    ).scalars().all()
    if unbacked:
        listed = ", ".join(str(i) for i in unbacked[:20])
        more = "" if len(unbacked) <= 20 else f" (and {len(unbacked) - 20} more)"
        raise RuntimeError(
            f"{len(unbacked)} network_solve row(s) carry no state energies: "
            f"{listed}{more}.\n"
            "This revision labels every solve 'computed', which asserts that "
            "this database holds the derivation. These rows do not support "
            "that claim, and 'reported' is not available to them either -- it "
            "requires a literature link this migration cannot invent.\n"
            "Classify them first, then re-run:\n"
            "  1. Inspect: SELECT id, network_id, me_method, literature_id "
            "FROM network_solve WHERE id IN (...);\n"
            "  2. If the master-equation inputs exist elsewhere, re-upload the "
            "network under the current contract (see "
            "backend/docs/deployment/migrations.md).\n"
            "  3. If the rates came from a publication, attach the literature "
            "and set kind='reported' by hand after this revision applies.\n"
            "  4. If the rows are abandoned scaffolding, delete them."
        )

    network_solve_kind.create(bind, checkfirst=True)

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
            "(GET /api/v1/scientific/network-solves/search?kind=reported).\n"
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
