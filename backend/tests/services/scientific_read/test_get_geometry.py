"""Service-layer tests for ``get_geometry``."""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
)
from app.db.models.geometry import GeometryAtom
from app.schemas.reads.scientific_geometry import GeometryReadRequest
from app.services.scientific_read.geometry import get_geometry
from tests.services.scientific_read._factories import (
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _seed_water_geometry(db_session):
    """Build a 3-atom geometry whose coordinates look like water."""
    geom = make_geometry(db_session, natoms=3, xyz_text="O 0 0 0\nH 0 .76 .58\nH 0 -.76 .58")
    rows = [
        GeometryAtom(geometry_id=geom.id, atom_index=1, element="O", x=0.0, y=0.0, z=0.0),
        GeometryAtom(geometry_id=geom.id, atom_index=2, element="H", x=0.0, y=0.76, z=0.58),
        GeometryAtom(geometry_id=geom.id, atom_index=3, element="H", x=0.0, y=-0.76, z=0.58),
    ]
    for r in rows:
        db_session.add(r)
    db_session.flush()
    return geom


def test_get_geometry_returns_atoms_in_order(db_session):
    geom = _seed_water_geometry(db_session)

    response = get_geometry(
        db_session,
        geometry_handle=geom.public_ref,
        request=GeometryReadRequest(),
    )

    assert response.geometry_ref == geom.public_ref
    assert response.geometry_id == geom.id
    assert response.natoms == 3
    assert response.geom_hash == geom.geom_hash
    assert response.format == "cartesian"
    assert response.coordinate_units == "angstrom"
    assert response.symbols == ["O", "H", "H"]
    assert response.coords == [
        [0.0, 0.0, 0.0],
        [0.0, 0.76, 0.58],
        [0.0, -0.76, 0.58],
    ]
    assert [a.atom_index for a in response.atoms] == [1, 2, 3]


def test_get_geometry_accepts_integer_handle(db_session):
    geom = _seed_water_geometry(db_session)

    response = get_geometry(
        db_session,
        geometry_handle=str(geom.id),
        request=GeometryReadRequest(),
    )
    assert response.geometry_ref == geom.public_ref


def test_get_geometry_handle_not_found_404(db_session):
    with pytest.raises(NotFoundError, match="geometry not found"):
        get_geometry(
            db_session,
            geometry_handle="geom_neverexistsabcdefxyzqr",
            request=GeometryReadRequest(),
        )


def test_get_geometry_wrong_prefix_422(db_session):
    with pytest.raises(ValueError, match="handle_type_mismatch"):
        get_geometry(
            db_session,
            geometry_handle="spe_abcdef0123456789",
            request=GeometryReadRequest(),
        )


def test_get_geometry_malformed_handle_422(db_session):
    with pytest.raises(ValueError, match="invalid_handle"):
        get_geometry(
            db_session,
            geometry_handle="not-a-handle",
            request=GeometryReadRequest(),
        )


def test_get_geometry_provenance_lists_input_and_output_calcs(db_session):
    """Provenance surfaces every calc that produced or consumed the geometry."""
    geom = _seed_water_geometry(db_session)
    species = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("GP")
    )
    entry = make_species_entry(db_session, species)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    freq_calc = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    # opt produced the geometry (role=final); sp + freq consumed it.
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=opt_calc.id,
            geometry_id=geom.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.add(
        CalculationInputGeometry(
            calculation_id=sp_calc.id, geometry_id=geom.id, input_order=1
        )
    )
    db_session.add(
        CalculationInputGeometry(
            calculation_id=freq_calc.id, geometry_id=geom.id, input_order=1
        )
    )
    db_session.flush()

    response = get_geometry(
        db_session,
        geometry_handle=geom.public_ref,
        request=GeometryReadRequest(),
    )
    prov = response.provenance

    produced_refs = {link.calculation_ref for link in prov.produced_by}
    assert produced_refs == {opt_calc.public_ref}
    assert prov.produced_by[0].role == "final"
    assert prov.produced_by[0].calculation_type == "opt"

    consumed_refs = {link.calculation_ref for link in prov.used_as_input_by}
    assert consumed_refs == {sp_calc.public_ref, freq_calc.public_ref}
    for link in prov.used_as_input_by:
        assert link.role is None


def test_get_geometry_unknown_include_token_422(db_session):
    geom = _seed_water_geometry(db_session)
    with pytest.raises(ValueError, match="unknown_include_token"):
        get_geometry(
            db_session,
            geometry_handle=geom.public_ref,
            request=GeometryReadRequest(include=["banana"]),
        )
