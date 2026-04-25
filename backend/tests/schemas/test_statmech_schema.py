from __future__ import annotations

import pytest

from app.schemas.entities.statmech import (
    StatmechCreate,
    StatmechSourceCalculationCreate,
    StatmechTorsionCoordinateCreate,
    StatmechTorsionCoordinateUpdate,
    StatmechTorsionCreate,
)


def test_statmech_torsion_create_requires_contiguous_coordinates() -> None:
    with pytest.raises(ValueError, match="contiguously from 1..dimension"):
        StatmechTorsionCreate(
            torsion_index=1,
            dimension=2,
            coordinates=[
                StatmechTorsionCoordinateCreate(
                    coordinate_index=1,
                    atom1_index=1,
                    atom2_index=2,
                    atom3_index=3,
                    atom4_index=4,
                ),
                StatmechTorsionCoordinateCreate(
                    coordinate_index=3,
                    atom1_index=5,
                    atom2_index=6,
                    atom3_index=7,
                    atom4_index=8,
                ),
            ],
        )


def test_statmech_torsion_create_requires_coordinate_count_to_match_dimension() -> None:
    with pytest.raises(ValueError, match="must equal dimension"):
        StatmechTorsionCreate(
            torsion_index=1,
            dimension=2,
            coordinates=[
                StatmechTorsionCoordinateCreate(
                    coordinate_index=1,
                    atom1_index=1,
                    atom2_index=2,
                    atom3_index=3,
                    atom4_index=4,
                )
            ],
        )


def test_statmech_torsion_coordinate_requires_distinct_atom_indices() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        StatmechTorsionCoordinateCreate(
            coordinate_index=1,
            atom1_index=1,
            atom2_index=2,
            atom3_index=2,
            atom4_index=4,
        )


def test_statmech_torsion_coordinate_update_checks_distinct_atoms_when_complete() -> (
    None
):
    with pytest.raises(ValueError, match="must be distinct"):
        StatmechTorsionCoordinateUpdate(
            atom1_index=1,
            atom2_index=2,
            atom3_index=2,
            atom4_index=4,
        )


def test_statmech_create_supports_nested_torsions_and_source_calculations() -> None:
    statmech = StatmechCreate(
        species_entry_id=1,
        scientific_origin="computed",
        source_calculations=[
            StatmechSourceCalculationCreate(
                calculation_id=10,
                role="freq",
            )
        ],
        torsions=[
            StatmechTorsionCreate(
                torsion_index=1,
                dimension=1,
                coordinates=[
                    StatmechTorsionCoordinateCreate(
                        coordinate_index=1,
                        atom1_index=1,
                        atom2_index=2,
                        atom3_index=3,
                        atom4_index=4,
                    )
                ],
            )
        ],
    )

    assert len(statmech.source_calculations) == 1
    assert len(statmech.torsions) == 1
    assert len(statmech.torsions[0].coordinates) == 1


def test_statmech_create_rejects_duplicate_torsion_indices() -> None:
    with pytest.raises(ValueError, match="Torsion indices must be unique"):
        StatmechCreate(
            species_entry_id=1,
            scientific_origin="computed",
            torsions=[
                StatmechTorsionCreate(
                    torsion_index=1,
                    dimension=1,
                    coordinates=[
                        StatmechTorsionCoordinateCreate(
                            coordinate_index=1,
                            atom1_index=1,
                            atom2_index=2,
                            atom3_index=3,
                            atom4_index=4,
                        )
                    ],
                ),
                StatmechTorsionCreate(
                    torsion_index=1,
                    dimension=1,
                    coordinates=[
                        StatmechTorsionCoordinateCreate(
                            coordinate_index=1,
                            atom1_index=5,
                            atom2_index=6,
                            atom3_index=7,
                            atom4_index=8,
                        )
                    ],
                ),
            ],
        )


def test_statmech_create_rejects_duplicate_source_calculation_pairs() -> None:
    with pytest.raises(
        ValueError,
        match="Source calculation \\(calculation_id, role\\) pairs must be unique",
    ):
        StatmechCreate(
            species_entry_id=1,
            scientific_origin="computed",
            source_calculations=[
                StatmechSourceCalculationCreate(
                    calculation_id=10,
                    role="freq",
                ),
                StatmechSourceCalculationCreate(
                    calculation_id=10,
                    role="freq",
                ),
            ],
        )
