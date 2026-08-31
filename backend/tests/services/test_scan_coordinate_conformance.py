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

import math
import random

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
    TOLERANCE_CEILING_ANGSTROM,
    TOLERANCE_CEILING_DEGREES,
    TOLERANCE_FLOOR_ANGSTROM,
    TOLERANCE_FLOOR_DEGREES,
    CoordinateSeriesConformance,
    PointCoordinateConformance,
    PointStatus,
    SeriesClassification,
    _residual_distribution,
    _wrap_deg,
    bond_angle_deg,
    bond_length_angstrom,
    build_scan_coordinate_conformance_report,
    classify_series,
    dihedral_deg,
    evaluate_point_coordinate_conformance,
    infer_precision_decimals,
)
from tests.services._scan_geometry import (
    next_geom_hash,
    place_next_atom,
    rdkit_dihedral_deg,
)
from tests.services.scientific_read._factories import make_species, make_species_entry

# ---------------------------------------------------------------------------
# Independent geometry construction (NeRF placement, shared with
# tests/scripts/test_scan_coordinate_conformance_report.py via
# tests/services/_scan_geometry.py) -- ground truth for the pure
# vector-math tests below.
#
# A prior version of this file negated the dihedral passed into the NeRF
# placement and justified it as "measured empirically against dihedral_deg
# below" -- which quietly matched a sign bug in dihedral_deg itself instead
# of catching it, for exactly the reason the (still true) warning below
# names: a round-trip test between two hand-written formulas cannot tell a
# shared sign error from correctness. The fix is that neither
# ``dihedral_deg`` nor ``place_next_atom`` is asserted correct by the
# other any more -- both are checked directly against RDKit,
# independently, in ``TestDihedralDegAgreesWithRDKit`` and
# ``TestPlacementHelperAgreesWithRDKit``. The round-trip test that
# follows them is kept only as a *secondary* consistency check between two
# already-independently-verified implementations, never as the sole
# evidence.
# ---------------------------------------------------------------------------

# A fixed, well-conditioned backbone: A-B-C with a realistic bond length and
# neither flanking angle anywhere near collinear.
_A = np.array([0.0, 0.0, 0.0])
_B = np.array([1.50, 0.0, 0.0])
_C = np.array([2.50, 1.30, 0.0])
_BOND_LENGTH_CD = 1.09
_ANGLE_BCD = 109.5


def _quartet_at_dihedral(dihedral_value: float) -> tuple[np.ndarray, ...]:
    d = place_next_atom(
        _A,
        _B,
        _C,
        bond_length=_BOND_LENGTH_CD,
        bond_angle_deg=_ANGLE_BCD,
        dihedral_deg=dihedral_value,
    )
    return _A, _B, _C, d


class TestDihedralDegAgreesWithRDKit:
    """The external pin. Not validated against a second formula in this
    repository -- RDKit is already an environment dependency, used
    throughout ``app.chemistry``, and is the authority a sign convention
    is checked against."""

    @pytest.mark.parametrize(
        "target", [0.0, 1.0, 30.0, 60.0, 90.0, 105.8, 120.0, 150.0, 179.0, -30.0, -90.0, -150.0]
    )
    def test_matches_rdkit_at_fixed_targets(self, target: float) -> None:
        a, b, c, d = _quartet_at_dihedral(target)
        rdkit_value = rdkit_dihedral_deg(a, b, c, d)
        assert dihedral_deg(a, b, c, d) == pytest.approx(rdkit_value, abs=1e-6)

    def test_matches_rdkit_over_random_configurations(self) -> None:
        rng = random.Random(20260831)
        max_diff = 0.0
        n_checked = 0
        for _ in range(300):
            pts = [np.array([rng.uniform(-3.0, 3.0) for _ in range(3)]) for _ in range(4)]
            a, b, c, d = pts
            if (
                np.linalg.norm(b - a) < 0.3
                or np.linalg.norm(c - b) < 0.3
                or np.linalg.norm(d - c) < 0.3
            ):
                continue
            theta_123 = bond_angle_deg(a, b, c)
            theta_234 = bond_angle_deg(b, c, d)
            if min(math.sin(math.radians(theta_123)), math.sin(math.radians(theta_234))) < 0.1:
                continue
            rdkit_value = rdkit_dihedral_deg(a, b, c, d)
            ours = dihedral_deg(a, b, c, d)
            diff = abs(((ours - rdkit_value + 180.0) % 360.0) - 180.0)
            max_diff = max(max_diff, diff)
            n_checked += 1
        assert n_checked > 100, "too many degenerate quartets were skipped to trust this"
        assert max_diff < 1e-6, f"dihedral_deg disagrees with RDKit by up to {max_diff} degrees"


class TestPlacementHelperAgreesWithRDKit:
    """``place_next_atom``'s own correctness, checked independently of
    ``dihedral_deg`` -- via RDKit measuring the quartet it built, not via
    this module's own formula."""

    @pytest.mark.parametrize(
        "target", [0.0, 1.0, 30.0, 60.0, 90.0, 120.0, 150.0, 179.0, -30.0, -90.0, -150.0]
    )
    def test_rdkit_measures_the_requested_dihedral(self, target: float) -> None:
        a, b, c, d = _quartet_at_dihedral(target)
        assert rdkit_dihedral_deg(a, b, c, d) == pytest.approx(target, abs=1e-6)


class TestPlacementHelperAgreesWithDihedralDeg:
    """Secondary consistency check between two implementations that are
    each *already* pinned to RDKit independently above. Kept because it is
    still useful (it also covers ``bond_length_angstrom``/``bond_angle_deg``
    against the same fixture), but it is not, on its own, evidence that
    either is correct -- see the two ``...AgreesWithRDKit`` classes above.
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

    def test_bond_angle_survives_floating_point_cos_theta_overshoot(self) -> None:
        """Kills the cos_theta clamp-deletion mutant.

        Without ``min(1.0, max(-1.0, cos_theta))``, ``math.acos`` raises
        ``ValueError`` on a domain input outside [-1, 1] -- which
        ordinary floating-point rounding produces for parallel vectors of
        very different magnitude. Found by search, not constructed by
        hand: ``b=(0,0,0)``, these ``a``/``c`` compute
        ``dot(v1, v2) / (|v1| |v2|) == 1.0000000000000002``.
        """
        b = np.array([0.0, 0.0, 0.0])
        a = np.array([-812.2808264515302, -943.3050469559873, 671.5302078397394])
        c = np.array([-351574.4668224095, -408284.86667994584, 290654.25046135345])
        # Confirm the fixture actually reproduces the overshoot (not just
        # asserted by fiat) -- if numpy's arithmetic ever changes this
        # under us, this line fails loudly instead of the test degrading
        # into "acos never raised, who knows why".
        v1, v2 = a - b, c - b
        raw_cos_theta = float(np.dot(v1, v2)) / (
            float(np.linalg.norm(v1)) * float(np.linalg.norm(v2))
        )
        assert raw_cos_theta > 1.0, "fixture no longer reproduces the fp overshoot"

        assert bond_angle_deg(a, b, c) == pytest.approx(0.0, abs=1e-6)


class TestWrapDeg:
    """Direct coverage of ``_wrap_deg``'s branch cut.

    Added to kill two mutants a review found surviving: replacing the
    whole function with the identity, and deleting the ``<= -180``
    correction branch. Neither showed up anywhere else in this suite --
    every fixture elsewhere used residuals comfortably inside
    ``(-180, 180)``, never at the boundary itself.
    """

    def test_ordinary_value_is_unchanged(self) -> None:
        assert _wrap_deg(10.0) == pytest.approx(10.0)

    def test_identity_mutant_is_killed(self) -> None:
        # 200 degrees is not a valid member of (-180, 180]; the identity
        # mutant would return it unchanged.
        assert _wrap_deg(200.0) == pytest.approx(-160.0)
        assert _wrap_deg(-200.0) == pytest.approx(160.0)
        assert _wrap_deg(359.0) == pytest.approx(-1.0)

    def test_plus_180_stays_plus_180(self) -> None:
        """The interval is (-180, 180]: +180 is a member, -180 is not.

        Kills the ``<= -180`` branch-deletion mutant: without that
        correction, the raw ``(value + 180) % 360 - 180`` formula sends
        180.0 to -180.0.
        """
        assert _wrap_deg(180.0) == pytest.approx(180.0)

    def test_minus_180_maps_to_plus_180(self) -> None:
        assert _wrap_deg(-180.0) == pytest.approx(180.0)

    def test_540_wraps_to_plus_180(self) -> None:
        assert _wrap_deg(540.0) == pytest.approx(180.0)


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

    def test_a_real_error_inside_a_sane_tolerance_still_deviates(self) -> None:
        """Direction one of the ceiling fix's two-direction proof: at a
        precision fine enough for the tolerance to stay well under the
        ceiling, a real error must still be caught. (This is also
        ``test_catches_a_hundredth_of_a_degree_error`` above, at 6 dp;
        this repeats it at 3 dp, where the derived tolerance is ~0.213
        degrees -- comfortably below the 1 degree ceiling -- to confirm
        the ceiling fix did not also loosen the ordinary case.)
        """
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.0 + 5.0,  # 5 degrees, well past a ~0.21 degree tolerance
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=3,
            point_index=1,
        )
        assert result.tolerance is not None
        assert result.tolerance < TOLERANCE_CEILING_DEGREES
        assert result.status is PointStatus.deviates

    def test_a_hopeless_tolerance_is_not_checkable_not_a_pass(self) -> None:
        """Direction two: the finding itself. Before the ceiling existed,
        a coordinate deposited at 0 decimal places derived a ~184.9 degree
        tolerance and a 150 degree error silently CONFORMED -- a false
        clean bill of health from a detector whose whole job is catching
        exactly that. It must now report ``not_checkable``.
        """
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.0 + 150.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=0,
            point_index=1,
        )
        assert result.status is PointStatus.not_checkable
        assert result.status is not PointStatus.conforms
        assert result.tolerance is None
        assert "exceeds the" in result.reason and "ceiling" in result.reason

    def test_a_hopeless_bond_tolerance_is_not_checkable_not_a_pass(self) -> None:
        """Same finding, the bond/Angstrom branch."""
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.5, 0.0, 0.0])
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.bond,
            stored_value=1.5 + 1.0,  # 1 Angstrom error
            stored_unit=CoordinateUnit.angstrom,
            coords_by_atom={1: a, 2: b},
            atom_indices=(1, 2),
            precision_decimals=0,
            point_index=1,
        )
        assert result.status is PointStatus.not_checkable
        assert result.tolerance is None
        assert "exceeds the" in result.reason
        assert f"{TOLERANCE_CEILING_ANGSTROM:g}" in result.reason

    def test_1dp_dihedral_matches_the_reviewed_measurement(self) -> None:
        """Pins the exact numbers a review measured against the unbounded
        tolerance: 1 decimal place derives a ~21.1 degree tolerance, and a
        15 degree error used to conform under it."""
        a, b, c, d = _quartet_at_dihedral(47.0)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.0 + 15.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=1,
            point_index=1,
        )
        assert result.status is PointStatus.not_checkable
        assert result.tolerance is None

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

    def test_not_checkable_when_only_one_flanking_angle_is_near_collinear(self) -> None:
        """The gate is ``min(sin_123, sin_234) < threshold``, not ``max``.

        Kills a mutant a review found surviving: replacing ``min`` with
        ``max``. The prior collinearity test made *both* flanking angles
        near-collinear at once, so ``min`` and ``max`` of two small
        numbers agree and the mutant passed unnoticed. Here only
        theta_234 is degenerate; theta_123 is a clean 90 degrees.
        ``max`` of (~1.0, ~0.0008) is ~1.0 -- comfortably above the
        threshold -- so the mutant would (wrongly) treat this quartet as
        checkable.
        """
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 0.0, 0.0])
        c = np.array([0.0, 1.5, 0.0])
        d = np.array([0.001, 2.7, 0.0])  # b-c-d nearly collinear
        theta_123 = bond_angle_deg(a, b, c)
        theta_234 = bond_angle_deg(b, c, d)
        sin_123 = math.sin(math.radians(theta_123))
        sin_234 = math.sin(math.radians(theta_234))
        assert sin_123 > 0.9, "theta_123 must be well-conditioned for this test to mean anything"
        assert sin_234 < DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA
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

    def test_dihedral_residual_wraps_across_the_360_branch_cut(self) -> None:
        """Kills the raw-subtraction mutant (deleting the ``_wrap_deg`` call).

        The true dihedral is +179.5 degrees; the stored value is -179.5,
        one degree away going the short way around the circle. A raw
        ``stored - expected`` gives -359.0, which would report ``deviates``
        with a wildly wrong residual; the wrapped residual is +/-1.0 and
        the tolerance (well under one degree) still correctly flags it as
        ``deviates`` -- but with a residual that says what actually
        happened, not the aliased -359.
        """
        a, b, c, d = _quartet_at_dihedral(179.5)
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=-179.5,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=6,
            point_index=1,
        )
        assert abs(result.residual) < 2.0, (
            f"residual {result.residual} looks like the unwrapped -359, not "
            "the true ~1 degree offset"
        )
        assert result.status is PointStatus.deviates

    def test_angle_residual_wraps_across_the_360_branch_cut(self) -> None:
        """Same mutant, the angle branch (a synthetic case: nothing stops a
        stored ``angle`` value from being deposited outside [0, 180])."""
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 0.0, 0.0])
        c = np.array([0.0, 1.0, 0.0])  # bond_angle_deg(a, b, c) == 90.0
        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.angle,
            stored_value=-270.0,  # -270 == 90 (mod 360)
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c},
            atom_indices=(1, 2, 3),
            precision_decimals=6,
            point_index=1,
        )
        assert result.residual == pytest.approx(0.0, abs=1e-6), (
            f"residual {result.residual} looks like the unwrapped -360, not 0"
        )
        assert result.status is PointStatus.conforms

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


class TestTheDerivedToleranceIsOperative:
    """Pins the precision-derived part of the tolerance directly, at 3
    decimal places -- not the corpus's real 6, where a review found it is
    always below the floor and never actually binds (the register's own
    prose now says so explicitly).

    The expected numbers below are computed by an independent formula
    written *in this test*, not by calling
    ``_sigma_pred_bond_angstrom``/``_sigma_pred_angle_deg``/
    ``_sigma_pred_dihedral_deg`` or ``_quantization_sigma_angstrom`` from
    the module under test -- calling them would validate a mutated
    formula against itself, which is exactly the mistake the dihedral
    sign bug taught this suite not to make. Kills, in one test class:
    each ``_sigma_pred_*`` collapsing to ``return 0.0``, and the
    quantization noise formula's ``lsb / sqrt(12)`` collapsing to
    ``lsb / 12`` (a factor of ``sqrt(12)`` ~ 3.46 apart from correct --
    comfortably outside the 1% tolerance below).
    """

    @staticmethod
    def _sigma_pos_angstrom(precision_decimals: int) -> float:
        """Independently re-derived: uniform rounding error in
        ``[-LSB/2, LSB/2]`` has standard deviation ``LSB / sqrt(12)``."""
        lsb = 10.0**-precision_decimals
        return lsb / math.sqrt(12.0)

    def test_bond_tolerance_at_3dp(self) -> None:
        a = np.array([0.0, 0.0, 0.0])
        b = np.array([1.5, 0.0, 0.0])
        sigma_pos = self._sigma_pos_angstrom(3)
        expected_sigma_pred = math.sqrt(2.0) * sigma_pos  # two atoms, unit sensitivity
        expected_tolerance = max(TOLERANCE_FLOOR_ANGSTROM, 10.0 * expected_sigma_pred)

        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.bond,
            stored_value=1.5,
            stored_unit=CoordinateUnit.angstrom,
            coords_by_atom={1: a, 2: b},
            atom_indices=(1, 2),
            precision_decimals=3,
            point_index=1,
        )
        assert result.tolerance == pytest.approx(expected_tolerance, rel=1e-3)
        assert result.tolerance > TOLERANCE_FLOOR_ANGSTROM, (
            "fixture must sit above the floor, or this pins the floor, not "
            "the derived term"
        )

    def test_angle_tolerance_at_3dp(self) -> None:
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 0.0, 0.0])
        c = np.array([0.0, 1.0, 0.0])  # r_ab = r_bc = 1.0, a clean right angle
        sigma_pos = self._sigma_pos_angstrom(3)
        sensitivity = math.sqrt((1.0 / 1.0) ** 2 + (1.0 / 1.0) ** 2)
        expected_sigma_pred_deg = math.degrees(sigma_pos * sensitivity)
        expected_tolerance = max(TOLERANCE_FLOOR_DEGREES, 10.0 * expected_sigma_pred_deg)

        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.angle,
            stored_value=90.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c},
            atom_indices=(1, 2, 3),
            precision_decimals=3,
            point_index=1,
        )
        assert result.tolerance == pytest.approx(expected_tolerance, rel=1e-3)
        assert result.tolerance > TOLERANCE_FLOOR_DEGREES

    def test_dihedral_tolerance_at_3dp(self) -> None:
        a, b, c, d = _quartet_at_dihedral(47.0)
        r_ab = bond_length_angstrom(a, b)
        r_cd = bond_length_angstrom(c, d)
        theta_123 = bond_angle_deg(a, b, c)
        theta_234 = bond_angle_deg(b, c, d)
        sigma_pos = self._sigma_pos_angstrom(3)
        sensitivity = math.sqrt(
            (1.0 / (r_ab * math.sin(math.radians(theta_123)))) ** 2
            + (1.0 / (r_cd * math.sin(math.radians(theta_234)))) ** 2
        )
        expected_sigma_pred_deg = math.degrees(sigma_pos * sensitivity)
        expected_tolerance = max(TOLERANCE_FLOOR_DEGREES, 10.0 * expected_sigma_pred_deg)

        result = evaluate_point_coordinate_conformance(
            kind=ScanCoordinateKind.dihedral,
            stored_value=47.0,
            stored_unit=CoordinateUnit.degree,
            coords_by_atom={1: a, 2: b, 3: c, 4: d},
            atom_indices=(1, 2, 3, 4),
            precision_decimals=3,
            point_index=1,
        )
        assert result.tolerance == pytest.approx(expected_tolerance, rel=1e-3)
        assert result.tolerance > TOLERANCE_FLOOR_DEGREES


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

    def test_trusts_a_genuinely_coarse_precision_given_enough_samples(self) -> None:
        """The exact defect a review found: this used to check the wrong
        quantity (the inferred decimal count, not the sample size) and
        fell back to 6 dp here, tightening the tolerance far past what a
        legitimately-coarse-but-conforming 2 dp deposit could pass."""
        values = [1.23, 4.56, 7.89, 0.12, 3.45, -2.34, 8.76]  # 7 values, all 2 dp
        assert infer_precision_decimals(values) == 2

    def test_trusts_a_genuinely_zero_decimal_sample(self) -> None:
        """A large sample that needs zero decimals is still trusted -- the
        guard is about how many numbers were seen, not what they imply."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert infer_precision_decimals(values) == 0

    def test_falls_back_when_the_sample_is_too_small(self) -> None:
        """Below _MIN_SAMPLE_SIZE_TO_TRUST, regardless of what those few
        values would themselves imply."""
        values = [1.0, 2.0]  # only 2 values; would infer 0 dp if trusted
        assert infer_precision_decimals(values) == 6

    def test_falls_back_on_empty_sample(self) -> None:
        assert infer_precision_decimals([]) == 6


class TestResidualDistribution:
    """Kills a p95 -> p50 mutant. With no test at all on this function, a
    review found it -- a report-script-facing number nobody was checking.
    Uses 20 distinct magnitudes so median (10.5) and the 95th percentile
    (19.05, numpy's default linear interpolation) are unmistakably
    different numbers -- a mutant returning the median under the p95 name
    fails immediately rather than by coincidence."""

    def test_min_median_p95_max_are_correct_and_distinct(self) -> None:
        residuals = [float(i) for i in range(1, 21)]  # 1.0 .. 20.0
        dist = _residual_distribution(residuals)
        assert dist is not None
        assert dist.min == pytest.approx(1.0)
        assert dist.median == pytest.approx(10.5)
        assert dist.p95 == pytest.approx(19.05)
        assert dist.max == pytest.approx(20.0)

    def test_uses_the_absolute_value_of_signed_residuals(self) -> None:
        residuals = [-20.0, -1.0, 1.0, 20.0]
        dist = _residual_distribution(residuals)
        assert dist is not None
        assert dist.min == pytest.approx(1.0)
        assert dist.max == pytest.approx(20.0)

    def test_empty_is_none(self) -> None:
        assert _residual_distribution([]) is None


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

    def test_constant_offset_at_the_branch_cut_is_still_recognised(self) -> None:
        """The real defect a review found: a constant residual near
        +/-180 degrees -- the shape produced by a series whose
        ``start_value`` is 180.0 -- alternates sign under ordinary
        floating-point noise (+179.9, -179.9, +179.9, ...). A linear
        ``mean``/``std`` over that averages to ~0 with a spread of ~180,
        which reads as totally incoherent for a series that is in fact
        perfectly constant. The circular mean/spread must recognise it.
        """
        alternating = [179.9, -179.9, 179.9, -179.9, 179.9, -179.9, 179.9, -179.9]
        points = [_checkable_point(i, r) for i, r in enumerate(alternating, start=1)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.consistent_with_legacy_relative_axis
        # The constant is reported on the (-180, 180] branch _wrap_deg uses;
        # 179.9 and -179.9 are ~0.2 degrees apart on the circle, symmetric
        # around +/-180, so the circular mean lands at +180 (not -180,
        # which the plain arithmetic mean would give if it worked at all).
        assert abs(result.implied_constant) == pytest.approx(180.0, abs=0.5)

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

    def test_linear_ramp_crossing_the_branch_cut_is_still_a_ramp(self) -> None:
        """A "also decide" item from review: an un-wrap-aware polyfit is
        unreachable for any real periodic series whose ramp crosses the
        360-degree cut, because the wrapped observation aliases into two
        disconnected halves that no single line fits.

        True residual is a clean ramp from -170 to +190 in steps of 40;
        the ninth observation (190) is what a real, wrapped dihedral
        residual would actually read: -170. Unwrapped before fitting, the
        line is recovered exactly.
        """
        true_ramp = [-170.0 + 40.0 * i for i in range(10)]
        observed = [_wrap_deg(v) for v in true_ramp]
        assert observed[-1] == pytest.approx(-170.0), "fixture must actually cross the cut"
        points = [_checkable_point(i, r) for i, r in enumerate(observed, start=1)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.linear_ramp
        assert result.implied_constant == pytest.approx(40.0, rel=0.05)

    def test_linear_ramp_with_index_holes_is_not_falsely_detected(self) -> None:
        """The exact defect a review reproduced: unwrapping ignores holes.

        ``np.unwrap`` reasons about array-adjacent elements, not
        point_index values. With a true slope of 100 degrees/point and
        every other point missing (not_checkable), the surviving points
        are really 200 degrees apart -- past unwrap's 180-degree
        discontinuity threshold -- so unconditionally unwrapping them
        aliased into a *confidently wrong* fit: slope -80.0 (fit residual
        spread ~1.4e-13, i.e. a "perfect" line) against a true slope of
        +100. That is a corrective migration being handed the wrong
        number with no sign anything was off.

        With the gap guard, the same input must not report ``linear_ramp``
        at all -- a missed detection is the honest failure mode here, not
        a wrong ``implied_constant``.
        """
        indices = [1, 3, 5, 7, 9, 11, 13]  # every other point_index is a hole
        true_ramp = [100.0 * i for i in indices]
        observed = [_wrap_deg(v) for v in true_ramp]
        # the fixture actually exercises the failure: two checkable points
        # in a row really are more than 180 degrees apart on the true ramp
        assert abs(true_ramp[1] - true_ramp[0]) > 180.0

        points = [
            PointCoordinateConformance(
                point_index=idx,
                status=PointStatus.deviates,
                stored_value=0.0,
                expected_value=-r,
                residual=r,
                tolerance=0.5,
            )
            for idx, r in zip(indices, observed, strict=True)
        ]
        points += [
            PointCoordinateConformance(
                point_index=hole, status=PointStatus.not_checkable, reason="hole"
            )
            for hole in (2, 4, 6, 8, 10, 12)
        ]

        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is not SeriesClassification.linear_ramp
        if result.implied_constant is not None:
            assert result.implied_constant != pytest.approx(-80.0, abs=5.0), (
                "reproduces the exact wrong slope a review found: -80.0 "
                "against a true slope of +100"
            )

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

    def test_partial_deviation_is_neither_outlier_nor_constant_pattern(self) -> None:
        """Kills the ``n_deviating == n`` -> ``n_deviating >= 1`` mutant.

        Three of six points conform (near-zero residual), three deviate
        incoherently. The correct gate requires *every* checkable point to
        deviate before the constant/ramp analysis runs; a mutant that
        loosens it to "at least one" would fold the three conforming,
        near-zero residuals into that analysis and never reach the
        fallback branch this test pins -- so its detail string, produced
        only by that fallback, is the signal a wrong branch was taken.
        """
        residuals = [0.0001, 6.0, 0.0001, -9.0, 0.0001, 13.0]
        points = [_checkable_point(i, r) for i, r in enumerate(residuals, start=1)]
        result = classify_series(points, step_size=8.0, is_periodic=True)
        assert result.classification is SeriesClassification.no_pattern_detected
        assert result.implied_constant is None
        assert "neither a single outlier nor every point" in result.detail
        assert "3 of 6" in result.detail

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


def _make_geometry_for_quartet(db_session, atoms: tuple[np.ndarray, ...]) -> Geometry:
    geometry = Geometry(natoms=len(atoms), geom_hash=next_geom_hash())
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
