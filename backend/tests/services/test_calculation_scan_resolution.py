from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationScanPoint, CalculationScanResult
from app.db.models.geometry import Geometry
from app.schemas.entities.calculation import (
    CalculationScanPointCreate,
    CalculationScanResultCreate,
)
from app.services.calculation_scan_resolution import persist_calculation_scan


def _create_species(connection, *, inchi_key: str, smiles: str = "[H]") -> int:
    return connection.execute(
        text("""
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """),
        {"smiles": smiles, "inchi_key": inchi_key},
    ).scalar_one()


def _create_species_entry(connection, species_id: int) -> int:
    return connection.execute(
        text("""
            INSERT INTO species_entry (species_id)
            VALUES (:species_id)
            RETURNING id
            """),
        {"species_id": species_id},
    ).scalar_one()


def _create_scan_calculation(connection, *, inchi_key: str) -> int:
    species_id = _create_species(connection, inchi_key=inchi_key)
    species_entry_id = _create_species_entry(connection, species_id)
    return connection.execute(
        text("""
            INSERT INTO calculation (type, species_entry_id)
            VALUES ('scan', :species_entry_id)
            RETURNING id
            """),
        {"species_entry_id": species_entry_id},
    ).scalar_one()


def test_persist_calculation_scan_persists_nested_scan_rows(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            calculation_id = _create_scan_calculation(
                session.connection(),
                inchi_key="SCANCALC0000000000000000001",
            )

            scan = persist_calculation_scan(
                session,
                calculation_id,
                CalculationScanResultCreate(
                    dimension=1,
                    is_relaxed=True,
                    coordinates=[
                        {
                            "coordinate_index": 1,
                            "coordinate_kind": "dihedral",
                            "atom1_index": 1,
                            "atom2_index": 2,
                            "atom3_index": 3,
                            "atom4_index": 4,
                            "resolution_degrees": 15.0,
                            "symmetry_number": 3,
                        }
                    ],
                    constraints=[
                        {
                            "constraint_index": 1,
                            "constraint_kind": "bond",
                            "atom1_index": 1,
                            "atom2_index": 2,
                            "target_value": 1.23,
                        }
                    ],
                    points=[
                        {
                            "point_index": 1,
                            "electronic_energy_hartree": -1.0,
                            "coordinate_values": [
                                {
                                    "coordinate_index": 1,
                                    "coordinate_value": 60.0,
                                }
                            ],
                        }
                    ],
                ),
            )

            stored = session.scalar(
                select(CalculationScanResult).where(
                    CalculationScanResult.calculation_id == scan.calculation_id
                )
            )
            assert stored is not None
            assert stored.dimension == 1
            assert len(stored.calculation.scan_coordinates) == 1
            assert len(stored.calculation.constraints) == 1
            assert (
                stored.calculation.constraints[0].constraint_kind.value == "bond"
            )
            assert len(stored.calculation.scan_points) == 1
            assert len(stored.calculation.scan_points[0].coordinate_values) == 1


def _xyz(z: float) -> str:
    return f"1\nscan-point\nH 0.0 0.0 {z:.3f}"


def _scan_payload_with_point_geometries(*, geometries: list[str | None]) -> dict:
    """Build a 1D scan payload whose points carry the supplied per-point
    inline ``geometry`` payloads. ``None`` entries leave the point with
    no geometry attached.
    """
    points = []
    for index, xyz in enumerate(geometries, start=1):
        point: dict = {
            "point_index": index,
            "electronic_energy_hartree": -1.0 - 1e-3 * index,
            "coordinate_values": [
                {"coordinate_index": 1, "coordinate_value": float(index) * 60.0},
            ],
        }
        if xyz is not None:
            point["geometry"] = {"xyz_text": xyz}
        points.append(point)

    return {
        "dimension": 1,
        "is_relaxed": True,
        "coordinates": [
            {
                "coordinate_index": 1,
                "coordinate_kind": "dihedral",
                "atom1_index": 1,
                "atom2_index": 2,
                "atom3_index": 3,
                "atom4_index": 4,
            }
        ],
        "points": points,
    }


def test_persist_calculation_scan_resolves_inline_point_geometry(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            calculation_id = _create_scan_calculation(
                session.connection(),
                inchi_key="SCANGEOM000000000000000001",
            )

            persist_calculation_scan(
                session,
                calculation_id,
                CalculationScanResultCreate(
                    **_scan_payload_with_point_geometries(
                        geometries=[_xyz(0.10), _xyz(0.20), _xyz(0.30)],
                    )
                ),
            )

            points = session.scalars(
                select(CalculationScanPoint)
                .where(CalculationScanPoint.calculation_id == calculation_id)
                .order_by(CalculationScanPoint.point_index)
            ).all()
            assert [p.point_index for p in points] == [1, 2, 3]
            assert all(p.geometry_id is not None for p in points)
            # All three resolved geometries must be distinct rows.
            assert len({p.geometry_id for p in points}) == 3


def test_persist_calculation_scan_dedupes_repeated_inline_geometry(db_engine) -> None:
    """Two scan points with the same XYZ collapse to one geometry row."""
    with Session(db_engine) as session:
        with session.begin():
            calculation_id = _create_scan_calculation(
                session.connection(),
                inchi_key="SCANGEOM000000000000000002",
            )

            same_xyz = _xyz(0.42)
            persist_calculation_scan(
                session,
                calculation_id,
                CalculationScanResultCreate(
                    **_scan_payload_with_point_geometries(
                        geometries=[same_xyz, same_xyz],
                    )
                ),
            )

            points = session.scalars(
                select(CalculationScanPoint)
                .where(CalculationScanPoint.calculation_id == calculation_id)
                .order_by(CalculationScanPoint.point_index)
            ).all()
            assert len(points) == 2
            assert points[0].geometry_id is not None
            assert points[0].geometry_id == points[1].geometry_id

            geom_count = session.scalar(
                select(Geometry.id).where(Geometry.id == points[0].geometry_id)
            )
            assert geom_count is not None


def test_persist_calculation_scan_allows_points_without_geometry(db_engine) -> None:
    """Mixed geometry-present / geometry-absent points: absent stays NULL."""
    with Session(db_engine) as session:
        with session.begin():
            calculation_id = _create_scan_calculation(
                session.connection(),
                inchi_key="SCANGEOM000000000000000003",
            )

            persist_calculation_scan(
                session,
                calculation_id,
                CalculationScanResultCreate(
                    **_scan_payload_with_point_geometries(
                        geometries=[_xyz(0.10), None, _xyz(0.30)],
                    )
                ),
            )

            points = session.scalars(
                select(CalculationScanPoint)
                .where(CalculationScanPoint.calculation_id == calculation_id)
                .order_by(CalculationScanPoint.point_index)
            ).all()
            assert points[0].geometry_id is not None
            assert points[1].geometry_id is None
            assert points[2].geometry_id is not None


def test_calculation_scan_point_rejects_geometry_with_geometry_id() -> None:
    """``geometry`` and ``geometry_id`` are mutually exclusive at the schema layer."""
    with pytest.raises(
        ValueError,
        match="either 'geometry' .* or 'geometry_id'",
    ):
        CalculationScanPointCreate(
            point_index=1,
            geometry={"xyz_text": _xyz(0.10)},
            geometry_id=42,
            coordinate_values=[
                {"coordinate_index": 1, "coordinate_value": 0.0},
            ],
        )


def test_calculation_scan_create_rejects_unknown_coordinate_reference() -> None:
    with pytest.raises(
        ValueError,
        match="must reference defined scan coordinates",
    ):
        CalculationScanResultCreate(
            dimension=1,
            coordinates=[
                {
                    "coordinate_index": 1,
                    "coordinate_kind": "dihedral",
                    "atom1_index": 1,
                    "atom2_index": 2,
                    "atom3_index": 3,
                    "atom4_index": 4,
                }
            ],
            points=[
                {
                    "point_index": 1,
                    "coordinate_values": [
                        {
                            "coordinate_index": 2,
                            "coordinate_value": 60.0,
                        }
                    ],
                }
            ],
        )


def test_persist_scan_persists_stepped_and_held_fixed_coords_together(db_engine) -> None:
    """A scan can carry both a stepped coordinate and held-fixed
    constraints; the two land in distinct tables and do not duplicate.
    """
    with Session(db_engine) as session:
        with session.begin():
            calculation_id = _create_scan_calculation(
                session.connection(),
                inchi_key="SCANBOTH00000000000000001",
            )

            scan = persist_calculation_scan(
                session,
                calculation_id,
                CalculationScanResultCreate(
                    dimension=1,
                    is_relaxed=True,
                    coordinates=[
                        {
                            "coordinate_index": 1,
                            "coordinate_kind": "dihedral",
                            "atom1_index": 1,
                            "atom2_index": 2,
                            "atom3_index": 3,
                            "atom4_index": 4,
                        }
                    ],
                    constraints=[
                        {
                            "constraint_index": 1,
                            "constraint_kind": "bond",
                            "atom1_index": 5,
                            "atom2_index": 6,
                            "target_value": 1.20,
                        },
                        {
                            "constraint_index": 2,
                            "constraint_kind": "angle",
                            "atom1_index": 5,
                            "atom2_index": 6,
                            "atom3_index": 7,
                            "target_value": 109.5,
                        },
                    ],
                    points=[
                        {
                            "point_index": 1,
                            "electronic_energy_hartree": -1.0,
                            "coordinate_values": [
                                {
                                    "coordinate_index": 1,
                                    "coordinate_value": 60.0,
                                }
                            ],
                        }
                    ],
                ),
            )

            assert len(scan.calculation.scan_coordinates) == 1
            assert scan.calculation.scan_coordinates[0].coordinate_kind.value == (
                "dihedral"
            )

            held_fixed = sorted(
                scan.calculation.constraints, key=lambda c: c.constraint_index
            )
            assert len(held_fixed) == 2
            assert [c.constraint_kind.value for c in held_fixed] == ["bond", "angle"]

            scanned_atoms = {
                scan.calculation.scan_coordinates[0].atom1_index,
                scan.calculation.scan_coordinates[0].atom2_index,
                scan.calculation.scan_coordinates[0].atom3_index,
                scan.calculation.scan_coordinates[0].atom4_index,
            }
            for c in held_fixed:
                fixed_atoms = {
                    c.atom1_index,
                    c.atom2_index,
                    c.atom3_index,
                    c.atom4_index,
                } - {None}
                # Held-fixed coordinates here intentionally use a disjoint
                # atom set from the scanned dihedral; the schema does not
                # require disjointness, but disjoint atoms are the
                # canonical case for a relaxed scan with frozen
                # bystanders.
                assert fixed_atoms.isdisjoint(scanned_atoms)
