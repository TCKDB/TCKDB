"""Does a deposited scan coordinate value match its own geometry?

ADR 0020 (``docs/adr/0020-a-scan-coordinate-value-is-the-coordinate-itself.md``)
fixes what ``calc_scan_point_coordinate_value.coordinate_value`` means: the
internal coordinate itself, in its own unit, at the point's own sampled
geometry -- never a displacement, never relative to ``calc_scan_coordinate
.start_value``. This module is the conformance check the ADR asks for: it
recomputes the coordinate from the point's own stored Cartesian geometry and
compares, **without ever reading ``start_value``/``end_value``**, which ADR
0020 fixes as requested-grid metadata rather than an anchor to add back.

**Nothing is written and nothing is refused.** This mirrors
:mod:`app.services.scientific_read.imaginary_mode_projection`: a session is
opened, geometry rows are read, and a verdict is returned. There is no table
to persist it to, and under ADR 0008 the tier is ``warn`` regardless -- ADR
0020 is explicit that a mis-stated axis and a mis-attached geometry present
identically to this comparison, so a disagreement is evidence that
*something* is wrong without saying which thing, and refusing a deposit over
that ambiguity would discard correct energies. The check is not wired into
any upload or read path; it is run standalone by
``backend/scripts/validation/scan_coordinate_conformance_report.py`` as the
diagnostic ADR 0020 asks for ahead of the corrective migration it defers to a
separate, later change.

**The deposited corpus is expected to fail this.** All 46 series deposited
before ADR 0020 hold ADR 0019's superseded relative-sweep convention, and
this check reports every one of them as non-conforming. That is the intended
finding, not a bug to chase: it is exactly what the follow-up migration needs
to know, one series at a time.

Recomputable coordinate kinds
------------------------------
``bond`` (distance, Angstrom) and ``angle``/``dihedral`` (degrees, standard
vector formulae) are recomputed directly from the point's own
``geometry_atom`` rows. ``improper`` is deliberately **not** recomputed: ADR
0020 records that TCKDB stores no field distinguishing an out-of-plane-angle
convention from a proper-style torsion, and different codes use different
conventions for the same four atoms. Guessing one would be exactly the
inference ADR 0011 refuses to make for atom maps, applied to a coordinate
convention instead -- so an ``improper`` point is reported ``not_applicable``,
never compared.

A dihedral quartet with a near-collinear flanking angle is reported
``not_checkable`` rather than compared: ADR 0020 names
``min(sin(theta_123), sin(theta_234)) < 0.05`` as the point past which "the
dihedral is not a usable coordinate at all", mirroring what the producing
tools themselves do.

Tolerance is derived, never fixed by hand
------------------------------------------
Coordinates are deposited at finite precision -- 6 decimal places on the
current corpus, inferred from the data where the sample supports it and
falling back to 6 otherwise (:func:`infer_precision_decimals`). Rounding to
``10**-precision_decimals`` Angstrom is uniform quantization noise with
1-sigma :func:`_quantization_sigma_angstrom`, which propagates into a bond
length with unit sensitivity and into a bond angle or dihedral through the
standard ``1/(r * sin(theta))`` ill-conditioning of an internal coordinate as
its defining vectors approach collinearity. ``tol = max(floor, 10 *
sigma_pred)`` (degrees for angle/dihedral, Angstrom for bond) is what
:func:`evaluate_point_coordinate_conformance` compares residuals against.
The degree floor, ``1e-3``, is fixed by the same reasoning ADR 0020 gives for
the check overall: never let it collapse below what a 6-decimal-place
deposit can distinguish, and explicitly **not** the 0.5-1.0 degree floor
that was rejected as roughly four orders of magnitude too loose --
correctly stored data reproduces to about 1.5e-4 degrees (ADR 0020), and
``1e-3`` still catches an injected 0.01 degree error with two orders of
magnitude to spare. The Angstrom floor, ``1e-4``, is this module's own
choice by the same logic -- ADR 0020 only fixes the degree number, so the
length analogue is documented here as an assumption for the follow-up
migration to revisit, not inherited from the ADR.

Classification: what a residual pattern implies
-------------------------------------------------
A single point's disagreement says only "this point is off". Read across a
whole series, the *shape* of the disagreement is diagnostic, and
:func:`classify_series` names four shapes plus "no pattern" and
"insufficient data":

* every checkable point off by the same constant -- ``consistent_with_
  legacy_relative_axis``, with the constant reported so a corrective
  migration can read it directly (on the real corpus this constant is
  ``-start_value mod 360``, though the classifier never reads
  ``start_value`` to reach that number -- it is found empirically);
* the same constant, but matching the coordinate's own grid ``step_size``
  -- ``uniform_one_step_offset``, consistent with a point paired to its
  neighbour's geometry;
* a residual that grows linearly with point index -- ``linear_ramp``,
  consistent with a direction or step-size disagreement;
* exactly one checkable point off while the rest agree -- ``single_
  outlier``, consistent with one mis-attached geometry.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, CoordinateUnit, ScanCoordinateKind
from app.db.models.geometry import GeometryAtom
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    PythonCheck,
    ScientificCheck,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fallback deposit precision (decimal places on a Cartesian coordinate, in
#: Angstrom) when it cannot be inferred from the sample itself. Measured as
#: the precision of the current (pre-ADR-0020) corpus; see ADR 0020's
#: "Two limits belong on the record" paragraph.
FALLBACK_PRECISION_DECIMALS = 6

#: Below this sample size, :func:`infer_precision_decimals` does not trust
#: what it found and falls back -- a handful of coordinates that happen to
#: round-trip at low precision is not evidence the whole geometry was
#: deposited that coarsely.
_MIN_PRECISION_DECIMALS_TO_TRUST = 3

#: ADR 0020: "where a quartet is near-collinear the dihedral is not a usable
#: coordinate at all". Below this, on either flanking bond angle, the point
#: is reported ``not_checkable``.
DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA = 0.05

#: Never let ten times the precision-derived sigma collapse the tolerance
#: below what a 6-decimal-place deposit can distinguish. See the module
#: docstring for the rejected 0.5/1.0 degree alternative.
TOLERANCE_FLOOR_DEGREES = 1e-3

#: This module's own choice, not fixed by ADR 0020 (which only derives the
#: degree floor). Documented here as an assumption for a future revision.
TOLERANCE_FLOOR_ANGSTROM = 1e-4

#: A degenerate (zero-length) bond makes every downstream direction
#: undefined; below this length a point is reported ``not_checkable``
#: rather than divided by a near-zero number.
_MIN_BOND_LENGTH_ANGSTROM = 1e-6

#: Kinds ADR 0020 gives a recomputable convention for, and the unit that
#: convention is expressed in. ``improper`` is deliberately absent -- see
#: the module docstring.
_RECOMPUTABLE_UNIT_FOR_KIND: dict[ScanCoordinateKind, CoordinateUnit] = {
    ScanCoordinateKind.bond: CoordinateUnit.angstrom,
    ScanCoordinateKind.angle: CoordinateUnit.degree,
    ScanCoordinateKind.dihedral: CoordinateUnit.degree,
}

#: A pattern is not asserted from fewer checkable points than this -- three
#: is the minimum that can tell "one outlier" apart from "everything is
#: off by the same amount" at all.
MIN_CHECKABLE_POINTS_FOR_PATTERN = 3


# ---------------------------------------------------------------------------
# Vector geometry -- pure functions, no database, no units beyond Angstrom/radian
# ---------------------------------------------------------------------------


def _wrap_deg(value: float) -> float:
    """Wrap an angle (or angle difference) into ``(-180, 180]`` degrees."""
    wrapped = (value + 180.0) % 360.0 - 180.0
    # The formula above sends 180.0 to -180.0; ADR 0020's own half-open
    # interval keeps +180 on the positive side.
    if wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


def bond_length_angstrom(a: np.ndarray, b: np.ndarray) -> float:
    """Distance between two atoms, in whatever unit the coordinates carry."""
    return float(np.linalg.norm(b - a))


def bond_angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Angle at ``b`` formed by ``a-b-c``, in degrees, in ``[0, 180]``."""
    v1 = a - b
    v2 = c - b
    n1 = np.linalg.norm(v1)
    n2 = np.linalg.norm(v2)
    cos_theta = float(np.dot(v1, v2)) / (n1 * n2)
    cos_theta = min(1.0, max(-1.0, cos_theta))
    return math.degrees(math.acos(cos_theta))


def dihedral_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    """Signed dihedral ``a-b-c-d``, in degrees, in ``(-180, 180]``.

    Standard "praxeolitic" formula (atan2 of the projection of the two
    wing-bond normals onto the central bond's frame), the convention shared
    by RDKit, MDAnalysis and most quantum-chemistry output parsers.
    """
    b1 = b - a
    b2 = c - b
    b3 = d - c
    b2_unit = b2 / np.linalg.norm(b2)
    n1 = np.cross(b1, b2)
    n2 = np.cross(b2, b3)
    m1 = np.cross(n1, b2_unit)
    x = float(np.dot(n1, n2))
    y = float(np.dot(m1, n2))
    return math.degrees(math.atan2(y, x))


def _quantization_sigma_angstrom(precision_decimals: int) -> float:
    """1-sigma positional noise (Angstrom) from rounding to N decimal places.

    A value rounded to the nearest ``10**-precision_decimals`` carries a
    uniform rounding error in ``[-LSB/2, LSB/2]``, whose variance is
    ``LSB**2 / 12``.
    """
    lsb = 10.0**-precision_decimals
    return lsb / math.sqrt(12.0)


def _sigma_pred_bond_angstrom(precision_decimals: int) -> float:
    """Predicted 1-sigma error on a recomputed bond length, in Angstrom.

    Two atoms each contribute independent positional noise with unit
    sensitivity (``d(distance)/d(position)`` has magnitude 1 along the bond
    direction), combined in quadrature.
    """
    sigma_pos = _quantization_sigma_angstrom(precision_decimals)
    return math.sqrt(2.0) * sigma_pos


def _sigma_pred_angle_deg(
    precision_decimals: int, r_ab: float, r_bc: float
) -> float:
    """Predicted 1-sigma error on a recomputed bond angle, in degrees.

    Displacing a terminal atom perpendicular to its bond changes the angle
    by roughly ``delta / r`` radians; the two terminal atoms' contributions
    are combined in quadrature.
    """
    sigma_pos = _quantization_sigma_angstrom(precision_decimals)
    sensitivity = math.sqrt((1.0 / r_ab) ** 2 + (1.0 / r_bc) ** 2)
    return math.degrees(sigma_pos * sensitivity)


def _sigma_pred_dihedral_deg(
    precision_decimals: int,
    r_ab: float,
    r_cd: float,
    sin_theta_123: float,
    sin_theta_234: float,
) -> float:
    """Predicted 1-sigma error on a recomputed dihedral, in degrees.

    The standard ``1/(r * sin(theta))`` conditioning of a four-atom
    dihedral: displacing a wing atom (``a`` or ``d``) perpendicular to its
    defining plane changes the torsion by roughly
    ``delta / (r * sin(theta))`` radians. The two wings' contributions are
    combined in quadrature.
    """
    sigma_pos = _quantization_sigma_angstrom(precision_decimals)
    sensitivity = math.sqrt(
        (1.0 / (r_ab * sin_theta_123)) ** 2 + (1.0 / (r_cd * sin_theta_234)) ** 2
    )
    return math.degrees(sigma_pos * sensitivity)


def _tolerance_degrees(sigma_pred_deg: float) -> float:
    return max(TOLERANCE_FLOOR_DEGREES, 10.0 * sigma_pred_deg)


def _tolerance_angstrom(sigma_pred_angstrom: float) -> float:
    return max(TOLERANCE_FLOOR_ANGSTROM, 10.0 * sigma_pred_angstrom)


def infer_precision_decimals(
    values: Iterable[float],
    *,
    fallback: int = FALLBACK_PRECISION_DECIMALS,
    max_decimals: int = 12,
) -> int:
    """How many decimal places a sample of Cartesian coordinates was deposited at.

    For each value, finds the fewest decimal places that reconstruct it
    (within floating-point noise) and takes the maximum across the sample --
    the coordinate needing the most decimals sets the precision the whole
    geometry was written at. Falls back to ``fallback`` when the sample is
    empty, or too small to trust a low reading from (see
    :data:`_MIN_PRECISION_DECIMALS_TO_TRUST` -- a handful of coordinates
    that happen to land on round numbers is not evidence of coarse
    precision).
    """
    values = [v for v in values if v is not None]
    if not values:
        return fallback

    needed = 0
    for value in values:
        for n in range(0, max_decimals + 1):
            if abs(round(value, n) - value) < 1e-9:
                needed = max(needed, n)
                break
        else:
            needed = max(needed, max_decimals)

    if needed < _MIN_PRECISION_DECIMALS_TO_TRUST:
        return fallback
    return needed


# ---------------------------------------------------------------------------
# Per-point evaluation
# ---------------------------------------------------------------------------


class PointStatus(str, Enum):
    """The four-way answer for one scan point's one coordinate."""

    #: Recomputed and agrees with the stored value within tolerance.
    conforms = "conforms"
    #: Recomputed and disagrees with the stored value beyond tolerance.
    deviates = "deviates"
    #: Not compared -- no geometry, no stored value, an unsupported kind
    #: (``improper``), or a declared unit that disagrees with the kind's
    #: implied one. Never a pass, never a failure.
    not_applicable = "not_applicable"
    #: Recomputable in principle but the quartet is too ill-conditioned to
    #: trust (near-collinear dihedral), or a defining bond has zero length.
    #: Never a pass, never a failure.
    not_checkable = "not_checkable"


@dataclass(frozen=True)
class PointCoordinateConformance:
    """One point's verdict for one scan coordinate.

    :param residual: ``stored_value - expected_value``, wrapped into
        ``(-180, 180]`` for degree kinds. ``None`` unless ``status`` is
        ``conforms`` or ``deviates``.
    :param tolerance: The derived ``tol`` this residual was compared
        against. ``None`` unless ``status`` is ``conforms`` or ``deviates``.
    """

    point_index: int
    status: PointStatus
    stored_value: float | None = None
    expected_value: float | None = None
    residual: float | None = None
    tolerance: float | None = None
    sigma_pred: float | None = None
    reason: str | None = None


def evaluate_point_coordinate_conformance(
    *,
    kind: ScanCoordinateKind,
    stored_value: float,
    stored_unit: CoordinateUnit | None,
    coords_by_atom: dict[int, np.ndarray],
    atom_indices: tuple[int, ...],
    precision_decimals: int,
    point_index: int,
) -> PointCoordinateConformance:
    """Compare one stored coordinate value against its own geometry.

    Never reads ``calc_scan_coordinate.start_value``/``end_value`` -- ADR
    0020 fixes those as grid metadata, not an anchor for the expected value.
    """
    expected_unit = _RECOMPUTABLE_UNIT_FOR_KIND.get(kind)
    if expected_unit is None:
        return PointCoordinateConformance(
            point_index=point_index,
            status=PointStatus.not_applicable,
            reason=(
                f"{kind.value} has no single recomputable convention "
                "(ADR 0020): TCKDB stores no field distinguishing one "
                "definition from another."
            ),
        )
    if stored_unit is not None and stored_unit != expected_unit:
        return PointCoordinateConformance(
            point_index=point_index,
            status=PointStatus.not_applicable,
            reason=(
                f"declared unit {stored_unit.value!r} does not match "
                f"{expected_unit.value!r}, the unit ADR 0020 implies for "
                f"kind={kind.value!r}."
            ),
        )

    coords: list[np.ndarray] = []
    for atom_index in atom_indices:
        coord = coords_by_atom.get(atom_index)
        if coord is None:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_applicable,
                reason=f"geometry has no atom_index={atom_index}",
            )
        coords.append(coord)

    if kind is ScanCoordinateKind.bond:
        a, b = coords
        r = bond_length_angstrom(a, b)
        if r < _MIN_BOND_LENGTH_ANGSTROM:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_checkable,
                reason=f"degenerate bond length ({r:.3g} Angstrom)",
            )
        expected = r
        sigma_pred = _sigma_pred_bond_angstrom(precision_decimals)
        tolerance = _tolerance_angstrom(sigma_pred)
        residual = stored_value - expected

    elif kind is ScanCoordinateKind.angle:
        a, b, c = coords
        r_ab = bond_length_angstrom(a, b)
        r_bc = bond_length_angstrom(b, c)
        if r_ab < _MIN_BOND_LENGTH_ANGSTROM or r_bc < _MIN_BOND_LENGTH_ANGSTROM:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_checkable,
                reason="degenerate bond length in the angle's defining atoms",
            )
        expected = bond_angle_deg(a, b, c)
        sigma_pred = _sigma_pred_angle_deg(precision_decimals, r_ab, r_bc)
        tolerance = _tolerance_degrees(sigma_pred)
        residual = _wrap_deg(stored_value - expected)

    else:  # dihedral
        a, b, c, d = coords
        r_ab = bond_length_angstrom(a, b)
        r_cd = bond_length_angstrom(c, d)
        if r_ab < _MIN_BOND_LENGTH_ANGSTROM or r_cd < _MIN_BOND_LENGTH_ANGSTROM:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_checkable,
                reason="degenerate bond length in the dihedral's wing atoms",
            )
        theta_123 = bond_angle_deg(a, b, c)
        theta_234 = bond_angle_deg(b, c, d)
        sin_123 = math.sin(math.radians(theta_123))
        sin_234 = math.sin(math.radians(theta_234))
        if min(sin_123, sin_234) < DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_checkable,
                reason=(
                    "near-collinear quartet: "
                    f"min(sin(theta_123), sin(theta_234)) = "
                    f"{min(sin_123, sin_234):.4g} < "
                    f"{DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA}"
                ),
            )
        expected = dihedral_deg(a, b, c, d)
        sigma_pred = _sigma_pred_dihedral_deg(
            precision_decimals, r_ab, r_cd, sin_123, sin_234
        )
        tolerance = _tolerance_degrees(sigma_pred)
        residual = _wrap_deg(stored_value - expected)

    status = PointStatus.conforms if abs(residual) <= tolerance else PointStatus.deviates
    return PointCoordinateConformance(
        point_index=point_index,
        status=status,
        stored_value=stored_value,
        expected_value=expected,
        residual=residual,
        tolerance=tolerance,
        sigma_pred=sigma_pred,
    )


# ---------------------------------------------------------------------------
# Series-level classification
# ---------------------------------------------------------------------------


class SeriesClassification(str, Enum):
    """What shape a series' checkable-point residuals take."""

    #: Every checkable point agrees with its own geometry.
    conforms = "conforms"
    #: Every checkable point is off by the same constant. On the real
    #: corpus this constant is the negative of the series' own
    #: ``start_value`` mod 360 -- ADR 0019's relative-sweep convention --
    #: though this classifier reaches the number empirically and never
    #: reads ``start_value`` to get there.
    consistent_with_legacy_relative_axis = "consistent_with_legacy_relative_axis"
    #: Residual grows roughly linearly with point index: a direction or
    #: step-size disagreement between the stored axis and the point order.
    linear_ramp = "linear_ramp"
    #: Every checkable point is off by the same constant, and that constant
    #: matches the coordinate's own grid ``step_size``: consistent with a
    #: point paired to its neighbour's geometry.
    uniform_one_step_offset = "uniform_one_step_offset"
    #: Exactly one checkable point disagrees while the rest conform:
    #: consistent with one mis-attached geometry.
    single_outlier = "single_outlier"
    #: Points disagree, but not in any of the shapes above.
    no_pattern_detected = "no_pattern_detected"
    #: Fewer than :data:`MIN_CHECKABLE_POINTS_FOR_PATTERN` checkable points;
    #: no pattern can be told apart from noise.
    insufficient_data = "insufficient_data"


@dataclass(frozen=True)
class SeriesClassificationResult:
    """The classifier's verdict for one coordinate's whole point series.

    :param implied_constant: Machine-readable number a corrective migration
        can act on directly. For ``consistent_with_legacy_relative_axis``
        and ``uniform_one_step_offset`` this is the constant residual
        (``stored - expected``) shared by every checkable point -- to
        recover the ADR-0020-conforming value, compute
        ``expected = stored - implied_constant``. For ``linear_ramp`` it is
        the fitted slope, in the coordinate's unit per unit of point index.
        ``None`` for every other classification.
    """

    classification: SeriesClassification
    implied_constant: float | None
    detail: str


#: Points are treated as agreeing with the constant/step/ramp hypothesis
#: being tested when they fall within this multiple of their own
#: tolerance. Generous on purpose: this stage asks "is there a pattern at
#: all", and :data:`PointStatus.deviates` on each point already used the
#: tight, precision-derived tolerance.
_PATTERN_TOLERANCE_MULTIPLE = 5.0


def classify_series(
    points: Sequence[PointCoordinateConformance],
    *,
    step_size: float | None,
    is_periodic: bool,
) -> SeriesClassificationResult:
    """Classify one coordinate's residual pattern across its scan points.

    :param points: Every point's :class:`PointCoordinateConformance` for
        this coordinate, in any order.
    :param step_size: ``calc_scan_coordinate.step_size`` -- grid metadata,
        read here only to recognise an off-by-one pairing, never used to
        compute an expected value.
    :param is_periodic: Whether residuals wrap at 360 degrees (``angle``,
        ``dihedral``) as opposed to a linear Angstrom difference (``bond``).
    """
    checkable = [
        p for p in points if p.status in (PointStatus.conforms, PointStatus.deviates)
    ]
    if len(checkable) < MIN_CHECKABLE_POINTS_FOR_PATTERN:
        return SeriesClassificationResult(
            classification=SeriesClassification.insufficient_data,
            implied_constant=None,
            detail=(
                f"only {len(checkable)} checkable point(s); at least "
                f"{MIN_CHECKABLE_POINTS_FOR_PATTERN} are needed to tell a "
                "pattern apart from noise."
            ),
        )

    checkable = sorted(checkable, key=lambda p: p.point_index)
    residuals = np.array([p.residual for p in checkable], dtype=float)
    tolerances = np.array([p.tolerance for p in checkable], dtype=float)
    indices = np.array([p.point_index for p in checkable], dtype=float)
    deviating = np.abs(residuals) > tolerances
    n_deviating = int(deviating.sum())
    n = len(checkable)

    if n_deviating == 0:
        return SeriesClassificationResult(
            classification=SeriesClassification.conforms,
            implied_constant=None,
            detail="every checkable point agrees with its own geometry within tolerance.",
        )

    pattern_tol = float(np.max(tolerances)) * _PATTERN_TOLERANCE_MULTIPLE

    if n_deviating == 1:
        outlier = checkable[int(np.argmax(deviating))]
        return SeriesClassificationResult(
            classification=SeriesClassification.single_outlier,
            implied_constant=outlier.residual,
            detail=(
                f"point_index={outlier.point_index} disagrees by "
                f"{outlier.residual:.6g} (tolerance {outlier.tolerance:.3g}); "
                f"the other {n - 1} checkable point(s) agree. Consistent "
                "with one mis-attached geometry."
            ),
        )

    if n_deviating == n:
        mean_residual = float(np.mean(residuals))
        spread = float(np.std(residuals))
        if spread <= pattern_tol:
            constant = _wrap_deg(mean_residual) if is_periodic else mean_residual
            if step_size is not None and step_size > 0:
                step_tol = max(pattern_tol, 0.1 * abs(step_size))
                if abs(abs(constant) - abs(step_size)) <= step_tol:
                    return SeriesClassificationResult(
                        classification=SeriesClassification.uniform_one_step_offset,
                        implied_constant=constant,
                        detail=(
                            f"every checkable point's residual is the same "
                            f"constant ({constant:.6g}), matching the grid "
                            f"step_size ({step_size:.6g}) to within "
                            f"{step_tol:.3g}. Consistent with each point's "
                            "coordinate value paired to its neighbour's "
                            "geometry (point/geometry off-by-one)."
                        ),
                    )
            return SeriesClassificationResult(
                classification=SeriesClassification.consistent_with_legacy_relative_axis,
                implied_constant=constant,
                detail=(
                    f"every checkable point's residual is the same constant "
                    f"({constant:.6g}, spread {spread:.3g}). To recover the "
                    "ADR-0020 value: expected = stored - "
                    f"({constant:.6g})."
                ),
            )

        slope, intercept = np.polyfit(indices, residuals, 1)
        fitted = slope * indices + intercept
        fit_spread = float(np.std(residuals - fitted))
        index_span = float(indices.max() - indices.min()) or 1.0
        if fit_spread <= pattern_tol and abs(slope) * index_span > pattern_tol:
            return SeriesClassificationResult(
                classification=SeriesClassification.linear_ramp,
                implied_constant=float(slope),
                detail=(
                    f"residual grows linearly with point index: slope="
                    f"{slope:.6g} per step, intercept={intercept:.6g} "
                    f"(fit residual spread {fit_spread:.3g}). Consistent "
                    "with a direction or step-size disagreement between the "
                    "stored axis and the point order."
                ),
            )

        return SeriesClassificationResult(
            classification=SeriesClassification.no_pattern_detected,
            implied_constant=None,
            detail=(
                f"all {n} checkable points deviate, but not with a constant "
                f"offset or a linear trend (residual spread {spread:.3g})."
            ),
        )

    return SeriesClassificationResult(
        classification=SeriesClassification.no_pattern_detected,
        implied_constant=None,
        detail=(
            f"{n_deviating} of {n} checkable points deviate -- neither a "
            "single outlier nor every point, so no clean pattern applies."
        ),
    )


# ---------------------------------------------------------------------------
# Residual distribution, for the report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResidualDistribution:
    """Distribution of ``|residual|`` over a series' checkable points."""

    min: float
    median: float
    p95: float
    max: float


def _residual_distribution(residuals: Sequence[float]) -> ResidualDistribution | None:
    if not residuals:
        return None
    magnitudes = np.abs(np.asarray(residuals, dtype=float))
    return ResidualDistribution(
        min=float(np.min(magnitudes)),
        median=float(np.median(magnitudes)),
        p95=float(np.percentile(magnitudes, 95)),
        max=float(np.max(magnitudes)),
    )


# ---------------------------------------------------------------------------
# Database glue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoordinateSeriesConformance:
    """One scan coordinate's full conformance report for one calculation."""

    calculation_id: int
    coordinate_index: int
    kind: ScanCoordinateKind
    declared_unit: CoordinateUnit | None
    points: tuple[PointCoordinateConformance, ...]
    classification: SeriesClassification
    implied_constant: float | None
    classification_detail: str
    residual_distribution: ResidualDistribution | None
    n_points: int
    n_not_applicable: int
    n_not_checkable: int


def build_scan_coordinate_conformance_report(
    session: Session, calculation_id: int
) -> tuple[CoordinateSeriesConformance, ...]:
    """Evaluate every scan coordinate of one calculation against its geometry.

    Read-only: opens no transaction of its own, writes nothing, and returns
    an empty tuple for a calculation that does not exist or is not a
    ``scan``.
    """
    calculation = session.get(Calculation, calculation_id)
    if calculation is None or calculation.type is not CalculationType.scan:
        return ()

    coordinates = sorted(
        calculation.scan_coordinates, key=lambda c: c.coordinate_index
    )
    points = sorted(calculation.scan_points, key=lambda p: p.point_index)
    if not coordinates:
        return ()

    geometry_ids = {p.geometry_id for p in points if p.geometry_id is not None}
    coords_by_geometry: dict[int, dict[int, np.ndarray]] = {}
    sample_components: list[float] = []
    if geometry_ids:
        atom_rows = session.scalars(
            select(GeometryAtom).where(GeometryAtom.geometry_id.in_(geometry_ids))
        ).all()
        for row in atom_rows:
            coords_by_geometry.setdefault(row.geometry_id, {})[row.atom_index] = (
                np.array([row.x, row.y, row.z], dtype=float)
            )
            sample_components.extend([row.x, row.y, row.z])

    precision_decimals = infer_precision_decimals(sample_components)

    reports: list[CoordinateSeriesConformance] = []
    for coordinate in coordinates:
        atom_indices = tuple(
            idx
            for idx in (
                coordinate.atom1_index,
                coordinate.atom2_index,
                coordinate.atom3_index,
                coordinate.atom4_index,
            )
            if idx is not None
        )

        point_results: list[PointCoordinateConformance] = []
        n_not_applicable = 0
        n_not_checkable = 0
        for point in points:
            coordinate_value = next(
                (
                    v
                    for v in point.coordinate_values
                    if v.coordinate_index == coordinate.coordinate_index
                ),
                None,
            )
            if coordinate_value is None:
                point_results.append(
                    PointCoordinateConformance(
                        point_index=point.point_index,
                        status=PointStatus.not_applicable,
                        reason="no coordinate value recorded for this point",
                    )
                )
                n_not_applicable += 1
                continue
            if point.geometry_id is None:
                point_results.append(
                    PointCoordinateConformance(
                        point_index=point.point_index,
                        status=PointStatus.not_applicable,
                        reason="scan point has no geometry",
                    )
                )
                n_not_applicable += 1
                continue

            result = evaluate_point_coordinate_conformance(
                kind=coordinate.coordinate_kind,
                stored_value=coordinate_value.coordinate_value,
                stored_unit=coordinate_value.value_unit or coordinate.value_unit,
                coords_by_atom=coords_by_geometry.get(point.geometry_id, {}),
                atom_indices=atom_indices,
                precision_decimals=precision_decimals,
                point_index=point.point_index,
            )
            if result.status is PointStatus.not_applicable:
                n_not_applicable += 1
            elif result.status is PointStatus.not_checkable:
                n_not_checkable += 1
            point_results.append(result)

        classification_result = classify_series(
            point_results,
            step_size=coordinate.step_size,
            is_periodic=coordinate.coordinate_kind is not ScanCoordinateKind.bond,
        )
        checkable_residuals = [
            p.residual
            for p in point_results
            if p.status in (PointStatus.conforms, PointStatus.deviates)
        ]

        reports.append(
            CoordinateSeriesConformance(
                calculation_id=calculation_id,
                coordinate_index=coordinate.coordinate_index,
                kind=coordinate.coordinate_kind,
                declared_unit=coordinate.value_unit,
                points=tuple(point_results),
                classification=classification_result.classification,
                implied_constant=classification_result.implied_constant,
                classification_detail=classification_result.detail,
                residual_distribution=_residual_distribution(checkable_residuals),
                n_points=len(points),
                n_not_applicable=n_not_applicable,
                n_not_checkable=n_not_checkable,
            )
        )

    return tuple(reports)


__all__ = [
    "DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA",
    "FALLBACK_PRECISION_DECIMALS",
    "MIN_CHECKABLE_POINTS_FOR_PATTERN",
    "TOLERANCE_FLOOR_ANGSTROM",
    "TOLERANCE_FLOOR_DEGREES",
    "CoordinateSeriesConformance",
    "PointCoordinateConformance",
    "PointStatus",
    "ResidualDistribution",
    "SeriesClassification",
    "SeriesClassificationResult",
    "bond_angle_deg",
    "bond_length_angstrom",
    "build_scan_coordinate_conformance_report",
    "classify_series",
    "dihedral_deg",
    "evaluate_point_coordinate_conformance",
    "infer_precision_decimals",
]


# ---------------------------------------------------------------------------
# Scientific check registration (see backend/app/scientific_checks)
# ---------------------------------------------------------------------------

CHECK_SCAN_COORDINATE_VALUE_MATCHES_GEOMETRY = ScientificCheck(
    group="Scan coordinates",
    sort_key=1,
    code=None,
    asserts=(
        "A scan point's stored coordinate_value is the internal coordinate "
        "at that point's own sampled geometry, in that coordinate's own "
        "unit (ADR 0020) -- never a displacement, and never compared "
        "against start_value as an anchor."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.none,
    tier_rationale=(
        "ADR 0020 is explicit that a disagreement here cannot say which of "
        "two things is wrong: a mis-stated axis and a mis-attached geometry "
        "present identically to a comparison that only sees the stored "
        "number and the recomputed one. Refusing the deposit would discard "
        "correct energies over an ambiguity this check cannot resolve on "
        "its own, which is the ADR 0008 argument for warn rather than "
        "block. It cannot become a blocking definition for a second, "
        "independent reason: every scan series deposited so far fails it "
        "-- all 46 hold ADR 0019's superseded relative-sweep convention -- "
        "and ADR 0008 reserves block for a record no correct calculation "
        "could produce, not for a corpus TCKDB itself has not yet "
        "corrected. No code or channel is declared because the check is "
        "not wired into any upload or read path: it runs standalone, from "
        "backend/scripts/validation/scan_coordinate_conformance_report.py, "
        "as the diagnostic ADR 0020 asks for ahead of the corrective "
        "migration it explicitly defers to a separate PR."
    ),
    adr="0020, 0008",
    enforced_by=(
        PythonCheck(
            evaluate_point_coordinate_conformance,
            note=(
                "Recomputes the coordinate from the point's own stored "
                "geometry only -- bond distance, bond angle, or the "
                "standard four-atom dihedral formula -- and never reads "
                "calc_scan_coordinate.start_value/end_value, which ADR "
                "0020 fixes as grid metadata, not an anchor. improper is "
                "reported not_applicable rather than guessed at: ADR 0020 "
                "records that TCKDB has no field distinguishing an "
                "out-of-plane-angle convention from a proper-style "
                "torsion, so assuming either would be exactly the "
                "inference ADR 0011 refuses to make for atom maps, applied "
                "one level up. A near-collinear dihedral quartet "
                "(min(sin(theta_123), sin(theta_234)) < 0.05) is reported "
                "not_checkable rather than compared, mirroring what the "
                "producing tools themselves do. classify_series in the "
                "same module turns a series of per-point disagreements "
                "into the specific pattern the follow-up corrective "
                "migration needs: a constant residual across every point, "
                "a linear ramp against point index, a residual equal to "
                "one grid step, or a lone outlier."
            ),
        ),
    ),
    escape_hatch=(
        "None needed -- the check never refuses, at any tier. Its cost "
        "runs the other way: today it reports every one of the 46 "
        "deposited scan series as non-conforming, which ADR 0020 records "
        "as the correct, intended finding rather than a defect to quiet."
    ),
    thresholds=(
        ConstantThreshold(
            name="dihedral_not_checkable_min_sin_theta",
            value=DIHEDRAL_NOT_CHECKABLE_MIN_SIN_THETA,
            unit="dimensionless (sin of a flanking bond angle)",
            rationale=(
                "ADR 0020: 'where a quartet is near-collinear the dihedral "
                "is not a usable coordinate at all'. Below this, "
                "1/(r * sin(theta)) conditioning makes the recomputed "
                "dihedral's uncertainty diverge, so comparing it would "
                "manufacture a false disagreement rather than find a real "
                "one."
            ),
        ),
        ConstantThreshold(
            name="tolerance_floor_degrees",
            value=TOLERANCE_FLOOR_DEGREES,
            unit="degree",
            rationale=(
                "Never let ten times the precision-derived sigma collapse "
                "below what a 6-decimal-place deposit can distinguish. "
                "Explicitly not the 0.5 or 1.0 degree floor rejected as "
                "roughly four orders of magnitude too loose -- correctly "
                "stored data reproduces to about 1.5e-4 degrees (ADR "
                "0020), and this floor still catches an injected 0.01 "
                "degree error with two orders of magnitude to spare."
            ),
        ),
    ),
)
