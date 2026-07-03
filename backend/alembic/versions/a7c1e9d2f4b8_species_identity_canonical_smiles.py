"""species identity on canonical smiles + charge + multiplicity

Swaps species deduplication from the InChIKey-only unique constraint to a
``(smiles, charge, multiplicity)`` unique constraint, and demotes
``inchi_key`` to a non-unique index. This lets spin states (singlet vs
triplet CH2, O2 states) and standard-InChIKey-merged tautomers be
represented as distinct species. See DR-0031.

Data posture (per plan.md locked decision): dev/lab data only, no
long-lived production species data, so this swaps the constraint without a
recompute/backfill. The ``smiles`` column already holds RDKit canonical
SMILES (written by the resolution layer), so the new unique key is
well-formed on existing rows. Should any pre-existing row pair violate the
new constraint (same canonical SMILES + charge + multiplicity under two
InChIKeys — not expected), the ADD CONSTRAINT will fail loudly, which is
the intended signal on dev data.

Revision ID: a7c1e9d2f4b8
Revises: 5eaf03c94f9b
Create Date: 2026-07-02 06:05:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c1e9d2f4b8'
down_revision: Union[str, Sequence[str], None] = '5eaf03c94f9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop the old InChIKey-only unique constraint (also removes its
    # implicit unique index).
    op.drop_constraint('uq_species_inchi_key', 'species', type_='unique')

    # New identity key.
    op.create_unique_constraint(
        'uq_species_identity', 'species', ['smiles', 'charge', 'multiplicity']
    )

    # Keep inchi_key fast to look up, but non-unique now.
    op.create_index('ix_species_inchi_key', 'species', ['inchi_key'])


def downgrade() -> None:
    """Downgrade schema.

    Reverses to the InChIKey-only unique constraint. This can fail if
    spin-state or tautomer siblings that share an InChIKey were created
    while the new identity was in force — that is the correct behaviour
    (the old model genuinely cannot represent them).
    """
    op.drop_index('ix_species_inchi_key', table_name='species')
    op.drop_constraint('uq_species_identity', 'species', type_='unique')
    op.create_unique_constraint('uq_species_inchi_key', 'species', ['inchi_key'])
