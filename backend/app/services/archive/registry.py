"""Fail-closed table and column registry for ``tckdb.archive.v1``.

The archive is lossless for the declared scientific/provenance/curation
surface.  Authentication credentials and ephemeral request/worker state are
not part of that surface.  Every SQLAlchemy table must be classified here;
``validate_registry`` deliberately fails when a model is added without an
archive decision.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import MetaData, Table

from app.schemas.reaction_family import CANONICAL_REACTION_FAMILIES

INCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "app_user",
        "applied_energy_correction",
        "applied_energy_correction_component",
        "applied_group_additivity",
        "applied_group_additivity_component",
        "author",
        "calc_freq_mode",
        "calc_freq_result",
        "calc_geometry_validation",
        "calc_hessian",
        "calc_irc_point",
        "calc_irc_result",
        "calc_opt_result",
        "calc_path_search_point",
        "calc_path_search_result",
        "calc_scan_coordinate",
        "calc_scan_point",
        "calc_scan_point_coordinate_value",
        "calc_scan_result",
        "calc_scf_stability",
        "calc_sp_result",
        "calc_spin_diagnostic",
        "calc_wavefunction_diagnostic",
        "calculation",
        "calculation_artifact",
        "calculation_constraint",
        "calculation_dependency",
        "calculation_input_geometry",
        "calculation_output_geometry",
        "calculation_parameter",
        "calculation_parameter_vocab",
        "chem_reaction",
        "conformer_assignment_scheme",
        "conformer_group",
        "conformer_observation",
        "conformer_selection",
        "energy_correction_scheme",
        "energy_correction_scheme_atom_param",
        "energy_correction_scheme_bond_param",
        "energy_correction_scheme_component_param",
        "frequency_scale_factor",
        "geometry",
        "geometry_atom",
        "group_additivity_scheme",
        "kinetics",
        "kinetics_arrhenius_entry",
        "kinetics_chebyshev",
        "kinetics_falloff",
        "kinetics_plog",
        "kinetics_source_calculation",
        "kinetics_third_body_efficiency",
        "level_of_theory",
        "literature",
        "literature_author",
        "machine_review_curator_task",
        "molecular_property_observation",
        "network",
        "network_channel",
        "network_kinetics",
        "network_kinetics_chebyshev",
        "network_kinetics_plog",
        "network_kinetics_point",
        "network_reaction",
        "network_solve",
        "network_solve_bath_gas",
        "network_solve_energy_transfer",
        "network_solve_source_calculation",
        "network_species",
        "network_state",
        "network_state_participant",
        "reaction_entry",
        "reaction_entry_structure_participant",
        "reaction_family",
        "reaction_participant",
        "record_machine_review",
        "record_reproducibility_assessment",
        "record_review",
        "record_review_event",
        "software",
        "software_release",
        "scientific_record_supersession",
        "species",
        "species_entry",
        "species_entry_review",
        "statmech",
        "statmech_electronic_level",
        "statmech_source_calculation",
        "statmech_torsion",
        "statmech_torsion_definition",
        "submission",
        "submission_audit_event",
        "submission_record_link",
        "thermo",
        "thermo_nasa",
        "thermo_nasa9_interval",
        "thermo_point",
        "thermo_source_calculation",
        "thermo_wilhoit",
        "transition_state",
        "transition_state_entry",
        "transition_state_selection",
        "transport",
        "transport_source_calculation",
        "workflow_tool",
        "workflow_tool_release",
    }
)


EXCLUDED_TABLES: Mapping[str, str] = {
    "api_key": "authentication credential state",
    "idempotency_record": "ephemeral HTTP retry state",
    "upload_job": "ephemeral worker queue state",
    "user_session": "authentication session state",
}


# These nullable deployment-local fields are intentionally absent from row
# payloads.  Their omission is recorded in the manifest and applied on restore.
EXCLUDED_COLUMNS: Mapping[str, Mapping[str, str]] = {
    "app_user": {
        "password_hash": "authentication credential state",
    },
    "submission": {
        "upload_job_id": "references excluded ephemeral worker queue state",
    },
}


# A freshly migrated database intentionally contains these identity rows.
# Restore accepts exactly this target-side set, removes it, and then restores
# the archived rows. Other included tables must be empty.
PRESEEDED_TABLES: Mapping[
    str,
    tuple[tuple[str, ...], frozenset[tuple[str, ...]]],
] = {
    "reaction_family": (
        ("name",),
        frozenset((name,) for name in CANONICAL_REACTION_FAMILIES),
    ),
    "calculation_parameter_vocab": (
        ("canonical_key",),
        frozenset(
            (key,)
            for key in (
                "grid.quality",
                "guess.strategy",
                "integral.accuracy",
                "internal_option.iop",
                "memory.maxcore_mb",
                "memory.raw",
                "opt.convergence",
                "opt.eigen_test",
                "opt.initial_hessian",
                "opt.max_cycles",
                "opt.max_step",
                "opt.saddle_order",
                "output.verbosity",
                "parallel.nproc",
                "parallel.nproc_shared",
                "pno.truncation",
                "scf.convergence",
                "scf.convergence_failure_action",
                "scf.convergence_failure_ignored",
                "scf.direct",
                "scf.fallback",
                "scf.max_cycles",
                "symmetry.disabled",
            )
        ),
    ),
    "conformer_assignment_scheme": (
        ("name", "version"),
        frozenset({("torsion_basin", "v1")}),
    ),
}


class ArchiveRegistryError(RuntimeError):
    """The ORM metadata and the archive registry disagree."""


def validate_registry(metadata: MetaData) -> None:
    """Require an explicit, non-overlapping decision for every ORM table."""

    actual = set(metadata.tables)
    included = set(INCLUDED_TABLES)
    excluded = set(EXCLUDED_TABLES)
    overlap = included & excluded
    missing = actual - included - excluded
    unknown = (included | excluded) - actual

    errors: list[str] = []
    if overlap:
        errors.append(f"both included and excluded: {sorted(overlap)}")
    if missing:
        errors.append(f"unclassified ORM tables: {sorted(missing)}")
    if unknown:
        errors.append(f"registry names absent from ORM metadata: {sorted(unknown)}")

    for table_name, columns in EXCLUDED_COLUMNS.items():
        table = metadata.tables.get(table_name)
        if table is None:
            errors.append(f"excluded-column table absent from metadata: {table_name}")
            continue
        unknown_columns = set(columns) - set(table.columns.keys())
        if unknown_columns:
            errors.append(f"excluded columns absent from {table_name}: {sorted(unknown_columns)}")

    for table_name, (identity_columns, _identities) in PRESEEDED_TABLES.items():
        table = metadata.tables.get(table_name)
        if table is None or table_name not in included:
            errors.append(f"preseeded table absent from included metadata: {table_name}")
            continue
        unknown_columns = set(identity_columns) - set(table.columns.keys())
        if unknown_columns:
            errors.append(f"preseed identity columns absent from {table_name}: {sorted(unknown_columns)}")

    if errors:
        raise ArchiveRegistryError("; ".join(errors))


def included_tables_in_fk_order(metadata: MetaData) -> list[Table]:
    """Return tables in FK- and accepted-science-trigger-safe order."""

    validate_registry(metadata)
    deferred_curation = (
        "record_review",
        "record_review_event",
        "scientific_record_supersession",
    )
    tables = [table for table in metadata.sorted_tables if table.name in INCLUDED_TABLES]
    by_name = {table.name: table for table in tables}
    return [table for table in tables if table.name not in deferred_curation] + [
        by_name[name] for name in deferred_curation
    ]


def included_column_names(table: Table) -> list[str]:
    """Return stored columns for ``table`` after declared safe exclusions."""

    excluded = EXCLUDED_COLUMNS.get(table.name, {})
    return [column.name for column in table.columns if column.name not in excluded]


__all__ = [
    "EXCLUDED_COLUMNS",
    "EXCLUDED_TABLES",
    "INCLUDED_TABLES",
    "PRESEEDED_TABLES",
    "ArchiveRegistryError",
    "included_column_names",
    "included_tables_in_fk_order",
    "validate_registry",
]
