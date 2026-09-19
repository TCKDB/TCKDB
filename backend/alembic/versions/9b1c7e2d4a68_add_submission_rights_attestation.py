"""add submission_rights_attestation and the manifest rights summary

Phase B, work package B1. Before this revision the only rights field in the
schema was the per-release ``dataset_release.data_license`` string, and a
release could ship records nobody had agreed to license: the string says what
terms the operator applies, not who consented. ``LICENSE-DATA`` states that
an operator can license their own deposits and nobody else's, and that a
default is not consent. This revision gives that sentence a table.

Created here:
  - enum ``rights_basis_kind`` (``depositor_agreement``,
    ``operator_own_data``, ``historical_review``, ``source_terms``)
  - table ``submission_rights_attestation`` -- append-only, keyed to the
    submission (curation of a deposit), never to a scientific row
  - trigger ``trg_submission_rights_attestation_immutable`` (+ ``_truncate``)
    and function ``reject_submission_rights_attestation_mutation``, refusing
    UPDATE / DELETE / TRUNCATE -- the ``release_manifest`` pattern
  - column ``release_manifest.rights_summary_json`` (JSONB, nullable): the
    ``rights`` block of a ``tckdb.dataset_release.v2`` manifest document.
    NULL on every manifest frozen under ``v1``; the renderer branches on the
    stored ``manifest_schema`` so those rows keep reproducing their digest.

Reused (NOT created here, ``create_type=False``):
  - enum ``submission_actor_kind``

No backfill. Historical coverage is a curator action with an actor, recorded
through ``POST /api/v1/submissions/{id}/rights-attestations``, never a
migration that writes consent nobody gave. A deployed database is
unreleasable for every record until somebody attests -- that is the intended
sensitivity, not a regression.

Additive migration under the phase-aware policy: a new revision on top of the
deployed chain. ``release_manifest`` is an already-deployed table and gains a
nullable column only; ``ALTER TABLE`` is DDL and is not stopped by its
row-level immutability trigger.

Revision ID: 9b1c7e2d4a68
Revises: a55cc983501a
Create Date: 2026-09-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9b1c7e2d4a68"
down_revision: Union[str, Sequence[str], None] = "a55cc983501a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BASIS_VALUES = (
    "depositor_agreement",
    "operator_own_data",
    "historical_review",
    "source_terms",
)

_TABLE = "submission_rights_attestation"
_TRIGGER = "trg_submission_rights_attestation_immutable"
_FUNCTION = "reject_submission_rights_attestation_mutation"


def upgrade() -> None:
    """Create the attestation table, its immutability guard, and the manifest column."""
    basis_enum = postgresql.ENUM(
        *_BASIS_VALUES, name="rights_basis_kind", create_type=False
    )
    basis_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("license_id", sa.String(length=64), nullable=False),
        sa.Column(
            "basis",
            postgresql.ENUM(*_BASIS_VALUES, name="rights_basis_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("attested_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "actor_kind",
            postgresql.ENUM(name="submission_actor_kind", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "attested_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_terms", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("supersedes_attestation_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(btrim(license_id)) > 0",
            name=op.f("ck_submission_rights_attestation_license_id_nonblank"),
        ),
        sa.CheckConstraint(
            "(basis <> 'source_terms') OR "
            "(source_terms IS NOT NULL AND length(btrim(source_terms)) > 0)",
            name=op.f("ck_submission_rights_attestation_source_terms_required"),
        ),
        sa.ForeignKeyConstraint(
            ["attested_by"],
            ["app_user.id"],
            name=op.f("fk_submission_rights_attestation_attested_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name=op.f("fk_submission_rights_attestation_submission_id_submission"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        # Explicit short name: the convention's spelling is 87 characters and
        # PostgreSQL would truncate it at 63 (see the model).
        sa.ForeignKeyConstraint(
            ["supersedes_attestation_id"],
            ["submission_rights_attestation.id"],
            name="fk_submission_rights_attestation_supersedes",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_rights_attestation")),
        sa.UniqueConstraint(
            "supersedes_attestation_id",
            name="uq_submission_rights_attestation_supersedes_attestation_id",
        ),
    )
    op.create_index(
        op.f("ix_submission_rights_attestation_public_ref"),
        _TABLE,
        ["public_ref"],
        unique=True,
    )
    op.create_index(
        "ix_submission_rights_attestation_submission_id",
        _TABLE,
        ["submission_id"],
        unique=False,
    )

    # Append-only at the database level, exactly as ``release_manifest``: a
    # row-level trigger for UPDATE/DELETE and a statement-level one for
    # TRUNCATE, which a row-level trigger never sees.
    op.execute(
        f"""
        CREATE FUNCTION {_FUNCTION}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = '55000',
                MESSAGE = '{_TABLE} is append-only';
        END;
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE UPDATE OR DELETE ON {_TABLE}
        FOR EACH ROW
        EXECUTE FUNCTION {_FUNCTION}()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}_truncate
        BEFORE TRUNCATE ON {_TABLE}
        FOR EACH STATEMENT
        EXECUTE FUNCTION {_FUNCTION}()
        """
    )

    op.add_column(
        "release_manifest",
        sa.Column(
            "rights_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the manifest column, the guard, the table, then the enum it owns."""
    op.drop_column("release_manifest", "rights_summary_json")

    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER}_truncate ON {_TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_FUNCTION}()")

    op.drop_index("ix_submission_rights_attestation_submission_id", table_name=_TABLE)
    op.drop_index(op.f("ix_submission_rights_attestation_public_ref"), table_name=_TABLE)
    op.drop_table(_TABLE)

    postgresql.ENUM(name="rights_basis_kind").drop(op.get_bind(), checkfirst=True)
