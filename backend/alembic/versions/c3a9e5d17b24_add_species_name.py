"""add species_name

Creates ``species_name``: names of species for finding a molecule by its common name. Brand-new table; no
backfill. A name is retired (``retired_at``), never deleted. Unique per species over live rows on the normalised
``name_key``; ``name_key`` has "C" collation, so its btree serves exact and prefix-range searches. The table carries a public
ref (prefix ``snm``) so a curator can cite a name to retire it.

Downgrade drops the table and the two new enum types; names are lost.

Revision ID: c3a9e5d17b24
Revises: b8e3f1a7c524
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3a9e5d17b24"
down_revision: Union[str, Sequence[str], None] = "b8e3f1a7c524"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KIND = postgresql.ENUM("common", "systematic", "abbreviation", name="species_name_kind", create_type=False)
_SOURCE = postgresql.ENUM("curated", "offline_import", name="species_name_source_kind", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    _KIND.create(bind, checkfirst=True)
    _SOURCE.create(bind, checkfirst=True)
    op.create_table(
        "species_name",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("species_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(collation="C"), nullable=False),
        sa.Column("kind", _KIND, nullable=False),
        sa.Column("source_kind", _SOURCE, nullable=False),
        sa.Column("external_source_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 128", name=op.f("ck_species_name_name_length")),
        sa.CheckConstraint("char_length(name_key) BETWEEN 1 AND 128", name=op.f("ck_species_name_name_key_length")),
        sa.ForeignKeyConstraint(
            ["species_id"], ["species.id"], name=op.f("fk_species_name_species_id_species"),
            deferrable=True, initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["external_source_id"], ["external_source.id"],
            name=op.f("fk_species_name_external_source_id_external_source"),
            deferrable=True, initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["app_user.id"], name=op.f("fk_species_name_created_by_user_id_app_user"),
            deferrable=True, initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_species_name")),
    )
    op.create_index(op.f("ix_species_name_public_ref"), "species_name", ["public_ref"], unique=True)
    op.create_index("ix_species_name_species_id", "species_name", ["species_id"], unique=False)
    op.create_index(
        "uq_species_name_species_id_name_key_live",
        "species_name",
        ["species_id", "name_key"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_index("ix_species_name_name_key", "species_name", ["name_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_species_name_name_key", table_name="species_name")
    op.drop_index("uq_species_name_species_id_name_key_live", table_name="species_name")
    op.drop_index("ix_species_name_species_id", table_name="species_name")
    op.drop_index(op.f("ix_species_name_public_ref"), table_name="species_name")
    op.drop_table("species_name")
    bind = op.get_bind()
    _SOURCE.drop(bind, checkfirst=True)
    _KIND.drop(bind, checkfirst=True)
