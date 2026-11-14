"""Record who retired a species name.

Adds ``species_name.retired_by_user_id``, a nullable reference to ``app_user``, set when a curator or admin retires a
name. Names retired before the column existed keep NULL, which reads as "retirer not recorded". A check keeps a
retirer from being set on a name that is not retired.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a7c3e1f5b942"
down_revision: Union[str, Sequence[str], None] = "d5b8e2f4a916"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("species_name", sa.Column("retired_by_user_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_species_name_retired_by_user_id_app_user",
        "species_name",
        "app_user",
        ["retired_by_user_id"],
        ["id"],
        deferrable=True,
    )
    op.create_check_constraint(
        "ck_species_name_retired_by_needs_retired_at",
        "species_name",
        "retired_by_user_id IS NULL OR retired_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_species_name_retired_by_needs_retired_at", "species_name", type_="check")
    op.drop_constraint("fk_species_name_retired_by_user_id_app_user", "species_name", type_="foreignkey")
    op.drop_column("species_name", "retired_by_user_id")
