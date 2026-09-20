"""add observation uncertainty state and source custody

Phase C-E1 (docs/research/tckdb-phase-c-implementation-plan.md, "C2 --
observation, state, uncertainty and source-custody contracts"). Gives
``molecular_property_observation`` what it needs to honestly hold an
experimental ideal-gas heat-capacity point -- a ``heat_capacity_cp``
property kind fixed to unit ``J/mol/K``, a pressure and a state basis, a
*typed* uncertainty (kind, coverage factor, confidence level, assessor)
instead of a bare scalar, and a link to a new source-custody chain
(``external_source`` / ``external_source_record``) that snapshots the
fetched document an importer derived the row from.

``molecular_property_observation`` is an already-deployed table (holds
live CCCBDB rows), so per the migration rules this is a new revision, not
an edit to ``a1b2c3d4e5f6``/``4869f598737d``. Every new column is
NULLABLE and every new CHECK is written so it holds vacuously for a row
that predates this revision -- no backfill, existing CCCBDB rows
untouched (see the CHECK-scoping note in ``upgrade()`` below and
``backend/docs/specs/cccbdb_importer.md`` §7 Gap 4, updated alongside
this revision).

Enum-value additions and the same-transaction restriction
-----------------------------------------------------------
Two enum VALUES are added to EXISTING PostgreSQL enum types:
``molecular_property_kind`` gains ``heat_capacity_cp`` and
``submission_record_type`` gains ``molecular_property_observation``.
PostgreSQL forbids *using* a value added by ``ALTER TYPE ... ADD VALUE``
within the same transaction that added it -- referencing it in a CHECK
constraint, an INSERT, or any other expression raises "unsafe use of new
value of enum type". This revision's CHECK constraints on
``heat_capacity_cp`` (below) need the value to already be committed, so
that one ``ALTER TYPE`` runs inside ``op.get_context().autocommit_block()``
-- Alembic's documented escape hatch for DDL that cannot share a
transaction with what depends on it (the same tool this repo already
uses for ``CREATE INDEX CONCURRENTLY``-shaped problems; see
``94daa2c345fb``'s docstring). ``molecular_property_observation`` is not
referenced by any DDL in this revision, so it runs in the ordinary
transaction, exactly like ``'artifact'`` did in ``e1f2a3b4c5d6``.

Trade-off, stated once: because the autocommit block commits on its own,
a failure later in this migration leaves ``heat_capacity_cp`` present in
``molecular_property_kind`` even though the rest of the upgrade rolled
back. That is PostgreSQL's restriction, not a choice made here -- there
is no way to add and use an enum value atomically in one transaction.

Four brand-new enum TYPES (``observed_uncertainty_kind``,
``observed_uncertainty_assessor``, ``observed_state_basis``,
``external_source_record_kind``) have no such restriction: a freshly
created type's values are usable immediately in the same transaction
(see ``b3e7d1f9a2c4``'s ``kinetics_direction`` for the same pattern in
this codebase), so they are created and used inline below without an
autocommit block.

Downgrade asymmetry
--------------------
The downgrade drops every new column, both new tables and all four new
enum TYPES -- all real, reversible DDL. It does **not** attempt to remove
``heat_capacity_cp`` from ``molecular_property_kind`` or
``molecular_property_observation`` from ``submission_record_type``:
PostgreSQL cannot drop a single enum value without rebuilding the whole
type, and any row already created with either value would be left
invalid. This is the same safe, standard asymmetry ``5eaf03c94f9b`` uses
for ``artifact_kind``'s ``hessian`` value.

Revision ID: 0b4a3afabfd3
Revises: 9b1c7e2d4a68
Create Date: 2026-09-19 23:46:02.571339

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0b4a3afabfd3'
down_revision: Union[str, Sequence[str], None] = '9b1c7e2d4a68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False on all four: created/dropped explicitly below (via
# .create()/.drop()) rather than implicitly by op.add_column()/
# op.create_table(), matching the calc_hessian/hessian_source precedent
# (5eaf03c94f9b) so create_table() never races the explicit create.
observed_uncertainty_kind = postgresql.ENUM(
    'standard', 'expanded', 'combined_standard', 'combined_expanded',
    name='observed_uncertainty_kind',
    create_type=False,
)
observed_uncertainty_assessor = postgresql.ENUM(
    'source_author', 'source_evaluator',
    name='observed_uncertainty_assessor',
    create_type=False,
)
observed_state_basis = postgresql.ENUM(
    'ideal_gas', 'real_gas',
    name='observed_state_basis',
    create_type=False,
)
external_source_record_kind = postgresql.ENUM(
    'thermoml_article', 'cccbdb_page',
    name='external_source_record_kind',
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema."""

    # ------------------------------------------------------------------
    # 1. Enum VALUES on existing types. See module docstring.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE molecular_property_kind ADD VALUE IF NOT EXISTS "
            "'heat_capacity_cp'"
        )
    op.execute(
        "ALTER TYPE submission_record_type ADD VALUE IF NOT EXISTS "
        "'molecular_property_observation'"
    )

    # ------------------------------------------------------------------
    # 2. Brand-new enum TYPES.
    observed_uncertainty_kind.create(op.get_bind(), checkfirst=True)
    observed_uncertainty_assessor.create(op.get_bind(), checkfirst=True)
    observed_state_basis.create(op.get_bind(), checkfirst=True)
    external_source_record_kind.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 3. Source custody: the database/collection a document came from,
    # and one immutable snapshot of one fetched document.
    op.create_table(
        'external_source',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column('source_name', sa.Text(), nullable=False),
        sa.Column('source_release', sa.Text(), nullable=False),
        sa.Column('source_database_doi', sa.Text(), nullable=True),
        sa.Column('citation_text', sa.Text(), nullable=True),
        sa.Column('terms_url', sa.Text(), nullable=True),
        sa.Column('terms_text', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_external_source')),
        sa.UniqueConstraint(
            'source_name', 'source_release',
            name='uq_external_source_name_release',
        ),
    )

    op.create_table(
        'external_source_record',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column('external_source_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'record_kind',
            postgresql.ENUM(
                'thermoml_article', 'cccbdb_page',
                name='external_source_record_kind', create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('source_uri', sa.Text(), nullable=False),
        sa.Column('source_record_key', sa.Text(), nullable=False),
        sa.Column('retrieved_at', sa.DateTime(), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('content_sha256', sa.Text(), nullable=False),
        sa.Column('content_length', sa.BigInteger(), nullable=False),
        sa.Column('raw_uri', sa.Text(), nullable=False),
        sa.Column('container_digest', sa.Text(), nullable=True),
        sa.Column('schema_id', sa.Text(), nullable=True),
        sa.Column('schema_valid', sa.Boolean(), nullable=True),
        sa.Column('parser_name', sa.Text(), nullable=False),
        sa.Column('parser_version', sa.Text(), nullable=False),
        sa.Column('mapping_version', sa.Text(), nullable=False),
        sa.Column('mapping_report_json', postgresql.JSONB(), nullable=True),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f('ck_external_source_record_content_sha256_hex'),
        ),
        sa.CheckConstraint(
            'content_length >= 0',
            name=op.f('ck_external_source_record_content_length_ge_0'),
        ),
        sa.CheckConstraint(
            'http_status IS NULL OR http_status BETWEEN 100 AND 599',
            name=op.f('ck_external_source_record_http_status_range'),
        ),
        sa.ForeignKeyConstraint(
            ['external_source_id'], ['external_source.id'],
            name=op.f('fk_external_source_record_external_source_id'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.PrimaryKeyConstraint(
            'id', name=op.f('pk_external_source_record'),
        ),
        sa.UniqueConstraint(
            'external_source_id', 'source_record_key', 'content_sha256',
            'parser_version', 'mapping_version',
            name='uq_external_source_record_identity',
        ),
    )

    # ------------------------------------------------------------------
    # 4. New columns on molecular_property_observation. All nullable --
    # no backfill for existing (CCCBDB) rows.
    op.add_column(
        'molecular_property_observation',
        sa.Column('pressure_bar', sa.Double(), nullable=True),
    )
    op.add_column(
        'molecular_property_observation',
        sa.Column(
            'state_basis',
            postgresql.ENUM(
                'ideal_gas', 'real_gas',
                name='observed_state_basis', create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        'molecular_property_observation',
        sa.Column(
            'uncertainty_kind',
            postgresql.ENUM(
                'standard', 'expanded', 'combined_standard',
                'combined_expanded',
                name='observed_uncertainty_kind', create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        'molecular_property_observation',
        sa.Column('uncertainty_coverage_factor', sa.Double(), nullable=True),
    )
    op.add_column(
        'molecular_property_observation',
        sa.Column(
            'uncertainty_level_of_confidence_pct', sa.Double(),
            nullable=True,
        ),
    )
    op.add_column(
        'molecular_property_observation',
        sa.Column(
            'uncertainty_assessor',
            postgresql.ENUM(
                'source_author', 'source_evaluator',
                name='observed_uncertainty_assessor', create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        'molecular_property_observation',
        sa.Column('external_source_record_id', sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        op.f(
            'fk_molecular_property_observation_external_source_record_id'
        ),
        'molecular_property_observation', 'external_source_record',
        ['external_source_record_id'], ['id'],
        deferrable=True, initially='IMMEDIATE',
    )

    # ------------------------------------------------------------------
    # 5. New CHECK constraints.
    #
    # The three referencing 'heat_capacity_cp' run only after step 1's
    # autocommit block committed that value, so they may reference it
    # safely.
    #
    # ``ck_mpo_uncertainty_kind_iff_value`` is scoped to
    # ``property_kind = 'heat_capacity_cp'`` rather than every row: the
    # existing (and still-running) CCCBDB importer
    # (app/importers/cccbdb/form_payload_builder.py) sets
    # ``scalar_uncertainty`` with no uncertainty kind -- CCCBDB carries no
    # per-value uncertainty-kind concept -- so a global iff-CHECK would
    # reject both the live archive's existing rows and every future
    # CCCBDB import. Scoping it to the new Cp contract keeps the
    # invariant meaningful exactly where it is new. Every CHECK below
    # holds vacuously for a pre-existing row, because no row could have
    # ``property_kind = 'heat_capacity_cp'`` before this revision added
    # that value.
    op.create_check_constraint(
        op.f('ck_mpo_pressure_bar_gt_0'), 'molecular_property_observation',
        'pressure_bar IS NULL OR pressure_bar > 0',
    )
    op.create_check_constraint(
        op.f('ck_mpo_uncertainty_coverage_factor_ge_1'),
        'molecular_property_observation',
        'uncertainty_coverage_factor IS NULL OR uncertainty_coverage_factor >= 1',
    )
    op.create_check_constraint(
        op.f('ck_mpo_uncertainty_confidence_pct_range'),
        'molecular_property_observation',
        'uncertainty_level_of_confidence_pct IS NULL '
        'OR (uncertainty_level_of_confidence_pct > 0 '
        'AND uncertainty_level_of_confidence_pct <= 100)',
    )
    op.create_check_constraint(
        op.f('ck_mpo_heat_capacity_cp_unit_j_mol_k'),
        'molecular_property_observation',
        "property_kind <> 'heat_capacity_cp' OR scalar_unit = 'J/mol/K'",
    )
    op.create_check_constraint(
        op.f('ck_mpo_heat_capacity_cp_requires_temperature'),
        'molecular_property_observation',
        "property_kind <> 'heat_capacity_cp' OR temperature_k IS NOT NULL",
    )
    op.create_check_constraint(
        op.f('ck_mpo_uncertainty_kind_iff_value'),
        'molecular_property_observation',
        "property_kind <> 'heat_capacity_cp' "
        "OR (scalar_uncertainty IS NULL) = (uncertainty_kind IS NULL)",
    )
    op.create_check_constraint(
        op.f('ck_mpo_coverage_factor_only_expanded'),
        'molecular_property_observation',
        "uncertainty_coverage_factor IS NULL "
        "OR uncertainty_kind IN ('expanded', 'combined_expanded')",
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops every new column, both new tables, and all four new enum
    TYPES. Does NOT remove 'heat_capacity_cp' from
    ``molecular_property_kind`` or 'molecular_property_observation' from
    ``submission_record_type`` -- see the module docstring's "Downgrade
    asymmetry" section.
    """
    op.drop_constraint(
        op.f('ck_mpo_coverage_factor_only_expanded'),
        'molecular_property_observation', type_='check',
    )
    op.drop_constraint(
        op.f('ck_mpo_uncertainty_kind_iff_value'),
        'molecular_property_observation', type_='check',
    )
    op.drop_constraint(
        op.f('ck_mpo_heat_capacity_cp_requires_temperature'),
        'molecular_property_observation', type_='check',
    )
    op.drop_constraint(
        op.f('ck_mpo_heat_capacity_cp_unit_j_mol_k'),
        'molecular_property_observation', type_='check',
    )
    op.drop_constraint(
        op.f('ck_mpo_uncertainty_confidence_pct_range'),
        'molecular_property_observation', type_='check',
    )
    op.drop_constraint(
        op.f('ck_mpo_uncertainty_coverage_factor_ge_1'),
        'molecular_property_observation', type_='check',
    )
    op.drop_constraint(
        op.f('ck_mpo_pressure_bar_gt_0'),
        'molecular_property_observation', type_='check',
    )

    op.drop_constraint(
        op.f(
            'fk_molecular_property_observation_external_source_record_id'
        ),
        'molecular_property_observation', type_='foreignkey',
    )
    op.drop_column('molecular_property_observation', 'external_source_record_id')
    op.drop_column('molecular_property_observation', 'uncertainty_assessor')
    op.drop_column(
        'molecular_property_observation', 'uncertainty_level_of_confidence_pct'
    )
    op.drop_column(
        'molecular_property_observation', 'uncertainty_coverage_factor'
    )
    op.drop_column('molecular_property_observation', 'uncertainty_kind')
    op.drop_column('molecular_property_observation', 'state_basis')
    op.drop_column('molecular_property_observation', 'pressure_bar')

    op.drop_table('external_source_record')
    op.drop_table('external_source')

    external_source_record_kind.drop(op.get_bind(), checkfirst=True)
    observed_state_basis.drop(op.get_bind(), checkfirst=True)
    observed_uncertainty_assessor.drop(op.get_bind(), checkfirst=True)
    observed_uncertainty_kind.drop(op.get_bind(), checkfirst=True)
