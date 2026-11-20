"""Method and basis-set catalogue.

Creates ``method_catalogue`` and ``basis_set_catalogue``, their identity-key tables (``*_key``) and their literature
link tables (``*_literature``), with the enums ``catalogue_method_kind``, ``basis_set_role`` and
``catalogue_reference_role``. Then fills them from the seed files in ``app/data/catalogue/`` through
``app.services.method_catalogue_sync.sync_catalogue``, creating the cited ``literature`` rows (and their ``author``
rows) that do not exist yet and upgrading a cited row whose title was not registry-resolved.

Downgrade drops the six tables and three enums. The literature and author rows stay: they are ordinary citations
other records may cite.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a6fc04f5667e"
down_revision: Union[str, Sequence[str], None] = "a7c3e1f5b942"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_METHOD_KIND = postgresql.ENUM("method", "dispersion_correction", name="catalogue_method_kind", create_type=False)
_BASIS_ROLE = postgresql.ENUM(
    "orbital", "auxiliary_correlation", "auxiliary_coulomb", "cabs", name="basis_set_role", create_type=False
)
_REFERENCE_ROLE = postgresql.ENUM(
    "original", "component", "implementation", name="catalogue_reference_role", create_type=False
)


def _entry_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.Text(), nullable=False),
    ]


def _common_columns(table: str) -> list[sa.SchemaItem]:
    return [
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("seed_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.UniqueConstraint("key", name=f"uq_{table}_key"),
        sa.CheckConstraint("key = lower(btrim(key)) AND key <> ''", name="key_is_normalised"),
        sa.CheckConstraint("seed_version >= 1", name="seed_version_positive"),
    ]


def _key_and_link_tables(table: str) -> None:
    fk = f"{table}_id"
    op.create_table(
        f"{table}_key",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(fk, sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=f"pk_{table}_key"),
        sa.ForeignKeyConstraint(
            [fk],
            [f"{table}.id"],
            name=f"fk_{table}_key_entry",
            ondelete="CASCADE",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.CheckConstraint("key = lower(btrim(key)) AND key <> ''", name="key_is_normalised"),
    )
    op.create_index(f"ix_{table}_key_{fk}", f"{table}_key", [fk])
    op.create_table(
        f"{table}_literature",
        sa.Column(fk, sa.BigInteger(), nullable=False),
        sa.Column("literature_id", sa.BigInteger(), nullable=False),
        sa.Column("role", _REFERENCE_ROLE, nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint(fk, "literature_id", name=f"pk_{table}_literature"),
        sa.ForeignKeyConstraint(
            [fk],
            [f"{table}.id"],
            name=f"fk_{table}_literature_entry",
            ondelete="CASCADE",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
            name=f"fk_{table}_literature_literature_id_literature",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.UniqueConstraint(fk, "position", name=f"uq_{table}_literature_position"),
        sa.CheckConstraint("position >= 1", name="position_positive"),
    )
    op.create_index(f"ix_{table}_literature_literature_id", f"{table}_literature", ["literature_id"])


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (_METHOD_KIND, _BASIS_ROLE, _REFERENCE_ROLE):
        enum.create(bind, checkfirst=False)

    op.create_table(
        "method_catalogue",
        *_entry_columns(),
        sa.Column("kind", _METHOD_KIND, nullable=False),
        *_common_columns("method_catalogue"),
    )
    op.create_index("ix_method_catalogue_public_ref", "method_catalogue", ["public_ref"], unique=True)
    _key_and_link_tables("method_catalogue")

    op.create_table(
        "basis_set_catalogue",
        *_entry_columns(),
        sa.Column("role", _BASIS_ROLE, nullable=False),
        sa.Column("family", sa.Text(), nullable=False),
        *_common_columns("basis_set_catalogue"),
    )
    op.create_index("ix_basis_set_catalogue_public_ref", "basis_set_catalogue", ["public_ref"], unique=True)
    _key_and_link_tables("basis_set_catalogue")

    from app.services.method_catalogue_seed import load_seed
    from app.services.method_catalogue_sync import sync_catalogue

    sync_catalogue(bind, load_seed(), apply=True)


def downgrade() -> None:
    for table in ("basis_set_catalogue", "method_catalogue"):
        op.drop_table(f"{table}_literature")
        op.drop_table(f"{table}_key")
        op.drop_table(table)
    bind = op.get_bind()
    for enum in (_REFERENCE_ROLE, _BASIS_ROLE, _METHOD_KIND):
        enum.drop(bind, checkfirst=False)
