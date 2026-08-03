"""Record what a ⟨ΔE⟩down declaration was actually specified over.

Adds ``network_solve_energy_transfer.scope``, a non-null machine token with
two members, and relaxes ``state_id`` / ``collider_species_entry_id`` to
nullable so the second member is representable:

``per_well``
    The declaration names one network state and one collider species and
    applies only to that pair. This is the preferred form — collisional
    energy transfer genuinely is a property of a (well, collider) pair.

``network_wide``
    One declaration the producer applied to every collisionally stabilised
    well and to the bath gas as a whole. Arkane, RMG and MESS inputs routinely
    specify exactly this: a single ``SingleExponentialDown`` for the network.
    Such a row names no state and no collider **by declaration**, not by
    omission.

Why a stored column rather than a nullable ``state_id`` alone
------------------------------------------------------------
Without the token, a NULL ``state_id`` would carry two incompatible readings —
"the producer declared this network-wide" and "the producer dropped the field"
— and nothing in the row would say which. That is the same argument that gave
``network_channel.mechanism`` its own column in ``d5b1a7c3e9f4``: a scientific
claim about the record must be positively asserted, never inferred from an
absence. The check constraint ``ck_..._scope_columns_agree`` then makes the
two shapes mutually exclusive, so a NULL is always an explained absence.

Backfill design
---------------
``server_default='per_well'`` is correct and unambiguous rather than a guess.
Before this revision both scope columns were NOT NULL, so every existing row
already resolves a (state, collider) pair — that is precisely what ``per_well``
means. The deployed hydrazine network on the Pi (network 4,
``net_o6bt63kjeyvhvxx26w6kdi433a``) migrates to ``per_well`` and keeps reading
exactly as it did. No legacy row is relabelled, and no row acquires a NULL.

The default is kept on the column so that an ORM client that does not know
about this axis still writes the more specific, more informative value.

``uq_network_solve_energy_transfer_scope`` (the ``(solve_id, state_id,
collider_species_entry_id)`` tuple) is retained: it still de-duplicates
per-well rows. It cannot police network-wide rows, because Postgres treats
NULLs as distinct and would accept two of them, so a partial unique index on
``solve_id WHERE scope = 'network_wide'`` is added alongside it.

Downgrade removes the axis. It refuses to run while any ``network_wide`` row
exists, because dropping the column would either delete a real declaration or
silently re-present it as a per-well value it never was — and there is no
state or collider to restore for it. The operator's recourse is the same as
for any lossy contract change: export the affected solves and re-upload them
against the older per-well contract.

Revision ID: b6e1d3a9c740
Revises: a7c2e4f8b6d9
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b6e1d3a9c740"
down_revision: Union[str, Sequence[str], None] = "a7c2e4f8b6d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


network_energy_transfer_scope = postgresql.ENUM(
    "per_well",
    "network_wide",
    name="network_energy_transfer_scope",
    create_type=False,
)

_SCOPE_COLUMNS_AGREE = (
    "(scope = 'per_well' AND state_id IS NOT NULL "
    "AND collider_species_entry_id IS NOT NULL) OR "
    "(scope = 'network_wide' AND state_id IS NULL "
    "AND collider_species_entry_id IS NULL)"
)


def upgrade() -> None:
    """Add the declaration-scope token and allow the unresolved shape."""
    bind = op.get_bind()
    network_energy_transfer_scope.create(bind, checkfirst=True)

    # Every pre-existing row resolves a (state, collider) pair by construction:
    # both columns were NOT NULL until this revision. 'per_well' is therefore a
    # statement of fact about them, not an assumption.
    op.add_column(
        "network_solve_energy_transfer",
        sa.Column(
            "scope",
            network_energy_transfer_scope,
            nullable=False,
            server_default="per_well",
        ),
    )

    op.alter_column(
        "network_solve_energy_transfer",
        "state_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.alter_column(
        "network_solve_energy_transfer",
        "collider_species_entry_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )

    op.create_check_constraint(
        "scope_columns_agree",
        "network_solve_energy_transfer",
        _SCOPE_COLUMNS_AGREE,
    )
    op.create_index(
        "uq_network_solve_energy_transfer_network_wide",
        "network_solve_energy_transfer",
        ["solve_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'network_wide'"),
    )


def downgrade() -> None:
    """Remove the scope axis, refusing to destroy a network-wide declaration."""
    bind = op.get_bind()
    network_wide = bind.execute(
        sa.text(
            "SELECT count(*) FROM network_solve_energy_transfer "
            "WHERE scope = 'network_wide'"
        )
    ).scalar_one()
    if network_wide:
        raise RuntimeError(
            "Cannot downgrade from b6e1d3a9c740: this database holds "
            f"{network_wide} network-wide energy-transfer declaration(s). "
            "The pre-b6e1d3a9c740 schema requires a (state, collider) pair on "
            "every row, and no such pair was ever determined for these — the "
            "producer declared a single model for the whole network. Dropping "
            "the column would either delete the declaration or re-present it "
            "as per-well data it never was.\n"
            "Operator steps:\n"
            "  1. Back up the database.\n"
            "  2. Export the affected solves "
            "(GET /scientific/network-solves?include=energy_transfer).\n"
            "  3. Delete the network-wide rows, or the solves that carry "
            "them.\n"
            "  4. Re-run the downgrade."
        )

    op.drop_index(
        "uq_network_solve_energy_transfer_network_wide",
        table_name="network_solve_energy_transfer",
    )
    # Bare name: the metadata naming convention expands it back to
    # ``ck_network_solve_energy_transfer_scope_columns_agree``.
    op.drop_constraint(
        "scope_columns_agree",
        "network_solve_energy_transfer",
        type_="check",
    )
    op.alter_column(
        "network_solve_energy_transfer",
        "collider_species_entry_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.alter_column(
        "network_solve_energy_transfer",
        "state_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.drop_column("network_solve_energy_transfer", "scope")
    network_energy_transfer_scope.drop(bind, checkfirst=True)
