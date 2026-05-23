"""backfill species_entry.mol and add GiST index for structure search

Audit P1-3: scientific structure search currently computes
``mol_from_smiles(species.smiles)`` inline for every species_entry on
every request. That works for small catalogs but is O(N) and cannot
benefit from the PostgreSQL RDKit cartridge's GiST index on stored
``mol`` columns.

The ``species_entry.mol`` column has existed in the schema since the
initial migration (created via the custom ``RDKitMol`` SQLAlchemy
type in ``app/db/types.py``). The write path
(``app/services/species_resolution.py``) populates it from a
canonicalized SMILES on insert. What was missing was:

1. A backfill for any row whose ``mol`` is NULL — e.g. rows written
   before the populating-on-insert behavior, or imported via paths
   that bypassed the resolver. Backfill uses the cartridge's
   ``mol_from_smiles`` against the canonical ``species.smiles`` via
   a join update; rows where the cartridge cannot parse the SMILES
   stay NULL and the structure-search service excludes them.
2. A GiST index on ``species_entry.mol`` so substructure (``@>``)
   and similarity (``tanimoto_sml(morganbv_fp(...), ...)``) queries
   can index-scan instead of seq-scan.

The structure-search service is updated in the same change to read
from ``se.mol`` directly so the new index actually gets used.

This is a normal blocking ``CREATE INDEX`` build inside the Alembic
transaction. For a small / self-hosted / single-node deployment the
build is essentially instant. For a larger deployed DB, schedule the
upgrade during a low-traffic window — see
``backend/docs/deployment/migrations.md``.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-23
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GIST_INDEX_NAME = "ix_species_entry_mol_gist"


def upgrade() -> None:
    # Backfill: rows whose mol is NULL but whose parent species has a
    # canonical SMILES. ``mol_from_smiles`` returns NULL on parse
    # failure (cartridge behavior); those rows stay NULL and the
    # structure-search service treats them as un-searchable, which
    # matches the v0 contract.
    op.execute(
        """
        UPDATE species_entry AS se
        SET mol = mol_from_smiles(sp.smiles)
        FROM species AS sp
        WHERE se.species_id = sp.id
          AND se.mol IS NULL
          AND sp.smiles IS NOT NULL
          AND mol_from_smiles(sp.smiles) IS NOT NULL;
        """
    )

    # GiST index. The cartridge's ``mol`` type uses its own GiST
    # opclass automatically; no explicit ``USING gist (mol opclass)``
    # is needed.
    op.execute(
        f"CREATE INDEX {_GIST_INDEX_NAME} ON species_entry USING gist (mol);"
    )


def downgrade() -> None:
    # Drop only the index. The ``mol`` column predates this migration
    # (created in d861dfd60891) and is not owned by this revision.
    op.execute(f"DROP INDEX IF EXISTS {_GIST_INDEX_NAME};")
