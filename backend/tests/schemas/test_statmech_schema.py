from __future__ import annotations

import pytest

from app.schemas.entities.statmech import (
    StatmechCreate,
    StatmechSourceCalculationCreate,
    StatmechTorsionCoordinateIn,
    StatmechTorsionCoordinateUpdate,
    StatmechTorsionCreate,
)


def test_statmech_torsion_create_requires_contiguous_coordinates() -> None:
    with pytest.raises(ValueError, match="contiguously from 1..dimension"):
        StatmechTorsionCreate(
            torsion_index=1,
            dimension=2,
            coordinates=[
                StatmechTorsionCoordinateIn(
                    coordinate_index=1,
                    atom1_index=1,
                    atom2_index=2,
                    atom3_index=3,
                    atom4_index=4,
                ),
                StatmechTorsionCoordinateIn(
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
                StatmechTorsionCoordinateIn(
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
        StatmechTorsionCoordinateIn(
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
                    StatmechTorsionCoordinateIn(
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
                        StatmechTorsionCoordinateIn(
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
                        StatmechTorsionCoordinateIn(
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


# ---------------------------------------------------------------------------
# One concept, one component (#119)
# ---------------------------------------------------------------------------


def test_torsion_coordinate_has_exactly_one_definition() -> None:
    """The atom quartet is defined once and inherited everywhere.

    ``StatmechTorsionCoordinateIn``, ``StatmechTorsionCoordinateBase`` and
    ``StatmechTorsionCoordinateCreate`` used to be field-for-field and
    validator-for-validator identical, which a client generator turned
    into two classes for one concept. The read schema now inherits the
    one class instead of restating its fields.
    """
    from app.schemas.entities.statmech import StatmechTorsionCoordinateRead

    assert StatmechTorsionCoordinateIn in StatmechTorsionCoordinateRead.__mro__
    quartet = {
        "coordinate_index",
        "atom1_index",
        "atom2_index",
        "atom3_index",
        "atom4_index",
    }
    assert quartet <= set(StatmechTorsionCoordinateIn.model_fields)
    # The read schema adds only the parent link on top of the quartet.
    assert set(StatmechTorsionCoordinateRead.model_fields) == quartet | {"torsion_id"}
    # And declares none of the quartet itself -- inheriting it is the point.
    assert quartet.isdisjoint(
        StatmechTorsionCoordinateRead.__annotations__.keys()
    )


def test_retired_torsion_coordinate_spellings_are_gone() -> None:
    """The duplicate names must not come back as import aliases either."""
    import tckdb_schemas.statmech_bits as wire_bits

    from app.schemas.entities import statmech as entity_statmech

    for retired in (
        "StatmechTorsionCoordinateBase",
        "StatmechTorsionCoordinateCreate",
    ):
        assert not hasattr(wire_bits, retired), retired
        assert not hasattr(entity_statmech, retired), retired
