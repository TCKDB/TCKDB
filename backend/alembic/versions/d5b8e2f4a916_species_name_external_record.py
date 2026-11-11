"""species_name external record key and retrieval time

Adds ``external_record_key`` (the record id at the external source, e.g. a PubChem CID as text) and
``retrieved_at`` (when it was fetched) to ``species_name``, and two CHECK constraints: an ``offline_import`` name
carries its source, record key and retrieval time; a ``curated`` name carries no record key or retrieval time.
``species_name`` holds no rows on any deployed database, so no backfill is needed.

Revision ID: d5b8e2f4a916
Revises: c3a9e5d17b24
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d5b8e2f4a916"
down_revision: Union[str, Sequence[str], None] = "c3a9e5d17b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("species_name", sa.Column("external_record_key", sa.Text(), nullable=True))
    op.add_column("species_name", sa.Column("retrieved_at", sa.DateTime(timezone=False), nullable=True))
    op.create_check_constraint(
        op.f("ck_species_name_offline_import_has_source"),
        "species_name",
        "source_kind <> 'offline_import' OR (external_source_id IS NOT NULL AND external_record_key IS NOT NULL "
        "AND retrieved_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_species_name_curated_has_no_record"),
        "species_name",
        "source_kind <> 'curated' OR (external_record_key IS NULL AND retrieved_at IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_species_name_curated_has_no_record"), "species_name", type_="check")
    op.drop_constraint(op.f("ck_species_name_offline_import_has_source"), "species_name", type_="check")
    op.drop_column("species_name", "retrieved_at")
    op.drop_column("species_name", "external_record_key")
