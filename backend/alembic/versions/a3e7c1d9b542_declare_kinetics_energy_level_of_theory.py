"""Let a kinetics record declare the level of theory its energies came from.

Additive, with no data step: ``kinetics.energy_level_of_theory_id``, a nullable foreign key
to ``level_of_theory`` with an index, the same shape ``thermo`` and ``statmech`` took in
``f2c8a5d1e9b7``.

The value is what the depositor declared (``energy_level_of_theory`` on the upload). Until
now the standalone kinetics route used it only to find source single points and then threw
it away, and a bundle's kinetics had no such field, so a record could not say which method
its barrier came from.

No backfill. A declaration is not recoverable from a stored row: the level the read layer
derives from the linked calculations is often the same, but a record that declared one and
a record that did not look identical in the stored rows, and writing a derived level into a
row that never declared one would record a claim nobody made. Existing rows keep NULL,
which means "nothing declared".

Accepted-science immutability: ``kinetics`` has an accepted-science row trigger, but adding a
nullable column runs no trigger and this revision issues no UPDATE or INSERT, so no approved
row is touched and no declared repair is needed.

Downgrade drops the index, the foreign key and the column. It forgets every declared level
made after the upgrade, and prints how many it is forgetting first.

Revision ID: a3e7c1d9b542
Revises: c7a2e5d9b148
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a3e7c1d9b542"
down_revision: Union[str, Sequence[str], None] = "c7a2e5d9b148"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = "fk_kinetics_energy_level_of_theory_id_level_of_theory"
_INDEX = "ix_kinetics_energy_level_of_theory_id"


def upgrade() -> None:
    op.add_column(
        "kinetics",
        sa.Column("energy_level_of_theory_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f(_FK),
        "kinetics",
        "level_of_theory",
        ["energy_level_of_theory_id"],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )
    op.create_index(
        op.f(_INDEX), "kinetics", ["energy_level_of_theory_id"], unique=False
    )


def downgrade() -> None:
    declared = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM kinetics WHERE energy_level_of_theory_id IS NOT NULL"
            )
        )
        .scalar_one()
    )
    print(
        f"a3e7c1d9b542 downgrade forgets: {declared} kinetics declared energy level(s)."
    )
    op.drop_index(op.f(_INDEX), table_name="kinetics")
    op.drop_constraint(op.f(_FK), "kinetics", type_="foreignkey")
    op.drop_column("kinetics", "energy_level_of_theory_id")
