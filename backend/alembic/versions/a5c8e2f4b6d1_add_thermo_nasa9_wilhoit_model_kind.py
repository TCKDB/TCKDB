"""add thermo NASA-9 + Wilhoit representations and explicit model_kind

Adds three additive pieces to the thermo group:

* ``thermo.model_kind`` — a nullable ``thermo_model_kind`` enum making the
  record's representation explicit (nasa7 / nasa9 / wilhoit / tabulated /
  scalar) instead of implied by which child rows exist. Existing rows are
  backfilled from their current child rows / scalar columns.
* ``thermo_nasa9_interval`` — one row per temperature interval of a NASA-9
  (Glenn/NASA-9) polynomial; a NASA-9 fit has an arbitrary number of
  intervals, each with nine coefficients (a1..a9).
* ``thermo_wilhoit`` — a 1:1 child holding the continuous Wilhoit Cp form.

The existing ``thermo_nasa`` (NASA-7) and ``thermo_point`` tables are left
untouched. ``thermo`` is a deployed table, so this is a new revision off the
current head; the only touch to ``thermo`` is the additive nullable
``model_kind`` column. Both ``upgrade`` and ``downgrade`` are implemented.

Revision ID: a5c8e2f4b6d1
Revises: b7e2d4f6a8c1
Create Date: 2026-07-16 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a5c8e2f4b6d1"
down_revision: Union[str, Sequence[str], None] = "b7e2d4f6a8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False: the type is created/dropped explicitly below so that
# add_column / create_table do not also try to auto-create it (which would
# raise DuplicateObject).
_THERMO_MODEL_KIND = postgresql.ENUM(
    "nasa7",
    "nasa9",
    "wilhoit",
    "tabulated",
    "scalar",
    name="thermo_model_kind",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""
    _THERMO_MODEL_KIND.create(op.get_bind(), checkfirst=True)

    # --- Additive nullable enum column on the deployed thermo table -------
    op.add_column(
        "thermo",
        sa.Column("model_kind", _THERMO_MODEL_KIND, nullable=True),
    )

    # --- NASA-9 intervals (one row per interval) -------------------------
    op.create_table(
        "thermo_nasa9_interval",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("thermo_id", sa.BigInteger(), nullable=False),
        sa.Column("interval_index", sa.Integer(), nullable=False),
        sa.Column("t_min_k", sa.Double(), nullable=False),
        sa.Column("t_max_k", sa.Double(), nullable=False),
        sa.Column("a1", sa.Double(), nullable=False),
        sa.Column("a2", sa.Double(), nullable=False),
        sa.Column("a3", sa.Double(), nullable=False),
        sa.Column("a4", sa.Double(), nullable=False),
        sa.Column("a5", sa.Double(), nullable=False),
        sa.Column("a6", sa.Double(), nullable=False),
        sa.Column("a7", sa.Double(), nullable=False),
        sa.Column("a8", sa.Double(), nullable=False),
        sa.Column("a9", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["thermo_id"],
            ["thermo.id"],
            name=op.f("fk_thermo_nasa9_interval_thermo_id_thermo"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_thermo_nasa9_interval")),
        sa.UniqueConstraint(
            "thermo_id",
            "interval_index",
            name="uq_thermo_nasa9_interval_index",
        ),
        sa.CheckConstraint(
            "interval_index >= 1",
            name=op.f("ck_thermo_nasa9_interval_interval_index_ge_1"),
        ),
        sa.CheckConstraint(
            "t_min_k > 0",
            name=op.f("ck_thermo_nasa9_interval_t_min_k_gt_0"),
        ),
        sa.CheckConstraint(
            "t_max_k > t_min_k",
            name=op.f("ck_thermo_nasa9_interval_t_max_k_gt_t_min_k"),
        ),
    )
    op.create_index(
        op.f("ix_thermo_nasa9_interval_thermo_id"),
        "thermo_nasa9_interval",
        ["thermo_id"],
        unique=False,
    )

    # --- Wilhoit (1:1 child) ---------------------------------------------
    op.create_table(
        "thermo_wilhoit",
        sa.Column("thermo_id", sa.BigInteger(), nullable=False),
        sa.Column("cp0_j_mol_k", sa.Double(), nullable=False),
        sa.Column("cp_inf_j_mol_k", sa.Double(), nullable=False),
        sa.Column("b_k", sa.Double(), nullable=False),
        sa.Column("a0", sa.Double(), nullable=False),
        sa.Column("a1", sa.Double(), nullable=False),
        sa.Column("a2", sa.Double(), nullable=False),
        sa.Column("a3", sa.Double(), nullable=False),
        sa.Column("h0_kj_mol", sa.Double(), nullable=True),
        sa.Column("s0_j_mol_k", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["thermo_id"],
            ["thermo.id"],
            name=op.f("fk_thermo_wilhoit_thermo_id_thermo"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("thermo_id", name=op.f("pk_thermo_wilhoit")),
        sa.CheckConstraint(
            "cp0_j_mol_k >= 0", name=op.f("ck_thermo_wilhoit_cp0_ge_0")
        ),
        sa.CheckConstraint(
            "cp_inf_j_mol_k >= 0", name=op.f("ck_thermo_wilhoit_cp_inf_ge_0")
        ),
        sa.CheckConstraint("b_k > 0", name=op.f("ck_thermo_wilhoit_b_k_gt_0")),
    )

    # --- Backfill existing thermo rows' model_kind -----------------------
    # Precedence: nasa7 (has a thermo_nasa row) > tabulated (has any
    # thermo_point rows) > scalar (h298/s298 present) > leave NULL.
    op.execute(
        """
        UPDATE thermo
        SET model_kind = 'nasa7'
        WHERE model_kind IS NULL
          AND EXISTS (
              SELECT 1 FROM thermo_nasa tn WHERE tn.thermo_id = thermo.id
          )
        """
    )
    op.execute(
        """
        UPDATE thermo
        SET model_kind = 'tabulated'
        WHERE model_kind IS NULL
          AND EXISTS (
              SELECT 1 FROM thermo_point tp WHERE tp.thermo_id = thermo.id
          )
        """
    )
    op.execute(
        """
        UPDATE thermo
        SET model_kind = 'scalar'
        WHERE model_kind IS NULL
          AND (h298_kj_mol IS NOT NULL OR s298_j_mol_k IS NOT NULL)
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("thermo_wilhoit")
    op.drop_index(
        op.f("ix_thermo_nasa9_interval_thermo_id"),
        table_name="thermo_nasa9_interval",
    )
    op.drop_table("thermo_nasa9_interval")
    op.drop_column("thermo", "model_kind")
    _THERMO_MODEL_KIND.drop(op.get_bind(), checkfirst=True)
