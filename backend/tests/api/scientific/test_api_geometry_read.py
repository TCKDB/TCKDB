"""API tests for ``GET /api/v1/scientific/geometries/{geometry_handle}``."""

from __future__ import annotations

from app.db.models.calculation import (
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
)
from app.db.models.geometry import GeometryAtom
from tests.services.scientific_read._factories import (
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _seed_geometry(db_session):
    geom = make_geometry(db_session, natoms=3)
    for idx, (sym, x, y, z) in enumerate(
        [("O", 0.0, 0.0, 0.0), ("H", 0.0, 0.76, 0.58), ("H", 0.0, -0.76, 0.58)],
        start=1,
    ):
        db_session.add(
            GeometryAtom(
                geometry_id=geom.id,
                atom_index=idx,
                element=sym,
                x=x,
                y=y,
                z=z,
            )
        )
    db_session.flush()
    return geom


# ---------------------------------------------------------------------------
# Happy path + path-handle inputs
# ---------------------------------------------------------------------------


def test_get_geometry_by_ref_returns_symbols_and_coords(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["geometry_ref"] == geom.public_ref
    assert body["natoms"] == 3
    assert body["format"] == "cartesian"
    assert body["coordinate_units"] == "angstrom"
    assert body["symbols"] == ["O", "H", "H"]
    assert body["coords"][0] == [0.0, 0.0, 0.0]
    assert body["geom_hash"] == geom.geom_hash
    # Phase D default: integer geometry_id is hidden.
    assert "geometry_id" not in body


def test_get_geometry_by_integer_id_still_works(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.id}")
    assert resp.status_code == 200
    body = resp.json()
    # The integer-path input is honored; the response identifies the row by ref.
    assert body["geometry_ref"] == geom.public_ref


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_get_geometry_unknown_ref_returns_404(client, db_session):
    resp = client.get(
        "/api/v1/scientific/geometries/geom_neverexistsabcdefxyzqr"
    )
    assert resp.status_code == 404
    assert "geometry not found" in resp.text


def test_get_geometry_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/geometries/spe_abcdef0123456789"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_get_geometry_malformed_handle_returns_422(client, db_session):
    resp = client.get("/api/v1/scientific/geometries/not-a-handle")
    assert resp.status_code == 422


def test_get_geometry_unknown_include_token_returns_422(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# Phase D internal-ID visibility
# ---------------------------------------------------------------------------


def test_get_geometry_default_omits_geometry_id(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    body = resp.json()
    assert "geometry_id" not in body
    # request.include echoes empty (no opt-in) and the body keeps the ref.
    assert body["request"]["include"] == []


def test_get_geometry_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    # The token is silently dropped — it doesn't appear in the echo and
    # the ID stays hidden.
    assert "internal_ids" not in body["request"]["include"]
    assert "geometry_id" not in body


def test_get_geometry_include_internal_ids_restores_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "internal_ids" in body["request"]["include"]
    assert body["geometry_id"] == geom.id


def test_get_geometry_include_all_does_not_restore_ids(client, db_session):
    geom = _seed_geometry(db_session)
    resp = client.get(
        f"/api/v1/scientific/geometries/{geom.public_ref}?include=all"
    )
    body = resp.json()
    # ``all`` does not expand to ``internal_ids``.
    assert "internal_ids" not in body["request"]["include"]
    assert "geometry_id" not in body


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_get_geometry_provenance_lists_producers_and_consumers(
    client, db_session
):
    geom = _seed_geometry(db_session)
    species = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("GP_API")
    )
    entry = make_species_entry(db_session, species)
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
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
    db_session.flush()

    resp = client.get(f"/api/v1/scientific/geometries/{geom.public_ref}")
    prov = resp.json()["provenance"]

    assert len(prov["produced_by"]) == 1
    assert prov["produced_by"][0]["calculation_ref"] == opt_calc.public_ref
    assert prov["produced_by"][0]["role"] == "final"
    assert prov["produced_by"][0]["calculation_type"] == "opt"

    assert len(prov["used_as_input_by"]) == 1
    assert (
        prov["used_as_input_by"][0]["calculation_ref"] == sp_calc.public_ref
    )
    assert prov["used_as_input_by"][0]["role"] is None
    # Phase D: integer calculation_id stripped by default in the
    # provenance sub-block too.
    assert "calculation_id" not in prov["produced_by"][0]
    assert "calculation_id" not in prov["used_as_input_by"][0]
