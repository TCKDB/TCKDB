"""Does a deposited scan coordinate value match its own geometry?

ADR 0020 (``docs/adr/0020-a-scan-coordinate-value-is-the-coordinate-itself.md``)
fixes what ``calc_scan_point_coordinate_value.coordinate_value`` means: the
internal coordinate itself, in its own unit, at the point's own sampled
geometry -- never a displacement, never relative to ``calc_scan_coordinate
.start_value``. This module is the conformance check the ADR asks for: it
recomputes the coordinate from the point's own stored Cartesian geometry and
compares, **without ever reading ``start_value``/``end_value``**, which ADR
0020 fixes as requested-grid metadata rather than an anchor to add back.

**Nothing is refused.** A deposit whose scan values disagree with their own geometries is accepted as sent
and earns :data:`W_SCAN_POINT_COORDINATE_DISAGREES_WITH_GEOMETRY` (one warning per disagreeing coordinate series,
:func:`collect_scan_coordinate_warnings`), run once the request's calculations are flushed. Under ADR 0008 the
tier is ``warn``: ADR 0020 is explicit that a mis-stated axis and a mis-attached geometry present identically to
this comparison, so a disagreement is evidence that *something* is wrong without saying which thing, and refusing
a deposit over that ambiguity would discard correct energies. The verdict is not stored: there is no table for it,
and the report script (``backend/scripts/validation/scan_coordinate_conformance_report.py``) recomputes it from
stored rows.

**Payloads in the superseded convention still fail this, correctly.** The 46 series
deposited before ADR 0020 held ADR 0019's superseded relative-sweep convention;
the corrective migration (``a4f7c2e9d651``) converted only the stored series
whose own geometry proves the conversion, not every one. Output written in
that convention and deposited now (the real payloads under
``tests/fixtures/arc_runs`` are kept in it, and 34 of their 36 series are
reported as ``consistent_with_legacy_relative_axis``, one as ``small_constant_offset`` because its start value is
0.023 degrees short of a full turn (a wrap coincidence), and one has no geometry) is still accepted and,
where it has geometry, earns the warning. That is the check doing its job, not a defect to quiet.

**Rounded values are not disagreement.** The tolerance is widened to half a
step of the decimals the stored values were written at (see
``stored_precision_decimals``) unless the stored values are an evenly spaced
grid (a grid is a label, not a rounded measurement), and a constant offset no
larger than the tolerance ceiling is classified ``small_constant_offset``: it
names no cause, because a relative sweep whose start lies within the ceiling
of zero (mod 360 for an angle) looks the same.

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
from itertools import pairwise

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    CoordinateUnit,
    ScanCoordinateKind,
    SubmissionRecordType,
)
from app.db.models.geometry import GeometryAtom
from app.schemas.upload_warning import UploadWarning
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    PythonCheck,
    ScientificCheck,
)
from app.services.deposit_warnings import note_warning_subject

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Fallback deposit precision (decimal places on a Cartesian coordinate, in
#: Angstrom) when it cannot be inferred from the sample itself. Measured as
#: the precision of the current (pre-ADR-0020) corpus; see ADR 0020's
#: "Two limits belong on the record" paragraph.
FALLBACK_PRECISION_DECIMALS = 6

#: Below this sample *size* -- the count of Cartesian components fed to
#: :func:`infer_precision_decimals`, not the decimal count it infers --
#: the function does not trust what it found and falls back. A handful of
#: coordinates that happen to round-trip at low precision by coincidence
#: is not evidence the whole geometry was deposited that coarsely; three
#: is deliberately small, one atom's worth of ``x``/``y``/``z`` -- a real
#: call site passes every component of every atom of every geometry a
#: scan touches, routinely hundreds, so this floor only ever bites a
#: hand-built, near-empty sample.
#:
#: A prior version of this guard checked the wrong quantity -- the
#: inferred decimal count itself, not the sample size the docstring above
#: it argued about -- so a genuinely coarse deposit (say, honestly stored
#: at 2 decimal places, in a large sample) fell back to the tighter
#: 6-decimal-place assumption and was judged non-conforming by an
#: artificially tight tolerance it never had a chance of meeting. See
#: ``TestInferPrecisionDecimals`` for both directions: too few
#: components falls back regardless of what they'd imply, and a large,
#: genuinely coarse sample is trusted rather than overridden.
_MIN_SAMPLE_SIZE_TO_TRUST = 3

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

#: Above this, a point is not "conforming with a generous window" -- it is
#: unjudgeable, and is reported ``not_checkable`` rather than compared at
#: all.
#:
#: Without a ceiling, ``max(floor, 10 * sigma_pred)`` has no upper bound:
#: at coarse enough deposit precision, ``sigma_pred`` grows without limit
#: (the ``1 / (r * sin(theta))`` conditioning is unbounded as precision
#: degrades), so the derived tolerance eventually exceeds the entire
#: physically meaningful range of a wrapped residual (+/-180 degrees) and
#: *every* residual "conforms" -- a coordinate deposited at 0 decimal
#: places was measured to yield a 184.9 degree tolerance, silently passing
#: a 150 degree error. That is the opposite failure from the sign bug this
#: module was built to catch first: not a false alarm, a false clean bill
#: of health, in a detector whose entire job is catching exactly that.
#:
#: Fixed at a flat 1 degree rather than derived from the coordinate's own
#: ``step_size``, deliberately. ``step_size`` is optional
#: (``calc_scan_coordinate.step_size`` is nullable) -- a fraction-of-step
#: ceiling would need a fallback for every coordinate that never declared
#: one, which is either the same flat number in different clothing or a
#: second unprincipled choice. A flat ceiling applies uniformly regardless
#: of what grid metadata a deposit happened to include. 1 degree is not
#: the floor's rejected 0.5/1.0 degree number wearing a different hat: the
#: floor asks "how tight can we trust well-behaved data to be", the
#: ceiling asks "how loose can a window get before it stops meaning
#: anything", and those are different questions with different right
#: answers. Measured against the precision ladder: 3 decimal places
#: derives ~0.213 degrees (comfortably checkable), 2 decimal places
#: derives above this ceiling (correctly not_checkable), so the cut lands
#: between "coarse but real" and "too coarse to say anything" rather than
#: inside either.
TOLERANCE_CEILING_DEGREES = 1.0

#: Same role as :data:`TOLERANCE_CEILING_DEGREES`, for ``bond``. 0.1
#: Angstrom is roughly 5-10 percent of a typical single bond length (about
#: 1-1.5 Angstrom) -- a derived tolerance that wide could not tell a
#: genuine bond-length error from deposit noise, so it must not pass one
#: silently either.
TOLERANCE_CEILING_ANGSTROM = 0.1

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

    Pinned directly against RDKit (``rdMolTransforms.GetDihedralDeg``) in
    ``tests/services/test_scan_coordinate_conformance.py`` -- not against a
    second hand-written formula in this repository, which a sign error in
    both could pass silently. An earlier version of this function computed
    ``y = dot(cross(n1, b2_unit), n2)``, which equals
    ``-dot(cross(n1, n2), b2_unit)`` and returned the *negative* of the
    dihedral; a test helper built to catch that was edited to negate its
    own convention to match instead of pinning it externally, and the sign
    error shipped for a full review cycle undetected. ``y`` below is
    computed the one way that is not that mistake.
    """
    b1 = b - a
    b2 = c - b
    b3 = d - c
    b2_unit = b2 / np.linalg.norm(b2)
    n1 = np.cross(b1, b2)
    n2 = np.cross(b2, b3)
    x = float(np.dot(n1, n2))
    y = float(np.dot(np.cross(n1, n2), b2_unit))
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


def _tolerance_degrees(sigma_pred_deg: float) -> float | None:
    """``max(floor, 10 * sigma)``, or ``None`` when that exceeds the ceiling.

    ``None`` is the caller's signal to report ``not_checkable``: a window
    this wide could not distinguish a real error from deposit noise, so
    comparing against it would manufacture a false clean bill of health
    rather than find a real one. See :data:`TOLERANCE_CEILING_DEGREES`.
    """
    candidate = 10.0 * sigma_pred_deg
    if candidate > TOLERANCE_CEILING_DEGREES:
        return None
    return max(TOLERANCE_FLOOR_DEGREES, candidate)


def _tolerance_angstrom(sigma_pred_angstrom: float) -> float | None:
    """Same contract as :func:`_tolerance_degrees`, for ``bond``."""
    candidate = 10.0 * sigma_pred_angstrom
    if candidate > TOLERANCE_CEILING_ANGSTROM:
        return None
    return max(TOLERANCE_FLOOR_ANGSTROM, candidate)


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
    empty, or its *size* is too small to trust any reading from at all
    (see :data:`_MIN_SAMPLE_SIZE_TO_TRUST`). A large sample is trusted at
    whatever precision it actually needs, including a low one: a
    genuinely coarse deposit is real information, not noise to override
    with the fallback.
    """
    values = [v for v in values if v is not None]
    if len(values) < _MIN_SAMPLE_SIZE_TO_TRUST:
        return fallback

    needed = 0
    for value in values:
        for n in range(0, max_decimals + 1):
            if abs(round(value, n) - value) < 1e-9:
                needed = max(needed, n)
                break
        else:
            needed = max(needed, max_decimals)

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
    stored_precision_decimals: int | None = None,
) -> PointCoordinateConformance:
    """Compare one stored coordinate value against its own geometry.

    Never reads ``calc_scan_coordinate.start_value``/``end_value`` -- ADR
    0020 fixes those as grid metadata, not an anchor for the expected value.

    :param precision_decimals: Decimal places of the *geometry's* Cartesian coordinates.
    :param stored_precision_decimals: Decimal places the stored ``coordinate_value`` series was written at, when it
        is known. A value rounded to ``d`` places can differ from the exact coordinate by up to ``0.5 * 10**-d``,
        so the tolerance is widened to that half-step plus the geometry's own derived allowance; a series
        rounded that coarsely is not disagreeing with its geometry. If the widened window passes the same ceiling
        the geometry-derived one is held to, the point is ``not_checkable``.
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
        if tolerance is None:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_checkable,
                sigma_pred=sigma_pred,
                reason=(
                    f"derived tolerance ({10.0 * sigma_pred:.3g} Angstrom, from "
                    f"{precision_decimals} decimal place(s)) exceeds the "
                    f"{TOLERANCE_CEILING_ANGSTROM:g} Angstrom ceiling -- too "
                    "coarse a deposit to tell a real error from noise."
                ),
            )
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
        if tolerance is None:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_checkable,
                sigma_pred=sigma_pred,
                reason=(
                    f"derived tolerance ({10.0 * sigma_pred:.3g} degrees, from "
                    f"{precision_decimals} decimal place(s)) exceeds the "
                    f"{TOLERANCE_CEILING_DEGREES:g} degree ceiling -- too "
                    "coarse a deposit to tell a real error from noise."
                ),
            )
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
        if tolerance is None:
            return PointCoordinateConformance(
                point_index=point_index,
                status=PointStatus.not_checkable,
                sigma_pred=sigma_pred,
                reason=(
                    f"derived tolerance ({10.0 * sigma_pred:.3g} degrees, from "
                    f"{precision_decimals} decimal place(s)) exceeds the "
                    f"{TOLERANCE_CEILING_DEGREES:g} degree ceiling -- too "
                    "coarse a deposit to tell a real error from noise."
                ),
            )
        residual = _wrap_deg(stored_value - expected)

    if stored_precision_decimals is not None:
        widened = 0.5 * 10.0**-stored_precision_decimals + 10.0 * sigma_pred
        if widened > tolerance:
            is_bond = kind is ScanCoordinateKind.bond
            ceiling = TOLERANCE_CEILING_ANGSTROM if is_bond else TOLERANCE_CEILING_DEGREES
            if widened > ceiling:
                return PointCoordinateConformance(
                    point_index=point_index,
                    status=PointStatus.not_checkable,
                    sigma_pred=sigma_pred,
                    reason=(
                        f"the stored values are rounded to {stored_precision_decimals} decimal place(s), a "
                        f"half-step of {0.5 * 10.0**-stored_precision_decimals:.3g} that, with the geometry's "
                        f"allowance, exceeds the {ceiling:g} ceiling -- too coarse to tell a real error from "
                        "rounding."
                    ),
                )
            tolerance = widened

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
    #: Every checkable point is off by the same constant, no larger than the
    #: module's tolerance ceiling (1 degree, 0.1 Angstrom). A relative sweep
    #: whose start lies within the ceiling of zero (mod 360 for an angle) lands
    #: here, as does any other small systematic difference; no cause is named.
    small_constant_offset = "small_constant_offset"
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


def _circular_mean_and_spread_deg(values_deg: np.ndarray) -> tuple[float, float]:
    """Mean and spread of a set of degree values, respecting the 360 wrap.

    A residual near +-180 degrees is the same physical offset whichever
    sign it happens to carry, so an arithmetic ``mean``/``std`` over the
    raw numbers is wrong exactly there: a series genuinely constant at
    +179 degrees, sampled as [179, -179, 179, -179, ...] by ordinary
    floating-point noise crossing the branch cut, averages to ~0 with a
    spread of ~180 under plain statistics -- which reads as "wildly
    incoherent" for a series that is in fact perfectly constant.

    The circular mean is ``atan2(mean(sin), mean(cos))``, and the spread
    returned is the circular standard deviation
    ``sqrt(-2 * ln(R))`` (in degrees) where
    ``R = |mean(exp(i * theta))|`` is the resultant length -- 1 for
    identical angles, shrinking towards 0 as they scatter around the
    circle. Standard directional-statistics definitions (Mardia & Jupp,
    *Directional Statistics*); used here in place of the linear mean/std
    that :func:`classify_series` used before this fix, which is what let
    the two deposited series with ``start_value = 180.0`` -- landing
    exactly on the branch cut -- classify as ``no_pattern_detected``
    instead of ``consistent_with_legacy_relative_axis``.
    """
    radians = np.radians(values_deg)
    sin_mean = float(np.mean(np.sin(radians)))
    cos_mean = float(np.mean(np.cos(radians)))
    mean_deg = math.degrees(math.atan2(sin_mean, cos_mean))
    resultant_length = min(1.0, math.hypot(sin_mean, cos_mean))
    if resultant_length <= 1e-12:
        return mean_deg, 180.0
    spread_deg = math.degrees(math.sqrt(-2.0 * math.log(resultant_length)))
    return mean_deg, spread_deg


def _unwrap_deg(values_deg: np.ndarray) -> np.ndarray:
    """Remove artificial +-360 jumps from a *sequence* of degree residuals.

    ``np.unwrap`` assumes consecutive *array elements* are close on the
    circle. That assumption is only true here when consecutive elements
    are also consecutive scan points -- i.e. when ``indices`` has no
    holes. Callers must check that themselves (see the gap guard in
    :func:`classify_series`): passing a residual sequence with a
    not_checkable/not_applicable point skipped over -- so array-adjacent
    residuals are really two or more point_index steps apart -- can hand
    ``np.unwrap`` a true angular change bigger than its 180-degree
    discontinuity threshold, which it will happily read as noise instead
    of a wrap and "correct" incorrectly. A review reproduced this: a true
    slope of 100 degrees/point with an alternating hole (real gap of 200
    degrees between kept samples) classified ``linear_ramp`` with an
    ``implied_constant`` of -80.0 against a true slope of 100 -- a
    confidently wrong number handed to exactly the corrective migration
    this field exists to feed.
    """
    return np.degrees(np.unwrap(np.radians(values_deg)))


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
        if is_periodic:
            mean_residual, spread = _circular_mean_and_spread_deg(residuals)
        else:
            mean_residual = float(np.mean(residuals))
            spread = float(np.std(residuals))
        if spread <= pattern_tol:
            constant = mean_residual
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
                            "geometry (point/geometry off-by-one). AMBIGUOUS: "
                            "a constant this close to one grid step is "
                            "structurally indistinguishable from "
                            "consistent_with_legacy_relative_axis with a "
                            "start_value that happens to equal roughly one "
                            "step -- this check reports the step-sized "
                            "reading because it is the narrower, more "
                            "specific claim, not because it has ruled the "
                            "other one out. It cannot: ADR 0020 forbids "
                            "reading start_value to decide, which is exactly "
                            "the number that would resolve the ambiguity. A "
                            "human (or the corrective migration, which may "
                            "read start_value) should confirm before acting "
                            "on point/geometry off-by-one specifically."
                        ),
                    )
            ceiling = TOLERANCE_CEILING_DEGREES if is_periodic else TOLERANCE_CEILING_ANGSTROM
            if abs(constant) <= ceiling:
                return SeriesClassificationResult(
                    classification=SeriesClassification.small_constant_offset,
                    implied_constant=constant,
                    detail=(
                        f"every checkable point's residual is the same small constant ({constant:.6g}, spread "
                        f"{spread:.3g}), no larger than the {ceiling:g} ceiling. Values measured from a scan "
                        "start that lies within the ceiling of zero (or of a full turn) look like this too, so "
                        "this check does not name a cause."
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

        # Unwrapped before fitting: a genuine ramp that crosses the 360-degree
        # branch cut (residual sweeping past +-180) would otherwise alias
        # into two disconnected halves that no single line fits, and the
        # ramp would never be detected for any periodic series that wraps.
        #
        # Only when the checkable points are index-contiguous, though.
        # ``np.unwrap`` reasons about array-adjacent elements, not
        # point_index values -- if a not_checkable/not_applicable point was
        # skipped, array-adjacent residuals are really two or more scan
        # steps apart, and unwrapping across that hole can silently invent
        # a wrap that never happened (or miss one that did). Refusing to
        # unwrap there is the honest answer: a real ramp with a hole in it
        # may then read as no_pattern_detected instead of linear_ramp, which
        # is a missed detection, not a wrong number -- the failure mode this
        # guard exists to rule out is a confidently wrong implied_constant.
        index_gaps = np.diff(indices)
        is_index_contiguous = bool(np.all(index_gaps == 1))
        if is_periodic and is_index_contiguous:
            ramp_residuals = _unwrap_deg(residuals)
        else:
            ramp_residuals = residuals
        slope, intercept = np.polyfit(indices, ramp_residuals, 1)
        fitted = slope * indices + intercept
        fit_spread = float(np.std(ramp_residuals - fitted))
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

        gap_note = (
            " point_index has one or more gaps (a not_checkable/"
            "not_applicable point in between), so a ramp crossing the "
            "360-degree branch cut was not attempted: unwrapping across a "
            "hole risks compressing the true spacing and reporting a "
            "confidently wrong slope. A genuine ramp may be hiding in this "
            "result."
            if (is_periodic and not is_index_contiguous)
            else ""
        )
        return SeriesClassificationResult(
            classification=SeriesClassification.no_pattern_detected,
            implied_constant=None,
            detail=(
                f"all {n} checkable points deviate, but not with a constant "
                f"offset or a linear trend (residual spread {spread:.3g})."
                f"{gap_note}"
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


#: A value is on a declared grid when it is this close (in the coordinate's unit) to ``base + k * step``.
_GRID_TOLERANCE = 1e-6

#: The common step of the distinct stored values must be at least this many units of their last decimal for the
#: series to count as a grid (5 degrees for whole-degree values, 0.5 for one-decimal values ...).
_GRID_MIN_STEP_UNITS = 5


def _decimals_needed(values: Sequence[float], *, max_decimals: int = 12) -> int:
    """Decimal places needed to write every value exactly (to float noise), at most ``max_decimals``."""
    needed = 0
    for value in values:
        for n in range(max_decimals + 1):
            if abs(round(value, n) - value) < 1e-9:
                needed = max(needed, n)
                break
        else:
            needed = max_decimals
    return needed


def _gcd_step_units(distinct: Sequence[float], *, periodic: bool) -> int:
    """The greatest common divisor of the gaps between the distinct values, as an integer number of units of their
    last decimal. A periodic coordinate includes the wrap-around gap."""
    scale = 10 ** _decimals_needed(distinct)
    ints = sorted(round(v * scale) for v in distinct)
    gaps = [b - a for a, b in pairwise(ints)]
    if periodic:
        gaps.append(ints[0] + 360 * scale - ints[-1])
    step = 0
    for gap in gaps:
        step = math.gcd(step, gap)
    return step


def _fits_declared_step(distinct: Sequence[float], *, periodic: bool, step_size: float | None) -> bool:
    """Whether the distinct values all sit on the coordinate's declared ``step_size`` lattice, with at most half of
    the slots between the smallest and the largest missing. Evidence only: it can add a grid, never remove one."""
    if step_size is None or step_size <= 0:
        return False
    if periodic:
        gaps = [b - a for a, b in pairwise(distinct)] + [distinct[0] + 360.0 - distinct[-1]]
        cut = max(range(len(gaps)), key=gaps.__getitem__)
        start = distinct[(cut + 1) % len(distinct)]
        offsets = sorted((v - start) % 360.0 for v in distinct)
    else:
        offsets = [v - distinct[0] for v in distinct]
    for offset in offsets:
        if abs(offset - round(offset / step_size) * step_size) > _GRID_TOLERANCE:
            return False
    slots = round(offsets[-1] / step_size) + 1
    if periodic:
        around = 360.0 / step_size
        if abs(around - round(around)) * step_size <= _GRID_TOLERANCE:
            slots = min(slots, round(around))
    return 2 * len(distinct) >= slots


def _is_even_grid(values: Sequence[float], *, periodic: bool, step_size: float | None = None) -> bool:
    """Whether the stored coordinate values are labels on an evenly spaced grid, gaps allowed.

    A grid is a label the producer chose (60, 75, 105, 120 ...), not a measurement, so its decimals say nothing about
    rounding: ``60`` is not evidence the producer rounded to whole degrees.

    **The rule.** Take the **distinct** values (a 2-D scan repeats each label; a periodic coordinate is taken mod 360,
    so ``0`` and ``360`` are one label), write them as integers in units of their last decimal, and take the greatest
    common divisor of the gaps between them (including the wrap-around gap for a periodic coordinate). The series is
    a grid when that step is at least :data:`_GRID_MIN_STEP_UNITS` units: 0/10/20/30/40 and 0/5/10/15/20/30/60/90
    are grids; 60/83/121 (step 1) and 60.3/74.8/90.2 (step 1 unit of 0.1) are not. The coordinate's declared
    ``step_size`` is additional evidence only: values that all sit on its lattice, with at most half the slots between
    the smallest and the largest missing, are also a grid (this is what recognises 1.0/1.1/1.3/1.4 angstrom, whose
    step is one unit of the last decimal), but a declared step that does not fit never makes a series less of a grid.

    **Accepted cost.** A short irregular series of whole numbers has a common divisor of 5 or more by chance more
    often than under a smallest-gap rule (about 12% at three values and 1.8% at four, against about 3.6% and 0.15%).
    A series wrongly taken for a grid is judged at the tight tolerance, so the error is loud (a warning), the safe
    direction.

    Fewer than three stored values is never a grid here. That series is still judged tightly, because
    :func:`infer_precision_decimals` falls back to 6 decimals for fewer than three values; both facts are relied on.
    One or two distinct labels repeated over three or more values (a coordinate held fixed) count as a grid.
    """
    if len(values) < 3:
        return False
    if periodic:
        distinct = sorted({round(v, 9) % 360.0 for v in values})
    else:
        distinct = sorted({round(v, 9) for v in values})
    if len(distinct) <= 2:
        return True
    if _gcd_step_units(distinct, periodic=periodic) >= _GRID_MIN_STEP_UNITS:
        return True
    return _fits_declared_step(distinct, periodic=periodic, step_size=step_size)


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
    stored_values_by_coordinate: dict[int, list[float]] = {}
    for point in points:  # ``points`` is in point_index order
        for value in point.coordinate_values:
            stored_values_by_coordinate.setdefault(value.coordinate_index, []).append(value.coordinate_value)

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

        # Decimals the stored values were written at: a 2-decimal dihedral is exact to half a hundredth, no more.
        # Not when the values are an evenly spaced grid: those are labels, and 60/90/120 would otherwise read as
        # "rounded to whole degrees" and hide a real half-degree disagreement.
        stored_values = stored_values_by_coordinate.get(coordinate.coordinate_index, [])
        stored_precision = (
            None
            if _is_even_grid(
                stored_values,
                periodic=coordinate.coordinate_kind is not ScanCoordinateKind.bond,
                step_size=coordinate.step_size,
            )
            else infer_precision_decimals(stored_values)
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
                stored_precision_decimals=stored_precision,
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
    "TOLERANCE_CEILING_ANGSTROM",
    "TOLERANCE_CEILING_DEGREES",
    "TOLERANCE_FLOOR_ANGSTROM",
    "TOLERANCE_FLOOR_DEGREES",
    "W_SCAN_POINT_COORDINATE_DISAGREES_WITH_GEOMETRY",
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
    "collect_scan_coordinate_warnings",
    "dihedral_deg",
    "evaluate_point_coordinate_conformance",
    "infer_precision_decimals",
]


#: The warning a scan series earns when a stored coordinate value disagrees with its own point's geometry.
W_SCAN_POINT_COORDINATE_DISAGREES_WITH_GEOMETRY = "scan_point_coordinate_disagrees_with_geometry"


def collect_scan_coordinate_warnings(
    session: Session,
    calculation_ids: Iterable[int],
) -> list[UploadWarning]:
    """Warn once per scan coordinate series whose stored values disagree with the series' own geometries.

    Runs once a request's calculations are flushed (the check reads the stored scan points and geometries), over every
    calculation id the request persisted; other types are filtered out by
    :func:`build_scan_coordinate_conformance_report`. **Never refuses**: ADR 0020 and ADR 0008 (see
    :data:`CHECK_SCAN_COORDINATE_VALUE_MATCHES_GEOMETRY`). A series is reported when at least one point is judged
    ``deviates``; points that are not applicable or not checkable are never reported.

    :param session: Active SQLAlchemy session.
    :param calculation_ids: Every calculation id the request persisted (or reused).
    :returns: One warning per disagreeing coordinate series, in the request's calculation order then coordinate
        order. ``context.scan_number`` counts all of the request's scan calculations (with or without a result) from 1 in that order, which is how a
        warning says which scan it is about without the payload path (each route has its own).
    """
    warnings: list[UploadWarning] = []
    scan_number = 0
    # In the order the request persisted them, so the number names the same scan on the direct route and in a job.
    for calculation_id in dict.fromkeys(calculation_ids):
        calculation = session.get(Calculation, calculation_id)
        if calculation is None or calculation.type is not CalculationType.scan:
            continue
        scan_number += 1  # every scan calculation counts, with or without a scan_result
        report = build_scan_coordinate_conformance_report(session, calculation_id)
        for series in report:
            judged = [p for p in series.points if p.status in (PointStatus.conforms, PointStatus.deviates)]
            deviating = [p for p in judged if p.status is PointStatus.deviates]
            if not deviating:
                continue
            warning = UploadWarning.from_code(
                field="scan_result",
                code=W_SCAN_POINT_COORDINATE_DISAGREES_WITH_GEOMETRY,
                context={
                    "scan_number": scan_number,
                    "coordinate_index": series.coordinate_index,
                    "coordinate_kind": series.kind.value,
                    "pattern": series.classification.value,
                    "n_points_disagreeing": len(deviating),
                    "n_points_checked": len(judged),
                    "max_residual": round(max(abs(p.residual) for p in deviating), 6),
                    "tolerance": round(max(p.tolerance for p in deviating), 6),
                },
            )
            note_warning_subject(session, warning, SubmissionRecordType.calculation, calculation_id)
            warnings.append(warning)
    return warnings


# ---------------------------------------------------------------------------
# Scientific check registration (see backend/app/scientific_checks)
# ---------------------------------------------------------------------------

CHECK_SCAN_COORDINATE_VALUE_MATCHES_GEOMETRY = ScientificCheck(
    group="Scan coordinates",
    sort_key=1,
    code=W_SCAN_POINT_COORDINATE_DISAGREES_WITH_GEOMETRY,
    asserts=(
        "A scan point's stored coordinate_value is the internal coordinate "
        "at that point's own sampled geometry, in that coordinate's own "
        "unit (ADR 0020) -- never a displacement, and never compared "
        "against start_value as an anchor."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "ADR 0020 is explicit that a disagreement here cannot say which of "
        "two things is wrong: a mis-stated axis and a mis-attached geometry "
        "present identically to a comparison that only sees the stored "
        "number and the recomputed one. Refusing the deposit would discard "
        "correct energies over an ambiguity this check cannot resolve on "
        "its own, which is the ADR 0008 argument for warn rather than "
        "block. It also cannot be a blocking definition for a second "
        "reason: ADR 0008 reserves block for a record no correct "
        "calculation could produce, and the producers that deposited the "
        "superseded relative-sweep convention (ADR 0019) did so in good "
        "faith. It was held back as a standalone report until the "
        "corrective migration (``a4f7c2e9d651``, #302) had converted the "
        "stored series whose own geometry proves the conversion; with that "
        "done, a new deposit written in the superseded convention is exactly "
        "what the warning targets, so it is wired into the "
        "write path of every route that persists a scan, once per "
        "disagreeing coordinate series, and placed on the scan "
        "calculation's own record."
    ),
    adr="0020, 0008",
    enforced_by=(
        PythonCheck(
            collect_scan_coordinate_warnings,
            note=(
                "The write path: run once a request's calculations are "
                "flushed, over every calculation the request persisted, by "
                "every workflow whose calculations can carry a scan result (conformer, "
                "transition state, computed species, computed reaction, "
                "thermo, statmech, transport). One warning per "
                "coordinate series with at least one point judged `deviates`; "
                "its context carries the scan's number in the request, the series' "
                "pattern (`classify_series`), the number of points that "
                "disagree, the largest residual and the tolerance. The "
                "tolerance is widened to half a step of the decimals the "
                "stored values were written at, so rounded measurements are "
                "not a disagreement, unless the stored values are an evenly "
                "spaced grid (gaps allowed): grid values are labels, not "
                "rounded measurements, and are judged at the tight "
                "tolerance. The warning targets output written in the "
                "superseded convention."
            ),
        ),
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
        "None needed -- the check never refuses, at any tier. Send each "
        "point's coordinate_value as the internal coordinate measured on "
        "that point's own geometry (not as an offset from the scan's start), "
        "or attach the geometry the value was measured on. An `improper` "
        "coordinate is never compared, and a near-collinear dihedral quartet "
        "or a deposit too coarse to judge is reported as not checkable "
        "rather than as a disagreement."
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
                "degree error with two orders of magnitude to spare. This "
                "is the tolerance every point in the current corpus is "
                "actually compared against: at the corpus's real 6 "
                "decimal-place precision the precision-derived term is "
                "far below this floor, so the floor -- not the derived "
                "term -- is what binds on real data today. The derived "
                "term only starts to matter at coarser precision than the "
                "corpus has ever used; see ``tolerance_ceiling_degrees`` "
                "for what happens if it grows too large to trust."
            ),
        ),
        ConstantThreshold(
            name="tolerance_ceiling_degrees",
            value=TOLERANCE_CEILING_DEGREES,
            unit="degree",
            rationale=(
                "Without an upper bound, the precision-derived tolerance "
                "grows without limit as deposit precision degrades -- "
                "measured to reach 184.9 degrees at 0 decimal places, "
                "silently passing an injected 150 degree error, because "
                "the tolerance then exceeds the entire physically "
                "meaningful range of a wrapped residual. Above this "
                "ceiling the point is reported not_checkable instead of a "
                "pass under a window too wide to mean anything -- the "
                "detector failing loudly rather than reporting a false "
                "clean bill of health. Fixed at a flat number rather than "
                "derived from the coordinate's own step_size because "
                "step_size is optional metadata (nullable) and a "
                "fraction-of-step ceiling would need the same flat "
                "fallback for every coordinate that never declared one. "
                "Chosen to land between 3 decimal places (~0.213 degrees, "
                "comfortably checkable) and 2 decimal places (derives "
                "above this ceiling, correctly not_checkable) on the "
                "measured precision ladder -- not the floor's rejected "
                "0.5/1.0 degree number reused: the floor asks how tight "
                "well-behaved data can be trusted to be, this asks how "
                "loose a window can get before it stops meaning anything, "
                "and those are different questions."
            ),
        ),
        ConstantThreshold(
            name="tolerance_ceiling_angstrom",
            value=TOLERANCE_CEILING_ANGSTROM,
            unit="Angstrom",
            rationale=(
                "Same role as tolerance_ceiling_degrees, for bond. 0.1 "
                "Angstrom is roughly 5-10 percent of a typical single bond "
                "length (about 1-1.5 Angstrom); a derived tolerance that "
                "wide could not tell a genuine bond-length error from "
                "deposit noise."
            ),
        ),
    ),
)
