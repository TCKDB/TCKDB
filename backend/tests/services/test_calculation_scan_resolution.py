from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationScanResult
from app.schemas.entities.calculation import CalculationScanResultCreate
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
