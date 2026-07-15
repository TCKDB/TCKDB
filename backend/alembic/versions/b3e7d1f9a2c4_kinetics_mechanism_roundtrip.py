"""kinetics: mechanism round-trip (direction, multi-Arrhenius, network bridge)

DR-0036. Closes the reaction-level kinetics round-trip gaps a Chemkin/Cantera
importer hits on day one:

* ``kinetics.direction`` (new ``kinetics_direction`` enum: ``forward`` /
  ``reverse`` / ``net``, nullable) — so a forward and a reverse Arrhenius fit
  for one ``reaction_entry`` coexist distinctly. NULL = unspecified, the
  historical default, so existing rows are unaffected.
* ``kinetics_arrhenius_entry`` — per-term modified-Arrhenius child rows for a
  Chemkin ``DUPLICATE`` channel (k = sum of N terms). Selected by the new
  ``multi_arrhenius`` value of ``kinetics_model_kind``; modelled exactly like
  ``kinetics_plog`` but indexed by term instead of pressure.
* ``kinetics.network_kinetics_id`` — nullable FK to ``network_kinetics``,
  bridging a reaction's HPL/apparent fit to its pressure-dependent network
  counterpart so "k(T,P) for reaction R" resolves in one join.

All additions are nullable/additive; no backfill and no existing rows rewritten.

Downgrade caveat: the ``multi_arrhenius`` enum value is added with
``ALTER TYPE ... ADD VALUE``. PostgreSQL cannot drop a single enum value, so
downgrade reverses everything *except* that label (which is left in place as a
harmless unused value). Re-running upgrade is idempotent (``ADD VALUE IF NOT
EXISTS``).

Revision ID: b3e7d1f9a2c4
Revises: d3f5b7a9c1e2
Create Date: 2026-07-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b3e7d1f9a2c4"
down_revision: Union[str, Sequence[str], None] = "d3f5b7a9c1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


arrhenius_a_units = postgresql.ENUM(name="arrhenius_a_units", create_type=False)
kinetics_direction = postgresql.ENUM(
    "forward", "reverse", "net", name="kinetics_direction"
)


def upgrade() -> None:
    """Upgrade schema."""
    # New enum VALUE on the existing kinetics_model_kind type. PostgreSQL 12+
    # permits ADD VALUE inside a transaction provided the value is not *used*
    # in the same transaction; this migration never inserts it.
    op.execute(
        "ALTER TYPE kinetics_model_kind ADD VALUE IF NOT EXISTS 'multi_arrhenius'"
    )

    # New enum TYPE for the fit direction (freshly created, so it can be used
    # by the add_column below within this same transaction).
    kinetics_direction.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "kinetics",
        sa.Column(
            "direction",
            postgresql.ENUM(
                "forward", "reverse", "net",
                name="kinetics_direction",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "kinetics",
        sa.Column("network_kinetics_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        op.f("ix_kinetics_network_kinetics_id"),
        "kinetics",
        ["network_kinetics_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_kinetics_network_kinetics_id_network_kinetics"),
        "kinetics",
        "network_kinetics",
        ["network_kinetics_id"],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )

    op.create_table(
        "kinetics_arrhenius_entry",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kinetics_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_index", sa.Integer(), nullable=False),
        sa.Column("a", sa.Double(), nullable=False),
        sa.Column("a_units", arrhenius_a_units, nullable=True),
        sa.Column("n", sa.Double(), nullable=True),
        sa.Column("ea_kj_mol", sa.Double(), nullable=True),
        sa.CheckConstraint(
            "entry_index >= 1",
            name=op.f("ck_kinetics_arrhenius_entry_arrhenius_entry_index_ge_1"),
        ),
        sa.ForeignKeyConstraint(
            ["kinetics_id"], ["kinetics.id"],
            name=op.f("fk_kinetics_arrhenius_entry_kinetics_id_kinetics"),
            initially="IMMEDIATE", deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kinetics_arrhenius_entry")),
        sa.UniqueConstraint(
            "kinetics_id", "entry_index", name="uq_kinetics_arrhenius_entry"
        ),
    )


def downgrade() -> None:
    """Downgrade schema.

    Reverses everything except the ``multi_arrhenius`` enum value, which
    PostgreSQL cannot drop individually; it is left as an unused label.
    """
    op.drop_table("kinetics_arrhenius_entry")
    op.drop_constraint(
        op.f("fk_kinetics_network_kinetics_id_network_kinetics"),
        "kinetics",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_kinetics_network_kinetics_id"), table_name="kinetics")
    op.drop_column("kinetics", "network_kinetics_id")
    op.drop_column("kinetics", "direction")
    kinetics_direction.drop(op.get_bind(), checkfirst=True)
