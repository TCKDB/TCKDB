from __future__ import annotations

from types import SimpleNamespace

from app.schemas.entities.calculation import (
    CalculationDependencyRead,
    CalculationInputGeometryRead,
    CalculationScanResultRead,
)
from app.schemas.entities.reaction import ReactionParticipantRead
from app.schemas.entities.statmech import (
    StatmechSourceCalculationRead,
    StatmechTorsionCoordinateRead,
    StatmechTorsionRead,
)


def test_orm_read_schemas_validate_from_attributes() -> None:
    reaction_participant = SimpleNamespace(
        reaction_id=1,
        species_id=2,
        role="reactant",
        stoichiometry=1,
    )
    assert ReactionParticipantRead.model_validate(reaction_participant).species_id == 2

    statmech_source = SimpleNamespace(
        statmech_id=3,
        calculation_id=4,
        role="freq",
    )
    assert (
        StatmechSourceCalculationRead.model_validate(statmech_source).statmech_id == 3
    )

    statmech_coordinate = SimpleNamespace(
        torsion_id=5,
        coordinate_index=1,
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
        atom4_index=4,
    )
    assert (
        StatmechTorsionCoordinateRead.model_validate(statmech_coordinate).torsion_id
        == 5
    )

    statmech_torsion = SimpleNamespace(
        id=10,
        created_at="2024-01-01T00:00:00",
        created_by=None,
        statmech_id=11,
        torsion_index=1,
        symmetry_number=1,
        treatment_kind=None,
        dimension=1,
        top_description=None,
        invalidated_reason=None,
        note=None,
        source_scan_calculation_id=None,
        coordinates=[statmech_coordinate],
    )
    validated_torsion = StatmechTorsionRead.model_validate(statmech_torsion)
    assert len(validated_torsion.coordinates) == 1
    assert validated_torsion.coordinates[0].coordinate_index == 1

    calculation_input = SimpleNamespace(
        calculation_id=6,
        geometry_id=7,
        input_order=1,
    )
    assert (
        CalculationInputGeometryRead.model_validate(calculation_input).geometry_id == 7
    )

    calculation_dependency = SimpleNamespace(
        parent_calculation_id=8,
        child_calculation_id=9,
        dependency_role="freq_on",
    )
    assert (
        CalculationDependencyRead.model_validate(
            calculation_dependency
        ).dependency_role.value
        == "freq_on"
    )

    scan_coordinate = SimpleNamespace(
        calculation_id=12,
        coordinate_index=1,
        coordinate_kind="dihedral",
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
        atom4_index=4,
        step_count=None,
        step_size=None,
        resolution_degrees=15.0,
        symmetry_number=3,
    )
    scan_constraint = SimpleNamespace(
        calculation_id=12,
        constraint_index=1,
        constraint_kind="bond",
        atom1_index=1,
        atom2_index=2,
        atom3_index=None,
        atom4_index=None,
        target_value=1.23,
    )
    scan_point_coordinate_value = SimpleNamespace(
        calculation_id=12,
        point_index=1,
        coordinate_index=1,
        coordinate_value=60.0,
        value_unit=None,
    )
    scan_point = SimpleNamespace(
        calculation_id=12,
        point_index=1,
        electronic_energy_hartree=-1.0,
        relative_energy_kj_mol=0.0,
        geometry_id=None,
        note=None,
        coordinate_values=[scan_point_coordinate_value],
    )
    scan_result = SimpleNamespace(
        calculation_id=12,
        dimension=1,
        is_relaxed=True,
        zero_energy_reference_hartree=0.0,
        note=None,
        coordinates=[scan_coordinate],
        constraints=[scan_constraint],
        points=[scan_point],
    )
    validated_scan_result = CalculationScanResultRead.model_validate(scan_result)
    assert len(validated_scan_result.coordinates) == 1
    assert len(validated_scan_result.constraints) == 1
    assert len(validated_scan_result.points) == 1
