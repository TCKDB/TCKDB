"""ADR 0020's conformance check: does coordinate_value match its geometry?

This suite is built around one non-negotiable: it must not be possible to
believe the check is doing anything just because a suite of good-data-only
assertions passes. So it is organised, deliberately, as one negative case
per positive one:

* pure vector geometry (:func:`bond_length_angstrom`, :func:`bond_angle_deg`,
  :func:`dihedral_deg`) is checked against constructed atoms with a *known*
  answer, built by an independent NeRF placement helper below -- not by
  round-tripping the module's own formula through itself;
* :func:`evaluate_point_coordinate_conformance` is proved to both pass a
  correct point and catch a **0.01 degree** injected error -- the exact
  number ADR 0020 names as one the previously-rejected 0.5/1.0 degree floor
  would have missed;
* ``not_applicable`` and ``not_checkable`` are each reached and asserted
  distinct from a pass and from a fail (``deviates``);
* :func:`classify_series` gets one fixture per named pattern -- constant
  offset, linear ramp, uniform one-step, lone outlier -- built directly
  from :class:`PointCoordinateConformance`, so the assertion is on the
  classification the module *names*, not just that it warns;
* one end-to-end test builds real Cartesian geometries for a whole scan
  series shaped exactly like the deposited corpus (stored = relative sweep,
  never touching ``start_value`` on the check side) and asserts the check
  recovers the classification and the implied constant through the full
  database path -- ``Calculation`` -> ``CalculationScanCoordinate`` ->
  ``CalculationScanPoint`` -> ``Geometry``/``GeometryAtom``.
"""

from __future__ import annotations

import hashlib
import itertools
import math

import numpy as np
import pytest

from app.db.models.calculation import (
    Calculation,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
)
from app.db.models.common import CalculationType, CoordinateUnit, ScanCoordinateKind
from app.db.models.geometry import Geometry, GeometryAtom
from app.services.scan_coordinate_conformance import (
    DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA,
    TOLERANCE_FLOOR_DEGREES,
    CoordinateSeriesConformance,
    PointCoordinateConformance,
    PointStatus,
    SeriesClassification,
    bond_angle_deg,
    bond_length_angstrom,
    build_scan_coordinate_conformance_report,
    classify_series,
    dihedral_deg,
    evaluate_point_coordinate_conformance,
    infer_precision_decimals,
)
from tests.services.scientific_read._factories import make_species, make_species_entry

# ---------------------------------------------------------------------------
# Independent geometry construction (NeRF placement) -- ground truth for the
# pure vector-math tests. Deliberately *not* derived from the module under
# test: if this placement and ``dihedral_deg`` disagreed on sign convention,
# a round-trip test would not have caught it, which is why the very first
# test below is exactly that cross-check.
# ---------------------------------------------------------------------------


def _place_next_atom(
    a: np.ndarray,
    b: np.ndarray,
    c: np.ndarray,
    *,
    bond_length: float,
    bond_angle_deg_: float,
    dihedral_deg_: float,
) -> np.ndarray:
    """Place atom D such that dihedral(a, b, c, d) == dihedral_deg_ exactly.

    Standard NeRF ("natural extension reference frame") placement. The sign
    passed to the internal formula is negated relative to the public
    parameter -- measured empirically against :func:`dihedral_deg` below,
    not assumed -- so that this helper's ``dihedral_deg_`` and this module's
    ``dihedral_deg`` agree.
    """
    theta = math.radians(bond_angle_deg_)
    phi = math.radians(-dihedral_deg_)
    bc = c - b
    bc_hat = bc / np.linalg.norm(bc)
    ab = b - a
    n = np.cross(ab, bc_hat)
    n_hat = n / np.linalg.norm(n)
    m = np.cross(n_hat, bc_hat)
    local = np.array(
        [
            -bond_length * math.cos(theta),
            bond_length * math.sin(theta) * math.cos(phi),
            bond_length * math.sin(theta) * math.sin(phi),
        ]
    )
    basis = np.column_stack([bc_hat, m, n_hat])
    return c + basis @ local


# A fixed, well-conditioned backbone: A-B-C with a realistic bond length and
# neither flanking angle anywhere near collinear.
_A = np.array([0.0, 0.0, 0.0])
_B = np.array([1.50, 0.0, 0.0])
_C = np.array([2.50, 1.30, 0.0])
_BOND_LENGTH_CD = 1.09
_ANGLE_BCD = 109.5


def _quartet_at_dihedral(dihedral_value: float) -> tuple[np.ndarray, ...]:
    d = _place_next_atom(
        _A,
        _B,
        _C,
        bond_length=_BOND_LENGTH_CD,
        bond_angle_deg_=_ANGLE_BCD,
        dihedral_deg_=dihedral_value,
    )
    return _A, _B, _C, d


class TestPlacementHelperAgreesWithDihedralDeg:
    """The ground-truth builder and the module's formula must agree.

    Without this, every other geometry test below would be validating the
    module against itself.
    """

    @pytest.mark.parametrize(
        "target", [0.0, 1.0, 30.0, 60.0, 90.0, 120.0, 150.0, 179.0, -30.0, -90.0, -150.0]
    )
    def test_round_trip(self, target: float) -> None:
        a, b, c, d = _quartet_at_dihedral(target)
        got = dihedral_deg(a, b, c, d)
        assert got == pytest.approx(target, abs=1e-9)
        assert bond_length_angstrom(c, d) == pytest.approx(_BOND_LENGTH_CD, abs=1e-9)
        assert bond_angle_deg(b, c, d) == pytest.approx(_ANGLE_BCD, abs=1e-9)


class TestPureVectorGeometry:
    def test_bond_length(self) -> None:
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.0, 2.0, 2.0])
        assert bond_length_angstrom(a, b) == pytest.approx(3.0)

    def test_bond_angle_right_angle(self) -> None:
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 0.0, 0.0])
        c = np.array([0.0, 1.0, 0.0])
        assert bond_angle_deg(a, b, c) == pytest.approx(90.0)

    def test_bond_angle_linear(self) -> None:
        a = np.array([-1.0, 0.0, 0.0])
        b = np.array([0.0, 0.0, 0.0])
        c = np.array([1.0, 0.0, 0.0])
        assert bond_angle_deg(a, b, c) == pytest.approx(180.0)


# ---------------------------------------------------------------------------
# evaluate_point_coordinate_conformance
# ---------------------------------------------------------------------------


class TestEvaluatePointCoordinateConformance:
    def test_conforms_on_exact_geometry(self) -> None:
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=6,
            point_index=1,
        )
        assert result.status is PointStatus.conforms
        assert result.residual == pytest.approx(0.0, abs=1e-6)

    def test_catches_a_hundredth_of_a_degree_error(self) -> None:
        """A 0.01 deg error must be caught -- the number the 0.5/1.0 deg
        floor (explicitly rejected) would have missed by 1-2 orders of
        magnitude."""
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.01,  # true value is 47.0
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=6,
            point_index=1,
        )
        assert result.tolerance is not None
        assert result.tolerance < 0.01, (
            f"tolerance {result.tolerance} is not tight enough to catch a "
            "0.01 degree error -- this is exactly the rejected 0.5/1.0 "
            "degree floor reappearing."
        )
        assert result.status is PointStatus.deviates
        assert result.residual == pytest.approx(0.01, abs=1e-6)

    def test_tolerance_never_below_the_documented_floor(self) -> None:
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=6,
            point_index=1,
        )
        assert result.tolerance >= TOLERANCE_FLOOR_DEGREES

    def test_bond_conforms_and_deviates(self) -> None:
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.5000000, 0.0, 0.0])
        ok = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.bond,
            stored_value=1.5,
            stored_unit=CoordinateUnit.angstrom,
            coords_by_atom={1: a, 2: b},
            atom_indices=(1, 2),
            precision_decimals=6,
            point_index=1,
        )
        assert ok.status is PointStatus.conforms

        bad = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.bond,
            stored_value=1.55,
            stored_unit=CoordinateUnit.angstrom,
            coords_by_atom={1: a, 2: b},
            atom_indices=(1, 2),
            precision_decimals=6,
            point_index=1,
        )
        assert bad.status is PointStatus.deviates

    def test_improper_is_not_applicable(self) -> None:
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.improper,
            stored_value=47.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=6,
            point_index=1,
        )
        assert result.status is PointStatus.not_applicable
        assert result.status is not PointStatus.conforms
        assert result.status is not PointStatus.deviates
        assert "improper" in result.reason

    def test_missing_atom_is_not_applicable(self) -> None:
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c},  # atom 4 missing
            atom_indices=(1, 2, 3, 4),
            precision_decimals=6,
            point_index=1,
        )
        assert result.status is PointStatus.not_applicable

    def test_declared_unit_mismatch_is_not_applicable(self) -> None:
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.5, 0.0, 0.0])
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.bond,
            stored_value=1.5,
            stored_unit=CoordinateUnit.degree,  # wrong unit for a bond
            coords_by_atom={1: a, 2: b},
            atom_indices=(1, 2),
            precision_decimals=6,
            point_index=1,
        )
        assert result.status is PointStatus.not_applicable

    def test_near_collinear_dihedral_is_not_checkable(self) -> None:
        """A quartet with theta_234 near 180 degrees is unusable, not wrong."""
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.5, 0.0, 0.0])
        c = np.array([3.0, 0.0, 0.0])  # A-B-C collinear
        d = np.array([4.09, 0.05, 0.0])  # nearly collinear B-C-D too
        theta_234 = bond_angle_deg(b, c, d)
        assert math.sin(math.radians(theta_234)) < DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=0.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=6,
            point_index=1,
        )
        assert result.status is PointStatus.not_checkable
        assert result.status is not PointStatus.conforms
        assert result.status is not PointStatus.deviates
        assert result.status is not PointStatus.not_applicable

    def test_degenerate_bond_is_not_checkable(self) -> None:
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([0.0, 0.0, 0.0])
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.bond,
            stored_value=0.0,
            stored_unit=CoordinateUnit.angstrom,
            coords_by_atom={1: a, 2: b},
            atom_indices=(1, 2),
            precision_decimals=6,
            point_index=1,
        )
        assert result.status is PointStatus.not_checkable


# ---------------------------------------------------------------------------
# infer_precision_decimals
# ---------------------------------------------------------------------------


class TestInferPrecisionDecimals:
    def test_detects_six_decimal_places(self) -> None:
        values = [1.234567, -0.000001, 3.000000, 2.500000]
        assert infer_precision_decimals(values) == 6

    def test_detects_fewer_decimal_places_when_genuinely_coarser(self) -> None:
        values = [1.2345, -0.0001, 3.0000, 2.5000] * 5  # large sample, all 4 dp
        assert infer_precision_decimals(values) == 4

    def test_falls_back_when_sample_too_coarse_to_trust(self) -> None:
        values = [1.0, 2.0, 3.0]  # 0 decimals needed, below the trust floor
        assert infer_precision_decimals(values) == 6

    def test_falls_back_on_empty_sample(self) -> None:
        assert infer_precision_decimals([]) == 6


# ---------------------------------------------------------------------------
# classify_series -- one fixture per named pattern, built directly from
# PointCoordinateConformance so the assertion is squarely on the classifier.
# ---------------------------------------------------------------------------


def _checkable_point(
    point_index: int, residual: float, *, tolerance: float = 0.05
) -> PointCoordinateConformance:
    return PointCoordinateConformance(
        point_index=point_index,
        status=PointStatus.conforms if abs(residual) <= tolerance else PointStatus.deviates,
        stored_value=0.0,
        expected_value=-residual,
        residual=residual,
        tolerance=tolerance,
    )


class TestClassifySeries:
    def test_conforms_when_every_point_agrees(self) -> None:
        points = [_checkable_point(i, 0.0001) for i in range(1, 6)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.conforms
        assert result.implied_constant is None

    def test_constant_offset_is_the_legacy_relative_axis(self) -> None:
        """Every point off by the same constant that does NOT match step_size."""
        constant = -73.4  # stands in for "-start_value mod 360"
        points = [_checkable_point(i, constant) for i in range(1, 9)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.consistent_with_legacy_relative_axis
        assert result.implied_constant == pytest.approx(constant, abs=1e-6)

    def test_uniform_one_step_offset_matches_grid_step_size(self) -> None:
        step = 8.0
        points = [_checkable_point(i, step) for i in range(1, 9)]
        result = classify_series(points, step_size=step, is_periodic=True)
        assert result.classification is SeriesClassification.uniform_one_step_offset
        assert result.implied_constant == pytest.approx(step, abs=1e-6)

    def test_linear_ramp_grows_with_point_index(self) -> None:
        slope = 0.5
        points = [_checkable_point(i, slope * i) for i in range(1, 10)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.linear_ramp
        assert result.implied_constant == pytest.approx(slope, rel=0.05)

    def test_single_outlier_is_one_bad_point_among_good_ones(self) -> None:
        points = [_checkable_point(i, 0.0001) for i in range(1, 8)]
        points[3] = _checkable_point(4, 15.0)  # point_index=4 is the outlier
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.single_outlier
        assert result.implied_constant == pytest.approx(15.0, abs=1e-6)

    def test_insufficient_data_below_minimum_checkable_points(self) -> None:
        points = [_checkable_point(1, 5.0), _checkable_point(2, 5.0)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.insufficient_data

    def test_no_clean_pattern_when_residuals_are_incoherent(self) -> None:
        residuals = [5.0, -12.0, 3.0, 20.0, -7.0, 11.0, -2.0]
        points = [_checkable_point(i, r) for i, r in enumerate(residuals, start=1)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.no_pattern_detected

    def test_classifications_are_mutually_distinguishable(self) -> None:
        """Every fixture above names a *different* classification -- the
        whole point of having four buckets rather than one 'warn'."""
        constant_result = classify_series(
            [_checkable_point(i, -73.4) for i in range(1, 9)],
            step_size=8.0,
            is_periodic=True,
        )
        step_result = classify_series(
            [_checkable_point(i, 8.0) for i in range(1, 9)],
            step_size=8.0,
            is_periodic=True,
        )
        ramp_result = classify_series(
            [_checkable_point(i, 0.5 * i) for i in range(1, 10)],
            step_size=8.0,
            is_periodic=True,
        )
        outlier_points = [_checkable_point(i, 0.0001) for i in range(1, 8)]
        outlier_points[3] = _checkable_point(4, 15.0)
        outlier_result = classify_series(outlier_points, step_size=8.0, is_periodic=True)

        classifications = {
            constant_result.classification,
            step_result.classification,
            ramp_result.classification,
            outlier_result.classification,
        }
        assert classifications == {
            SeriesClassification.consistent_with_legacy_relative_axis,
            SeriesClassification.uniform_one_step_offset,
            SeriesClassification.linear_ramp,
            SeriesClassification.single_outlier,
        }


# ---------------------------------------------------------------------------
# End-to-end through the database: a whole scan series shaped like the real
# corpus (ADR 0019's relative sweep), built from real Cartesian geometries.
# ---------------------------------------------------------------------------


def _make_scan_calculation(db_session) -> Calculation:
    """A minimal scan Calculation, owned by a fresh species entry.

    ``ck_calculation_one_owner`` requires exactly one of
    ``species_entry_id``/``transition_state_entry_id``; which one is
    irrelevant to this module, so a throwaway species is the cheapest way
    to satisfy it.
    """
    species = make_species(db_session)
    species_entry = make_species_entry(db_session, species)
    calc = Calculation(type=CalculationType.scan, species_entry_id=species_entry.id)
    db_session.add(calc)
    db_session.flush()
    return calc


_geom_hash_counter = itertools.count()


def _next_geom_hash() -> str:
    return hashlib.sha256(f"scan-conformance-test-{next(_geom_hash_counter)}".encode()).hexdigest()


def _make_geometry_for_quartet(db_session, atoms: tuple[np.ndarray, ...]) -> Geometry:
    geometry = Geometry(natoms=len(atoms), geom_hash=_next_geom_hash())
    db_session.add(geometry)
    db_session.flush()
    elements = ["C", "C", "C", "H"]
    for i, coord in enumerate(atoms, start=1):
        db_session.add(
            GeometryAtom(
                geometry_id=geometry.id,
                atom_index=i,
                element=elements[(i - 1) % len(elements)],
                x=float(coord[0]),
                y=float(coord[1]),
                z=float(coord[2]),
            )
        )
    db_session.flush()
    return geometry


class TestEndToEndThroughTheDatabase:
    def test_legacy_relative_sweep_is_recognised_from_real_geometries(self, db_session) -> None:
        """Reproduces the shape of all 46 deposited series: stored values are
        a sweep relative to the series' own start_value, while the actual
        geometry at every point carries the absolute dihedral. The check
        must recover this as ``consistent_with_legacy_relative_axis`` and
        report the implied constant -- **without ever reading
        start_value/end_value itself** (they are set on the row only so the
        fixture looks like the real corpus; the assertion is that the check
        gets the right answer without needing them).
        """
        start_value = 244.0  # an arbitrary absolute starting dihedral
        step = 8.0
        n_points = 12

        calc = _make_scan_calculation(db_session)
        coordinate = CalculationScanCoordinate(
            calculation_id=calc.id,
            coordinate_index=1,
            coordinate_kind=ScanCoordinateKind.dihedral,
            atom1_index=1,
            atom2_index=2,
            atom3_index=3,
            atom4_index=4,
            step_count=n_points,
            step_size=step,
            start_value=start_value,
            end_value=start_value + step * (n_points - 1),
            value_unit=CoordinateUnit.degree,
        )
        db_session.add(coordinate)
        db_session.flush()

        for i in range(n_points):
            relative_value = i * step  # 0, 8, 16, ... -- ADR 0019's axis
            absolute_dihedral = ((start_value + relative_value + 180.0) % 360.0) - 180.0
            atoms = _quartet_at_dihedral(absolute_dihedral)
            geometry = _make_geometry_for_quartet(db_session, atoms)

            point = CalculationScanPoint(
                calculation_id=calc.id,
                point_index=i + 1,
                geometry_id=geometry.id,
            )
            db_session.add(point)
            db_session.flush()
            db_session.add(
                CalculationScanPointCoordinateValue(
                    calculation_id=calc.id,
                    point_index=i + 1,
                    coordinate_index=1,
                    coordinate_value=relative_value,  # the legacy, non-conforming value
                    value_unit=CoordinateUnit.degree,
                )
            )
        db_session.flush()

        (series,) = build_scan_coordinate_conformance_report(db_session, calc.id)
        assert isinstance(series, CoordinateSeriesConformance)
        assert series.classification is SeriesClassification.consistent_with_legacy_relative_axis
        assert series.n_not_applicable == 0
        assert series.n_not_checkable == 0
        # stored - expected == implied_constant for every point; the real
        # migration would compute expected = stored - implied_constant.
        # The classifier reached this number empirically, from residuals
        # alone -- it never touched coordinate.start_value.
        expected_constant = ((-start_value + 180.0) % 360.0) - 180.0
        assert series.implied_constant == pytest.approx(expected_constant, abs=1e-3)

    def test_scan_point_with_no_geometry_is_not_applicable_not_a_pass(self, db_session) -> None:
        calc = _make_scan_calculation(db_session)
        coordinate = CalculationScanCoordinate(
            calculation_id=calc.id,
            coordinate_index=1,
            coordinate_kind=ScanCoordinateKind.dihedral,
            atom1_index=1,
            atom2_index=2,
            atom3_index=3,
            atom4_index=4,
            value_unit=CoordinateUnit.degree,
        )
        db_session.add(coordinate)
        db_session.flush()

        point = CalculationScanPoint(calculation_id=calc.id, point_index=1, geometry_id=None)
        db_session.add(point)
        db_session.flush()
        db_session.add(
            CalculationScanPointCoordinateValue(
                calculation_id=calc.id,
                point_index=1,
                coordinate_index=1,
                coordinate_value=30.0,
                value_unit=CoordinateUnit.degree,
            )
        )
        db_session.flush()

        (series,) = build_scan_coordinate_conformance_report(db_session, calc.id)
        assert series.n_not_applicable == 1
        assert series.points[0].status is PointStatus.not_applicable
        assert series.classification is SeriesClassification.insufficient_data

    def test_non_scan_calculation_yields_no_report(self, db_session) -> None:
        species = make_species(db_session)
        species_entry = make_species_entry(db_session, species)
        calc = Calculation(type=CalculationType.opt, species_entry_id=species_entry.id)
        db_session.add(calc)
        db_session.flush()
        assert build_scan_coordinate_conformance_report(db_session, calc.id) == ()

    def test_unknown_calculation_id_yields_no_report(self, db_session) -> None:
        assert build_scan_coordinate_conformance_report(db_session, -1) == ()
