"""API tests for the specialized scientific calculation path-data
endpoints.

Covers:

- ``GET /api/v1/scientific/calculations/{calculation_ref_or_id}/scan``
- ``GET /api/v1/scientific/calculations/{calculation_ref_or_id}/irc``
- ``GET /api/v1/scientific/calculations/{calculation_ref_or_id}/path-search``

All three share the same path-data contract pattern (paginated points,
shared geometry-link policy, shared internal-id visibility policy).
"""

from __future__ import annotations

from app.db.models.calculation import (
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
    CalculationScanResult,
)
from app.db.models.common import (
    CalculationType,
    CoordinateUnit,
    IRCDirection,
    PathSearchMethod,
    ScanCoordinateKind,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


def _make_species_owned_calc(db_session, calc_type=CalculationType.scan):
    species = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("PATH")
    )
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session, type=calc_type, species_entry_id=entry.id
    )
    return species, entry, calc


def _make_scan_calc(
    db_session,
    *,
    dimension: int = 1,
    is_relaxed: bool = True,
    note: str | None = None,
):
    _, _, calc = _make_species_owned_calc(db_session)
    db_session.add(
        CalculationScanResult(
            calculation_id=calc.id,
            dimension=dimension,
            is_relaxed=is_relaxed,
            zero_energy_reference_hartree=-100.0,
            note=note,
        )
    )
    db_session.flush()
    return calc


def _attach_scan_coordinate(
    db_session,
    *,
    calculation,
    coordinate_index: int,
    coordinate_kind: ScanCoordinateKind = ScanCoordinateKind.bond,
    atom1_index: int = 1,
    atom2_index: int = 2,
    atom3_index: int | None = None,
    atom4_index: int | None = None,
    step_count: int | None = None,
    step_size: float | None = None,
    start_value: float | None = None,
    end_value: float | None = None,
    value_unit: CoordinateUnit | None = None,
):
    row = CalculationScanCoordinate(
        calculation_id=calculation.id,
        coordinate_index=coordinate_index,
        coordinate_kind=coordinate_kind,
        atom1_index=atom1_index,
        atom2_index=atom2_index,
        atom3_index=atom3_index,
        atom4_index=atom4_index,
        step_count=step_count,
        step_size=step_size,
        start_value=start_value,
        end_value=end_value,
        value_unit=value_unit,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _attach_scan_point(
    db_session,
    *,
    calculation,
    point_index: int,
    electronic_energy_hartree: float | None = None,
    relative_energy_kj_mol: float | None = None,
    geometry=None,
    note: str | None = None,
):
    row = CalculationScanPoint(
        calculation_id=calculation.id,
        point_index=point_index,
        electronic_energy_hartree=electronic_energy_hartree,
        relative_energy_kj_mol=relative_energy_kj_mol,
        geometry_id=geometry.id if geometry is not None else None,
        note=note,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _attach_scan_point_coordinate_value(
    db_session,
    *,
    calculation,
    point_index: int,
    coordinate_index: int,
    coordinate_value: float,
    value_unit: CoordinateUnit | None = None,
):
    row = CalculationScanPointCoordinateValue(
        calculation_id=calculation.id,
        point_index=point_index,
        coordinate_index=coordinate_index,
        coordinate_value=coordinate_value,
        value_unit=value_unit,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_one_dim_scan(db_session, *, n_points: int = 3):
    """Build a 1-D bond scan with *n_points* points + one geometry.

    Returns ``(calc, geometry, energies)`` so tests can assert on
    the seeded values.
    """
    calc = _make_scan_calc(db_session, dimension=1, note="test scan")
    _attach_scan_coordinate(
        db_session,
        calculation=calc,
        coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond,
        atom1_index=1,
        atom2_index=2,
        step_count=n_points,
        step_size=0.1,
        start_value=0.8,
        end_value=0.8 + 0.1 * (n_points - 1),
        value_unit=CoordinateUnit.angstrom,
    )
    geom = make_geometry(db_session, natoms=3)
    energies = []
    for i in range(1, n_points + 1):
        energy = -99.0 - i * 0.1
        energies.append(energy)
        _attach_scan_point(
            db_session,
            calculation=calc,
            point_index=i,
            electronic_energy_hartree=energy,
            relative_energy_kj_mol=float(i) * 5.0,
            geometry=geom if i == 1 else None,
        )
        _attach_scan_point_coordinate_value(
            db_session,
            calculation=calc,
            point_index=i,
            coordinate_index=1,
            coordinate_value=0.8 + 0.1 * (i - 1),
            value_unit=CoordinateUnit.angstrom,
        )
    return calc, geom, energies


def _scan_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/calculations/{handle}/scan"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# Happy-path + handle resolution
# ---------------------------------------------------------------------------


def test_scan_endpoint_by_calculation_ref_returns_200(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=3)
    resp = client.get(_scan_url(calc.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["calculation"]["calculation_ref"] == calc.public_ref


def test_scan_endpoint_by_integer_id_works(client, db_session):
    """Integer-id path is accepted; response identifies the row by ref."""
    calc, _, _ = _seed_one_dim_scan(db_session)
    resp = client.get(_scan_url(str(calc.id)))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["calculation"]["calculation_ref"] == calc.public_ref


def test_scan_endpoint_unknown_calculation_returns_404(client, db_session):
    resp = client.get(
        "/api/v1/scientific/calculations/calc_doesnotexist000/scan"
    )
    assert resp.status_code == 404
    assert "calculation not found" in resp.text.lower()


def test_scan_endpoint_wrong_prefix_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/calculations/spe_abcdef0123456789/scan"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_scan_endpoint_malformed_handle_returns_422(client, db_session):
    resp = client.get("/api/v1/scientific/calculations/not-a-handle/scan")
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_scan_endpoint_no_scan_result_returns_404(client, db_session):
    """A calc that exists but has no calc_scan_result row → 404
    ``scan_result_not_found``."""
    _, _, calc = _make_species_owned_calc(db_session, calc_type=CalculationType.opt)
    resp = client.get(_scan_url(calc.public_ref))
    assert resp.status_code == 404
    assert "scan_result_not_found" in resp.text


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_scan_response_includes_core_block_with_review_badge(
    client, db_session
):
    calc, _, _ = _seed_one_dim_scan(db_session)
    body = client.get(_scan_url(calc.public_ref)).json()
    core = body["calculation"]
    assert core["calculation_ref"] == calc.public_ref
    assert core["type"] == "scan"
    # Compact review badge always present.
    assert "review" in core
    assert core["review"]["status"] == "not_reviewed"


def test_scan_response_includes_owner_summary(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session)
    body = client.get(_scan_url(calc.public_ref)).json()
    owner = body["owner"]
    assert owner["kind"] == "species_entry"
    assert "species_entry" in owner
    # Phase D default: integer ids stripped from owner block.
    assert "species_entry_id" not in owner["species_entry"]


def test_scan_response_includes_scan_summary(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=4)
    body = client.get(_scan_url(calc.public_ref)).json()
    scan = body["scan"]
    assert scan["dimension"] == 1
    assert scan["coordinate_count"] == 1
    assert scan["point_count"] == 4
    assert scan["min_electronic_energy_hartree"] is not None
    assert scan["max_electronic_energy_hartree"] is not None


def test_scan_response_includes_coordinates(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session)
    body = client.get(_scan_url(calc.public_ref)).json()
    coords = body["coordinates"]
    assert len(coords) == 1
    assert coords[0]["coordinate_kind"] == "bond"
    assert coords[0]["atom1_index"] == 1
    assert coords[0]["atom2_index"] == 2
    assert coords[0]["value_unit"] == "angstrom"


def test_scan_response_includes_paginated_points(client, db_session):
    calc, _, energies = _seed_one_dim_scan(db_session, n_points=3)
    body = client.get(_scan_url(calc.public_ref)).json()
    points = body["points"]
    assert len(points) == 3
    # Ordered by point_index ASC.
    assert [p["point_index"] for p in points] == [1, 2, 3]
    assert [p["electronic_energy_hartree"] for p in points] == energies


def test_scan_response_includes_point_coordinate_values(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=2)
    body = client.get(_scan_url(calc.public_ref)).json()
    p1 = body["points"][0]
    assert p1["coordinate_values"] == [
        {
            "coordinate_index": 1,
            "coordinate_value": 0.8,
            "value_unit": "angstrom",
        }
    ]


# ---------------------------------------------------------------------------
# Ordering + pagination
# ---------------------------------------------------------------------------


def test_scan_coordinates_ordered_by_coordinate_index(client, db_session):
    calc = _make_scan_calc(db_session, dimension=3)
    # Insert out of declaration order.
    _attach_scan_coordinate(
        db_session, calculation=calc, coordinate_index=3,
        coordinate_kind=ScanCoordinateKind.bond, atom1_index=1, atom2_index=2,
    )
    _attach_scan_coordinate(
        db_session, calculation=calc, coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond, atom1_index=3, atom2_index=4,
    )
    _attach_scan_coordinate(
        db_session, calculation=calc, coordinate_index=2,
        coordinate_kind=ScanCoordinateKind.angle,
        atom1_index=1, atom2_index=2, atom3_index=3,
    )
    body = client.get(_scan_url(calc.public_ref)).json()
    indices = [c["coordinate_index"] for c in body["coordinates"]]
    assert indices == [1, 2, 3]


def test_scan_points_ordered_by_point_index(client, db_session):
    calc = _make_scan_calc(db_session)
    _attach_scan_coordinate(
        db_session, calculation=calc, coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond, atom1_index=1, atom2_index=2,
    )
    # Insert out of order.
    for idx in (3, 1, 2):
        _attach_scan_point(
            db_session, calculation=calc, point_index=idx,
            electronic_energy_hartree=-99.0 - idx * 0.1,
        )
    body = client.get(_scan_url(calc.public_ref)).json()
    assert [p["point_index"] for p in body["points"]] == [1, 2, 3]


def test_scan_point_coordinate_values_ordered_by_coordinate_index(
    client, db_session
):
    calc = _make_scan_calc(db_session, dimension=3)
    for ci in (3, 1, 2):
        _attach_scan_coordinate(
            db_session, calculation=calc, coordinate_index=ci,
            coordinate_kind=ScanCoordinateKind.bond,
            atom1_index=1, atom2_index=2,
        )
    _attach_scan_point(
        db_session, calculation=calc, point_index=1,
        electronic_energy_hartree=-99.0,
    )
    # Insert coordinate values out of order.
    for ci, val in ((3, 0.3), (1, 0.1), (2, 0.2)):
        _attach_scan_point_coordinate_value(
            db_session, calculation=calc, point_index=1,
            coordinate_index=ci, coordinate_value=val,
        )
    body = client.get(_scan_url(calc.public_ref)).json()
    p1 = body["points"][0]
    assert [v["coordinate_index"] for v in p1["coordinate_values"]] == [1, 2, 3]
    assert [v["coordinate_value"] for v in p1["coordinate_values"]] == [
        0.1, 0.2, 0.3,
    ]


def test_scan_pagination_envelope_correct(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=5)
    body = client.get(_scan_url(calc.public_ref, limit=2)).json()
    page = body["pagination"]
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert page["returned"] == 2
    assert page["total"] == 5
    assert len(body["points"]) == 2


def test_scan_pagination_second_page_disjoint(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=5)
    body_a = client.get(_scan_url(calc.public_ref, limit=2, offset=0)).json()
    body_b = client.get(_scan_url(calc.public_ref, limit=2, offset=2)).json()
    a_ids = {p["point_index"] for p in body_a["points"]}
    b_ids = {p["point_index"] for p in body_b["points"]}
    assert a_ids.isdisjoint(b_ids)
    assert a_ids == {1, 2}
    assert b_ids == {3, 4}


def test_scan_limit_overrun_returns_422(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session)
    resp = client.get(_scan_url(calc.public_ref, limit=999999))
    assert resp.status_code == 422


def test_scan_sort_param_rejected(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session)
    resp = client.get(_scan_url(calc.public_ref, sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


# ---------------------------------------------------------------------------
# Geometry behavior
# ---------------------------------------------------------------------------


def test_scan_point_with_geometry_exposes_geometry_ref_by_default(
    client, db_session
):
    calc, geom, _ = _seed_one_dim_scan(db_session, n_points=2)
    body = client.get(_scan_url(calc.public_ref)).json()
    p1 = body["points"][0]
    p2 = body["points"][1]
    assert p1["geometry_ref"] == geom.public_ref
    # Phase D default: integer geometry_id stripped.
    assert "geometry_id" not in p1
    # Second point seeded without a geometry.
    assert p2["geometry_ref"] is None
    # ``geometry_link`` is populated only with include_geometries=true.
    assert p1.get("geometry_link") is None


def test_scan_geometry_id_restored_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    """Phase D: ``include=internal_ids`` must be passed explicitly to
    restore integer ids. Without it, ids stay stripped even when the
    deployment policy permits them."""
    calc, geom, _ = _seed_one_dim_scan(db_session, n_points=1)
    body = client.get(
        _scan_url(calc.public_ref, include="internal_ids")
    ).json()
    p1 = body["points"][0]
    assert p1["geometry_id"] == geom.id
    # Calculation core block ids restored too.
    assert body["calculation"]["calculation_id"] == calc.id


def test_scan_internal_ids_silently_dropped_when_policy_disallows(
    client, db_session
):
    """Without the ``allow_internal_ids`` fixture, the deployment
    policy disallows internal-id exposure. ``include=internal_ids`` is
    silently dropped from the resolved set and ids stay stripped."""
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=1)
    body = client.get(
        _scan_url(calc.public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "calculation_id" not in body["calculation"]
    assert "geometry_id" not in body["points"][0]


def test_scan_unknown_include_token_returns_422(client, db_session):
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=1)
    resp = client.get(_scan_url(calc.public_ref, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_scan_include_geometries_true_returns_lightweight_link(
    client, db_session
):
    calc, geom, _ = _seed_one_dim_scan(db_session, n_points=1)
    body = client.get(
        _scan_url(calc.public_ref, include_geometries="true")
    ).json()
    p1 = body["points"][0]
    link = p1["geometry_link"]
    assert link is not None
    assert link["geometry_ref"] == geom.public_ref
    assert link["natoms"] == geom.natoms
    assert link["geom_hash"] == geom.geom_hash
    # Phase D default: geometry_id on the link is also stripped.
    assert "geometry_id" not in link
    # Defense-in-depth: no XYZ inlining even with include_geometries=true.
    for forbidden in ("xyz_text", "atoms", "coords", "symbols"):
        assert forbidden not in link


# ---------------------------------------------------------------------------
# Defense-in-depth: no full payload leakage
# ---------------------------------------------------------------------------


def test_scan_response_does_not_leak_full_payload_keys(client, db_session):
    """Recursive walk: the scan endpoint must never inline full XYZ
    coordinates, atom rows, artifact bodies, or pre-signed URLs at
    any depth. ``points`` and ``coordinate_values`` are valid scan
    payload keys and are explicitly NOT in the forbidden set."""
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=2)
    body = client.get(
        _scan_url(calc.public_ref, include_geometries="true")
    ).json()
    forbidden_keys = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, (
                    f"/scan leaked forbidden key {k!r} into the response"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ---------------------------------------------------------------------------
# Cross-endpoint agreement with include=scan summary
# ---------------------------------------------------------------------------


def test_scan_endpoint_agrees_with_include_scan_summary(client, db_session):
    """The ``scan`` block returned by ``/scan`` is the same shape and
    values as the ``record.scan`` block returned by the detail
    endpoint with ``include=scan``."""
    calc, _, _ = _seed_one_dim_scan(db_session, n_points=3)
    detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    full = client.get(_scan_url(calc.public_ref)).json()
    detail_scan = detail["record"]["scan"]
    full_scan = full["scan"]
    assert detail_scan == full_scan
    # Sanity: the aggregates that the summary surfaces must agree
    # with the actual full-data trip.
    assert full_scan["point_count"] == full["pagination"]["total"]
    assert full_scan["coordinate_count"] == len(full["coordinates"])


# ===========================================================================
# /irc endpoint
# ===========================================================================


def _make_irc_calc(
    db_session,
    *,
    direction: IRCDirection = IRCDirection.both,
    has_forward: bool = True,
    has_reverse: bool = True,
    ts_point_index: int | None = 0,
    point_count: int | None = None,
    note: str | None = None,
):
    """Build a calc + ``calc_irc_result`` row."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.irc
    )
    db_session.add(
        CalculationIRCResult(
            calculation_id=calc.id,
            direction=direction,
            has_forward=has_forward,
            has_reverse=has_reverse,
            ts_point_index=ts_point_index,
            point_count=point_count,
            zero_energy_reference_hartree=-100.0,
            note=note,
        )
    )
    db_session.flush()
    return calc


def _attach_irc_point(
    db_session,
    *,
    calculation,
    point_index: int,
    direction: IRCDirection | None = None,
    is_ts: bool = False,
    reaction_coordinate: float | None = None,
    electronic_energy_hartree: float | None = None,
    relative_energy_kj_mol: float | None = None,
    max_gradient: float | None = None,
    rms_gradient: float | None = None,
    geometry=None,
    note: str | None = None,
):
    row = CalculationIRCPoint(
        calculation_id=calculation.id,
        point_index=point_index,
        direction=direction,
        is_ts=is_ts,
        reaction_coordinate=reaction_coordinate,
        electronic_energy_hartree=electronic_energy_hartree,
        relative_energy_kj_mol=relative_energy_kj_mol,
        max_gradient=max_gradient,
        rms_gradient=rms_gradient,
        geometry_id=geometry.id if geometry is not None else None,
        note=note,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_orca_style_irc(db_session):
    """ORCA-style bidirectional IRC: 1 TS marker (no direction) +
    2 forward + 2 reverse = 5 points. Returns
    ``(calc, geometry_for_ts, energies)``."""
    calc = _make_irc_calc(
        db_session,
        direction=IRCDirection.both,
        has_forward=True,
        has_reverse=True,
        ts_point_index=0,
        point_count=5,
        note="orca bidirectional",
    )
    geom_ts = make_geometry(db_session, natoms=3)
    _attach_irc_point(
        db_session, calculation=calc, point_index=0,
        direction=None, is_ts=True,
        reaction_coordinate=0.0, electronic_energy_hartree=-99.5,
        relative_energy_kj_mol=20.0, max_gradient=1e-4, rms_gradient=5e-5,
        geometry=geom_ts,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=1,
        direction=IRCDirection.forward,
        reaction_coordinate=0.5, electronic_energy_hartree=-99.7,
        relative_energy_kj_mol=15.0,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=2,
        direction=IRCDirection.forward,
        reaction_coordinate=1.0, electronic_energy_hartree=-99.9,
        relative_energy_kj_mol=5.0,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=3,
        direction=IRCDirection.reverse,
        reaction_coordinate=-0.5, electronic_energy_hartree=-99.6,
        relative_energy_kj_mol=18.0,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=4,
        direction=IRCDirection.reverse,
        reaction_coordinate=-1.0, electronic_energy_hartree=-100.1,
        relative_energy_kj_mol=0.0,
    )
    return calc, geom_ts


def _irc_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/calculations/{handle}/irc"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# Happy-path + handle resolution
# ---------------------------------------------------------------------------


def test_irc_endpoint_by_calculation_ref_returns_200(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    resp = client.get(_irc_url(calc.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["calculation"]["calculation_ref"] == calc.public_ref


def test_irc_endpoint_by_integer_id_works(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    resp = client.get(_irc_url(str(calc.id)))
    assert resp.status_code == 200, resp.text
    assert resp.json()["calculation"]["calculation_ref"] == calc.public_ref


def test_irc_endpoint_unknown_calculation_returns_404(client, db_session):
    resp = client.get(
        "/api/v1/scientific/calculations/calc_doesnotexist000/irc"
    )
    assert resp.status_code == 404
    assert "calculation not found" in resp.text.lower()


def test_irc_endpoint_wrong_prefix_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/calculations/spe_abcdef0123456789/irc"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_irc_endpoint_malformed_handle_returns_422(client, db_session):
    resp = client.get("/api/v1/scientific/calculations/not-a-handle/irc")
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_irc_endpoint_no_irc_result_returns_404(client, db_session):
    """Calc exists but no calc_irc_result row → 404
    ``irc_result_not_found``."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    resp = client.get(_irc_url(calc.public_ref))
    assert resp.status_code == 404
    assert "irc_result_not_found" in resp.text


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_irc_response_includes_core_block_with_review_badge(
    client, db_session
):
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(_irc_url(calc.public_ref)).json()
    core = body["calculation"]
    assert core["calculation_ref"] == calc.public_ref
    assert core["type"] == "irc"
    assert "review" in core
    assert core["review"]["status"] == "not_reviewed"


def test_irc_response_includes_owner_summary(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(_irc_url(calc.public_ref)).json()
    owner = body["owner"]
    assert owner["kind"] == "species_entry"
    assert "species_entry" in owner


def test_irc_response_includes_irc_summary(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(_irc_url(calc.public_ref)).json()
    irc = body["irc"]
    assert irc["direction"] == "both"
    assert irc["forward_point_count"] == 2
    assert irc["reverse_point_count"] == 2
    assert irc["ts_point_count"] == 1


def test_irc_response_includes_paginated_points(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(_irc_url(calc.public_ref)).json()
    points = body["points"]
    assert len(points) == 5
    assert [p["point_index"] for p in points] == [0, 1, 2, 3, 4]


def test_irc_points_carry_full_per_point_state(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(_irc_url(calc.public_ref)).json()
    by_idx = {p["point_index"]: p for p in body["points"]}
    # TS marker row.
    p_ts = by_idx[0]
    assert p_ts["is_ts"] is True
    assert p_ts["direction"] is None
    assert p_ts["reaction_coordinate"] == 0.0
    assert p_ts["electronic_energy_hartree"] == -99.5
    assert p_ts["max_gradient"] == 1e-4
    assert p_ts["rms_gradient"] == 5e-5
    # Forward arm.
    p_fwd = by_idx[1]
    assert p_fwd["is_ts"] is False
    assert p_fwd["direction"] == "forward"
    assert p_fwd["reaction_coordinate"] == 0.5
    # Reverse arm carries signed reaction coordinate.
    p_rev = by_idx[3]
    assert p_rev["direction"] == "reverse"
    assert p_rev["reaction_coordinate"] == -0.5


# ---------------------------------------------------------------------------
# Ordering + pagination
# ---------------------------------------------------------------------------


def test_irc_points_ordered_by_point_index(client, db_session):
    calc = _make_irc_calc(db_session, direction=IRCDirection.forward)
    # Insert out of order including across directions.
    for idx, dirn in [(3, IRCDirection.forward),
                      (1, IRCDirection.forward),
                      (2, IRCDirection.forward)]:
        _attach_irc_point(
            db_session, calculation=calc, point_index=idx,
            direction=dirn,
            electronic_energy_hartree=-99.0 - idx * 0.1,
        )
    body = client.get(_irc_url(calc.public_ref)).json()
    assert [p["point_index"] for p in body["points"]] == [1, 2, 3]


def test_irc_pagination_envelope_correct(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(_irc_url(calc.public_ref, limit=2)).json()
    page = body["pagination"]
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert page["returned"] == 2
    assert page["total"] == 5
    assert len(body["points"]) == 2


def test_irc_pagination_second_page_disjoint(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    body_a = client.get(_irc_url(calc.public_ref, limit=2, offset=0)).json()
    body_b = client.get(_irc_url(calc.public_ref, limit=2, offset=2)).json()
    a_ids = {p["point_index"] for p in body_a["points"]}
    b_ids = {p["point_index"] for p in body_b["points"]}
    assert a_ids.isdisjoint(b_ids)
    assert a_ids == {0, 1}
    assert b_ids == {2, 3}


def test_irc_limit_overrun_returns_422(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    resp = client.get(_irc_url(calc.public_ref, limit=999999))
    assert resp.status_code == 422


def test_irc_sort_param_rejected(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    resp = client.get(_irc_url(calc.public_ref, sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


# ---------------------------------------------------------------------------
# Geometry behavior
# ---------------------------------------------------------------------------


def test_irc_point_with_geometry_exposes_geometry_ref_by_default(
    client, db_session
):
    calc, geom_ts = _seed_orca_style_irc(db_session)
    body = client.get(_irc_url(calc.public_ref)).json()
    p_ts = next(p for p in body["points"] if p["is_ts"])
    p_fwd = next(p for p in body["points"] if p["direction"] == "forward")
    assert p_ts["geometry_ref"] == geom_ts.public_ref
    assert "geometry_id" not in p_ts
    assert p_fwd["geometry_ref"] is None
    assert p_ts.get("geometry_link") is None


def test_irc_geometry_id_restored_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    calc, geom_ts = _seed_orca_style_irc(db_session)
    body = client.get(
        _irc_url(calc.public_ref, include="internal_ids")
    ).json()
    p_ts = next(p for p in body["points"] if p["is_ts"])
    assert p_ts["geometry_id"] == geom_ts.id
    assert body["calculation"]["calculation_id"] == calc.id


def test_irc_internal_ids_silently_dropped_when_policy_disallows(
    client, db_session
):
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(
        _irc_url(calc.public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "calculation_id" not in body["calculation"]


def test_irc_unknown_include_token_returns_422(client, db_session):
    calc, _ = _seed_orca_style_irc(db_session)
    resp = client.get(_irc_url(calc.public_ref, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_irc_include_geometries_true_returns_lightweight_link(
    client, db_session
):
    calc, geom_ts = _seed_orca_style_irc(db_session)
    body = client.get(
        _irc_url(calc.public_ref, include_geometries="true")
    ).json()
    p_ts = next(p for p in body["points"] if p["is_ts"])
    link = p_ts["geometry_link"]
    assert link is not None
    assert link["geometry_ref"] == geom_ts.public_ref
    assert link["natoms"] == geom_ts.natoms
    assert link["geom_hash"] == geom_ts.geom_hash
    for forbidden in ("xyz_text", "atoms", "coords", "symbols"):
        assert forbidden not in link


# ---------------------------------------------------------------------------
# Defense-in-depth: no full payload leakage
# ---------------------------------------------------------------------------


def test_irc_response_does_not_leak_full_payload_keys(client, db_session):
    """Recursive walk: the IRC endpoint must never inline full XYZ
    coordinates, atom rows, artifact bodies, or pre-signed URLs."""
    calc, _ = _seed_orca_style_irc(db_session)
    body = client.get(
        _irc_url(calc.public_ref, include_geometries="true")
    ).json()
    forbidden_keys = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, (
                    f"/irc leaked forbidden key {k!r} into the response"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ---------------------------------------------------------------------------
# Cross-endpoint agreement with include=irc summary
# ---------------------------------------------------------------------------


def test_irc_endpoint_agrees_with_include_irc_summary(client, db_session):
    """``response.irc`` from ``/irc`` is byte-identical to
    ``record.irc`` from the detail endpoint with ``include=irc``."""
    calc, _ = _seed_orca_style_irc(db_session)
    detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    full = client.get(_irc_url(calc.public_ref)).json()
    detail_irc = detail["record"]["irc"]
    full_irc = full["irc"]
    assert detail_irc == full_irc
    # Cross-check the aggregates that drive the summary against the
    # full-data trip's pagination total.
    assert full["pagination"]["total"] == 5
    assert full_irc["forward_point_count"] == 2
    assert full_irc["reverse_point_count"] == 2
    assert full_irc["ts_point_count"] == 1
    assert full_irc["min_electronic_energy_hartree"] == -100.1
    assert full_irc["max_electronic_energy_hartree"] == -99.5
    assert full_irc["min_reaction_coordinate"] == -1.0
    assert full_irc["max_reaction_coordinate"] == 1.0


# ===========================================================================
# /path-search endpoint
# ===========================================================================


def _make_path_search_calc(
    db_session,
    *,
    method: PathSearchMethod = PathSearchMethod.neb,
    is_double_ended: bool | None = True,
    converged: bool | None = True,
    n_points: int | None = None,
    selected_ts_point_index: int | None = None,
    climbing_image_index: int | None = None,
    source_endpoint_count: int | None = 2,
    note: str | None = None,
):
    """Build a calc + ``calc_path_search_result`` row."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.path_search
    )
    db_session.add(
        CalculationPathSearchResult(
            calculation_id=calc.id,
            method=method,
            is_double_ended=is_double_ended,
            converged=converged,
            n_points=n_points,
            selected_ts_point_index=selected_ts_point_index,
            climbing_image_index=climbing_image_index,
            source_endpoint_count=source_endpoint_count,
            zero_energy_reference_hartree=-100.0,
            note=note,
        )
    )
    db_session.flush()
    return calc


def _attach_path_search_point(
    db_session,
    *,
    calculation,
    point_index: int,
    path_coordinate: float | None = None,
    electronic_energy_hartree: float | None = None,
    relative_energy_kj_mol: float | None = None,
    max_force: float | None = None,
    rms_force: float | None = None,
    max_gradient: float | None = None,
    rms_gradient: float | None = None,
    is_ts_guess: bool = False,
    is_climbing_image: bool = False,
    geometry=None,
    note: str | None = None,
):
    row = CalculationPathSearchPoint(
        calculation_id=calculation.id,
        point_index=point_index,
        path_coordinate=path_coordinate,
        electronic_energy_hartree=electronic_energy_hartree,
        relative_energy_kj_mol=relative_energy_kj_mol,
        max_force=max_force,
        rms_force=rms_force,
        max_gradient=max_gradient,
        rms_gradient=rms_gradient,
        is_ts_guess=is_ts_guess,
        is_climbing_image=is_climbing_image,
        geometry_id=geometry.id if geometry is not None else None,
        note=note,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_neb_path_search(db_session):
    """Five-image NEB: image 2 is the climbing image / TS guess.
    Returns ``(calc, geom_for_ts)``.
    """
    calc = _make_path_search_calc(
        db_session,
        method=PathSearchMethod.neb,
        is_double_ended=True,
        converged=True,
        n_points=5,
        selected_ts_point_index=2,
        climbing_image_index=2,
        source_endpoint_count=2,
        note="neb climb",
    )
    geom_ts = make_geometry(db_session, natoms=3)
    # Reactant endpoint.
    _attach_path_search_point(
        db_session, calculation=calc, point_index=0,
        path_coordinate=0.0,
        electronic_energy_hartree=-100.0,
        relative_energy_kj_mol=0.0,
        max_force=1e-5, rms_force=5e-6,
    )
    _attach_path_search_point(
        db_session, calculation=calc, point_index=1,
        path_coordinate=0.25,
        electronic_energy_hartree=-99.8,
        relative_energy_kj_mol=10.0,
        max_force=2e-4, rms_force=1e-4,
    )
    # Climbing image / TS guess.
    _attach_path_search_point(
        db_session, calculation=calc, point_index=2,
        path_coordinate=0.5,
        electronic_energy_hartree=-99.5,
        relative_energy_kj_mol=25.0,
        max_force=1e-4, rms_force=5e-5,
        max_gradient=8e-5, rms_gradient=3e-5,
        is_ts_guess=True, is_climbing_image=True,
        geometry=geom_ts,
        note="climbing image",
    )
    _attach_path_search_point(
        db_session, calculation=calc, point_index=3,
        path_coordinate=0.75,
        electronic_energy_hartree=-99.9,
        relative_energy_kj_mol=15.0,
        max_force=2e-4, rms_force=1e-4,
    )
    # Product endpoint.
    _attach_path_search_point(
        db_session, calculation=calc, point_index=4,
        path_coordinate=1.0,
        electronic_energy_hartree=-100.2,
        relative_energy_kj_mol=5.0,
        max_force=1e-5, rms_force=5e-6,
    )
    return calc, geom_ts


def _path_search_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/calculations/{handle}/path-search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# Happy-path + handle resolution
# ---------------------------------------------------------------------------


def test_path_search_endpoint_by_calculation_ref_returns_200(
    client, db_session
):
    calc, _ = _seed_neb_path_search(db_session)
    resp = client.get(_path_search_url(calc.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["calculation"]["calculation_ref"] == calc.public_ref


def test_path_search_endpoint_by_integer_id_works(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    resp = client.get(_path_search_url(str(calc.id)))
    assert resp.status_code == 200, resp.text
    assert resp.json()["calculation"]["calculation_ref"] == calc.public_ref


def test_path_search_endpoint_unknown_calculation_returns_404(
    client, db_session
):
    resp = client.get(
        "/api/v1/scientific/calculations/calc_doesnotexist000/path-search"
    )
    assert resp.status_code == 404
    assert "calculation not found" in resp.text.lower()


def test_path_search_endpoint_wrong_prefix_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/calculations/spe_abcdef0123456789/path-search"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_path_search_endpoint_malformed_handle_returns_422(
    client, db_session
):
    resp = client.get(
        "/api/v1/scientific/calculations/not-a-handle/path-search"
    )
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_path_search_endpoint_no_path_search_result_returns_404(
    client, db_session
):
    """Calc exists but has no calc_path_search_result row → 404
    ``path_search_result_not_found``."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    resp = client.get(_path_search_url(calc.public_ref))
    assert resp.status_code == 404
    assert "path_search_result_not_found" in resp.text


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_path_search_response_includes_core_block_with_review_badge(
    client, db_session
):
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(_path_search_url(calc.public_ref)).json()
    core = body["calculation"]
    assert core["calculation_ref"] == calc.public_ref
    assert core["type"] == "path_search"
    assert "review" in core
    assert core["review"]["status"] == "not_reviewed"


def test_path_search_response_includes_owner_summary(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(_path_search_url(calc.public_ref)).json()
    owner = body["owner"]
    assert owner["kind"] == "species_entry"
    assert "species_entry" in owner


def test_path_search_response_includes_path_search_summary(
    client, db_session
):
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(_path_search_url(calc.public_ref)).json()
    ps = body["path_search"]
    assert ps["method"] == "neb"
    assert ps["is_double_ended"] is True
    assert ps["converged"] is True
    assert ps["stored_point_count"] == 5
    assert ps["ts_guess_count"] == 1
    assert ps["climbing_image_count"] == 1


def test_path_search_response_includes_paginated_points(
    client, db_session
):
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(_path_search_url(calc.public_ref)).json()
    points = body["points"]
    assert len(points) == 5
    assert [p["point_index"] for p in points] == [0, 1, 2, 3, 4]


def test_path_search_points_carry_full_per_point_state(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(_path_search_url(calc.public_ref)).json()
    by_idx = {p["point_index"]: p for p in body["points"]}
    ts = by_idx[2]
    assert ts["is_ts_guess"] is True
    assert ts["is_climbing_image"] is True
    assert ts["path_coordinate"] == 0.5
    assert ts["electronic_energy_hartree"] == -99.5
    assert ts["relative_energy_kj_mol"] == 25.0
    assert ts["max_force"] == 1e-4
    assert ts["rms_force"] == 5e-5
    assert ts["max_gradient"] == 8e-5
    assert ts["rms_gradient"] == 3e-5
    assert ts["note"] == "climbing image"
    # Non-TS row.
    endpoint = by_idx[0]
    assert endpoint["is_ts_guess"] is False
    assert endpoint["is_climbing_image"] is False
    assert endpoint["path_coordinate"] == 0.0


# ---------------------------------------------------------------------------
# Ordering + pagination
# ---------------------------------------------------------------------------


def test_path_search_points_ordered_by_point_index(client, db_session):
    calc = _make_path_search_calc(db_session)
    # Insert out of order, not by energy.
    for idx, energy in [(2, -99.0), (0, -100.0), (1, -99.5)]:
        _attach_path_search_point(
            db_session, calculation=calc, point_index=idx,
            electronic_energy_hartree=energy,
        )
    body = client.get(_path_search_url(calc.public_ref)).json()
    assert [p["point_index"] for p in body["points"]] == [0, 1, 2]


def test_path_search_pagination_envelope_correct(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(_path_search_url(calc.public_ref, limit=2)).json()
    page = body["pagination"]
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert page["returned"] == 2
    assert page["total"] == 5
    assert len(body["points"]) == 2


def test_path_search_pagination_second_page_disjoint(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    body_a = client.get(
        _path_search_url(calc.public_ref, limit=2, offset=0)
    ).json()
    body_b = client.get(
        _path_search_url(calc.public_ref, limit=2, offset=2)
    ).json()
    a_ids = {p["point_index"] for p in body_a["points"]}
    b_ids = {p["point_index"] for p in body_b["points"]}
    assert a_ids.isdisjoint(b_ids)
    assert a_ids == {0, 1}
    assert b_ids == {2, 3}


def test_path_search_limit_overrun_returns_422(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    resp = client.get(_path_search_url(calc.public_ref, limit=999999))
    assert resp.status_code == 422


def test_path_search_sort_param_rejected(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    resp = client.get(
        _path_search_url(calc.public_ref, sort="electronic_energy_hartree")
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


# ---------------------------------------------------------------------------
# Geometry behavior
# ---------------------------------------------------------------------------


def test_path_search_point_with_geometry_exposes_geometry_ref_by_default(
    client, db_session
):
    calc, geom_ts = _seed_neb_path_search(db_session)
    body = client.get(_path_search_url(calc.public_ref)).json()
    ts = next(p for p in body["points"] if p["is_ts_guess"])
    other = next(p for p in body["points"] if not p["is_ts_guess"])
    assert ts["geometry_ref"] == geom_ts.public_ref
    assert "geometry_id" not in ts
    assert other["geometry_ref"] is None
    assert ts.get("geometry_link") is None


def test_path_search_geometry_id_restored_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    calc, geom_ts = _seed_neb_path_search(db_session)
    body = client.get(
        _path_search_url(calc.public_ref, include="internal_ids")
    ).json()
    ts = next(p for p in body["points"] if p["is_ts_guess"])
    assert ts["geometry_id"] == geom_ts.id
    assert body["calculation"]["calculation_id"] == calc.id


def test_path_search_internal_ids_silently_dropped_when_policy_disallows(
    client, db_session
):
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(
        _path_search_url(calc.public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "calculation_id" not in body["calculation"]


def test_path_search_unknown_include_token_returns_422(client, db_session):
    calc, _ = _seed_neb_path_search(db_session)
    resp = client.get(_path_search_url(calc.public_ref, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_path_search_include_geometries_true_returns_lightweight_link(
    client, db_session
):
    calc, geom_ts = _seed_neb_path_search(db_session)
    body = client.get(
        _path_search_url(calc.public_ref, include_geometries="true")
    ).json()
    ts = next(p for p in body["points"] if p["is_ts_guess"])
    link = ts["geometry_link"]
    assert link is not None
    assert link["geometry_ref"] == geom_ts.public_ref
    assert link["natoms"] == geom_ts.natoms
    assert link["geom_hash"] == geom_ts.geom_hash
    for forbidden in ("xyz_text", "atoms", "coords", "symbols"):
        assert forbidden not in link


# ---------------------------------------------------------------------------
# Defense-in-depth: no full payload leakage
# ---------------------------------------------------------------------------


def test_path_search_response_does_not_leak_full_payload_keys(
    client, db_session
):
    """Recursive walk: the path-search endpoint must never inline full
    XYZ coordinates, atom rows, artifact bodies, or pre-signed URLs."""
    calc, _ = _seed_neb_path_search(db_session)
    body = client.get(
        _path_search_url(calc.public_ref, include_geometries="true")
    ).json()
    forbidden_keys = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, (
                    f"/path-search leaked forbidden key {k!r} into the response"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ---------------------------------------------------------------------------
# Cross-endpoint agreement with include=path_search summary
# ---------------------------------------------------------------------------


def test_path_search_endpoint_agrees_with_include_path_search_summary(
    client, db_session
):
    """``response.path_search`` from ``/path-search`` is byte-identical
    to ``record.path_search`` from the detail endpoint with
    ``include=path_search``."""
    calc, _ = _seed_neb_path_search(db_session)
    detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        f"?include=path_search"
    ).json()
    full = client.get(_path_search_url(calc.public_ref)).json()
    detail_ps = detail["record"]["path_search"]
    full_ps = full["path_search"]
    assert detail_ps == full_ps
    assert full["pagination"]["total"] == 5
    assert full_ps["stored_point_count"] == 5
    assert full_ps["ts_guess_count"] == 1
    assert full_ps["climbing_image_count"] == 1
    assert full_ps["min_electronic_energy_hartree"] == -100.2
    assert full_ps["max_electronic_energy_hartree"] == -99.5
    assert full_ps["min_path_coordinate"] == 0.0
    assert full_ps["max_path_coordinate"] == 1.0
