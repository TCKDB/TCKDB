"""Create initial schema.

Revision ID: d861dfd60891
Revises: 60b67e360daf
Create Date: 2026-03-07 20:04:50.330495

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import app.db.types
from app.schemas.reaction_family import CANONICAL_REACTION_FAMILIES
from app.services.public_refs import make_content_ref

# revision identifiers, used by Alembic.
revision: str = "d861dfd60891"
down_revision: Union[str, Sequence[str], None] = "60b67e360daf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _seed_reaction_families() -> None:
    """Insert canonical reaction family names."""
    reaction_family_table = sa.table(
        "reaction_family",
        sa.column("name", sa.Text()),
    )
    op.bulk_insert(
        reaction_family_table,
        [{"name": name} for name in sorted(CANONICAL_REACTION_FAMILIES)],
    )


def _seed_calculation_parameter_vocab() -> None:
    """Insert Phase 1 canonical parameter keys.

    Dotted namespaces (``scf.convergence``, ``parallel.nproc``, ...) make
    the vocabulary read as paths rather than Python identifiers and let
    related settings group visibly under a shared prefix. The Gaussian
    and ORCA parser ``_CANONICAL_MAP`` tables emit these same dotted
    keys so parsed canonical_keys link through the FK instead of being
    silently demoted to NULL.
    """
    vocab_table = sa.table(
        "calculation_parameter_vocab",
        sa.column("canonical_key", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("expected_value_type", sa.Text()),
        sa.column("affects_scientific_result", sa.Boolean()),
        sa.column("affects_numerics", sa.Boolean()),
        sa.column("affects_resources", sa.Boolean()),
        sa.column("note", sa.Text()),
    )
    rows = [
        # SCF block
        {"canonical_key": "scf.convergence", "description": "SCF energy/density convergence target.", "expected_value_type": "enum", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": None},
        {"canonical_key": "scf.max_cycles", "description": "Maximum SCF iterations before fallback.", "expected_value_type": "int", "affects_scientific_result": False, "affects_numerics": True, "affects_resources": False, "note": None},
        {"canonical_key": "scf.direct", "description": "SCF integral handling: direct/incore.", "expected_value_type": "enum", "affects_scientific_result": False, "affects_numerics": True, "affects_resources": False, "note": None},
        {"canonical_key": "scf.fallback", "description": "SCF fallback algorithm (e.g. xqc, qc).", "expected_value_type": "enum", "affects_scientific_result": False, "affects_numerics": True, "affects_resources": False, "note": None},
        {"canonical_key": "scf.convergence_failure_ignored", "description": "Producer configured the SCF to continue if convergence fails. Reported energy/geometry comes from a non-converged wavefunction.", "expected_value_type": "bool", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": "Triggered by Gaussian IOp(5/13=1). This is a calculation trust flag, not SCF wavefunction stability evidence -- it should not populate calc_scf_stability."},
        {"canonical_key": "scf.convergence_failure_action", "description": "Action the producer instructed for SCF convergence failure (e.g. continue, fallback).", "expected_value_type": "enum", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": "Co-emitted with scf.convergence_failure_ignored from Gaussian IOp(5/13=1)."},
        # Optimisation block
        {"canonical_key": "opt.convergence", "description": "Geometry optimisation convergence target.", "expected_value_type": "enum", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": None},
        {"canonical_key": "opt.max_cycles", "description": "Maximum optimisation steps.", "expected_value_type": "int", "affects_scientific_result": False, "affects_numerics": True, "affects_resources": False, "note": None},
        {"canonical_key": "opt.max_step", "description": "Maximum trust-radius / step size.", "expected_value_type": "float", "affects_scientific_result": False, "affects_numerics": True, "affects_resources": False, "note": None},
        {"canonical_key": "opt.initial_hessian", "description": "Initial Hessian source: calcfc / calcall / readfc.", "expected_value_type": "enum", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": "calcall changes converged path, not just speed."},
        {"canonical_key": "opt.saddle_order", "description": "Saddle-point order for transition-state search.", "expected_value_type": "int", "affects_scientific_result": True, "affects_numerics": False, "affects_resources": False, "note": None},
        {"canonical_key": "opt.eigen_test", "description": "Eigenvalue test toggle for opt steps.", "expected_value_type": "enum", "affects_scientific_result": False, "affects_numerics": True, "affects_resources": False, "note": None},
        # Numerics
        {"canonical_key": "grid.quality", "description": "DFT integration grid quality.", "expected_value_type": "string", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": True, "note": None},
        {"canonical_key": "integral.accuracy", "description": "Two-electron integral accuracy threshold.", "expected_value_type": "int", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": True, "note": None},
        {"canonical_key": "guess.strategy", "description": "Initial-guess strategy: harris/mix/read/INDO/etc.", "expected_value_type": "string", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": "Affects converged electronic state and convergence success."},
        {"canonical_key": "symmetry.disabled", "description": "Disables use of molecular symmetry in the electronic-structure calculation.", "expected_value_type": "bool", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": None},
        # PNO (post-HF)
        {"canonical_key": "pno.truncation", "description": "PNO truncation level for DLPNO methods.", "expected_value_type": "enum", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": True, "note": None},
        # Bookkeeping
        {"canonical_key": "output.verbosity", "description": "Output verbosity tier.", "expected_value_type": "enum", "affects_scientific_result": False, "affects_numerics": False, "affects_resources": False, "note": None},
        {"canonical_key": "internal_option.iop", "description": "Gaussian IOp(overlay/option=value) internal option. Raw key carries the overlay/option coordinate; raw value carries the assigned value.", "expected_value_type": "string", "affects_scientific_result": True, "affects_numerics": True, "affects_resources": False, "note": "Generic catch-all for IOp(...) directives. Behavior depends on the specific overlay/option."},
        # Resources
        {"canonical_key": "parallel.nproc", "description": "Total processor count requested.", "expected_value_type": "int", "affects_scientific_result": False, "affects_numerics": False, "affects_resources": True, "note": None},
        {"canonical_key": "parallel.nproc_shared", "description": "Shared-memory processor count.", "expected_value_type": "int", "affects_scientific_result": False, "affects_numerics": False, "affects_resources": True, "note": None},
        {"canonical_key": "memory.raw", "description": "Memory request as software-emitted string (units in row).", "expected_value_type": "string", "affects_scientific_result": False, "affects_numerics": False, "affects_resources": True, "note": None},
        {"canonical_key": "memory.maxcore_mb", "description": "Per-process memory cap in megabytes (ORCA %maxcore).", "expected_value_type": "int", "affects_scientific_result": False, "affects_numerics": False, "affects_resources": True, "note": None},
    ]
    op.bulk_insert(vocab_table, rows)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('app_user',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.Text(), nullable=False),
    sa.Column('email', sa.Text(), nullable=True),
    sa.Column('full_name', sa.Text(), nullable=True),
    sa.Column('affiliation', sa.Text(), nullable=True),
    sa.Column('orcid', sa.CHAR(length=19), nullable=True),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('role', sa.Enum('user', 'curator', 'admin', name='app_user_role'), server_default='user', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_app_user')),
    sa.UniqueConstraint('email', name=op.f('uq_app_user_email')),
    sa.UniqueConstraint('orcid', name=op.f('uq_app_user_orcid')),
    sa.UniqueConstraint('username', name=op.f('uq_app_user_username'))
    )
    op.create_table('api_key',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('key_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('label', sa.Text(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], name=op.f('fk_api_key_user_id_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_api_key')),
    sa.UniqueConstraint('key_hash', name=op.f('uq_api_key_key_hash'))
    )
    op.create_table('user_session',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], name=op.f('fk_user_session_user_id_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_session')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_user_session_token_hash'))
    )
    op.create_table('idempotency_record',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('request_method', sa.String(length=8), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=200), nullable=False),
    sa.Column('payload_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=False),
    sa.Column('response_body_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], name=op.f('fk_idempotency_record_user_id_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_idempotency_record')),
    sa.UniqueConstraint('user_id', 'request_method', 'endpoint', 'idempotency_key', name='uq_idempotency_record_user_method_endpoint_key')
    )
    op.create_index('ix_idempotency_record_expires_at', 'idempotency_record', ['expires_at'], unique=False)
    op.create_table('author',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('given_name', sa.Text(), nullable=True),
    sa.Column('family_name', sa.Text(), nullable=False),
    sa.Column('full_name', sa.Text(), nullable=False),
    sa.Column('orcid', sa.CHAR(length=19), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_author')),
    sa.UniqueConstraint('orcid', name=op.f('uq_author_orcid'))
    )
    op.create_table('geometry',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('natoms', sa.Integer(), nullable=False),
    sa.Column('geom_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('xyz_text', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('natoms >= 1', name=op.f('ck_geometry_natoms_ge_1')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_geometry')),
    sa.UniqueConstraint('geom_hash', name=op.f('uq_geometry_geom_hash'))
    )
    op.create_table('level_of_theory',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('method', sa.Text(), nullable=False),
    sa.Column('basis', sa.Text(), nullable=True),
    sa.Column('aux_basis', sa.Text(), nullable=True),
    sa.Column('cabs_basis', sa.Text(), nullable=True),
    sa.Column('dispersion', sa.Text(), nullable=True),
    sa.Column('solvent', sa.Text(), nullable=True),
    sa.Column('solvent_model', sa.Text(), nullable=True),
    sa.Column('keywords', sa.Text(), nullable=True),
    sa.Column('lot_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_level_of_theory')),
    sa.UniqueConstraint('lot_hash', name=op.f('uq_level_of_theory_lot_hash'))
    )
    op.create_table('calculation_parameter_vocab',
    sa.Column('canonical_key', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('expected_value_type', sa.Text(), nullable=True),
    sa.Column('affects_scientific_result', sa.Boolean(), nullable=True),
    sa.Column('affects_numerics', sa.Boolean(), nullable=True),
    sa.Column('affects_resources', sa.Boolean(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('canonical_key', name=op.f('pk_calculation_parameter_vocab'))
    )
    op.create_table('literature',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('article', 'book', 'thesis', 'report', 'dataset', 'webpage', name='literature_kind'), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('journal', sa.Text(), nullable=True),
    sa.Column('year', sa.Integer(), nullable=True),
    sa.Column('volume', sa.Text(), nullable=True),
    sa.Column('issue', sa.Text(), nullable=True),
    sa.Column('pages', sa.Text(), nullable=True),
    sa.Column('doi', sa.Text(), nullable=True),
    sa.Column('isbn', sa.Text(), nullable=True),
    sa.Column('url', sa.Text(), nullable=True),
    sa.Column('publisher', sa.Text(), nullable=True),
    sa.Column('institution', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_literature'))
    )
    op.execute("""
        CREATE INDEX ix_literature_doi_normalized
        ON literature (
            lower(regexp_replace(doi, '^https?://(dx\\.)?doi\\.org/', ''))
        )
    """)
    op.execute("""
        CREATE INDEX ix_literature_isbn_normalized
        ON literature (
            regexp_replace(isbn, '[- ]', '', 'g')
        )
    """)
    op.create_table('reaction_family',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reaction_family')),
    sa.UniqueConstraint('name', name=op.f('uq_reaction_family_name'))
    )
    op.create_table('software',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('website', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_software')),
    sa.UniqueConstraint('name', name=op.f('uq_software_name'))
    )
    op.create_table('species',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('molecule', 'pseudo', name='molecule_kind'), nullable=False),
    sa.Column('smiles', sa.Text(), nullable=False),
    sa.Column('inchi_key', sa.CHAR(length=27), nullable=False),
    sa.Column('charge', sa.SmallInteger(), nullable=False),
    sa.Column('multiplicity', sa.SmallInteger(), nullable=False),
    sa.Column('stereo_kind', sa.Enum('unspecified', 'achiral', 'enantiomer', 'diastereomer', 'ez_isomer', name='stereo_kind'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('multiplicity >= 1', name=op.f('ck_species_multiplicity_ge_1')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_species')),
    sa.UniqueConstraint('inchi_key', name=op.f('uq_species_inchi_key'))
    )
    op.create_table('workflow_tool',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_tool')),
    sa.UniqueConstraint('name', name=op.f('uq_workflow_tool_name'))
    )
    op.create_table('chem_reaction',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('stoichiometry_hash', sa.CHAR(length=64), nullable=True),
    sa.Column('reversible', sa.Boolean(), nullable=False),
    sa.Column('reaction_family_id', sa.BigInteger(), nullable=True),
    sa.Column('reaction_family_raw', sa.Text(), nullable=True),
    sa.Column('reaction_family_source_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('reaction_family_raw IS NULL OR reaction_family_source_note IS NOT NULL', name=op.f('ck_chem_reaction_reaction_family_raw_requires_source_note')),
    sa.ForeignKeyConstraint(['reaction_family_id'], ['reaction_family.id'], name=op.f('fk_chem_reaction_reaction_family_id_reaction_family'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chem_reaction')),
    sa.UniqueConstraint('stoichiometry_hash', name=op.f('uq_chem_reaction_stoichiometry_hash'))
    )
    op.create_table('conformer_assignment_scheme',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('version', sa.String(length=64), nullable=False),
    sa.Column('scope', sa.Enum('canonical', 'imported', 'experimental', 'custom', name='conformer_assignment_scope_kind'), server_default='canonical', nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('parameters_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('code_commit', sa.String(length=64), nullable=True),
    sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_conformer_assignment_scheme_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conformer_assignment_scheme')),
    sa.UniqueConstraint('name', 'version', name='uq_conformer_assignment_scheme_name')
    )
    op.create_table('energy_correction_scheme',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('atom_energy', 'atom_hf', 'atom_thermal', 'soc', 'bac_petersson', 'bac_melius', 'isodesmic', 'other', name='energy_correction_scheme_kind'), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('level_of_theory_id', sa.BigInteger(), nullable=True),
    sa.Column('source_literature_id', sa.BigInteger(), nullable=True),
    sa.Column('version', sa.Text(), nullable=True),
    sa.Column('units', sa.Enum('hartree', 'kj_mol', 'kcal_mol', name='energy_unit'), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_energy_correction_scheme_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['level_of_theory_id'], ['level_of_theory.id'], name=op.f('fk_energy_correction_scheme_level_of_theory_id_level_of_theory'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['source_literature_id'], ['literature.id'], name=op.f('fk_energy_correction_scheme_source_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_energy_correction_scheme'))
    )
    op.create_index('uq_energy_correction_scheme_kind_name_lot_version', 'energy_correction_scheme', ['kind', 'name', 'level_of_theory_id', 'version'], unique=True, postgresql_nulls_not_distinct=True)
    op.create_table('geometry_atom',
    sa.Column('geometry_id', sa.BigInteger(), nullable=False),
    sa.Column('atom_index', sa.Integer(), nullable=False),
    sa.Column('element', sa.CHAR(length=2), nullable=False),
    sa.Column('x', sa.Float(), nullable=False),
    sa.Column('y', sa.Float(), nullable=False),
    sa.Column('z', sa.Float(), nullable=False),
    sa.CheckConstraint('atom_index >= 1', name=op.f('ck_geometry_atom_atom_index_ge_1')),
    sa.ForeignKeyConstraint(['geometry_id'], ['geometry.id'], name=op.f('fk_geometry_atom_geometry_id_geometry')),
    sa.PrimaryKeyConstraint('geometry_id', 'atom_index', name=op.f('pk_geometry_atom'))
    )
    op.create_table('literature_author',
    sa.Column('literature_id', sa.BigInteger(), nullable=False),
    sa.Column('author_id', sa.BigInteger(), nullable=False),
    sa.Column('author_order', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['author.id'], name=op.f('fk_literature_author_author_id_author'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_literature_author_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('literature_id', 'author_id', name=op.f('pk_literature_author')),
    sa.UniqueConstraint('literature_id', 'author_order', name='uq_literature_author_literature_id')
    )
    op.create_table('software_release',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('software_id', sa.BigInteger(), nullable=False),
    sa.Column('version', sa.Text(), nullable=True),
    sa.Column('revision', sa.Text(), nullable=True),
    sa.Column('build', sa.Text(), nullable=True),
    sa.Column('release_date', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['software_id'], ['software.id'], name=op.f('fk_software_release_software_id_software'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_software_release'))
    )
    op.create_index('uq_software_release_software_id', 'software_release', ['software_id', 'version', 'revision', 'build'], unique=True, postgresql_nulls_not_distinct=True)
    op.create_table('species_entry',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('species_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('minimum', 'vdw_complex', name='stationary_point_kind'), server_default='minimum', nullable=False),
    sa.Column('mol', app.db.types.RDKitMol(), nullable=True),
    sa.Column('unmapped_smiles', sa.Text(), nullable=True),
    sa.Column('stereo_label', sa.String(length=64), nullable=True),
    sa.Column('electronic_state_kind', sa.Enum('ground', 'excited', name='species_entry_state_kind'), server_default='ground', nullable=False),
    sa.Column('electronic_state_label', sa.String(length=8), nullable=True),
    sa.Column('term_symbol_raw', sa.String(length=64), nullable=True),
    sa.Column('term_symbol', sa.String(length=64), nullable=True),
    sa.Column('isotopologue_label', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_species_entry_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_id'], ['species.id'], name=op.f('fk_species_entry_species_id_species'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_species_entry')),
    sa.UniqueConstraint('species_id', 'stereo_label', 'electronic_state_kind', 'electronic_state_label', 'term_symbol', 'isotopologue_label', name='uq_species_entry_species_id', postgresql_nulls_not_distinct=True)
    )
    op.create_index('ix_species_entry_species_id', 'species_entry', ['species_id'], unique=False)
    op.create_table('workflow_tool_release',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('workflow_tool_id', sa.BigInteger(), nullable=False),
    sa.Column('version', sa.Text(), nullable=True),
    sa.Column('git_commit', sa.CHAR(length=40), nullable=True),
    sa.Column('release_date', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['workflow_tool_id'], ['workflow_tool.id'], name=op.f('fk_workflow_tool_release_workflow_tool_id_workflow_tool'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workflow_tool_release'))
    )
    op.create_index('uq_workflow_tool_release_workflow_tool_id', 'workflow_tool_release', ['workflow_tool_id', 'version', 'git_commit'], unique=True, postgresql_nulls_not_distinct=True)
    op.create_table('frequency_scale_factor',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('level_of_theory_id', sa.BigInteger(), nullable=False),
    sa.Column('software_id', sa.BigInteger(), nullable=True),
    sa.Column('scale_kind', sa.Enum('fundamental', 'zpe', 'enthalpy', 'entropy', 'heat_capacity', name='frequency_scale_kind'), nullable=False),
    sa.Column('value', sa.Double(), nullable=False),
    sa.Column('source_literature_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('value > 0', name=op.f('ck_frequency_scale_factor_value_gt_0')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_frequency_scale_factor_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['level_of_theory_id'], ['level_of_theory.id'], name=op.f('fk_frequency_scale_factor_level_of_theory_id_level_of_theory'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_id'], ['software.id'], name=op.f('fk_frequency_scale_factor_software_id_software'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['source_literature_id'], ['literature.id'], name=op.f('fk_frequency_scale_factor_source_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_frequency_scale_factor_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_frequency_scale_factor'))
    )
    op.create_index('uq_frequency_scale_factor_identity', 'frequency_scale_factor', ['level_of_theory_id', 'software_id', 'scale_kind', 'value', 'source_literature_id', 'workflow_tool_release_id'], unique=True, postgresql_nulls_not_distinct=True)
    op.create_table('conformer_group',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('label', sa.String(length=64), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('representative_fingerprint_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('representative_coords_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_conformer_group_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_conformer_group_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conformer_group')),
    sa.UniqueConstraint('species_entry_id', 'label', name='uq_conformer_group_species_entry_id')
    )
    op.create_index('ix_conformer_group_species_entry_id', 'conformer_group', ['species_entry_id'], unique=False)
    op.create_table('energy_correction_scheme_atom_param',
    sa.Column('scheme_id', sa.BigInteger(), nullable=False),
    sa.Column('element', sa.Text(), nullable=False),
    sa.Column('value', sa.Double(), nullable=False),
    sa.ForeignKeyConstraint(['scheme_id'], ['energy_correction_scheme.id'], name=op.f('fk_energy_correction_scheme_atom_param_scheme_id_energy_correction_scheme'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('scheme_id', 'element', name=op.f('pk_energy_correction_scheme_atom_param'))
    )
    op.create_table('energy_correction_scheme_bond_param',
    sa.Column('scheme_id', sa.BigInteger(), nullable=False),
    sa.Column('bond_key', sa.Text(), nullable=False),
    sa.Column('value', sa.Double(), nullable=False),
    sa.ForeignKeyConstraint(['scheme_id'], ['energy_correction_scheme.id'], name=op.f('fk_energy_correction_scheme_bond_param_scheme_id_energy_correction_scheme'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('scheme_id', 'bond_key', name=op.f('pk_energy_correction_scheme_bond_param'))
    )
    op.create_table('energy_correction_scheme_component_param',
    sa.Column('scheme_id', sa.BigInteger(), nullable=False),
    sa.Column('component_kind', sa.Enum('atom_corr', 'bond_corr_length', 'bond_corr_neighbor', 'mol_corr', name='melius_bac_component_kind'), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('value', sa.Double(), nullable=False),
    sa.ForeignKeyConstraint(['scheme_id'], ['energy_correction_scheme.id'], name=op.f('fk_energy_correction_scheme_component_param_scheme_id_energy_correction_scheme'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('scheme_id', 'component_kind', 'key', name=op.f('pk_energy_correction_scheme_component_param'))
    )
    op.create_table('network',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('literature_id', sa.BigInteger(), nullable=True),
    sa.Column('software_release_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_network_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_network_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_release_id'], ['software_release.id'], name=op.f('fk_network_software_release_id_software_release'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_network_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network'))
    )
    op.create_table('reaction_entry',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('reaction_id', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_reaction_entry_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['reaction_id'], ['chem_reaction.id'], name=op.f('fk_reaction_entry_reaction_id_chem_reaction'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reaction_entry'))
    )
    op.create_table('reaction_participant',
    sa.Column('reaction_id', sa.BigInteger(), nullable=False),
    sa.Column('species_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('reactant', 'product', name='reaction_role'), nullable=False),
    sa.Column('stoichiometry', sa.SmallInteger(), nullable=False),
    sa.CheckConstraint('stoichiometry >= 1', name=op.f('ck_reaction_participant_stoichiometry_ge_1')),
    sa.ForeignKeyConstraint(['reaction_id'], ['chem_reaction.id'], name=op.f('fk_reaction_participant_reaction_id_chem_reaction'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_id'], ['species.id'], name=op.f('fk_reaction_participant_species_id_species'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('reaction_id', 'species_id', 'role', name=op.f('pk_reaction_participant'))
    )
    op.create_table('species_entry_review',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('curator', 'reviewer', 'validator', 'linker', name='species_entry_review_role'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_species_entry_review_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], name=op.f('fk_species_entry_review_user_id_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_species_entry_review')),
    sa.UniqueConstraint('species_entry_id', 'user_id', 'role', name='uq_species_entry_review_species_entry_id')
    )
    op.create_table('statmech',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('scientific_origin', sa.Enum('computed', 'experimental', 'estimated', name='scientific_origin_kind'), nullable=False),
    sa.Column('literature_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('software_release_id', sa.BigInteger(), nullable=True),
    sa.Column('external_symmetry', sa.SmallInteger(), nullable=True),
    sa.Column('point_group', sa.Text(), nullable=True),
    sa.Column('is_linear', sa.Boolean(), nullable=True),
    sa.Column('rigid_rotor_kind', sa.Enum('atom', 'linear', 'spherical_top', 'symmetric_top', 'asymmetric_top', name='rigid_rotor_kind'), nullable=True),
    sa.Column('statmech_treatment', sa.Enum('rrho', 'rrho_1d', 'rrho_nd', 'rrho_1d_nd', 'rrho_ad', 'rrao', name='statmech_treatment_kind'), nullable=True),
    sa.Column('frequency_scale_factor_id', sa.BigInteger(), nullable=True),
    sa.Column('uses_projected_frequencies', sa.Boolean(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('external_symmetry IS NULL OR external_symmetry >= 1', name=op.f('ck_statmech_external_symmetry_ge_1')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_statmech_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['frequency_scale_factor_id'], ['frequency_scale_factor.id'], name=op.f('fk_statmech_frequency_scale_factor_id_frequency_scale_factor'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_statmech_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_release_id'], ['software_release.id'], name=op.f('fk_statmech_software_release_id_software_release'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_statmech_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_statmech_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_statmech'))
    )
    op.create_table('thermo',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('scientific_origin', sa.Enum('computed', 'experimental', 'estimated', name='scientific_origin_kind'), nullable=False),
    sa.Column('literature_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('software_release_id', sa.BigInteger(), nullable=True),
    sa.Column('h298_kj_mol', sa.Double(), nullable=True),
    sa.Column('s298_j_mol_k', sa.Double(), nullable=True),
    sa.Column('h298_uncertainty_kj_mol', sa.Double(), nullable=True),
    sa.Column('s298_uncertainty_j_mol_k', sa.Double(), nullable=True),
    sa.Column('tmin_k', sa.Double(), nullable=True),
    sa.Column('tmax_k', sa.Double(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('h298_uncertainty_kj_mol IS NULL OR h298_uncertainty_kj_mol >= 0', name=op.f('ck_thermo_h298_uncertainty_ge_0')),
    sa.CheckConstraint('s298_uncertainty_j_mol_k IS NULL OR s298_uncertainty_j_mol_k >= 0', name=op.f('ck_thermo_s298_uncertainty_ge_0')),
    sa.CheckConstraint('tmax_k IS NULL OR tmax_k > 0', name=op.f('ck_thermo_tmax_k_gt_0')),
    sa.CheckConstraint('tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k', name=op.f('ck_thermo_tmin_le_tmax')),
    sa.CheckConstraint('tmin_k IS NULL OR tmin_k > 0', name=op.f('ck_thermo_tmin_k_gt_0')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_thermo_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_thermo_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_release_id'], ['software_release.id'], name=op.f('fk_thermo_software_release_id_software_release'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_thermo_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_thermo_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thermo'))
    )
    op.create_table('transport',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('scientific_origin', sa.Enum('computed', 'experimental', 'estimated', name='scientific_origin_kind'), nullable=False),
    sa.Column('literature_id', sa.BigInteger(), nullable=True),
    sa.Column('software_release_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('sigma_angstrom', sa.Double(), nullable=True),
    sa.Column('epsilon_over_k_k', sa.Double(), nullable=True),
    sa.Column('dipole_debye', sa.Double(), nullable=True),
    sa.Column('polarizability_angstrom3', sa.Double(), nullable=True),
    sa.Column('rotational_relaxation', sa.Double(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('(sigma_angstrom IS NULL AND epsilon_over_k_k IS NULL) OR (sigma_angstrom IS NOT NULL AND epsilon_over_k_k IS NOT NULL)', name=op.f('ck_transport_lj_pair_both_or_neither')),
    sa.CheckConstraint('epsilon_over_k_k IS NULL OR epsilon_over_k_k > 0', name=op.f('ck_transport_epsilon_over_k_k_gt_0')),
    sa.CheckConstraint('rotational_relaxation IS NULL OR rotational_relaxation >= 0', name=op.f('ck_transport_rotational_relaxation_ge_0')),
    sa.CheckConstraint('sigma_angstrom IS NULL OR sigma_angstrom > 0', name=op.f('ck_transport_sigma_angstrom_gt_0')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_transport_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_transport_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_release_id'], ['software_release.id'], name=op.f('fk_transport_software_release_id_software_release'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_transport_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_transport_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transport'))
    )
    op.create_table('conformer_selection',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('conformer_group_id', sa.BigInteger(), nullable=False),
    sa.Column('assignment_scheme_id', sa.BigInteger(), nullable=True),
    sa.Column('selection_kind', sa.Enum('display_default', 'curator_pick', 'lowest_energy', 'benchmark_reference', 'preferred_for_thermo', 'preferred_for_kinetics', 'representative_geometry', name='conformer_selection_kind'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['assignment_scheme_id'], ['conformer_assignment_scheme.id'], name=op.f('fk_conformer_selection_assignment_scheme_id_conformer_assignment_scheme'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['conformer_group_id'], ['conformer_group.id'], name=op.f('fk_conformer_selection_conformer_group_id_conformer_group'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_conformer_selection_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conformer_selection')),
    sa.UniqueConstraint('conformer_group_id', 'assignment_scheme_id', 'selection_kind', name='uq_conformer_selection_conformer_group_id', postgresql_nulls_not_distinct=True)
    )
    op.create_table('kinetics',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('reaction_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('scientific_origin', sa.Enum('computed', 'experimental', 'estimated', name='scientific_origin_kind'), nullable=False),
    sa.Column('model_kind', sa.Enum('arrhenius', 'modified_arrhenius', name='kinetics_model_kind'), server_default='modified_arrhenius', nullable=False),
    sa.Column('literature_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('software_release_id', sa.BigInteger(), nullable=True),
    sa.Column('a', sa.Double(), nullable=True),
    sa.Column('a_units', sa.Enum('per_s', 'cm3_mol_s', 'cm3_molecule_s', 'm3_mol_s', 'cm6_mol2_s', 'cm6_molecule2_s', 'm6_mol2_s', name='arrhenius_a_units'), nullable=True),
    sa.Column('n', sa.Double(), nullable=True),
    sa.Column('ea_kj_mol', sa.Double(), nullable=True),
    sa.Column('a_uncertainty', sa.Double(), nullable=True),
    sa.Column('a_uncertainty_kind', sa.Enum('additive', 'multiplicative', name='kinetics_uncertainty_kind'), nullable=True),
    sa.Column('n_uncertainty', sa.Double(), nullable=True),
    sa.Column('ea_uncertainty_kj_mol', sa.Double(), nullable=True),
    sa.Column('tmin_k', sa.Double(), nullable=True),
    sa.Column('tmax_k', sa.Double(), nullable=True),
    sa.Column('degeneracy', sa.Double(), nullable=True),
    sa.Column('tunneling_model', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('(a_uncertainty IS NULL) = (a_uncertainty_kind IS NULL)', name=op.f('ck_kinetics_a_uncertainty_kind_required_with_value')),
    sa.CheckConstraint("a_uncertainty_kind <> 'multiplicative' OR a_uncertainty >= 1.0", name=op.f('ck_kinetics_a_uncertainty_multiplicative_ge_1')),
    sa.CheckConstraint('tmax_k IS NULL OR tmax_k > 0', name=op.f('ck_kinetics_tmax_k_gt_0')),
    sa.CheckConstraint('tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k', name=op.f('ck_kinetics_tmin_le_tmax')),
    sa.CheckConstraint('tmin_k IS NULL OR tmin_k > 0', name=op.f('ck_kinetics_tmin_k_gt_0')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_kinetics_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_kinetics_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['reaction_entry_id'], ['reaction_entry.id'], name=op.f('fk_kinetics_reaction_entry_id_reaction_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_release_id'], ['software_release.id'], name=op.f('fk_kinetics_software_release_id_software_release'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_kinetics_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kinetics'))
    )
    op.create_table('network_reaction',
    sa.Column('network_id', sa.BigInteger(), nullable=False),
    sa.Column('reaction_entry_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['network_id'], ['network.id'], name=op.f('fk_network_reaction_network_id_network'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['reaction_entry_id'], ['reaction_entry.id'], name=op.f('fk_network_reaction_reaction_entry_id_reaction_entry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('network_id', 'reaction_entry_id', name=op.f('pk_network_reaction'))
    )
    op.create_table('network_solve',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('network_id', sa.BigInteger(), nullable=False),
    sa.Column('literature_id', sa.BigInteger(), nullable=True),
    sa.Column('software_release_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('me_method', sa.Text(), nullable=True),
    sa.Column('interpolation_model', sa.Text(), nullable=True),
    sa.Column('grain_size_cm_inv', sa.Double(), nullable=True),
    sa.Column('grain_count', sa.Integer(), nullable=True),
    sa.Column('emax_kj_mol', sa.Double(), nullable=True),
    sa.Column('tmin_k', sa.Double(), nullable=True),
    sa.Column('tmax_k', sa.Double(), nullable=True),
    sa.Column('pmin_bar', sa.Double(), nullable=True),
    sa.Column('pmax_bar', sa.Double(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('grain_count IS NULL OR grain_count >= 1', name=op.f('ck_network_solve_grain_count_ge_1')),
    sa.CheckConstraint('pmax_bar IS NULL OR pmax_bar > 0', name=op.f('ck_network_solve_pmax_bar_gt_0')),
    sa.CheckConstraint('pmin_bar IS NULL OR pmax_bar IS NULL OR pmin_bar <= pmax_bar', name=op.f('ck_network_solve_pmin_le_pmax')),
    sa.CheckConstraint('pmin_bar IS NULL OR pmin_bar > 0', name=op.f('ck_network_solve_pmin_bar_gt_0')),
    sa.CheckConstraint('tmax_k IS NULL OR tmax_k > 0', name=op.f('ck_network_solve_tmax_k_gt_0')),
    sa.CheckConstraint('tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k', name=op.f('ck_network_solve_tmin_le_tmax')),
    sa.CheckConstraint('tmin_k IS NULL OR tmin_k > 0', name=op.f('ck_network_solve_tmin_k_gt_0')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_network_solve_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_network_solve_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['network_id'], ['network.id'], name=op.f('fk_network_solve_network_id_network'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_release_id'], ['software_release.id'], name=op.f('fk_network_solve_software_release_id_software_release'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_network_solve_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_solve'))
    )
    op.create_table('network_species',
    sa.Column('network_id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('well', 'reactant', 'product', 'bath_gas', name='network_species_role'), nullable=False),
    sa.ForeignKeyConstraint(['network_id'], ['network.id'], name=op.f('fk_network_species_network_id_network'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_network_species_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('network_id', 'species_entry_id', 'role', name=op.f('pk_network_species'))
    )
    op.create_table('network_state',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('network_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('well', 'bimolecular', 'termolecular', name='network_state_kind'), nullable=False),
    sa.Column('composition_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('label', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['network_id'], ['network.id'], name=op.f('fk_network_state_network_id_network'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_state')),
    sa.UniqueConstraint('network_id', 'composition_hash', name=op.f('uq_network_state_network_id'))
    )
    op.create_table('reaction_entry_structure_participant',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('reaction_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('reactant', 'product', name='reaction_role'), nullable=False),
    sa.Column('participant_index', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('participant_index >= 1', name=op.f('ck_reaction_entry_structure_participant_participant_index_ge_1')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_reaction_entry_structure_participant_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['reaction_entry_id'], ['reaction_entry.id'], name=op.f('fk_reaction_entry_structure_participant_reaction_entry_id_reaction_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_reaction_entry_structure_participant_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reaction_entry_structure_participant')),
    sa.UniqueConstraint('reaction_entry_id', 'role', 'participant_index', name='uq_reaction_entry_structure_participant_reaction_entry_id')
    )
    op.create_table('thermo_nasa',
    sa.Column('thermo_id', sa.BigInteger(), nullable=False),
    sa.Column('t_low', sa.Double(), nullable=True),
    sa.Column('t_mid', sa.Double(), nullable=True),
    sa.Column('t_high', sa.Double(), nullable=True),
    sa.Column('a1', sa.Double(), nullable=True),
    sa.Column('a2', sa.Double(), nullable=True),
    sa.Column('a3', sa.Double(), nullable=True),
    sa.Column('a4', sa.Double(), nullable=True),
    sa.Column('a5', sa.Double(), nullable=True),
    sa.Column('a6', sa.Double(), nullable=True),
    sa.Column('a7', sa.Double(), nullable=True),
    sa.Column('b1', sa.Double(), nullable=True),
    sa.Column('b2', sa.Double(), nullable=True),
    sa.Column('b3', sa.Double(), nullable=True),
    sa.Column('b4', sa.Double(), nullable=True),
    sa.Column('b5', sa.Double(), nullable=True),
    sa.Column('b6', sa.Double(), nullable=True),
    sa.Column('b7', sa.Double(), nullable=True),
    sa.CheckConstraint('\n            (\n                t_low IS NULL\n                AND t_mid IS NULL\n                AND t_high IS NULL\n            )\n            OR\n            (\n                t_low IS NOT NULL\n                AND t_mid IS NOT NULL\n                AND t_high IS NOT NULL\n            )\n            ', name=op.f('ck_thermo_nasa_temperature_bounds_all_or_none')),
    sa.CheckConstraint('t_low IS NULL OR t_low > 0', name=op.f('ck_thermo_nasa_t_low_gt_0')),
    sa.CheckConstraint('t_low IS NULL OR t_mid IS NULL OR t_mid > t_low', name=op.f('ck_thermo_nasa_t_mid_gt_t_low')),
    sa.CheckConstraint('t_mid IS NULL OR t_high IS NULL OR t_high > t_mid', name=op.f('ck_thermo_nasa_t_high_gt_t_mid')),
    sa.ForeignKeyConstraint(['thermo_id'], ['thermo.id'], name=op.f('fk_thermo_nasa_thermo_id_thermo'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('thermo_id', name=op.f('pk_thermo_nasa'))
    )
    op.create_table('thermo_point',
    sa.Column('thermo_id', sa.BigInteger(), nullable=False),
    sa.Column('temperature_k', sa.Double(), nullable=False),
    sa.Column('cp_j_mol_k', sa.Double(), nullable=True),
    sa.Column('h_kj_mol', sa.Double(), nullable=True),
    sa.Column('s_j_mol_k', sa.Double(), nullable=True),
    sa.Column('g_kj_mol', sa.Double(), nullable=True),
    sa.ForeignKeyConstraint(['thermo_id'], ['thermo.id'], name=op.f('fk_thermo_point_thermo_id_thermo'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('thermo_id', 'temperature_k', name=op.f('pk_thermo_point'))
    )
    op.create_table('transition_state',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('reaction_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('label', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_transition_state_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['reaction_entry_id'], ['reaction_entry.id'], name=op.f('fk_transition_state_reaction_entry_id_reaction_entry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transition_state'))
    )
    op.create_table('network_channel',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('network_id', sa.BigInteger(), nullable=False),
    sa.Column('source_state_id', sa.BigInteger(), nullable=False),
    sa.Column('sink_state_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('isomerization', 'association', 'dissociation', 'stabilization', 'exchange', name='network_channel_kind'), nullable=False),
    sa.CheckConstraint('source_state_id <> sink_state_id', name=op.f('ck_network_channel_source_ne_sink')),
    sa.ForeignKeyConstraint(['network_id'], ['network.id'], name=op.f('fk_network_channel_network_id_network'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['sink_state_id'], ['network_state.id'], name=op.f('fk_network_channel_sink_state_id_network_state'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['source_state_id'], ['network_state.id'], name=op.f('fk_network_channel_source_state_id_network_state'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_channel')),
    sa.UniqueConstraint('network_id', 'source_state_id', 'sink_state_id', name=op.f('uq_network_channel_network_id'))
    )
    op.create_table('network_solve_bath_gas',
    sa.Column('solve_id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('mole_fraction', sa.Double(), nullable=False),
    sa.CheckConstraint('mole_fraction > 0 AND mole_fraction <= 1', name=op.f('ck_network_solve_bath_gas_mole_fraction_range')),
    sa.ForeignKeyConstraint(['solve_id'], ['network_solve.id'], name=op.f('fk_network_solve_bath_gas_solve_id_network_solve'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_network_solve_bath_gas_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('solve_id', 'species_entry_id', name=op.f('pk_network_solve_bath_gas'))
    )
    op.create_table('network_solve_energy_transfer',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('solve_id', sa.BigInteger(), nullable=False),
    sa.Column('model', sa.Text(), nullable=True),
    sa.Column('alpha0_cm_inv', sa.Double(), nullable=True),
    sa.Column('t_exponent', sa.Double(), nullable=True),
    sa.Column('t_ref_k', sa.Double(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['solve_id'], ['network_solve.id'], name=op.f('fk_network_solve_energy_transfer_solve_id_network_solve'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_solve_energy_transfer'))
    )
    op.create_table('network_state_participant',
    sa.Column('state_id', sa.BigInteger(), nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=False),
    sa.Column('stoichiometry', sa.SmallInteger(), server_default='1', nullable=False),
    sa.CheckConstraint('stoichiometry >= 1', name=op.f('ck_network_state_participant_stoichiometry_ge_1')),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_network_state_participant_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['state_id'], ['network_state.id'], name=op.f('fk_network_state_participant_state_id_network_state'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('state_id', 'species_entry_id', name=op.f('pk_network_state_participant'))
    )
    op.create_table('transition_state_entry',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('transition_state_id', sa.BigInteger(), nullable=False),
    sa.Column('charge', sa.SmallInteger(), nullable=False),
    sa.Column('multiplicity', sa.SmallInteger(), nullable=False),
    sa.Column('mol', app.db.types.RDKitMol(), nullable=True),
    sa.Column('unmapped_smiles', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('guess', 'optimized', 'validated', 'rejected', name='transition_state_entry_status'), server_default='optimized', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('multiplicity >= 1', name=op.f('ck_transition_state_entry_multiplicity_ge_1')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_transition_state_entry_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['transition_state_id'], ['transition_state.id'], name=op.f('fk_transition_state_entry_transition_state_id_transition_state'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transition_state_entry'))
    )
    op.create_table('calculation',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.Enum('opt', 'freq', 'sp', 'irc', 'scan', 'path_search', 'conf', name='calc_type'), nullable=False),
    sa.Column('quality', sa.Enum('raw', 'curated', 'rejected', name='calc_quality'), server_default='raw', nullable=False),
    sa.Column('species_entry_id', sa.BigInteger(), nullable=True),
    sa.Column('transition_state_entry_id', sa.BigInteger(), nullable=True),
    sa.Column('software_release_id', sa.BigInteger(), nullable=True),
    sa.Column('workflow_tool_release_id', sa.BigInteger(), nullable=True),
    sa.Column('lot_id', sa.BigInteger(), nullable=True),
    sa.Column('literature_id', sa.BigInteger(), nullable=True),
    sa.Column('conformer_observation_id', sa.BigInteger(), nullable=True),
    sa.Column('parameters_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('parameters_parser_version', sa.Text(), nullable=True),
    sa.Column('parameters_extracted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('\n                (\n                    transition_state_entry_id IS NOT NULL\n                    AND species_entry_id IS NULL\n                )\n                OR\n                (\n                    transition_state_entry_id IS NULL\n                    AND species_entry_id IS NOT NULL\n                )\n                ', name=op.f('ck_calculation_one_owner')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_calculation_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['literature_id'], ['literature.id'], name=op.f('fk_calculation_literature_id_literature'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['lot_id'], ['level_of_theory.id'], name=op.f('fk_calculation_lot_id_level_of_theory'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['software_release_id'], ['software_release.id'], name=op.f('fk_calculation_software_release_id_software_release'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['species_entry_id'], ['species_entry.id'], name=op.f('fk_calculation_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['transition_state_entry_id'], ['transition_state_entry.id'], name=op.f('fk_calculation_transition_state_entry_id_transition_state_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['workflow_tool_release_id'], ['workflow_tool_release.id'], name=op.f('fk_calculation_workflow_tool_release_id_workflow_tool_release'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calculation'))
    )
    op.create_table('network_kinetics',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('channel_id', sa.BigInteger(), nullable=False),
    sa.Column('solve_id', sa.BigInteger(), nullable=False),
    sa.Column('model_kind', sa.Enum('chebyshev', 'plog', 'tabulated', name='network_kinetics_model_kind'), nullable=False),
    sa.Column('tmin_k', sa.Double(), nullable=True),
    sa.Column('tmax_k', sa.Double(), nullable=True),
    sa.Column('pmin_bar', sa.Double(), nullable=True),
    sa.Column('pmax_bar', sa.Double(), nullable=True),
    sa.Column('rate_units', sa.Enum('per_s', 'cm3_mol_s', 'cm3_molecule_s', 'm3_mol_s', 'cm6_mol2_s', 'cm6_molecule2_s', 'm6_mol2_s', name='arrhenius_a_units'), nullable=True),
    sa.Column('pressure_units', sa.Enum('bar', 'atm', name='pressure_unit'), nullable=True),
    sa.Column('temperature_units', sa.Enum('kelvin', name='temperature_unit'), nullable=True),
    sa.Column('stores_log10_k', sa.Boolean(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('pmax_bar IS NULL OR pmax_bar > 0', name=op.f('ck_network_kinetics_pmax_bar_gt_0')),
    sa.CheckConstraint('pmin_bar IS NULL OR pmax_bar IS NULL OR pmin_bar <= pmax_bar', name=op.f('ck_network_kinetics_pmin_le_pmax')),
    sa.CheckConstraint('pmin_bar IS NULL OR pmin_bar > 0', name=op.f('ck_network_kinetics_pmin_bar_gt_0')),
    sa.CheckConstraint('tmax_k IS NULL OR tmax_k > 0', name=op.f('ck_network_kinetics_tmax_k_gt_0')),
    sa.CheckConstraint('tmin_k IS NULL OR tmax_k IS NULL OR tmin_k <= tmax_k', name=op.f('ck_network_kinetics_tmin_le_tmax')),
    sa.CheckConstraint('tmin_k IS NULL OR tmin_k > 0', name=op.f('ck_network_kinetics_tmin_k_gt_0')),
    sa.ForeignKeyConstraint(['channel_id'], ['network_channel.id'], name=op.f('fk_network_kinetics_channel_id_network_channel'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['solve_id'], ['network_solve.id'], name=op.f('fk_network_kinetics_solve_id_network_solve'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_network_kinetics'))
    )
    op.create_table('calc_freq_result',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('n_imag', sa.Integer(), nullable=True),
    sa.Column('imag_freq_cm1', sa.Float(), nullable=True),
    sa.Column('zpe_hartree', sa.Float(), nullable=True),
    sa.Column('zpe_uncertainty_hartree', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_freq_result_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_freq_result'))
    )
    op.create_table('calc_freq_mode',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('mode_index', sa.Integer(), nullable=False),
    sa.Column('frequency_cm1', sa.Float(), nullable=False),
    sa.Column('is_imaginary', sa.Boolean(), nullable=False),
    sa.Column('reduced_mass_amu', sa.Float(), nullable=True),
    sa.Column('force_constant_mdyne_angstrom', sa.Float(), nullable=True),
    sa.Column('ir_intensity_km_mol', sa.Float(), nullable=True),
    sa.Column('raman_activity', sa.Float(), nullable=True),
    sa.Column('symmetry_label', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint('mode_index >= 1', name=op.f('ck_calc_freq_mode_mode_index_ge_1')),
    sa.CheckConstraint('reduced_mass_amu IS NULL OR reduced_mass_amu > 0', name=op.f('ck_calc_freq_mode_reduced_mass_amu_gt_0')),
    sa.CheckConstraint('ir_intensity_km_mol IS NULL OR ir_intensity_km_mol >= 0', name=op.f('ck_calc_freq_mode_ir_intensity_km_mol_ge_0')),
    sa.CheckConstraint('(is_imaginary AND frequency_cm1 < 0) OR (NOT is_imaginary AND frequency_cm1 >= 0)', name=op.f('ck_calc_freq_mode_frequency_sign_matches_is_imaginary')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_freq_mode_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'mode_index', name=op.f('pk_calc_freq_mode'))
    )
    op.create_table('calc_opt_result',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('converged', sa.Boolean(), nullable=True),
    sa.Column('n_steps', sa.Integer(), nullable=True),
    sa.Column('final_energy_hartree', sa.Float(), nullable=True),
    sa.CheckConstraint('n_steps IS NULL OR n_steps >= 0', name=op.f('ck_calc_opt_result_n_steps_ge_0')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_opt_result_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_opt_result'))
    )
    op.create_table('calculation_constraint',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('constraint_index', sa.Integer(), nullable=False),
    sa.Column('constraint_kind', sa.Enum('cartesian_atom', 'bond', 'angle', 'dihedral', 'improper', name='constraint_kind'), nullable=False),
    sa.Column('atom1_index', sa.Integer(), nullable=False),
    sa.Column('atom2_index', sa.Integer(), nullable=True),
    sa.Column('atom3_index', sa.Integer(), nullable=True),
    sa.Column('atom4_index', sa.Integer(), nullable=True),
    sa.Column('target_value', sa.Float(), nullable=True),
    sa.CheckConstraint('atom1_index >= 1', name=op.f('ck_calculation_constraint_atom1_index_ge_1')),
    sa.CheckConstraint('atom2_index IS NULL OR atom2_index >= 1', name=op.f('ck_calculation_constraint_atom2_index_ge_1')),
    sa.CheckConstraint('atom3_index IS NULL OR atom3_index >= 1', name=op.f('ck_calculation_constraint_atom3_index_ge_1')),
    sa.CheckConstraint('atom4_index IS NULL OR atom4_index >= 1', name=op.f('ck_calculation_constraint_atom4_index_ge_1')),
    sa.CheckConstraint('constraint_index >= 1', name=op.f('ck_calculation_constraint_constraint_index_ge_1')),
    sa.CheckConstraint("CASE constraint_kind WHEN 'cartesian_atom' THEN atom2_index IS NULL AND atom3_index IS NULL AND atom4_index IS NULL WHEN 'bond' THEN atom2_index IS NOT NULL AND atom3_index IS NULL AND atom4_index IS NULL WHEN 'angle' THEN atom2_index IS NOT NULL AND atom3_index IS NOT NULL AND atom4_index IS NULL ELSE atom2_index IS NOT NULL AND atom3_index IS NOT NULL AND atom4_index IS NOT NULL END", name=op.f('ck_calculation_constraint_constraint_arity_matches_kind')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calculation_constraint_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'constraint_index', name=op.f('pk_calculation_constraint'))
    )
    op.create_table('calc_scan_coordinate',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('coordinate_index', sa.Integer(), nullable=False),
    sa.Column('coordinate_kind', sa.Enum('bond', 'angle', 'dihedral', 'improper', name='scan_coordinate_kind'), nullable=False),
    sa.Column('atom1_index', sa.Integer(), nullable=False),
    sa.Column('atom2_index', sa.Integer(), nullable=False),
    sa.Column('atom3_index', sa.Integer(), nullable=True),
    sa.Column('atom4_index', sa.Integer(), nullable=True),
    sa.Column('step_count', sa.Integer(), nullable=True),
    sa.Column('step_size', sa.Float(), nullable=True),
    sa.Column('start_value', sa.Float(), nullable=True),
    sa.Column('end_value', sa.Float(), nullable=True),
    sa.Column('value_unit', sa.Enum('angstrom', 'degree', name='coordinate_unit'), nullable=True),
    sa.Column('resolution_degrees', sa.Integer(), nullable=True),
    sa.Column('symmetry_number', sa.SmallInteger(), nullable=True),
    sa.CheckConstraint('atom1_index >= 1', name=op.f('ck_calc_scan_coordinate_atom1_index_ge_1')),
    sa.CheckConstraint('atom2_index >= 1', name=op.f('ck_calc_scan_coordinate_atom2_index_ge_1')),
    sa.CheckConstraint('atom3_index IS NULL OR atom3_index >= 1', name=op.f('ck_calc_scan_coordinate_atom3_index_ge_1')),
    sa.CheckConstraint('atom4_index IS NULL OR atom4_index >= 1', name=op.f('ck_calc_scan_coordinate_atom4_index_ge_1')),
    sa.CheckConstraint('coordinate_index >= 1', name=op.f('ck_calc_scan_coordinate_coordinate_index_ge_1')),
    sa.CheckConstraint("CASE coordinate_kind WHEN 'bond' THEN atom3_index IS NULL AND atom4_index IS NULL WHEN 'angle' THEN atom3_index IS NOT NULL AND atom4_index IS NULL ELSE atom3_index IS NOT NULL AND atom4_index IS NOT NULL END", name=op.f('ck_calc_scan_coordinate_coordinate_arity_matches_kind')),
    sa.CheckConstraint('step_count IS NULL OR step_count >= 1', name=op.f('ck_calc_scan_coordinate_step_count_ge_1')),
    sa.CheckConstraint('resolution_degrees IS NULL OR resolution_degrees >= 1', name=op.f('ck_calc_scan_coordinate_resolution_degrees_ge_1')),
    sa.CheckConstraint('symmetry_number IS NULL OR symmetry_number >= 1', name=op.f('ck_calc_scan_coordinate_symmetry_number_ge_1')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_scan_coordinate_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'coordinate_index', name=op.f('pk_calc_scan_coordinate'))
    )
    op.create_table('calc_scan_point',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('point_index', sa.Integer(), nullable=False),
    sa.Column('electronic_energy_hartree', sa.Float(), nullable=True),
    sa.Column('relative_energy_kj_mol', sa.Float(), nullable=True),
    sa.Column('geometry_id', sa.BigInteger(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint('point_index >= 1', name=op.f('ck_calc_scan_point_point_index_ge_1')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_scan_point_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['geometry_id'], ['geometry.id'], name=op.f('fk_calc_scan_point_geometry_id_geometry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'point_index', name=op.f('pk_calc_scan_point'))
    )
    op.create_table('calc_scan_result',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('dimension', sa.Integer(), nullable=False),
    sa.Column('is_relaxed', sa.Boolean(), nullable=True),
    sa.Column('zero_energy_reference_hartree', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint('dimension >= 1', name=op.f('ck_calc_scan_result_dimension_ge_1')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_scan_result_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_scan_result'))
    )
    op.create_table('calc_irc_result',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('direction', sa.Enum('forward', 'reverse', 'both', name='irc_direction'), nullable=False),
    sa.Column('has_forward', sa.Boolean(), nullable=False, server_default='false'),
    sa.Column('has_reverse', sa.Boolean(), nullable=False, server_default='false'),
    sa.Column('ts_point_index', sa.Integer(), nullable=True),
    sa.Column('point_count', sa.Integer(), nullable=True),
    sa.Column('zero_energy_reference_hartree', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint('point_count IS NULL OR point_count >= 0', name=op.f('ck_calc_irc_result_point_count_ge_0')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_irc_result_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_irc_result'))
    )
    op.create_table('calc_irc_point',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('point_index', sa.Integer(), nullable=False),
    sa.Column('direction', sa.Enum('forward', 'reverse', 'both', name='irc_direction', create_type=False), nullable=True),
    sa.Column('is_ts', sa.Boolean(), nullable=False, server_default='false'),
    sa.Column('reaction_coordinate', sa.Float(), nullable=True),
    sa.Column('electronic_energy_hartree', sa.Float(), nullable=True),
    sa.Column('relative_energy_kj_mol', sa.Float(), nullable=True),
    sa.Column('max_gradient', sa.Float(), nullable=True),
    sa.Column('rms_gradient', sa.Float(), nullable=True),
    sa.Column('geometry_id', sa.BigInteger(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint('point_index >= 0', name=op.f('ck_calc_irc_point_point_index_ge_0')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_irc_point_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['geometry_id'], ['geometry.id'], name=op.f('fk_calc_irc_point_geometry_id_geometry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'point_index', name=op.f('pk_calc_irc_point'))
    )
    op.create_table('calc_path_search_result',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('method', sa.Enum('neb', 'gsm', 'growing_string', 'freezing_string', 'other', name='path_search_method'), nullable=False),
    sa.Column('is_double_ended', sa.Boolean(), nullable=True),
    sa.Column('converged', sa.Boolean(), nullable=True),
    sa.Column('n_points', sa.Integer(), nullable=True),
    sa.Column('selected_ts_point_index', sa.Integer(), nullable=True),
    sa.Column('climbing_image_index', sa.Integer(), nullable=True),
    sa.Column('source_endpoint_count', sa.Integer(), nullable=True),
    sa.Column('zero_energy_reference_hartree', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint('climbing_image_index IS NULL OR climbing_image_index >= 0', name=op.f('ck_calc_path_search_result_climbing_image_index_ge_0')),
    sa.CheckConstraint('n_points IS NULL OR n_points >= 1', name=op.f('ck_calc_path_search_result_n_points_ge_1')),
    sa.CheckConstraint('selected_ts_point_index IS NULL OR selected_ts_point_index >= 0', name=op.f('ck_calc_path_search_result_selected_ts_point_index_ge_0')),
    sa.CheckConstraint('source_endpoint_count IS NULL OR source_endpoint_count >= 1', name=op.f('ck_calc_path_search_result_source_endpoint_count_ge_1')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_path_search_result_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_path_search_result'))
    )
    op.create_table('calc_path_search_point',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('point_index', sa.Integer(), nullable=False),
    sa.Column('electronic_energy_hartree', sa.Float(), nullable=True),
    sa.Column('relative_energy_kj_mol', sa.Float(), nullable=True),
    sa.Column('path_coordinate', sa.Float(), nullable=True),
    sa.Column('max_force', sa.Float(), nullable=True),
    sa.Column('rms_force', sa.Float(), nullable=True),
    sa.Column('max_gradient', sa.Float(), nullable=True),
    sa.Column('rms_gradient', sa.Float(), nullable=True),
    sa.Column('is_ts_guess', sa.Boolean(), nullable=False, server_default='false'),
    sa.Column('is_climbing_image', sa.Boolean(), nullable=False, server_default='false'),
    sa.Column('geometry_id', sa.BigInteger(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.CheckConstraint('point_index >= 0', name=op.f('ck_calc_path_search_point_point_index_ge_0')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_path_search_point_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['geometry_id'], ['geometry.id'], name=op.f('fk_calc_path_search_point_geometry_id_geometry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'point_index', name=op.f('pk_calc_path_search_point'))
    )
    op.create_table('calc_sp_result',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('electronic_energy_hartree', sa.Float(), nullable=True),
    sa.Column('electronic_energy_uncertainty_hartree', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_sp_result_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_sp_result'))
    )
    op.create_table('calc_geometry_validation',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('input_geometry_id', sa.BigInteger(), nullable=True),
    sa.Column('output_geometry_id', sa.BigInteger(), nullable=True),
    sa.Column('species_smiles', sa.Text(), nullable=False),
    sa.Column('is_isomorphic', sa.Boolean(), nullable=False),
    sa.Column('rmsd', sa.Float(), nullable=True),
    sa.Column('atom_mapping', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('n_mappings', sa.Integer(), nullable=True),
    sa.Column('validation_status', sa.Enum('passed', 'warning', 'fail', name='validation_status'), nullable=False),
    sa.Column('validation_reason', sa.Text(), nullable=True),
    sa.Column('rmsd_warning_threshold', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_geometry_validation_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['input_geometry_id'], ['geometry.id'], name=op.f('fk_calc_geometry_validation_input_geometry_id_geometry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['output_geometry_id'], ['geometry.id'], name=op.f('fk_calc_geometry_validation_output_geometry_id_geometry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_geometry_validation'))
    )
    op.create_table('calculation_artifact',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Enum('input', 'output_log', 'checkpoint', 'formatted_checkpoint', 'ancillary', name='artifact_kind'), nullable=False),
    sa.Column('uri', sa.Text(), nullable=False),
    sa.Column('sha256', sa.CHAR(length=64), nullable=True),
    sa.Column('bytes', sa.BigInteger(), nullable=True),
    sa.Column('filename', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calculation_artifact_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_calculation_artifact_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calculation_artifact'))
    )
    op.create_table('calc_scf_stability',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('stable', 'unstable', 'stabilized', 'inconclusive', name='scf_stability_status'), nullable=False),
    sa.Column('lowest_eigenvalue', sa.Float(), nullable=True),
    sa.Column('instability_count', sa.Integer(), nullable=True),
    sa.Column('instability_type', sa.Text(), nullable=True),
    sa.Column('reoptimized_wavefunction', sa.Boolean(), nullable=True),
    sa.Column('source_calculation_id', sa.BigInteger(), nullable=True),
    sa.Column('source_artifact_id', sa.BigInteger(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('instability_count IS NULL OR instability_count >= 0', name=op.f('ck_calc_scf_stability_instability_count_ge_0')),
    sa.CheckConstraint("NOT (status = 'stable' AND reoptimized_wavefunction IS TRUE)", name=op.f('ck_calc_scf_stability_stable_no_reopt')),
    sa.CheckConstraint("NOT (status = 'stabilized' AND instability_count = 0)", name=op.f('ck_calc_scf_stability_stabilized_has_instability')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_scf_stability_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['source_calculation_id'], ['calculation.id'], name=op.f('fk_calc_scf_stability_source_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['source_artifact_id'], ['calculation_artifact.id'], name=op.f('fk_calc_scf_stability_source_artifact_id_calculation_artifact'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_calc_scf_stability_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_scf_stability'))
    )
    op.create_table('calc_wavefunction_diagnostic',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('t1_diagnostic', sa.Float(), nullable=True),
    sa.Column('d1_diagnostic', sa.Float(), nullable=True),
    sa.Column('t1_norm', sa.Float(), nullable=True),
    sa.Column('largest_t2_amplitude', sa.Float(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('t1_diagnostic IS NULL OR t1_diagnostic >= 0', name=op.f('ck_calc_wavefunction_diagnostic_t1_diagnostic_ge_0')),
    sa.CheckConstraint('d1_diagnostic IS NULL OR d1_diagnostic >= 0', name=op.f('ck_calc_wavefunction_diagnostic_d1_diagnostic_ge_0')),
    sa.CheckConstraint('t1_norm IS NULL OR t1_norm >= 0', name=op.f('ck_calc_wavefunction_diagnostic_t1_norm_ge_0')),
    sa.CheckConstraint('largest_t2_amplitude IS NULL OR largest_t2_amplitude >= 0', name=op.f('ck_calc_wavefunction_diagnostic_largest_t2_amplitude_ge_0')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calc_wavefunction_diagnostic_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_calc_wavefunction_diagnostic_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', name=op.f('pk_calc_wavefunction_diagnostic'))
    )
    op.create_table('calculation_parameter',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('raw_key', sa.Text(), nullable=False),
    sa.Column('canonical_key', sa.Text(), nullable=True),
    sa.Column('raw_value', sa.Text(), nullable=False),
    sa.Column('canonical_value', sa.Text(), nullable=True),
    sa.Column('section', sa.Text(), nullable=True),
    sa.Column('value_type', sa.Text(), nullable=True),
    sa.Column('unit', sa.Text(), nullable=True),
    sa.Column('parameter_index', sa.Integer(), nullable=True),
    sa.Column('source', sa.Enum('parser', 'upload', 'curated', name='calculation_parameter_source'), server_default='upload', nullable=False),
    sa.Column('parser_version', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('parameter_index IS NULL OR parameter_index >= 0', name=op.f('ck_calculation_parameter_parameter_index_ge_0')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calculation_parameter_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['canonical_key'], ['calculation_parameter_vocab.canonical_key'], name=op.f('fk_calculation_parameter_canonical_key_calculation_parameter_vocab'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_calculation_parameter'))
    )
    op.create_index('ix_calculation_parameter_calculation_id', 'calculation_parameter', ['calculation_id'], unique=False)
    op.create_index('ix_calculation_parameter_canonical_key', 'calculation_parameter', ['canonical_key'], unique=False)
    op.create_index('ix_calculation_parameter_raw_key_section', 'calculation_parameter', ['raw_key', 'section'], unique=False)
    op.create_index('ix_calculation_parameter_canonical_key_value', 'calculation_parameter', ['canonical_key', 'canonical_value'], unique=False)
    op.create_index('ix_calculation_parameter_source', 'calculation_parameter', ['calculation_id', 'source'], unique=False)
    op.create_table('calculation_dependency',
    sa.Column('parent_calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('child_calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('dependency_role', sa.Enum('optimized_from', 'freq_on', 'single_point_on', 'arkane_source', 'irc_start', 'irc_followup', 'scan_parent', name='calculation_dependency_role'), nullable=False),
    sa.CheckConstraint('parent_calculation_id <> child_calculation_id', name=op.f('ck_calculation_dependency_not_self')),
    sa.ForeignKeyConstraint(['child_calculation_id'], ['calculation.id'], name=op.f('fk_calculation_dependency_child_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['parent_calculation_id'], ['calculation.id'], name=op.f('fk_calculation_dependency_parent_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('parent_calculation_id', 'child_calculation_id', name=op.f('pk_calculation_dependency'))
    )
    op.create_index('uq_calculation_dependency_child_calculation_id_freq_on', 'calculation_dependency', ['child_calculation_id'], unique=True, postgresql_where=sa.text("dependency_role = 'freq_on'"))
    op.create_index('uq_calculation_dependency_child_calculation_id_optimized_from', 'calculation_dependency', ['child_calculation_id'], unique=True, postgresql_where=sa.text("dependency_role = 'optimized_from'"))
    op.create_index('uq_calculation_dependency_child_calculation_id_scan_parent', 'calculation_dependency', ['child_calculation_id'], unique=True, postgresql_where=sa.text("dependency_role = 'scan_parent'"))
    op.create_index('uq_calculation_dependency_child_calculation_id_single_point_on', 'calculation_dependency', ['child_calculation_id'], unique=True, postgresql_where=sa.text("dependency_role = 'single_point_on'"))
    op.create_table('calculation_input_geometry',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('geometry_id', sa.BigInteger(), nullable=False),
    sa.Column('input_order', sa.Integer(), server_default='1', nullable=False),
    sa.CheckConstraint('input_order >= 1', name=op.f('ck_calculation_input_geometry_input_order_ge_1')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calculation_input_geometry_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['geometry_id'], ['geometry.id'], name=op.f('fk_calculation_input_geometry_geometry_id_geometry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'input_order', name=op.f('pk_calculation_input_geometry')),
    sa.UniqueConstraint('calculation_id', 'geometry_id', name='uq_calculation_input_geometry_calculation_id')
    )
    op.create_table('calculation_output_geometry',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('geometry_id', sa.BigInteger(), nullable=False),
    sa.Column('output_order', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('final', 'initial', 'scan_point', 'irc_forward', 'irc_reverse', 'path_search_point', name='calculation_geometry_role'), nullable=True),
    sa.CheckConstraint('output_order >= 1', name=op.f('ck_calculation_output_geometry_output_order_ge_1')),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_calculation_output_geometry_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['geometry_id'], ['geometry.id'], name=op.f('fk_calculation_output_geometry_geometry_id_geometry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'output_order', name=op.f('pk_calculation_output_geometry')),
    sa.UniqueConstraint('calculation_id', 'geometry_id', name='uq_calculation_output_geometry_calculation_id')
    )
    op.create_table('conformer_observation',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('conformer_group_id', sa.BigInteger(), nullable=False),
    sa.Column('assignment_scheme_id', sa.BigInteger(), nullable=True),
    sa.Column('scientific_origin', sa.Enum('computed', 'experimental', 'estimated', name='scientific_origin_kind'), server_default='computed', nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('torsion_fingerprint_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['assignment_scheme_id'], ['conformer_assignment_scheme.id'], name=op.f('fk_conformer_observation_assignment_scheme_id_conformer_assignment_scheme'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['conformer_group_id'], ['conformer_group.id'], name=op.f('fk_conformer_observation_conformer_group_id_conformer_group'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_conformer_observation_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conformer_observation'))
    )
    op.create_index('ix_conformer_observation_conformer_group_id', 'conformer_observation', ['conformer_group_id'], unique=False)
    # Deferred FK: calculation.conformer_observation_id → conformer_observation.id
    # (calculation is created before conformer_observation, so the FK is added after)
    op.create_foreign_key(
        op.f('fk_calculation_conformer_observation_id_conformer_observation'),
        'calculation', 'conformer_observation',
        ['conformer_observation_id'], ['id'],
        initially='IMMEDIATE', deferrable=True,
    )
    op.create_table('kinetics_source_calculation',
    sa.Column('kinetics_id', sa.BigInteger(), nullable=False),
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('reactant_energy', 'product_energy', 'ts_energy', 'freq', 'irc', 'master_equation', 'fit_source', name='kinetics_calc_role'), nullable=False),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_kinetics_source_calculation_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['kinetics_id'], ['kinetics.id'], name=op.f('fk_kinetics_source_calculation_kinetics_id_kinetics'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('kinetics_id', 'calculation_id', 'role', name=op.f('pk_kinetics_source_calculation'))
    )
    op.create_table('network_kinetics_chebyshev',
    sa.Column('network_kinetics_id', sa.BigInteger(), nullable=False),
    sa.Column('n_temperature', sa.SmallInteger(), nullable=False),
    sa.Column('n_pressure', sa.SmallInteger(), nullable=False),
    sa.Column('coefficients', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint('n_pressure >= 1', name=op.f('ck_network_kinetics_chebyshev_n_pressure_ge_1')),
    sa.CheckConstraint('n_temperature >= 1', name=op.f('ck_network_kinetics_chebyshev_n_temperature_ge_1')),
    sa.ForeignKeyConstraint(['network_kinetics_id'], ['network_kinetics.id'], name=op.f('fk_network_kinetics_chebyshev_network_kinetics_id_network_kinetics'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('network_kinetics_id', name=op.f('pk_network_kinetics_chebyshev'))
    )
    op.create_table('network_kinetics_plog',
    sa.Column('network_kinetics_id', sa.BigInteger(), nullable=False),
    sa.Column('pressure_bar', sa.Double(), nullable=False),
    sa.Column('entry_index', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('a', sa.Double(), nullable=False),
    sa.Column('a_units', sa.Enum('per_s', 'cm3_mol_s', 'cm3_molecule_s', 'm3_mol_s', 'cm6_mol2_s', 'cm6_molecule2_s', 'm6_mol2_s', name='arrhenius_a_units'), nullable=True),
    sa.Column('n', sa.Double(), nullable=False),
    sa.Column('ea_kj_mol', sa.Double(), nullable=False),
    sa.CheckConstraint('entry_index >= 1', name=op.f('ck_network_kinetics_plog_entry_index_ge_1')),
    sa.CheckConstraint('pressure_bar > 0', name=op.f('ck_network_kinetics_plog_pressure_bar_gt_0')),
    sa.ForeignKeyConstraint(['network_kinetics_id'], ['network_kinetics.id'], name=op.f('fk_network_kinetics_plog_network_kinetics_id_network_kinetics'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('network_kinetics_id', 'pressure_bar', 'entry_index', name=op.f('pk_network_kinetics_plog'))
    )
    op.create_table('network_kinetics_point',
    sa.Column('network_kinetics_id', sa.BigInteger(), nullable=False),
    sa.Column('temperature_k', sa.Double(), nullable=False),
    sa.Column('pressure_bar', sa.Double(), nullable=False),
    sa.Column('rate_value', sa.Double(), nullable=False),
    sa.CheckConstraint('pressure_bar > 0', name=op.f('ck_network_kinetics_point_pressure_bar_gt_0')),
    sa.CheckConstraint('temperature_k > 0', name=op.f('ck_network_kinetics_point_temperature_k_gt_0')),
    sa.ForeignKeyConstraint(['network_kinetics_id'], ['network_kinetics.id'], name=op.f('fk_network_kinetics_point_network_kinetics_id_network_kinetics'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('network_kinetics_id', 'temperature_k', 'pressure_bar', name=op.f('pk_network_kinetics_point'))
    )
    op.create_table('network_solve_source_calculation',
    sa.Column('solve_id', sa.BigInteger(), nullable=False),
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('well_energy', 'barrier_energy', 'well_freq', 'barrier_freq', 'master_equation_run', 'fit_source', name='network_solve_calc_role'), nullable=False),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_network_solve_source_calculation_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['solve_id'], ['network_solve.id'], name=op.f('fk_network_solve_source_calculation_solve_id_network_solve'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('solve_id', 'calculation_id', 'role', name=op.f('pk_network_solve_source_calculation'))
    )
    op.create_table('statmech_source_calculation',
    sa.Column('statmech_id', sa.BigInteger(), nullable=False),
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('opt', 'freq', 'sp', 'scan', 'composite', 'imported', name='statmech_calc_role'), nullable=False),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_statmech_source_calculation_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['statmech_id'], ['statmech.id'], name=op.f('fk_statmech_source_calculation_statmech_id_statmech'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('statmech_id', 'calculation_id', 'role', name=op.f('pk_statmech_source_calculation'))
    )
    op.create_table('statmech_torsion',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('statmech_id', sa.BigInteger(), nullable=False),
    sa.Column('torsion_index', sa.Integer(), nullable=False),
    sa.Column('symmetry_number', sa.SmallInteger(), nullable=True),
    sa.Column('treatment_kind', sa.Enum('hindered_rotor', 'free_rotor', 'rigid_top', 'hindered_rotor_dos', name='torsion_treatment_kind'), nullable=True),
    sa.Column('dimension', sa.Integer(), nullable=False),
    sa.Column('top_description', sa.Text(), nullable=True),
    sa.Column('invalidated_reason', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('source_scan_calculation_id', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('dimension >= 1', name=op.f('ck_statmech_torsion_dimension_ge_1')),
    sa.CheckConstraint('symmetry_number IS NULL OR symmetry_number >= 1', name=op.f('ck_statmech_torsion_symmetry_number_ge_1')),
    sa.CheckConstraint('torsion_index >= 1', name=op.f('ck_statmech_torsion_torsion_index_ge_1')),
    sa.ForeignKeyConstraint(['source_scan_calculation_id'], ['calculation.id'], name=op.f('fk_statmech_torsion_source_scan_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['statmech_id'], ['statmech.id'], name=op.f('fk_statmech_torsion_statmech_id_statmech'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_statmech_torsion'))
    )
    op.create_index('uq_statmech_torsion_statmech_id', 'statmech_torsion', ['statmech_id', 'torsion_index'], unique=True)
    op.create_table('thermo_source_calculation',
    sa.Column('thermo_id', sa.BigInteger(), nullable=False),
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('opt', 'freq', 'sp', 'composite', 'imported', name='thermo_calc_role'), nullable=False),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_thermo_source_calculation_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['thermo_id'], ['thermo.id'], name=op.f('fk_thermo_source_calculation_thermo_id_thermo'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('thermo_id', 'calculation_id', 'role', name=op.f('pk_thermo_source_calculation'))
    )
    op.create_table('transport_source_calculation',
    sa.Column('transport_id', sa.BigInteger(), nullable=False),
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('role', sa.Enum('full_transport', 'dipole', 'polarizability', 'supporting_geometry', name='transport_calc_role'), nullable=False),
    sa.ForeignKeyConstraint(['calculation_id'], ['calculation.id'], name=op.f('fk_transport_source_calculation_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['transport_id'], ['transport.id'], name=op.f('fk_transport_source_calculation_transport_id_transport'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('transport_id', 'calculation_id', 'role', name=op.f('pk_transport_source_calculation'))
    )
    op.create_table('applied_energy_correction',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('target_species_entry_id', sa.BigInteger(), nullable=True),
    sa.Column('target_reaction_entry_id', sa.BigInteger(), nullable=True),
    sa.Column('target_transition_state_entry_id', sa.BigInteger(), nullable=True),
    sa.Column('source_conformer_observation_id', sa.BigInteger(), nullable=True),
    sa.Column('source_calculation_id', sa.BigInteger(), nullable=True),
    sa.Column('scheme_id', sa.BigInteger(), nullable=True),
    sa.Column('frequency_scale_factor_id', sa.BigInteger(), nullable=True),
    sa.Column('application_role', sa.Enum('zpe', 'thermal_correction_energy', 'thermal_correction_enthalpy', 'thermal_correction_gibbs', 'entropy_contribution', 'bac_total', 'aec_total', 'soc_total', 'atomization_reference_adjustment', 'composite_delta', 'custom', name='energy_correction_application_role'), nullable=False),
    sa.Column('value', sa.Double(), nullable=False),
    sa.Column('value_unit', sa.Enum('hartree', 'kj_mol', 'kcal_mol', name='energy_unit'), nullable=False),
    sa.Column('temperature_k', sa.Double(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('num_nonnulls(scheme_id, frequency_scale_factor_id) = 1', name=op.f('ck_applied_energy_correction_exactly_one_provenance_source')),
    sa.CheckConstraint('num_nonnulls(target_species_entry_id, target_reaction_entry_id, target_transition_state_entry_id) = 1', name=op.f('ck_applied_energy_correction_exactly_one_target')),
    sa.CheckConstraint('temperature_k IS NULL OR temperature_k > 0', name=op.f('ck_applied_energy_correction_temperature_k_gt_0')),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_applied_energy_correction_created_by_app_user'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['frequency_scale_factor_id'], ['frequency_scale_factor.id'], name=op.f('fk_applied_energy_correction_frequency_scale_factor_id_frequency_scale_factor'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['scheme_id'], ['energy_correction_scheme.id'], name=op.f('fk_applied_energy_correction_scheme_id_energy_correction_scheme'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['source_calculation_id'], ['calculation.id'], name=op.f('fk_applied_energy_correction_source_calculation_id_calculation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['source_conformer_observation_id'], ['conformer_observation.id'], name=op.f('fk_applied_energy_correction_source_conformer_observation_id_conformer_observation'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['target_reaction_entry_id'], ['reaction_entry.id'], name=op.f('fk_applied_energy_correction_target_reaction_entry_id_reaction_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['target_species_entry_id'], ['species_entry.id'], name=op.f('fk_applied_energy_correction_target_species_entry_id_species_entry'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['target_transition_state_entry_id'], ['transition_state_entry.id'], name=op.f('fk_applied_energy_correction_target_transition_state_entry_id_transition_state_entry'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_applied_energy_correction'))
    )
    op.create_index('uq_applied_energy_correction_dedup', 'applied_energy_correction', ['target_species_entry_id', 'target_reaction_entry_id', 'target_transition_state_entry_id', 'source_conformer_observation_id', 'scheme_id', 'frequency_scale_factor_id', 'application_role', 'temperature_k', 'source_calculation_id'], unique=True, postgresql_nulls_not_distinct=True)
    op.create_table('calc_scan_point_coordinate_value',
    sa.Column('calculation_id', sa.BigInteger(), nullable=False),
    sa.Column('point_index', sa.Integer(), nullable=False),
    sa.Column('coordinate_index', sa.Integer(), nullable=False),
    sa.Column('coordinate_value', sa.Float(), nullable=False),
    sa.Column('value_unit', sa.Enum('angstrom', 'degree', name='coordinate_unit'), nullable=True),
    sa.CheckConstraint('coordinate_index >= 1', name=op.f('ck_calc_scan_point_coordinate_value_coordinate_index_ge_1')),
    sa.CheckConstraint('point_index >= 1', name=op.f('ck_calc_scan_point_coordinate_value_point_index_ge_1')),
    sa.ForeignKeyConstraint(['calculation_id', 'coordinate_index'], ['calc_scan_coordinate.calculation_id', 'calc_scan_coordinate.coordinate_index'], name=op.f('fk_calc_scan_point_coordinate_value_calculation_id_calc_scan_coordinate'), initially='IMMEDIATE', deferrable=True),
    sa.ForeignKeyConstraint(['calculation_id', 'point_index'], ['calc_scan_point.calculation_id', 'calc_scan_point.point_index'], name=op.f('fk_calc_scan_point_coordinate_value_calculation_id_calc_scan_point'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('calculation_id', 'point_index', 'coordinate_index', name=op.f('pk_calc_scan_point_coordinate_value'))
    )
    op.create_table('statmech_torsion_definition',
    sa.Column('torsion_id', sa.BigInteger(), nullable=False),
    sa.Column('coordinate_index', sa.Integer(), nullable=False),
    sa.Column('atom1_index', sa.Integer(), nullable=False),
    sa.Column('atom2_index', sa.Integer(), nullable=False),
    sa.Column('atom3_index', sa.Integer(), nullable=False),
    sa.Column('atom4_index', sa.Integer(), nullable=False),
    sa.CheckConstraint('atom1_index >= 1', name=op.f('ck_statmech_torsion_definition_atom1_index_ge_1')),
    sa.CheckConstraint('atom2_index >= 1', name=op.f('ck_statmech_torsion_definition_atom2_index_ge_1')),
    sa.CheckConstraint('atom3_index >= 1', name=op.f('ck_statmech_torsion_definition_atom3_index_ge_1')),
    sa.CheckConstraint('atom4_index >= 1', name=op.f('ck_statmech_torsion_definition_atom4_index_ge_1')),
    sa.CheckConstraint('coordinate_index >= 1', name=op.f('ck_statmech_torsion_definition_coordinate_index_ge_1')),
    sa.ForeignKeyConstraint(['torsion_id'], ['statmech_torsion.id'], name=op.f('fk_statmech_torsion_definition_torsion_id_statmech_torsion'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('torsion_id', 'coordinate_index', name=op.f('pk_statmech_torsion_definition'))
    )
    op.create_table('applied_energy_correction_component',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('applied_correction_id', sa.BigInteger(), nullable=False),
    sa.Column('component_kind', sa.Enum('atom', 'bond', 'molecular', 'zpe_scale', 'soc', 'other', name='applied_correction_component_kind'), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('multiplicity', sa.Integer(), server_default='1', nullable=False),
    sa.Column('parameter_value', sa.Double(), nullable=False),
    sa.Column('contribution_value', sa.Double(), nullable=False),
    sa.CheckConstraint('multiplicity >= 1', name=op.f('ck_applied_energy_correction_component_multiplicity_ge_1')),
    sa.ForeignKeyConstraint(['applied_correction_id'], ['applied_energy_correction.id'], name=op.f('fk_applied_energy_correction_component_applied_correction_id_applied_energy_correction'), initially='IMMEDIATE', deferrable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_applied_energy_correction_component'))
    )

    op.create_table(
        'upload_job',
        sa.Column('id', postgresql.UUID(as_uuid=False), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('status', sa.Enum('queued', 'processing', 'complete', 'failed', name='upload_job_status'), server_default='queued', nullable=False),
        sa.Column('kind', sa.Enum('computed_reaction', 'conformer', 'reaction', 'kinetics', 'network', 'network_pdep', 'thermo', 'transition_state', 'transport', name='upload_job_kind'), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('max_attempts', sa.Integer(), server_default='3', nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['app_user.id'], name=op.f('fk_upload_job_created_by_app_user'), deferrable=True, initially='IMMEDIATE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_upload_job')),
    )
    op.create_index('ix_upload_job_status_created_at', 'upload_job', ['status', 'created_at'], unique=False)

    # ------------------------------------------------------------------
    # Submission moderation layer
    # ------------------------------------------------------------------
    submission_status_enum = postgresql.ENUM(
        'pending', 'precheck_passed', 'auto_flagged', 'approved', 'rejected', 'superseded',
        name='submission_status',
        create_type=True,
    )
    submission_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'submission',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=False),
        sa.Column(
            'submission_kind',
            sa.Enum(
                'computed_reaction', 'conformer', 'reaction', 'kinetics', 'network',
                'network_pdep', 'thermo', 'transition_state', 'transport', 'other',
                name='submission_kind',
            ),
            nullable=False,
        ),
        sa.Column(
            'source_kind',
            sa.Enum(
                'api', 'web', 'bulk_import', 'system', 'migration',
                name='submission_source_kind',
            ),
            server_default='api',
            nullable=False,
        ),
        sa.Column('upload_job_id', postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column(
            'status',
            postgresql.ENUM(name='submission_status', create_type=False),
            server_default='pending',
            nullable=False,
        ),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('approved_by', sa.BigInteger(), nullable=True),
        sa.Column('rejected_at', sa.DateTime(), nullable=True),
        sa.Column('rejected_by', sa.BigInteger(), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('correction_due_at', sa.DateTime(), nullable=True),
        sa.Column('supersedes_submission_id', sa.BigInteger(), nullable=True),
        sa.Column(
            'llm_precheck_label',
            sa.Enum('passed', 'flagged', name='submission_precheck_label'),
            nullable=True,
        ),
        sa.Column('llm_precheck_summary', sa.Text(), nullable=True),
        sa.Column('llm_precheck_model', sa.String(length=128), nullable=True),
        sa.Column('llm_precheck_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "(status <> 'rejected') OR (rejection_reason IS NOT NULL)",
            name=op.f('ck_submission_submission_rejected_requires_reason'),
        ),
        sa.CheckConstraint(
            "(status <> 'approved') OR (approved_by IS NOT NULL AND approved_by <> created_by)",
            name=op.f('ck_submission_submission_approver_not_creator'),
        ),
        sa.CheckConstraint(
            "(status <> 'rejected') OR (rejected_by IS NOT NULL AND rejected_by <> created_by)",
            name=op.f('ck_submission_submission_rejecter_not_creator'),
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['app_user.id'],
            name=op.f('fk_submission_created_by_app_user'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['approved_by'], ['app_user.id'],
            name=op.f('fk_submission_approved_by_app_user'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['rejected_by'], ['app_user.id'],
            name=op.f('fk_submission_rejected_by_app_user'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['upload_job_id'], ['upload_job.id'],
            name=op.f('fk_submission_upload_job_id_upload_job'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['supersedes_submission_id'], ['submission.id'],
            name=op.f('fk_submission_supersedes_submission_id_submission'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_submission')),
    )
    op.create_index('ix_submission_status_created_at', 'submission', ['status', 'created_at'], unique=False)
    op.create_index('ix_submission_created_by', 'submission', ['created_by'], unique=False)
    op.create_index('ix_submission_upload_job_id', 'submission', ['upload_job_id'], unique=False)

    op.create_table(
        'submission_audit_event',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('submission_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('actor_user_id', sa.BigInteger(), nullable=True),
        sa.Column(
            'actor_kind',
            sa.Enum('user', 'curator', 'admin', 'llm', 'system', name='submission_actor_kind'),
            nullable=False,
        ),
        sa.Column(
            'event_kind',
            sa.Enum(
                'submission_created', 'ingestion_succeeded', 'ingestion_failed',
                'llm_precheck_passed', 'llm_precheck_flagged', 'curator_approved',
                'curator_rejected', 'correction_window_opened', 'correction_uploaded',
                'submission_superseded', 'status_changed', 'public_visibility_changed',
                name='submission_audit_event_kind',
            ),
            nullable=False,
        ),
        sa.Column('from_status', postgresql.ENUM(name='submission_status', create_type=False), nullable=True),
        sa.Column('to_status', postgresql.ENUM(name='submission_status', create_type=False), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('details_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('related_submission_id', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ['submission_id'], ['submission.id'],
            name=op.f('fk_submission_audit_event_submission_id_submission'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['actor_user_id'], ['app_user.id'],
            name=op.f('fk_submission_audit_event_actor_user_id_app_user'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['related_submission_id'], ['submission.id'],
            name=op.f('fk_submission_audit_event_related_submission_id_submission'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_submission_audit_event')),
    )
    op.create_index('ix_submission_audit_event_submission_id', 'submission_audit_event', ['submission_id'], unique=False)
    op.create_index('ix_submission_audit_event_event_kind', 'submission_audit_event', ['event_kind'], unique=False)

    op.create_table(
        'submission_record_link',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('submission_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'record_type',
            sa.Enum(
                'species', 'species_entry', 'conformer_group', 'conformer_observation',
                'reaction', 'reaction_entry', 'transition_state', 'transition_state_entry',
                'calculation', 'statmech', 'thermo', 'kinetics', 'transport',
                'network', 'network_solve', 'applied_energy_correction',
                name='submission_record_type',
            ),
            nullable=False,
        ),
        sa.Column('record_id', sa.BigInteger(), nullable=False),
        sa.Column('role', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(
            ['submission_id'], ['submission.id'],
            name=op.f('fk_submission_record_link_submission_id_submission'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_submission_record_link')),
        sa.UniqueConstraint(
            'submission_id', 'record_type', 'record_id', 'role',
            name=op.f('uq_submission_record_link_identity'),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index('ix_submission_record_link_submission_id', 'submission_record_link', ['submission_id'], unique=False)
    op.create_index('ix_submission_record_link_record', 'submission_record_link', ['record_type', 'record_id'], unique=False)

    op.create_table(
        'record_review',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column(
            'record_type',
            postgresql.ENUM(name='submission_record_type', create_type=False),
            nullable=False,
        ),
        sa.Column('record_id', sa.BigInteger(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'not_reviewed', 'under_review', 'approved', 'rejected', 'deprecated',
                name='record_review_status',
            ),
            server_default='not_reviewed',
            nullable=False,
        ),
        sa.Column('submission_id', sa.BigInteger(), nullable=True),
        sa.Column('reviewed_by', sa.BigInteger(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ['submission_id'], ['submission.id'],
            name=op.f('fk_record_review_submission_id_submission'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['reviewed_by'], ['app_user.id'],
            name=op.f('fk_record_review_reviewed_by_app_user'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['app_user.id'],
            name=op.f('fk_record_review_created_by_app_user'),
            deferrable=True, initially='IMMEDIATE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_record_review')),
        sa.UniqueConstraint(
            'record_type', 'record_id',
            name='uq_record_review_record',
        ),
        sa.CheckConstraint(
            "(status NOT IN ('approved', 'rejected', 'deprecated')) "
            "OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name=op.f('ck_record_review_record_review_terminal_requires_reviewer'),
        ),
    )
    op.create_index('ix_record_review_status_record_type', 'record_review', ['status', 'record_type'], unique=False)
    op.create_index('ix_record_review_submission_id', 'record_review', ['submission_id'], unique=False)

    _seed_reaction_families()
    _seed_calculation_parameter_vocab()

    # Seed default conformer assignment scheme
    op.execute(sa.text("""
        INSERT INTO conformer_assignment_scheme
            (name, version, scope, description, parameters_json, is_default)
        VALUES
            ('torsion_basin', 'v1', 'canonical',
             'Torsional basin matching: group conformers whose comparable '
             'rotatable-bond torsions all differ by less than the threshold '
             'under circular comparison with optional symmetry folding.',
             '{"require_all_comparable_torsions_within_threshold": true, "torsion_match_threshold_degrees": 15, "use_circular_difference": true, "exclude_methyl_rotors": false, "exclude_terminal_noisy_rotors": true, "methyl_symmetry_fold": 3, "quantization_bin_degrees": 15, "rigid_fallback_use_rmsd": true, "rmsd_threshold_angstrom": 0.5, "tie_break": "closest_torsional_distance"}'::jsonb,
             true)
    """))

    _add_public_ref_columns_and_indexes()


# Phase A — public ref columns + UNIQUE indexes for ref-bearing tables.
# See docs/specs/public_identifier_policy.md.
#
# Each entry is (table_name, ref_prefix). The prefix is embedded in the
# server-side fallback so raw-SQL inserts (test fixtures, manual SQL)
# still produce a legible per-table placeholder ref. The ORM
# ``before_insert`` listener overrides the placeholder with a real
# content-derived or opaque ref on every ORM-mediated insert.
_PUBLIC_REF_TABLES: tuple[tuple[str, str], ...] = (
    ("species", "spc"),
    ("species_entry", "spe"),
    ("chem_reaction", "rxn"),
    ("reaction_entry", "rxe"),
    ("thermo", "thm"),
    ("kinetics", "kin"),
    ("calculation", "calc"),
    ("geometry", "geom"),
    ("conformer_group", "cg"),
    ("conformer_observation", "co"),
    ("conformer_assignment_scheme", "cas"),
    ("statmech", "sm"),
    ("transport", "trn"),
    ("transition_state", "ts"),
    ("transition_state_entry", "tse"),
    ("network", "net"),
    ("network_solve", "nsolve"),
    ("level_of_theory", "lot"),
    ("software", "soft"),
    ("software_release", "srel"),
    ("workflow_tool", "wft"),
    ("workflow_tool_release", "wfr"),
    ("literature", "lit"),
    ("frequency_scale_factor", "fsf"),
    ("energy_correction_scheme", "ecs"),
    ("submission", "sub"),
)


def _add_public_ref_columns_and_indexes() -> None:
    """Add ``public_ref`` columns + UNIQUE indexes to every ref-bearing table.

    Phase A. Behavior split between two layers:

    1. **Server-side fallback** — every column carries a per-table
       ``server_default`` of ``'<prefix>_' || gen_random_uuid()::text``.
       This satisfies NOT NULL whenever a row is inserted via raw SQL
       (test fixtures using ``connection.execute(text("INSERT…"))`` and
       similar manual paths). PostgreSQL 13+ provides ``gen_random_uuid()``
       built-in.

    2. **ORM-level override** — for ORM-mediated inserts the global
       ``before_insert`` listener installed in ``app.db.models.__init__``
       computes the right ref (content-derived for identity tables,
       opaque base32 for event tables per
       ``docs/specs/public_identifier_policy.md``) and assigns it before
       the row is sent to the database. The ORM-supplied value wins over
       the server_default.

    The one row that needs an explicit migration-time backfill is the
    seeded default ``conformer_assignment_scheme`` (``torsion_basin v1``).
    """
    # Step 1 — add columns with the per-table server_default fallback.
    # Columns are added NULLable initially because PostgreSQL applies
    # server_defaults only when the value is omitted from INSERT, and the
    # backfill UPDATE for the seeded scheme row needs to write its own
    # value. We flip NOT NULL after the backfill.
    for table, prefix in _PUBLIC_REF_TABLES:
        # Strip hyphens and truncate to 26 chars so the placeholder body
        # matches the ORM-generated body length and the whole ref fits
        # inside ``String(40)`` for any prefix in _PUBLIC_REF_TABLES
        # (longest prefix = 4 chars → 4 + 1 + 26 = 31 chars).
        op.add_column(
            table,
            sa.Column(
                "public_ref",
                sa.String(length=40),
                nullable=True,
                server_default=sa.text(
                    f"'{prefix}_' || substring("
                    f"replace(gen_random_uuid()::text, '-', ''), 1, 26)"
                ),
            ),
        )

    # Step 2 — backfill the seeded default conformer_assignment_scheme so
    # its public_ref is the documented content-derived value (not a UUID
    # placeholder). Other seeded tables (reaction_family,
    # calculation_parameter_vocab) are not in the public-ref scope.
    cas_ref = make_content_ref(
        "cas", "cas:name=torsion_basin;version=v1;scope=canonical"
    )
    op.execute(
        sa.text(
            "UPDATE conformer_assignment_scheme SET public_ref = :ref "
            "WHERE name = 'torsion_basin' AND version = 'v1'"
        ).bindparams(ref=cas_ref)
    )

    # Step 3 — flip NOT NULL and add the UNIQUE index per table.
    for table, _prefix in _PUBLIC_REF_TABLES:
        op.alter_column(
            table,
            "public_ref",
            existing_type=sa.String(length=40),
            nullable=False,
        )
        op.create_index(
            op.f(f"ix_{table}_public_ref"),
            table,
            ["public_ref"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_record_review_submission_id', table_name='record_review')
    op.drop_index('ix_record_review_status_record_type', table_name='record_review')
    op.drop_table('record_review')
    op.execute("DROP TYPE IF EXISTS record_review_status")

    op.drop_index('ix_submission_record_link_record', table_name='submission_record_link')
    op.drop_index('ix_submission_record_link_submission_id', table_name='submission_record_link')
    op.drop_table('submission_record_link')
    op.execute("DROP TYPE IF EXISTS submission_record_type")

    op.drop_index('ix_submission_audit_event_event_kind', table_name='submission_audit_event')
    op.drop_index('ix_submission_audit_event_submission_id', table_name='submission_audit_event')
    op.drop_table('submission_audit_event')
    op.execute("DROP TYPE IF EXISTS submission_audit_event_kind")
    op.execute("DROP TYPE IF EXISTS submission_actor_kind")

    op.drop_index('ix_submission_upload_job_id', table_name='submission')
    op.drop_index('ix_submission_created_by', table_name='submission')
    op.drop_index('ix_submission_status_created_at', table_name='submission')
    op.drop_table('submission')
    op.execute("DROP TYPE IF EXISTS submission_precheck_label")
    op.execute("DROP TYPE IF EXISTS submission_status")
    op.execute("DROP TYPE IF EXISTS submission_source_kind")
    op.execute("DROP TYPE IF EXISTS submission_kind")

    op.drop_table('applied_energy_correction_component')
    op.drop_table('statmech_torsion_definition')
    op.drop_table('calc_scan_point_coordinate_value')
    op.drop_index('uq_applied_energy_correction_dedup', table_name='applied_energy_correction', postgresql_nulls_not_distinct=True)
    op.drop_table('applied_energy_correction')
    op.drop_table('transport_source_calculation')
    op.drop_table('thermo_source_calculation')
    op.drop_index('uq_statmech_torsion_statmech_id', table_name='statmech_torsion')
    op.drop_table('statmech_torsion')
    op.drop_table('statmech_source_calculation')
    op.drop_table('network_solve_source_calculation')
    op.drop_table('network_kinetics_point')
    op.drop_table('network_kinetics_plog')
    op.drop_table('network_kinetics_chebyshev')
    op.drop_table('kinetics_source_calculation')
    op.drop_index('ix_conformer_observation_conformer_group_id', table_name='conformer_observation')
    op.drop_table('conformer_observation')
    op.drop_table('calculation_output_geometry')
    op.drop_table('calculation_input_geometry')
    op.drop_index('uq_calculation_dependency_child_calculation_id_single_point_on', table_name='calculation_dependency', postgresql_where=sa.text("dependency_role = 'single_point_on'"))
    op.drop_index('uq_calculation_dependency_child_calculation_id_scan_parent', table_name='calculation_dependency', postgresql_where=sa.text("dependency_role = 'scan_parent'"))
    op.drop_index('uq_calculation_dependency_child_calculation_id_optimized_from', table_name='calculation_dependency', postgresql_where=sa.text("dependency_role = 'optimized_from'"))
    op.drop_index('uq_calculation_dependency_child_calculation_id_freq_on', table_name='calculation_dependency', postgresql_where=sa.text("dependency_role = 'freq_on'"))
    op.drop_table('calculation_dependency')
    op.drop_table('calc_wavefunction_diagnostic')
    op.drop_table('calc_scf_stability')
    op.execute("DROP TYPE IF EXISTS scf_stability_status")
    op.drop_table('calculation_artifact')
    op.drop_index('ix_calculation_parameter_source', table_name='calculation_parameter')
    op.drop_index('ix_calculation_parameter_canonical_key_value', table_name='calculation_parameter')
    op.drop_index('ix_calculation_parameter_raw_key_section', table_name='calculation_parameter')
    op.drop_index('ix_calculation_parameter_canonical_key', table_name='calculation_parameter')
    op.drop_index('ix_calculation_parameter_calculation_id', table_name='calculation_parameter')
    op.drop_table('calculation_parameter')
    op.execute("DROP TYPE IF EXISTS calculation_parameter_source")
    op.drop_table('calculation_parameter_vocab')
    op.drop_table('calc_geometry_validation')
    op.execute("DROP TYPE IF EXISTS validation_status")
    op.drop_table('calc_sp_result')
    op.drop_table('calc_path_search_point')
    op.drop_table('calc_path_search_result')
    op.execute("DROP TYPE IF EXISTS path_search_method")
    op.drop_table('calc_irc_point')
    op.drop_table('calc_irc_result')
    op.execute("DROP TYPE IF EXISTS irc_direction")
    op.drop_table('calc_scan_result')
    op.drop_table('calc_scan_point')
    op.drop_table('calc_scan_coordinate')
    op.execute("DROP TYPE IF EXISTS scan_coordinate_kind")
    op.execute("DROP TYPE IF EXISTS coordinate_unit")
    op.drop_table('calculation_constraint')
    op.execute("DROP TYPE IF EXISTS constraint_kind")
    op.drop_table('calc_opt_result')
    op.drop_table('calc_freq_mode')
    op.drop_table('calc_freq_result')
    op.drop_table('network_kinetics')
    op.execute("DROP TYPE IF EXISTS pressure_unit")
    op.execute("DROP TYPE IF EXISTS temperature_unit")
    op.drop_table('calculation')
    op.drop_table('transition_state_entry')
    op.drop_table('network_state_participant')
    op.drop_table('network_solve_energy_transfer')
    op.drop_table('network_solve_bath_gas')
    op.drop_table('network_channel')
    op.drop_table('transition_state')
    op.execute("DROP TYPE IF EXISTS transition_state_selection_kind")
    op.drop_table('thermo_point')
    op.drop_table('thermo_nasa')
    op.drop_table('reaction_entry_structure_participant')
    op.drop_table('network_state')
    op.drop_table('network_species')
    op.drop_table('network_solve')
    op.drop_table('network_reaction')
    op.drop_table('kinetics')
    op.execute("DROP TYPE IF EXISTS kinetics_uncertainty_kind")
    op.drop_table('conformer_selection')
    op.drop_table('transport')
    op.drop_table('thermo')
    op.drop_table('statmech')
    op.drop_table('species_entry_review')
    op.drop_table('reaction_participant')
    op.drop_table('reaction_entry')
    op.drop_table('network')
    op.drop_table('energy_correction_scheme_component_param')
    op.drop_table('energy_correction_scheme_bond_param')
    op.drop_table('energy_correction_scheme_atom_param')
    op.drop_index('ix_conformer_group_species_entry_id', table_name='conformer_group')
    op.drop_table('conformer_group')
    op.drop_index('uq_frequency_scale_factor_identity', table_name='frequency_scale_factor', postgresql_nulls_not_distinct=True)
    op.drop_table('frequency_scale_factor')
    op.drop_index('uq_workflow_tool_release_workflow_tool_id', table_name='workflow_tool_release', postgresql_nulls_not_distinct=True)
    op.drop_table('workflow_tool_release')
    op.drop_index('ix_species_entry_species_id', table_name='species_entry')
    op.drop_table('species_entry')
    op.drop_index('uq_software_release_software_id', table_name='software_release', postgresql_nulls_not_distinct=True)
    op.drop_table('software_release')
    op.drop_table('literature_author')
    op.drop_table('geometry_atom')
    op.drop_index('uq_energy_correction_scheme_kind_name_lot_version', table_name='energy_correction_scheme', postgresql_nulls_not_distinct=True)
    op.drop_table('energy_correction_scheme')
    op.execute("DROP TYPE IF EXISTS energy_unit")
    op.drop_table('conformer_assignment_scheme')
    op.drop_table('chem_reaction')
    op.drop_table('workflow_tool')
    op.drop_table('species')
    op.drop_table('software')
    op.drop_table('reaction_family')
    op.drop_index('ix_literature_isbn_normalized', table_name='literature')
    op.drop_index('ix_literature_doi_normalized', table_name='literature')
    op.drop_table('literature')
    op.drop_table('level_of_theory')
    op.drop_table('geometry')
    op.drop_table('author')
    op.drop_index('ix_upload_job_status_created_at', table_name='upload_job')
    op.drop_table('upload_job')
    op.execute("DROP TYPE IF EXISTS upload_job_status")
    op.execute("DROP TYPE IF EXISTS upload_job_kind")
    op.drop_index('ix_idempotency_record_expires_at', table_name='idempotency_record')
    op.drop_table('idempotency_record')
    op.drop_table('user_session')
    op.drop_table('api_key')
    op.drop_table('app_user')
