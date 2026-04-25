from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.calculation import (
    Calculation,
    CalculationConstraint,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
    CalculationScanResult,
)
from app.schemas.entities.calculation import CalculationScanResultCreate


def persist_calculation_scan(
    session: Session,
    calculation_id: int,
    payload: CalculationScanResultCreate,
) -> CalculationScanResult:
    """Persist a full scan-result bundle for one calculation.

    :param session: Active SQLAlchemy session.
    :param calculation_id: Parent calculation id taken from the route or workflow context.
    :param payload: Resource-shaped scan payload including coordinates, constraints, and points.
    :returns: Newly created ``CalculationScanResult`` row.
    :raises ValueError: If the calculation does not exist or already has a scan result.
    """

    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise ValueError(f"Unknown calculation_id={calculation_id}")
    if calculation.scan_result is not None:
        raise ValueError(f"Calculation {calculation_id} already has a scan result.")

    scan_result = CalculationScanResult(
        calculation_id=calculation_id,
        dimension=payload.dimension,
        is_relaxed=payload.is_relaxed,
        zero_energy_reference_hartree=payload.zero_energy_reference_hartree,
        note=payload.note,
    )
    session.add(scan_result)

    for coordinate in payload.coordinates:
        session.add(
            CalculationScanCoordinate(
                calculation_id=calculation_id,
                coordinate_index=coordinate.coordinate_index,
                coordinate_kind=coordinate.coordinate_kind,
                atom1_index=coordinate.atom1_index,
                atom2_index=coordinate.atom2_index,
                atom3_index=coordinate.atom3_index,
                atom4_index=coordinate.atom4_index,
                step_count=coordinate.step_count,
                step_size=coordinate.step_size,
                start_value=coordinate.start_value,
                end_value=coordinate.end_value,
                value_unit=coordinate.value_unit,
                resolution_degrees=coordinate.resolution_degrees,
                symmetry_number=coordinate.symmetry_number,
            )
        )

    for constraint in payload.constraints:
        session.add(
            CalculationConstraint(
                calculation_id=calculation_id,
                constraint_index=constraint.constraint_index,
                constraint_kind=constraint.constraint_kind,
                atom1_index=constraint.atom1_index,
                atom2_index=constraint.atom2_index,
                atom3_index=constraint.atom3_index,
                atom4_index=constraint.atom4_index,
                target_value=constraint.target_value,
            )
        )

    for point in payload.points:
        session.add(
            CalculationScanPoint(
                calculation_id=calculation_id,
                point_index=point.point_index,
                electronic_energy_hartree=point.electronic_energy_hartree,
                relative_energy_kj_mol=point.relative_energy_kj_mol,
                geometry_id=point.geometry_id,
                note=point.note,
            )
        )
        for coordinate_value in point.coordinate_values:
            session.add(
                CalculationScanPointCoordinateValue(
                    calculation_id=calculation_id,
                    point_index=point.point_index,
                    coordinate_index=coordinate_value.coordinate_index,
                    coordinate_value=coordinate_value.coordinate_value,
                    value_unit=coordinate_value.value_unit,
                )
            )

    session.flush()
    return scan_result
