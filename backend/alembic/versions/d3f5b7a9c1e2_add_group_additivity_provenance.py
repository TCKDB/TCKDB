"""add group-additivity (Benson) estimation provenance

Reifies ``scientific_origin=estimated`` thermo provenance with a two-layer
design mirroring the energy-correction tables (DR-0003 / DR-0035):

* ``group_additivity_scheme`` — reusable, literature-sourced description of a
  group-additivity library / estimator (reference layer). Deduped on
  ``(name, version)``; carries a ``public_ref``.
* ``applied_group_additivity`` — one estimation, linking a scheme to the
  ``thermo`` record it produced (application layer). ``thermo_id`` is NOT NULL
  and UNIQUE (one breakdown per thermo; applied-GA is always attached to a
  persisted thermo). This is a brand-new, not-yet-deployed table, so the NOT
  NULL is set at create time in-revision per the migration rules.
* ``applied_group_additivity_component`` — per-Benson-group contribution to
  H298 / S298 / Cp298 (fixed-unit columns per the unit policy).

These are brand-new tables, so they are defined here in a single new revision
off the current head (deployed-table rules: ``thermo`` is deployed, but the
only touch to ``thermo`` is an additive nullable FK from the new applied
table — no change to existing ``thermo`` columns). Both ``upgrade`` and
``downgrade`` are implemented.

Revision ID: d3f5b7a9c1e2
Revises: c4a7e2f1b8d9
Create Date: 2026-07-15 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f5b7a9c1e2"
down_revision: Union[str, Sequence[str], None] = "c4a7e2f1b8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# One enum object, created explicitly and reused as the column type with
# ``create_type=False`` so ``create_table`` does not emit a second CREATE TYPE
# (mirrors the c4a7e2f1b8d9 phase_kind pattern).
_GA_COMPONENT_KIND = postgresql.ENUM(
    "group",
    "ring_correction",
    "gauche_correction",
    "cis_correction",
    "symmetry_correction",
    "other",
    name="group_additivity_component_kind",
    create_type=False,
)

# Prefix for the scheme's public_ref server-side fallback (mirrors the
# baseline _add_public_ref_columns_and_indexes pattern). ORM inserts override
# this placeholder with an opaque base32 ref.
_SCHEME_PUBLIC_REF_DEFAULT = sa.text(
    "'gasch_' || substring(replace(gen_random_uuid()::text, '-', ''), 1, 26)"
)


def upgrade() -> None:
    """Upgrade schema."""
    _GA_COMPONENT_KIND.create(op.get_bind(), checkfirst=True)

    # --- Reference layer -------------------------------------------------
    op.create_table(
        "group_additivity_scheme",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_literature_id", sa.BigInteger(), nullable=True),
        sa.Column("code_commit", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "public_ref",
            sa.String(length=40),
            nullable=False,
            server_default=_SCHEME_PUBLIC_REF_DEFAULT,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_group_additivity_scheme_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["source_literature_id"],
            ["literature.id"],
            name=op.f(
                "fk_group_additivity_scheme_source_literature_id_literature"
            ),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_additivity_scheme")),
    )
    op.create_index(
        op.f("ix_group_additivity_scheme_public_ref"),
        "group_additivity_scheme",
        ["public_ref"],
        unique=True,
    )
    op.create_index(
        "uq_group_additivity_scheme_name_version",
        "group_additivity_scheme",
        ["name", "version"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # --- Application layer ----------------------------------------------
    op.create_table(
        "applied_group_additivity",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("thermo_id", sa.BigInteger(), nullable=False),
        sa.Column("scheme_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_applied_group_additivity_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["scheme_id"],
            ["group_additivity_scheme.id"],
            name=op.f(
                "fk_applied_group_additivity_scheme_id_group_additivity_scheme"
            ),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["thermo_id"],
            ["thermo.id"],
            name=op.f("fk_applied_group_additivity_thermo_id_thermo"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_applied_group_additivity")),
        sa.UniqueConstraint(
            "thermo_id", name=op.f("uq_applied_group_additivity_thermo_id")
        ),
    )

    op.create_table(
        "applied_group_additivity_component",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "applied_group_additivity_id", sa.BigInteger(), nullable=False
        ),
        sa.Column("component_kind", _GA_COMPONENT_KIND, nullable=False),
        sa.Column("group_label", sa.Text(), nullable=False),
        sa.Column(
            "count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("h298_contribution_kj_mol", sa.Double(), nullable=True),
        sa.Column("s298_contribution_j_mol_k", sa.Double(), nullable=True),
        sa.Column("cp298_contribution_j_mol_k", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["applied_group_additivity_id"],
            ["applied_group_additivity.id"],
            name=op.f(
                "fk_applied_group_additivity_component_applied_group_additivity_id_applied_group_additivity"
            ),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_applied_group_additivity_component")
        ),
        sa.CheckConstraint(
            "count >= 1",
            name=op.f("ck_applied_group_additivity_component_count_ge_1"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("applied_group_additivity_component")
    op.drop_table("applied_group_additivity")
    op.drop_index(
        "uq_group_additivity_scheme_name_version",
        table_name="group_additivity_scheme",
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index(
        op.f("ix_group_additivity_scheme_public_ref"),
        table_name="group_additivity_scheme",
    )
    op.drop_table("group_additivity_scheme")
    _GA_COMPONENT_KIND.drop(op.get_bind(), checkfirst=True)
