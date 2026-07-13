"""add species formula expression index

PR #4 made ``formula=`` search work on ``Species`` by computing Hill-notation
formula on the fly via the RDKit cartridge:

    mol_formula(mol_from_smiles(species.smiles))::text = :formula

(see ``app/services/scientific_read/species.py::_query_matching_species``).
Since ``species`` has no stored ``formula`` column, every ``formula=``
request was a full sequential scan that re-parsed every row's SMILES. This
revision adds a matching expression (functional) index so the planner can
use an Index Scan instead.

Immutability check: ``mol_formula`` and ``mol_from_smiles`` are both
``provolatile = 'i'`` (IMMUTABLE) in this cartridge build (verified via
``SELECT provolatile FROM pg_proc WHERE proname IN ('mol_formula',
'mol_from_smiles')``), so an expression index over them is legal.

Cartridge quirk (why the index expression casts to ``::cstring``): the
cartridge's ``mol_from_smiles(text)`` overload is a thin SQL-language
wrapper around the C-language ``mol_from_smiles(cstring)`` function
(``SELECT mol_from_smiles($1::cstring)``). Plain
``CREATE INDEX ... ((mol_formula(mol_from_smiles(smiles)))::text)`` (no
explicit cast) fails at DDL time with
``function mol_from_smiles(cstring) does not exist`` — apparently a
limitation in how Postgres inlines/const-folds this particular SQL wrapper
during ``CREATE INDEX``'s immutability check, distinct from ordinary query
planning (a plain ``SELECT`` using the same uncast expression works fine).
Casting the column to ``smiles::cstring`` explicitly in the index
definition sidesteps the wrapper and calls the C function directly, which
CREATE INDEX accepts.

This does NOT break the "exact expression match" requirement for the
planner to use the index. Verified via
``EXPLAIN SELECT * FROM species WHERE (mol_formula(mol_from_smiles(smiles)))::text = '...'``
(the literal query-service expression, uncast): once this index exists,
Postgres's own planning-time inlining normalizes
``mol_from_smiles(smiles)`` into the identical
``mol_from_smiles(smiles::cstring)`` form the index is built on, so the
query matches and (with ``enable_seqscan=off`` to force the choice on this
small dev dataset) uses ``Index Scan using ix_species_formula_lookup``
rather than a Seq Scan.

Not declared on the ``Species`` ORM model: unlike the plain-SQL
``ix_literature_doi_normalized``/``ix_literature_isbn_normalized`` indexes
(see ``app/db/models/literature.py``), this index is built on RDKit
cartridge functions with the DDL-time inlining quirk above. Declaring it
via SQLAlchemy's ``Index(..., text(...))`` risks ``alembic revision
--autogenerate`` trying to (re)create it with the uncast form and hitting
the same DDL-time error, or flagging spurious drift. This follows the same
migration-only precedent as ``ix_species_entry_mol_gist``
(``d4e5f6a7b8c9_add_species_entry_mol_gist_index.py``) — a future
``--autogenerate`` run may surface this index as "missing from metadata";
that is expected and should not be auto-dropped.

``species`` is a deployed table (DR-000x / migration-rules.md), so this is
a new revision rather than an edit to ``d861dfd60891``. The table currently
holds a small number of rows, so a plain blocking ``CREATE INDEX`` inside
the migration transaction is fine. For a larger deployed DB, use
``CREATE INDEX CONCURRENTLY`` instead (which cannot run inside a
transaction — would need ``autocommit_block()``/non-transactional DDL); see
``backend/docs/deployment/migrations.md``.

Revision ID: 94daa2c345fb
Revises: a3f1c7e9b2d5
Create Date: 2026-07-13 15:14:20.634134

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '94daa2c345fb'
down_revision: Union[str, Sequence[str], None] = 'a3f1c7e9b2d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX_NAME = "ix_species_formula_lookup"


def upgrade() -> None:
    # Expression index matching the read-service's formula lookup:
    #   mol_formula(mol_from_smiles(species.smiles))::text = :formula
    # The explicit ``smiles::cstring`` cast avoids a CREATE-INDEX-time
    # inlining failure in the cartridge's ``mol_from_smiles(text)`` SQL
    # wrapper (see revision docstring); Postgres's query-time planning
    # normalizes the uncast query expression to this same form, so the
    # index is still used by the service's un-cast query.
    op.execute(
        f"""
        CREATE INDEX {_INDEX_NAME}
        ON species (((mol_formula(mol_from_smiles(smiles::cstring)))::text));
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME};")
