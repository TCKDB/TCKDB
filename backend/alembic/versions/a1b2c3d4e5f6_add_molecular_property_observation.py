"""add molecular_property_observation

Phase 4a — closes CCCBDB Schema Gap 1. Adds a first-class home for
scalar/vector/tensor molecular properties (dipole, IE/EA/PA, HOMO/LUMO,
atomization energy, enthalpy of formation, spectroscopic constants,
etc.) without overloading ``thermo`` / ``statmech`` / ``transport``.

This is an **additive** migration. Per the Phase 4a prompt, the hosted
database is now treated as deployed and schema changes layer on top
of the initial migration rather than folding into it. The historical
"single initial migration" rule (rules/migration-rules.md +
feedback_single_initial_migration memory) is explicitly relaxed for
additive changes from this point forward.

Revision ID: a1b2c3d4e5f6
Revises: d861dfd60891
Create Date: 2026-05-20 (Phase 4a)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d861dfd60891"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PROPERTY_KIND_VALUES = (
    "dipole_moment",
    "quadrupole_moment",
    "polarizability",
    "polarizability_iso",
    "ionization_energy",
    "electron_affinity",
    "proton_affinity",
    "enthalpy_of_formation",
    "atomization_energy",
    "homo_energy",
    "lumo_energy",
    "homo_lumo_gap",
    "rotational_constant",
    "spectroscopic_constant",
    "other",
)


def upgrade() -> None:
    """Upgrade schema."""

    # ``scientific_origin_kind`` already exists from the initial
    # migration (used by thermo/statmech/transport). Only the new
    # property-kind enum is created here.
    property_kind_enum = postgresql.ENUM(
        *_PROPERTY_KIND_VALUES,
        name="molecular_property_kind",
        create_type=False,
    )
    property_kind_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "molecular_property_observation",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        # Identity (NULLABLE: CCCBDB importer bridge).
        sa.Column("species_entry_id", sa.BigInteger(), nullable=True),
        # Classification.
        sa.Column(
            "scientific_origin",
            postgresql.ENUM(
                "computed",
                "experimental",
                "estimated",
                name="scientific_origin_kind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "property_kind",
            postgresql.ENUM(
                *_PROPERTY_KIND_VALUES,
                name="molecular_property_kind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("property_label", sa.Text(), nullable=True),
        # Value.
        sa.Column("scalar_value", sa.Double(), nullable=True),
        sa.Column("scalar_unit", sa.Text(), nullable=True),
        sa.Column("scalar_uncertainty", sa.Double(), nullable=True),
        sa.Column("vector_json", postgresql.JSONB(), nullable=True),
        sa.Column("tensor_json", postgresql.JSONB(), nullable=True),
        # Conditions.
        sa.Column("temperature_k", sa.Double(), nullable=True),
        sa.Column("wavelength_nm", sa.Double(), nullable=True),
        sa.Column("method_note", sa.Text(), nullable=True),
        sa.Column("state_label_raw", sa.Text(), nullable=True),
        # TCKDB-internal provenance.
        sa.Column("literature_id", sa.Integer(), nullable=True),
        sa.Column("software_release_id", sa.Integer(), nullable=True),
        sa.Column("workflow_tool_release_id", sa.Integer(), nullable=True),
        sa.Column("source_calculation_id", sa.BigInteger(), nullable=True),
        # External-source provenance.
        sa.Column("external_source_name", sa.Text(), nullable=True),
        sa.Column("external_source_release", sa.Text(), nullable=True),
        sa.Column("external_source_doi", sa.Text(), nullable=True),
        sa.Column("external_source_url", sa.Text(), nullable=True),
        sa.Column("external_source_record_key", sa.Text(), nullable=True),
        sa.Column("external_source_page_kind", sa.Text(), nullable=True),
        sa.Column("external_source_content_sha256", sa.Text(), nullable=True),
        sa.Column("external_source_parser_version", sa.Text(), nullable=True),
        # Row-level reference.
        sa.Column("reference_label", sa.Text(), nullable=True),
        sa.Column("reference_comment", sa.Text(), nullable=True),
        sa.Column("raw_reference_text", sa.Text(), nullable=True),
        # Raw payload + free-text.
        sa.Column("raw_payload_json", postgresql.JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_molecular_property_observation"),
        ),
        sa.ForeignKeyConstraint(
            ["species_entry_id"],
            ["species_entry.id"],
            name=op.f("fk_molecular_property_observation_species_entry_id_species_entry"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
            name=op.f("fk_molecular_property_observation_literature_id_literature"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["software_release_id"],
            ["software_release.id"],
            name=op.f(
                "fk_molecular_property_observation_software_release_id_software_release"
            ),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_tool_release_id"],
            ["workflow_tool_release.id"],
            name=op.f(
                "fk_molecular_property_observation_workflow_tool_release_id_workflow_tool_release"
            ),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["source_calculation_id"],
            ["calculation.id"],
            name=op.f(
                "fk_molecular_property_observation_source_calculation_id_calculation"
            ),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_molecular_property_observation_created_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.CheckConstraint(
            "scalar_value IS NOT NULL "
            "OR vector_json IS NOT NULL "
            "OR tensor_json IS NOT NULL",
            name="mpo_value_at_least_one",
        ),
        sa.CheckConstraint(
            "scalar_value IS NULL OR scalar_unit IS NOT NULL",
            name="mpo_scalar_value_has_unit",
        ),
        sa.CheckConstraint(
            "scalar_uncertainty IS NULL OR scalar_uncertainty >= 0",
            name="mpo_scalar_uncertainty_ge_0",
        ),
        sa.CheckConstraint(
            "temperature_k IS NULL OR temperature_k > 0",
            name="mpo_temperature_k_gt_0",
        ),
        sa.CheckConstraint(
            "wavelength_nm IS NULL OR wavelength_nm > 0",
            name="mpo_wavelength_nm_gt_0",
        ),
        sa.UniqueConstraint(
            "species_entry_id",
            "property_kind",
            "scientific_origin",
            "external_source_name",
            "external_source_release",
            "external_source_url",
            "external_source_record_key",
            "reference_label",
            "scalar_value",
            "temperature_k",
            name="mpo_dedupe_key",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        "ix_mpo_property_kind_origin",
        "molecular_property_observation",
        ["property_kind", "scientific_origin"],
    )
    op.create_index(
        "ix_mpo_species_entry_id",
        "molecular_property_observation",
        ["species_entry_id"],
    )
    op.create_index(
        "ix_mpo_external_source_release",
        "molecular_property_observation",
        ["external_source_name", "external_source_release"],
    )
    op.create_index(
        "ix_mpo_external_source_content_sha256",
        "molecular_property_observation",
        ["external_source_content_sha256"],
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(
        "ix_mpo_external_source_content_sha256",
        table_name="molecular_property_observation",
    )
    op.drop_index(
        "ix_mpo_external_source_release",
        table_name="molecular_property_observation",
    )
    op.drop_index(
        "ix_mpo_species_entry_id",
        table_name="molecular_property_observation",
    )
    op.drop_index(
        "ix_mpo_property_kind_origin",
        table_name="molecular_property_observation",
    )
    op.drop_table("molecular_property_observation")
    postgresql.ENUM(name="molecular_property_kind").drop(
        op.get_bind(), checkfirst=True
    )
