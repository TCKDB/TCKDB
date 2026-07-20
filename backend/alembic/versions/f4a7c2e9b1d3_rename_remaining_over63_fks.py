"""rename all remaining over-63-char FKs to explicit short names

Every foreign key whose convention-generated name
(``fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s``) rendered to
more than PostgreSQL's 63-character identifier limit was previously relying on
SQLAlchemy's silent deterministic truncation (a short hash suffix) for the name
actually stored in every deployed DB. That is fragile and opaque (see the
sibling revision ``d9c4a1e7f2b6`` which fixed ``transition_state_selection``).

The models now declare an explicit short ``name=`` on each of these 25 foreign
keys (rule: ``fk_<table>_<columns>`` — dropping the redundant referred-table
suffix; two names are further abbreviated: ``applied_group_additivity`` -> ``ga``
and table ``calc_scan_point_coordinate_value`` -> ``cspcv``). This revision
renames the existing constraints in place so each deployed DB matches its model.

All of these tables are already deployed, so per
``.claude/rules/migration-rules.md`` this is a new revision. ``RENAME
CONSTRAINT`` is a metadata-only operation (no table rewrite, the FK and its
enforcement are preserved); we deliberately do NOT drop/recreate any FK. The
renames are independent, so order does not matter.

The ``_OLD_NAME`` values are the truncated names SQLAlchemy currently emits and
that are stored on every deployed DB; they must match byte-for-byte for the
``RENAME`` to find the constraint.

Revision ID: f4a7c2e9b1d3
Revises: d9c4a1e7f2b6
Create Date: 2026-07-20 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a7c2e9b1d3"
down_revision: Union[str, Sequence[str], None] = "d9c4a1e7f2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, old_truncated_deployed_name, new_explicit_name)
_RENAMES: list[tuple[str, str, str]] = [
    # applied_energy_correction (5 FKs)
    (
        "applied_energy_correction",
        "fk_applied_energy_correction_frequency_scale_factor_id__854e",
        "fk_applied_energy_correction_frequency_scale_factor_id",
    ),
    (
        "applied_energy_correction",
        "fk_applied_energy_correction_source_conformer_observati_3c7b",
        "fk_applied_energy_correction_source_conformer_observation_id",
    ),
    (
        "applied_energy_correction",
        "fk_applied_energy_correction_target_reaction_entry_id_r_6e16",
        "fk_applied_energy_correction_target_reaction_entry_id",
    ),
    (
        "applied_energy_correction",
        "fk_applied_energy_correction_target_species_entry_id_sp_1fa2",
        "fk_applied_energy_correction_target_species_entry_id",
    ),
    (
        "applied_energy_correction",
        "fk_applied_energy_correction_target_transition_state_en_2d20",
        "fk_applied_energy_correction_target_transition_state_entry_id",
    ),
    # applied_energy_correction_component (1 FK)
    (
        "applied_energy_correction_component",
        "fk_applied_energy_correction_component_applied_correcti_f11e",
        "fk_applied_energy_correction_component_applied_correction_id",
    ),
    # applied_group_additivity_component (1 FK)
    (
        "applied_group_additivity_component",
        "fk_applied_group_additivity_component_applied_group_add_61fe",
        "fk_applied_ga_component_applied_ga_id",
    ),
    # calc_scan_point_coordinate_value (2 composite FKs)
    (
        "calc_scan_point_coordinate_value",
        "fk_calc_scan_point_coordinate_value_calculation_id_calc_e1ec",
        "fk_cspcv_calc_id_coordinate_index",
    ),
    (
        "calc_scan_point_coordinate_value",
        "fk_calc_scan_point_coordinate_value_calculation_id_calc_fe70",
        "fk_cspcv_calc_id_point_index",
    ),
    # calculation_parameter (1 FK)
    (
        "calculation_parameter",
        "fk_calculation_parameter_canonical_key_calculation_para_5ead",
        "fk_calculation_parameter_canonical_key",
    ),
    # conformer_observation (1 FK)
    (
        "conformer_observation",
        "fk_conformer_observation_assignment_scheme_id_conformer_133b",
        "fk_conformer_observation_assignment_scheme_id",
    ),
    # conformer_selection (1 FK)
    (
        "conformer_selection",
        "fk_conformer_selection_assignment_scheme_id_conformer_a_a968",
        "fk_conformer_selection_assignment_scheme_id",
    ),
    # energy_correction_scheme_atom_param (1 FK)
    (
        "energy_correction_scheme_atom_param",
        "fk_energy_correction_scheme_atom_param_scheme_id_energy_f259",
        "fk_energy_correction_scheme_atom_param_scheme_id",
    ),
    # energy_correction_scheme_bond_param (1 FK)
    (
        "energy_correction_scheme_bond_param",
        "fk_energy_correction_scheme_bond_param_scheme_id_energy_4f68",
        "fk_energy_correction_scheme_bond_param_scheme_id",
    ),
    # energy_correction_scheme_component_param (1 FK)
    (
        "energy_correction_scheme_component_param",
        "fk_energy_correction_scheme_component_param_scheme_id_e_af8b",
        "fk_energy_correction_scheme_component_param_scheme_id",
    ),
    # frequency_scale_factor (1 FK)
    (
        "frequency_scale_factor",
        "fk_frequency_scale_factor_workflow_tool_release_id_work_0479",
        "fk_frequency_scale_factor_workflow_tool_release_id",
    ),
    # machine_review_curator_task (1 FK)
    (
        "machine_review_curator_task",
        "fk_machine_review_curator_task_source_audit_event_id_su_73be",
        "fk_machine_review_curator_task_source_audit_event_id",
    ),
    # molecular_property_observation (4 FKs)
    (
        "molecular_property_observation",
        "fk_molecular_property_observation_software_release_id_s_d355",
        "fk_molecular_property_observation_software_release_id",
    ),
    (
        "molecular_property_observation",
        "fk_molecular_property_observation_source_calculation_id_b964",
        "fk_molecular_property_observation_source_calculation_id",
    ),
    (
        "molecular_property_observation",
        "fk_molecular_property_observation_species_entry_id_spec_0300",
        "fk_molecular_property_observation_species_entry_id",
    ),
    (
        "molecular_property_observation",
        "fk_molecular_property_observation_workflow_tool_release_79e4",
        "fk_molecular_property_observation_workflow_tool_release_id",
    ),
    # network_kinetics_chebyshev (1 FK)
    (
        "network_kinetics_chebyshev",
        "fk_network_kinetics_chebyshev_network_kinetics_id_netwo_a3bf",
        "fk_network_kinetics_chebyshev_network_kinetics_id",
    ),
    # reaction_entry_structure_participant (2 FKs)
    (
        "reaction_entry_structure_participant",
        "fk_reaction_entry_structure_participant_reaction_entry__ee77",
        "fk_reaction_entry_structure_participant_reaction_entry_id",
    ),
    (
        "reaction_entry_structure_participant",
        "fk_reaction_entry_structure_participant_species_entry_i_f32c",
        "fk_reaction_entry_structure_participant_species_entry_id",
    ),
    # record_machine_review (1 FK)
    (
        "record_machine_review",
        "fk_record_machine_review_source_audit_event_id_submissi_26f4",
        "fk_record_machine_review_source_audit_event_id",
    ),
]


def upgrade() -> None:
    """Upgrade schema: rename each FK from its truncated name to the explicit."""
    for table, old_name, new_name in _RENAMES:
        op.execute(
            f'ALTER TABLE {table} '
            f'RENAME CONSTRAINT "{old_name}" TO "{new_name}"'
        )


def downgrade() -> None:
    """Downgrade schema: restore each truncated name (exact inverses)."""
    for table, old_name, new_name in _RENAMES:
        op.execute(
            f'ALTER TABLE {table} '
            f'RENAME CONSTRAINT "{new_name}" TO "{old_name}"'
        )
