from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


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


def _create_geometry(connection, *, geom_hash: str, xyz_text: str) -> int:
    return connection.execute(
        text("""
            INSERT INTO geometry (natoms, geom_hash, xyz_text)
            VALUES (1, :geom_hash, :xyz_text)
            RETURNING id
            """),
        {"geom_hash": geom_hash, "xyz_text": xyz_text},
    ).scalar_one()


def _create_transition_state_entry(connection) -> int:
    reaction_id = connection.execute(text("""
            INSERT INTO chem_reaction (reversible)
            VALUES (true)
            RETURNING id
            """)).scalar_one()
    reaction_entry_id = connection.execute(
        text("""
            INSERT INTO reaction_entry (reaction_id)
            VALUES (:reaction_id)
            RETURNING id
            """),
        {"reaction_id": reaction_id},
    ).scalar_one()
    transition_state_id = connection.execute(
        text("""
            INSERT INTO transition_state (reaction_entry_id)
            VALUES (:reaction_entry_id)
            RETURNING id
            """),
        {"reaction_entry_id": reaction_entry_id},
    ).scalar_one()
    return connection.execute(
        text("""
            INSERT INTO transition_state_entry (transition_state_id, charge, multiplicity)
            VALUES (:transition_state_id, 0, 1)
            RETURNING id
            """),
        {"transition_state_id": transition_state_id},
    ).scalar_one()


def _create_species_calculation(connection) -> int:
    species_id = _create_species(connection, inchi_key=_next_inchi_key("SPCALC"))
    species_entry_id = _create_species_entry(connection, species_id)
    return connection.execute(
        text("""
            INSERT INTO calculation (type, species_entry_id)
            VALUES ('sp', :species_entry_id)
            RETURNING id
            """),
        {"species_entry_id": species_entry_id},
    ).scalar_one()


_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def _assert_integrity_error(
    connection, statement: str, params: dict | None = None
) -> None:
    savepoint = connection.begin_nested()
    try:
        with pytest.raises(IntegrityError):
            connection.execute(text(statement), params or {})
    finally:
        if savepoint.is_active:
            savepoint.rollback()


def test_species_entry_identity_rejects_duplicate_null_identity(db_conn) -> None:
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("SPECIESENTRY"))
    _create_species_entry(db_conn, species_id)

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO species_entry (species_id)
        VALUES (:species_id)
        """,
        {"species_id": species_id},
    )


def test_thermo_allows_multiple_records_per_species_entry(db_conn) -> None:
    """Thermo is a result table — multiple records per species entry are valid."""
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("THERMOMULTI"))
    species_entry_id = _create_species_entry(db_conn, species_id)

    db_conn.execute(
        text("""
            INSERT INTO thermo (species_entry_id, scientific_origin)
            VALUES (:species_entry_id, 'computed')
            """),
        {"species_entry_id": species_entry_id},
    )

    # Second insert with identical provenance tuple must succeed
    db_conn.execute(
        text("""
            INSERT INTO thermo (species_entry_id, scientific_origin)
            VALUES (:species_entry_id, 'computed')
            """),
        {"species_entry_id": species_entry_id},
    )


def test_calculation_requires_exactly_one_owner(db_conn) -> None:
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("CALCOWNER"))
    species_entry_id = _create_species_entry(db_conn, species_id)
    transition_state_entry_id = _create_transition_state_entry(db_conn)

    db_conn.execute(
        text("""
            INSERT INTO calculation (type, species_entry_id)
            VALUES ('sp', :species_entry_id)
            """),
        {"species_entry_id": species_entry_id},
    )
    db_conn.execute(
        text("""
            INSERT INTO calculation (type, transition_state_entry_id)
            VALUES ('sp', :transition_state_entry_id)
            """),
        {"transition_state_entry_id": transition_state_entry_id},
    )

    _assert_integrity_error(
        db_conn,
        "INSERT INTO calculation (type) VALUES ('sp')",
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation (type, species_entry_id, transition_state_entry_id)
        VALUES ('sp', :species_entry_id, :transition_state_entry_id)
        """,
        {
            "species_entry_id": species_entry_id,
            "transition_state_entry_id": transition_state_entry_id,
        },
    )


def test_calculation_dependency_rejects_self_edge(db_conn) -> None:
    calculation_id = _create_species_calculation(db_conn)

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role)
        VALUES (:calculation_id, :calculation_id, 'optimized_from')
        """,
        {"calculation_id": calculation_id},
    )


def test_calculation_dependency_requires_role(db_conn) -> None:
    child_id = _create_species_calculation(db_conn)
    parent_id = _create_species_calculation(db_conn)

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role)
        VALUES (:parent_id, :child_id, NULL)
        """,
        {"parent_id": parent_id, "child_id": child_id},
    )


def test_geometry_atom_requires_positive_atom_index(db_conn) -> None:
    geometry_id = _create_geometry(
        db_conn,
        geom_hash=_next_inchi_key("GEOMATOM").ljust(64, "0"),
        xyz_text="H 0.0 0.0 0.0",
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO geometry_atom (geometry_id, atom_index, element, x, y, z)
        VALUES (:geometry_id, 0, 'H', 0.0, 0.0, 0.0)
        """,
        {"geometry_id": geometry_id},
    )


def test_calculation_dependency_selected_roles_allow_at_most_one_parent(
    db_conn,
) -> None:
    child_id = _create_species_calculation(db_conn)
    first_parent_id = _create_species_calculation(db_conn)
    second_parent_id = _create_species_calculation(db_conn)

    db_conn.execute(
        text("""
            INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role)
            VALUES (:parent_id, :child_id, 'optimized_from')
            """),
        {"parent_id": first_parent_id, "child_id": child_id},
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role)
        VALUES (:parent_id, :child_id, 'optimized_from')
        """,
        {"parent_id": second_parent_id, "child_id": child_id},
    )

    other_child_id = _create_species_calculation(db_conn)
    freq_parent_1 = _create_species_calculation(db_conn)
    freq_parent_2 = _create_species_calculation(db_conn)
    db_conn.execute(
        text("""
            INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role)
            VALUES (:parent_id, :child_id, 'freq_on')
            """),
        {"parent_id": freq_parent_1, "child_id": other_child_id},
    )
    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation_dependency (parent_calculation_id, child_calculation_id, dependency_role)
        VALUES (:parent_id, :child_id, 'freq_on')
        """,
        {"parent_id": freq_parent_2, "child_id": other_child_id},
    )


def test_conformer_selection_treats_null_assignment_scheme_as_duplicate(
    db_conn,
) -> None:
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("CONFORMERSEL"))
    species_entry_id = _create_species_entry(db_conn, species_id)
    conformer_group_id = db_conn.execute(
        text("""
            INSERT INTO conformer_group (species_entry_id)
            VALUES (:species_entry_id)
            RETURNING id
            """),
        {"species_entry_id": species_entry_id},
    ).scalar_one()

    db_conn.execute(
        text("""
            INSERT INTO conformer_selection (conformer_group_id, selection_kind)
            VALUES (:conformer_group_id, 'display_default')
            """),
        {"conformer_group_id": conformer_group_id},
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO conformer_selection (conformer_group_id, selection_kind)
        VALUES (:conformer_group_id, 'display_default')
        """,
        {"conformer_group_id": conformer_group_id},
    )


def test_calculation_allows_multiple_rows_per_conformer_observation(db_conn) -> None:
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("CONFOBS"))
    species_entry_id = _create_species_entry(db_conn, species_id)
    conformer_group_id = db_conn.execute(
        text("""
            INSERT INTO conformer_group (species_entry_id)
            VALUES (:species_entry_id)
            RETURNING id
            """),
        {"species_entry_id": species_entry_id},
    ).scalar_one()
    conformer_observation_id = db_conn.execute(
        text("""
            INSERT INTO conformer_observation (conformer_group_id, scientific_origin)
            VALUES (:conformer_group_id, 'computed')
            RETURNING id
            """),
        {"conformer_group_id": conformer_group_id},
    ).scalar_one()

    db_conn.execute(
        text("""
            INSERT INTO calculation (type, species_entry_id, conformer_observation_id)
            VALUES ('opt', :species_entry_id, :conformer_observation_id)
            """),
        {
            "species_entry_id": species_entry_id,
            "conformer_observation_id": conformer_observation_id,
        },
    )
    db_conn.execute(
        text("""
            INSERT INTO calculation (type, species_entry_id, conformer_observation_id)
            VALUES ('sp', :species_entry_id, :conformer_observation_id)
            """),
        {
            "species_entry_id": species_entry_id,
            "conformer_observation_id": conformer_observation_id,
        },
    )

    count = db_conn.execute(
        text("""
            SELECT COUNT(*)
            FROM calculation
            WHERE conformer_observation_id = :conformer_observation_id
            """),
        {"conformer_observation_id": conformer_observation_id},
    ).scalar_one()
    assert count == 2


def test_calculation_input_geometry_supports_multiple_ordered_inputs(db_conn) -> None:
    calculation_id = _create_species_calculation(db_conn)
    geometry_1 = _create_geometry(
        db_conn,
        geom_hash="a" * 64,
        xyz_text="1\ngeom1\nH 0.0 0.0 0.0\n",
    )
    geometry_2 = _create_geometry(
        db_conn,
        geom_hash="b" * 64,
        xyz_text="1\ngeom2\nH 0.0 0.0 1.0\n",
    )

    db_conn.execute(
        text("""
            INSERT INTO calculation_input_geometry (calculation_id, geometry_id, input_order)
            VALUES (:calculation_id, :geometry_id, 1)
            """),
        {"calculation_id": calculation_id, "geometry_id": geometry_1},
    )
    db_conn.execute(
        text("""
            INSERT INTO calculation_input_geometry (calculation_id, geometry_id, input_order)
            VALUES (:calculation_id, :geometry_id, 2)
            """),
        {"calculation_id": calculation_id, "geometry_id": geometry_2},
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation_input_geometry (calculation_id, geometry_id, input_order)
        VALUES (:calculation_id, :geometry_id, 3)
        """,
        {"calculation_id": calculation_id, "geometry_id": geometry_1},
    )

    geometry_3 = _create_geometry(
        db_conn,
        geom_hash="c" * 64,
        xyz_text="1\ngeom3\nH 0.0 0.0 2.0\n",
    )
    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation_input_geometry (calculation_id, geometry_id, input_order)
        VALUES (:calculation_id, :geometry_id, 2)
        """,
        {"calculation_id": calculation_id, "geometry_id": geometry_3},
    )


def test_basic_positive_count_checks_reject_nonsense_rows(db_conn) -> None:
    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
        VALUES ('molecule', '[H]', :inchi_key, 0, 0, 'achiral')
        """,
        {"inchi_key": _next_inchi_key("BADMULT")},
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO geometry (natoms, geom_hash, xyz_text)
        VALUES (0, :geom_hash, '0\nempty\n')
        """,
        {"geom_hash": "d" * 64},
    )

    reaction_id = db_conn.execute(text("""
            INSERT INTO chem_reaction (reversible)
            VALUES (true)
            RETURNING id
            """)).scalar_one()
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("STOICH"))
    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO reaction_participant (reaction_id, species_id, role, stoichiometry)
        VALUES (:reaction_id, :species_id, 'reactant', 0)
        """,
        {"reaction_id": reaction_id, "species_id": species_id},
    )

    species_entry_id = _create_species_entry(db_conn, species_id)
    reaction_entry_id = db_conn.execute(
        text("""
            INSERT INTO reaction_entry (reaction_id)
            VALUES (:reaction_id)
            RETURNING id
            """),
        {"reaction_id": reaction_id},
    ).scalar_one()
    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO reaction_entry_structure_participant
            (reaction_entry_id, species_entry_id, role, participant_index)
        VALUES (:reaction_entry_id, :species_entry_id, 'reactant', 0)
        """,
        {
            "reaction_entry_id": reaction_entry_id,
            "species_entry_id": species_entry_id,
        },
    )

    calculation_id = _create_species_calculation(db_conn)
    geometry_id = _create_geometry(
        db_conn,
        geom_hash="e" * 64,
        xyz_text="1\ngeom\nH 0.0 0.0 0.0\n",
    )
    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calculation_output_geometry (calculation_id, geometry_id, output_order)
        VALUES (:calculation_id, :geometry_id, 0)
        """,
        {"calculation_id": calculation_id, "geometry_id": geometry_id},
    )


def test_thermo_nasa_requires_consistent_temperature_bounds(db_conn) -> None:
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("THERMONASA"))
    species_entry_id = _create_species_entry(db_conn, species_id)
    thermo_id = db_conn.execute(
        text("""
            INSERT INTO thermo (species_entry_id, scientific_origin)
            VALUES (:species_entry_id, 'computed')
            RETURNING id
            """),
        {"species_entry_id": species_entry_id},
    ).scalar_one()

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO thermo_nasa (thermo_id, t_mid, t_high)
        VALUES (:thermo_id, 500.0, 1000.0)
        """,
        {"thermo_id": thermo_id},
    )


def test_scan_result_tables_enforce_basic_constraints(db_conn) -> None:
    species_id = _create_species(db_conn, inchi_key=_next_inchi_key("SCANRESULT"))
    species_entry_id = _create_species_entry(db_conn, species_id)
    calculation_id = db_conn.execute(
        text("""
            INSERT INTO calculation (type, species_entry_id)
            VALUES ('scan', :species_entry_id)
            RETURNING id
            """),
        {"species_entry_id": species_entry_id},
    ).scalar_one()

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calc_scan_result (calculation_id, dimension)
        VALUES (:calculation_id, 0)
        """,
        {"calculation_id": calculation_id},
    )

    db_conn.execute(
        text("""
            INSERT INTO calc_scan_result (calculation_id, dimension)
            VALUES (:calculation_id, 1)
            """),
        {"calculation_id": calculation_id},
    )
    db_conn.execute(
        text("""
            INSERT INTO calc_scan_coordinate (
                calculation_id, coordinate_index, coordinate_kind,
                atom1_index, atom2_index, atom3_index, atom4_index
            )
            VALUES (:calculation_id, 1, 'dihedral', 1, 2, 3, 4)
            """),
        {"calculation_id": calculation_id},
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calc_scan_point (calculation_id, point_index)
        VALUES (:calculation_id, 0)
        """,
        {"calculation_id": calculation_id},
    )

    db_conn.execute(
        text("""
            INSERT INTO calc_scan_point (calculation_id, point_index)
            VALUES (:calculation_id, 1)
            """),
        {"calculation_id": calculation_id},
    )

    _assert_integrity_error(
        db_conn,
        """
        INSERT INTO calc_scan_point_coordinate_value (
            calculation_id, point_index, coordinate_index, coordinate_value
        )
        VALUES (:calculation_id, 2, 1, 45.0)
        """,
        {"calculation_id": calculation_id},
    )
