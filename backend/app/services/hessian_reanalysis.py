"""Recover the vibrational spectrum from every stored Hessian and compare it
with the frequency list that was deposited beside it.

The manuscript claims that a spectrum recovered from stored data alone --
the packed Cartesian force-constant matrix in ``calc_hessian``, the
geometry it is bound to, and the masses those atoms determine -- agrees
with the separately parsed ``calc_freq_mode`` list. This module is the
deposited check behind that sentence: DB-read-only, nothing persisted,
one result per calculation, every calculation in scope accounted for.

What is compared
----------------

For each calculation the Hessian is unpacked
(:func:`~app.chemistry.normal_modes.unpack_lower_triangle`), mass-weighted
with the same isotope-aware masses the read-time projection uses
(:func:`~app.chemistry.normal_modes.atomic_mass`; ``isotope_mass_number IS
NULL`` means the most abundant isotope), the rigid-body subspace is built
and **projected out**, and the remaining ``3N - k`` modes are diagonalised
(:func:`~app.chemistry.normal_modes.solve_vibrational_modes`). Units are
hartree/bohr^2 in and cm^-1 out, through the one conversion the numerics
module states (:func:`~app.chemistry.normal_modes.wavenumber_from_eigenvalue`).
The recovered spectrum, ascending and signed (imaginary modes negative),
is paired position by position with the stored list sorted the same way.
That pairing is a bijection, so it can neither double-assign a recovered
mode nor quietly leave a stored one out.

Why the rigid-body projection is not optional here. Every program
projects translation and rotation out before it prints a frequency list,
so the stored list is the projected spectrum. A stored Hessian at a real
(imperfectly converged) geometry carries a few cm^-1 of rigid-body
curvature -- 12.8 cm^-1 on the Gaussian fixture, up to 12.2 cm^-1 in the
live corpus -- and left in, that residue couples into the lowest
vibrations. Measured on ``freq_g09.log``: the 110 cm^-1 torsion comes
back 0.129 cm^-1 high without the projection and 0.006 cm^-1 high with
it. ``project_rigid_body=False`` is kept as a diagnostic so that this
effect can be demonstrated, not as an alternative analysis.

The tolerance: what is derived, what is estimated
-------------------------------------------------

The comparison is judged against a bound declared before any record is
read. (A figure of 0.045 cm^-1 was recorded for the 2026-08-11 corpus by
the unprojected read-time matcher; it is a measurement, and it is not
used.) Two roundings separate the stored list from the stored matrix, and
the bound has one term for each.

**Derived, term 1: the frequency list is printed to a fixed number of
decimals.** Gaussian prints four (``Frequencies -- 110.1603``); ORCA and
Molpro print two. TCKDB has no frequency-list parser -- the list arrives
from the client -- so the term is the coarsest print format among the ESS
logs TCKDB's depositors parse: a half-ULP of
:data:`FREQUENCY_PRINT_HALF_ULP_CM1` = 0.005 cm^-1, flat in omega. This
is the *estimate* in the bound: a deposit whose list was parsed from a
Gaussian log is entitled to 0.00005, and a client that rounded before
uploading is entitled to more; TCKDB does not record which.

**Derived, term 2: the force constants are printed to a fixed number of
digits, and every element's rounding moves every mode by a computable
amount.** Each printed element ``H_ij`` carries an absolute half-ULP
``e_ij`` fixed by its print format (:class:`HessianPrintFormat`):
Gaussian's log block has six significant figures (``0.410282D-01``, so
``e = 0.5 * 10^(floor(log10|H|) - 5)``), Molpro's has seven decimals
(``e = 5e-8`` flat), ORCA's ``.hess`` has eleven significant figures.
To first order in those roundings the mass-weighted eigenvalue of a mode
with unit mass-weighted eigenvector ``v`` moves by at most

    d(lambda) <= sum_ij |u_i| |u_j| e_ij,   u_i = v_i / sqrt(m_i)

(the Rayleigh quotient is linear in H, and ``|dH_ij| <= e_ij``), and
:func:`~app.chemistry.normal_modes.wavenumber_from_eigenvalue`'s own
constants turn that into an omega-squared allowance ``B`` in cm^-2,
**per mode** (:func:`mode_omega2_allowance_cm2`). It is flat in omega
squared, not in omega -- ADR 0012's observation -- so the same ``B`` is
a small cm^-1 allowance on a stretch and a larger one on a torsion.
Measured on ``freq_g09.log`` the per-mode ``B`` runs from 4.6 to
34.6 cm^-2 (the 3446 cm^-1 C-H stretch, whose eigenvector concentrates
on the largest, most coarsely printed elements), against measured
omega-squared deviations of 0.1 to 11.2 cm^-2. The mode closest to its
bound on that fixture is the 837 cm^-1 one, at 11.2 of 28.6 cm^-2 --
39% of its omega-squared allowance, which is 30% of the resulting cm^-1
tolerance (0.0067 of 0.0221 cm^-1).

A stored mode therefore passes when

    |w_rec - w_stored| <= max_deviation_cm1 + (sqrt(w_stored^2 + B_mode) - |w_stored|)

with ``max_deviation_cm1`` = :data:`DEFAULT_MAX_DEVIATION_CM1` (term 1)
and ``B_mode`` the per-mode allowance (term 2); the second bracket is the
exact inverse of ``|w_rec^2 - w_stored^2| <= B_mode``. Passing
``max_omega2_deviation_cm2`` replaces every ``B_mode`` with that flat
figure (``0`` makes the bound flat in cm^-1); it is an override, not a
derivation.

**Estimated, and stated as such:**

* Term 2 is first order: rounding is treated as a perturbation linear in
  the elements, which is exact to relative order ``e / H``, i.e. 1e-6.
* The print format is resolved from provenance
  (:func:`resolve_print_format`: ``calc_hessian.source`` and the
  calculation's software name) and falls back to the **coarsest**
  format, Gaussian's six significant figures, when the provenance does
  not say. The choice and its basis are reported on every record.
* Mass conventions are deliberately **not** in the bound. TCKDB's
  convention is isotopic masses, which is what Gaussian and Molpro use;
  RDKit's isotopic table and Gaussian's agree to 1e-8 relative, which is
  2e-5 cm^-1 at 4000 cm^-1 and below either term. ORCA's ``.hess``
  carries *average* atomic weights, and a record whose list came from
  average weights will exceed the bound on any mode dominated by a heavy
  atom (1.0 cm^-1 on the C-Cl stretch of ``Orca_TS_test.hess``;
  0.35 cm^-1 on the Molpro fixture's X-H stretches, whose printed list
  appears to use a different hydrogen mass). Those are reported as
  exceeding, with the numbers, because that is what they are: a
  convention difference the deposit did not record, not agreement.

What is refused, and why refusals stay in the denominator
---------------------------------------------------------

Every calculation in scope gets exactly one status. Only ``analysed``
carries a comparison; the others each name what stopped it:

``hessian_not_stored``
    No ``calc_hessian`` row. This is most of the corpus's frequency
    calculations and it is **not** agreement -- it is the case the
    manuscript's denominator has to include.
``frequency_list_missing``
    A Hessian with no ``calc_freq_mode`` rows to compare against.
``geometry_incomplete``
    The bound geometry has no atom rows, or its atom count disagrees
    with the matrix dimension.
``masses_unresolved``
    An element or isotope the periodic table cannot weigh. No guess is
    substituted.
``rigid_body_curvature_too_large``
    The geometry on file is not the frame the matrix was computed in
    (:data:`~app.chemistry.normal_modes.FRAME_CONSISTENCY_TOLERANCE_CM1`).
``mode_count_mismatch``
    The stored list does not have ``3N - k`` entries, so no bijection
    exists. A partial list, or a linearity disagreement between the
    depositor's program and the stored geometry, lands here.

Determinism
-----------

:func:`hessian_reanalysis` returns a plain dictionary shaped for a paper
generator: records ordered by public ref, no wall-clock values, every
float rounded to a fixed number of decimals so that two runs on the same
database are byte-identical after ``json.dumps(sort_keys=True)``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.normal_modes import (
    FRAME_CONSISTENCY_TOLERANCE_CM1,
    NormalMode,
    atomic_mass,
    match_stored_frequency,
    rigid_body_curvature_cm1,
    rigid_body_subspace,
    solve_normal_modes,
    solve_vibrational_modes,
    unpack_lower_triangle,
    wavenumber_from_eigenvalue,
)
from app.db.models.calculation import Calculation, CalculationFreqMode, CalculationHessian
from app.db.models.common import HessianSource
from app.db.models.geometry import GeometryAtom
from app.services.scientific_read.imaginary_mode_projection import (
    ProjectionStatus,
    build_imaginary_mode_projection,
)

__all__ = [
    "COARSEST_PRINT_FORMAT",
    "DEFAULT_MAX_DEVIATION_CM1",
    "FREQUENCY_PRINT_HALF_ULP_CM1",
    "OMEGA2_PER_UNIT_EIGENVALUE_CM2",
    "HessianPrintFormat",
    "HessianReanalysis",
    "ImaginaryModeComparison",
    "ModeComparison",
    "ReanalysisStatus",
    "compare_spectrum",
    "element_half_ulps",
    "hessian_reanalysis",
    "mode_omega2_allowance_cm2",
    "mode_tolerance_cm1",
    "reanalyse_calculation",
    "reanalysis_scope",
    "resolve_print_format",
]

#: Half of the last printed decimal of the coarsest frequency list TCKDB
#: parses (ORCA and Molpro print two decimals; Gaussian prints four).
FREQUENCY_PRINT_HALF_ULP_CM1 = 0.005

#: cm^-2 per hartree/bohr^2/amu, from the numerics module's own constants
#: so the bound and the recovery cannot disagree about the conversion.
OMEGA2_PER_UNIT_EIGENVALUE_CM2 = wavenumber_from_eigenvalue(1.0) ** 2

#: Term 1 of the bound in the module docstring: flat in omega.
DEFAULT_MAX_DEVIATION_CM1 = FREQUENCY_PRINT_HALF_ULP_CM1


class HessianPrintFormat(str, Enum):
    """How the force constants were printed, which fixes each element's
    half-ULP. One member per format ``app.services.hessian_parsing`` reads."""

    #: ``Force constants in Cartesian coordinates`` block, ``0.410282D-01``:
    #: six significant figures. The coarsest, and the fallback.
    gaussian_log = "gaussian_log"

    #: ``Force Constants (Second Derivatives of the Energy) in [a.u.]``,
    #: ``0.3700857``: seven decimals, absolute.
    molpro_log = "molpro_log"

    #: ``$hessian`` block of a ``.hess`` file, ``-6.9820446273E-02``:
    #: eleven significant figures.
    orca_hess = "orca_hess"


COARSEST_PRINT_FORMAT = HessianPrintFormat.gaussian_log
_SIGNIFICANT_FIGURES = {HessianPrintFormat.gaussian_log: 6, HessianPrintFormat.orca_hess: 11}
_ABSOLUTE_DECIMALS = {HessianPrintFormat.molpro_log: 7}

#: Decimal places every reported cm^-1 quantity is rounded to. Six is two
#: beyond the finest printed frequency and well above LAPACK's run-to-run
#: jitter, so the output is stable without claiming precision it lacks.
_CM1_DECIMALS = 6
_CM2_DECIMALS = 3
_OVERLAP_DECIMALS = 4


class ReanalysisStatus(str, Enum):
    """One per calculation in scope. Only ``analysed`` carries numbers."""

    analysed = "analysed"
    hessian_not_stored = "hessian_not_stored"
    frequency_list_missing = "frequency_list_missing"
    geometry_incomplete = "geometry_incomplete"
    masses_unresolved = "masses_unresolved"
    rigid_body_curvature_too_large = "rigid_body_curvature_too_large"
    mode_count_mismatch = "mode_count_mismatch"


@dataclass(frozen=True)
class ModeComparison:
    """One stored frequency beside the recovered mode paired with it.

    :param mode_index: ``calc_freq_mode.mode_index`` of the stored value.
    :param stored_frequency_cm1: As stored, signed.
    :param recovered_frequency_cm1: From the Hessian, signed.
    :param deviation_cm1: ``recovered - stored``, signed.
    :param omega2_deviation_cm2: ``|sgn(r) r^2 - sgn(s) s^2|``.
    :param omega2_allowance_cm2: Term 2 of the bound for this mode: the
        first-order sensitivity of its eigenvalue to the per-element
        print rounding, in cm^-2 (or the flat override when one was given).
    :param tolerance_cm1: The bound this mode was judged against.
    :param within_tolerance: ``|deviation_cm1| <= tolerance_cm1``.
    :param nearest_match: Whether :func:`match_stored_frequency` would
        attach a recovered mode to this stored value at all (its wider,
        2 cm^-1 / 1% tolerance). A stored value that fails this is not
        merely imprecise; the matrix is not the one it came from.
    """

    mode_index: int
    stored_frequency_cm1: float
    recovered_frequency_cm1: float
    deviation_cm1: float
    omega2_deviation_cm2: float
    omega2_allowance_cm2: float
    tolerance_cm1: float
    within_tolerance: bool
    nearest_match: bool


@dataclass(frozen=True)
class ImaginaryModeComparison:
    """The depositor's declaration beside the projection's determination.

    Read straight off :func:`build_imaginary_mode_projection`, which is
    the production code path; nothing is re-decided here.
    """

    mode_index: int
    stored_frequency_cm1: float
    declared_disposition: str | None
    determination: str | None
    not_determined_reason: str | None
    agreement: str
    rigid_body_overlap: float | None
    torsion_overlap: float | None


@dataclass(frozen=True)
class HessianReanalysis:
    """The whole result for one calculation.

    Every field after ``status`` is ``None`` or empty unless the status
    that produces it was reached; the docstring of
    :class:`ReanalysisStatus` says which.
    """

    status: ReanalysisStatus
    calculation_ref: str | None = None
    calculation_type: str | None = None
    species_entry_ref: str | None = None
    transition_state_entry_ref: str | None = None
    natoms: int | None = None
    hessian_print_format: str | None = None
    print_format_basis: str | None = None
    rigid_body_dimension: int | None = None
    is_linear: bool | None = None
    max_rigid_body_curvature_cm1: float | None = None
    stored_count: int | None = None
    recovered_count: int | None = None
    modes: tuple[ModeComparison, ...] = ()
    matched_count: int | None = None
    within_tolerance_count: int | None = None
    max_abs_deviation_cm1: float | None = None
    rms_abs_deviation_cm1: float | None = None
    max_omega2_deviation_cm2: float | None = None
    stored_order_violations: int | None = None
    stored_imaginary_count: int | None = None
    recovered_imaginary_count: int | None = None
    projection_status: str | None = None
    imaginary_modes: tuple[ImaginaryModeComparison, ...] = ()

    @property
    def analysed(self) -> bool:
        return self.status is ReanalysisStatus.analysed

    @property
    def within_tolerance(self) -> bool | None:
        """``None`` unless analysed; otherwise whether every mode passed."""

        if not self.analysed:
            return None
        return self.within_tolerance_count == self.stored_count


def element_half_ulps(hessian_hartree_bohr2: np.ndarray, print_format: HessianPrintFormat) -> np.ndarray:
    """Absolute half-ULP of every element as it was printed, hartree/bohr^2.

    For a significant-figure format the half-ULP of a non-zero element
    ``H`` is ``0.5 * 10^(floor(log10|H|) - (n - 1))``; a printed zero
    says only that ``|H|`` is below half a unit in the last place of the
    smallest magnitude the format can show, which is taken as ``0.5 *
    10^-n``. For an absolute-decimals format every element has the same
    half-ULP.
    """

    matrix = np.asarray(hessian_hartree_bohr2, dtype=float)
    if print_format in _ABSOLUTE_DECIMALS:
        return np.full(matrix.shape, 0.5 * 10.0 ** (-_ABSOLUTE_DECIMALS[print_format]))
    figures = _SIGNIFICANT_FIGURES[print_format]
    magnitude = np.abs(matrix)
    with np.errstate(divide="ignore"):
        exponent = np.floor(np.log10(np.where(magnitude > 0.0, magnitude, 1.0)))
    half = 0.5 * 10.0 ** (exponent - (figures - 1))
    return np.where(magnitude > 0.0, half, 0.5 * 10.0 ** (-figures))


def mode_omega2_allowance_cm2(
    displacement: np.ndarray,
    masses_amu: Sequence[float],
    half_ulps: np.ndarray,
) -> float:
    """Term 2 of the bound for one mode, in cm^-2.

    ``sum_ij |u_i| |u_j| e_ij`` with ``u = v / sqrt(m)`` the Cartesian
    form of the unit mass-weighted eigenvector ``v`` and ``e`` from
    :func:`element_half_ulps`, converted with the numerics module's own
    constants. First order in the roundings; see the module docstring.
    """

    root_mass = np.repeat(np.sqrt(np.asarray(masses_amu, dtype=float)), 3)
    u = np.abs(np.asarray(displacement, dtype=float) / root_mass)
    return float(u @ np.asarray(half_ulps, dtype=float) @ u) * OMEGA2_PER_UNIT_EIGENVALUE_CM2


def mode_tolerance_cm1(
    stored_frequency_cm1: float,
    *,
    max_deviation_cm1: float = DEFAULT_MAX_DEVIATION_CM1,
    omega2_allowance_cm2: float,
) -> float:
    """The bound one stored mode is judged against.

    ``max_deviation_cm1`` plus the exact width, in omega, of an
    omega-squared interval of half-width ``omega2_allowance_cm2`` around
    the stored value. See the module docstring.
    """

    magnitude = abs(stored_frequency_cm1)
    curvature_term = math.sqrt(magnitude * magnitude + omega2_allowance_cm2) - magnitude
    return max_deviation_cm1 + curvature_term


def resolve_print_format(
    source: HessianSource | None,
    software_name: str | None,
) -> tuple[HessianPrintFormat, str]:
    """Pick the print format from provenance, falling back to the coarsest.

    ``parsed_hess`` is ORCA's format; ``parsed_log`` is Gaussian's or
    Molpro's, told apart by the calculation's software name. Anything
    else -- an fchk, an uploaded or derived matrix, a log from a program
    with no format registered here -- gets the coarsest format, and the
    returned basis string says so.
    """

    name = (software_name or "").strip().lower()
    if source is HessianSource.parsed_hess:
        return HessianPrintFormat.orca_hess, "calc_hessian.source=parsed_hess"
    if source is HessianSource.parsed_log:
        if "molpro" in name:
            return HessianPrintFormat.molpro_log, f"calc_hessian.source=parsed_log software={name}"
        if "gaussian" in name:
            return HessianPrintFormat.gaussian_log, f"calc_hessian.source=parsed_log software={name}"
    return (
        COARSEST_PRINT_FORMAT,
        f"fallback to coarsest format (source={source.value if source is not None else None} software={name or None})",
    )


def _signed_square(value: float) -> float:
    return math.copysign(value * value, value)


def _naive_vibrational_modes(matrix: np.ndarray, masses: Sequence[float], dimension: int) -> list[NormalMode]:
    """Diagnostic only: unprojected diagonalisation, dropping the ``k``
    modes of smallest magnitude. What a comparison looks like when the
    rigid-body residue is left in -- see the module docstring."""

    modes = sorted(solve_normal_modes(matrix, masses), key=lambda mode: abs(mode.frequency_cm1))
    kept = modes[dimension:]
    kept.sort(key=lambda mode: mode.frequency_cm1)
    return kept


def compare_spectrum(
    hessian_hartree_bohr2: np.ndarray,
    coordinates_angstrom: np.ndarray,
    masses_amu: Sequence[float],
    stored_modes: Sequence[tuple[int, float]],
    *,
    print_format: HessianPrintFormat = COARSEST_PRINT_FORMAT,
    print_format_basis: str | None = None,
    max_deviation_cm1: float = DEFAULT_MAX_DEVIATION_CM1,
    max_omega2_deviation_cm2: float | None = None,
    project_rigid_body: bool = True,
) -> HessianReanalysis:
    """The pure comparison: matrix, frame, masses and stored list in.

    :param hessian_hartree_bohr2: ``(3N, 3N)`` Cartesian force constants.
    :param coordinates_angstrom: ``(N, 3)`` positions of the bound geometry.
    :param masses_amu: ``N`` masses, already resolved.
    :param stored_modes: ``(mode_index, frequency_cm1)`` pairs in
        ``mode_index`` order, imaginary modes negative.
    :param print_format: How the matrix was printed; fixes the per-element
        half-ULPs term 2 of the bound is computed from.
    :param print_format_basis: Where ``print_format`` came from, echoed.
    :param max_deviation_cm1: Term 1 of the bound, flat in cm^-1.
    :param max_omega2_deviation_cm2: Override: replace the per-mode term 2
        with this flat cm^-2 figure. ``None`` (the default) derives it.
    :param project_rigid_body: ``False`` is the diagnostic described in
        the module docstring, never the analysis.
    :returns: ``analysed``, ``rigid_body_curvature_too_large`` or
        ``mode_count_mismatch``; the database wrapper adds the rest.
    """

    masses = [float(m) for m in masses_amu]
    natoms = len(masses)
    matrix = np.asarray(hessian_hartree_bohr2, dtype=float)
    coordinates = np.asarray(coordinates_angstrom, dtype=float)

    rigid_body = rigid_body_subspace(coordinates, masses)
    curvatures = rigid_body_curvature_cm1(matrix, masses, rigid_body)
    max_curvature = max(curvatures, key=abs) if curvatures else 0.0
    common = {
        "natoms": natoms,
        "hessian_print_format": print_format.value,
        "print_format_basis": print_format_basis or "given",
        "rigid_body_dimension": rigid_body.dimension,
        "is_linear": rigid_body.is_linear,
        "max_rigid_body_curvature_cm1": round(max_curvature, _CM1_DECIMALS),
        "stored_count": len(stored_modes),
        "recovered_count": 3 * natoms - rigid_body.dimension,
        "stored_imaginary_count": sum(1 for _, f in stored_modes if f < 0.0),
    }
    if abs(max_curvature) > FRAME_CONSISTENCY_TOLERANCE_CM1:
        return HessianReanalysis(status=ReanalysisStatus.rigid_body_curvature_too_large, **common)

    if project_rigid_body:
        recovered = solve_vibrational_modes(matrix, masses, rigid_body)
    else:
        recovered = _naive_vibrational_modes(matrix, masses, rigid_body.dimension)

    if len(stored_modes) != len(recovered):
        return HessianReanalysis(
            status=ReanalysisStatus.mode_count_mismatch,
            recovered_imaginary_count=sum(1 for mode in recovered if mode.frequency_cm1 < 0.0),
            **common,
        )

    stored_in_index_order = [float(f) for _, f in stored_modes]
    order_violations = sum(
        1 for earlier, later in pairwise(stored_in_index_order) if earlier > later
    )
    stored_sorted = sorted(stored_modes, key=lambda pair: (pair[1], pair[0]))
    half_ulps = element_half_ulps(matrix, print_format)

    comparisons: list[ModeComparison] = []
    for (mode_index, stored), mode in zip(stored_sorted, recovered, strict=True):
        deviation = mode.frequency_cm1 - stored
        allowance = (
            mode_omega2_allowance_cm2(mode.displacement, masses, half_ulps)
            if max_omega2_deviation_cm2 is None
            else float(max_omega2_deviation_cm2)
        )
        tolerance = mode_tolerance_cm1(
            stored,
            max_deviation_cm1=max_deviation_cm1,
            omega2_allowance_cm2=allowance,
        )
        comparisons.append(
            ModeComparison(
                mode_index=int(mode_index),
                stored_frequency_cm1=round(stored, _CM1_DECIMALS),
                recovered_frequency_cm1=round(mode.frequency_cm1, _CM1_DECIMALS),
                deviation_cm1=round(deviation, _CM1_DECIMALS),
                omega2_deviation_cm2=round(
                    abs(_signed_square(mode.frequency_cm1) - _signed_square(stored)), _CM2_DECIMALS
                ),
                omega2_allowance_cm2=round(allowance, _CM2_DECIMALS),
                tolerance_cm1=round(tolerance, _CM1_DECIMALS),
                within_tolerance=abs(deviation) <= tolerance,
                nearest_match=match_stored_frequency(stored, recovered).mode is not None,
            )
        )

    absolute = [abs(c.deviation_cm1) for c in comparisons]
    rms = math.sqrt(sum(d * d for d in absolute) / len(absolute)) if absolute else 0.0
    return HessianReanalysis(
        status=ReanalysisStatus.analysed,
        modes=tuple(comparisons),
        matched_count=sum(1 for c in comparisons if c.nearest_match),
        within_tolerance_count=sum(1 for c in comparisons if c.within_tolerance),
        max_abs_deviation_cm1=round(max(absolute), _CM1_DECIMALS) if absolute else 0.0,
        rms_abs_deviation_cm1=round(rms, _CM1_DECIMALS),
        max_omega2_deviation_cm2=(max(c.omega2_deviation_cm2 for c in comparisons) if comparisons else 0.0),
        stored_order_violations=order_violations,
        recovered_imaginary_count=sum(1 for mode in recovered if mode.frequency_cm1 < 0.0),
        **common,
    )


def _with_identity(result: HessianReanalysis, calc: Calculation) -> HessianReanalysis:
    from dataclasses import replace

    return replace(
        result,
        calculation_ref=calc.public_ref,
        calculation_type=calc.type.value if calc.type is not None else None,
        species_entry_ref=(calc.species_entry.public_ref if calc.species_entry is not None else None),
        transition_state_entry_ref=(
            calc.transition_state_entry.public_ref if calc.transition_state_entry is not None else None
        ),
    )


def _imaginary_block(session: Session, calculation_id: int) -> tuple[str | None, tuple[ImaginaryModeComparison, ...]]:
    """The declared-versus-determined block, from the production projection."""

    projection = build_imaginary_mode_projection(session, calculation_id)
    if projection.status is ProjectionStatus.no_imaginary_modes:
        return projection.status.value, ()
    modes = tuple(
        ImaginaryModeComparison(
            mode_index=mode.mode_index,
            stored_frequency_cm1=round(mode.frequency_cm1, _CM1_DECIMALS),
            declared_disposition=(mode.declared_disposition.value if mode.declared_disposition is not None else None),
            determination=(mode.determination.value if mode.determination is not None else None),
            not_determined_reason=mode.not_determined_reason,
            agreement=mode.agreement.value,
            rigid_body_overlap=(
                round(mode.rigid_body_overlap, _OVERLAP_DECIMALS) if mode.rigid_body_overlap is not None else None
            ),
            torsion_overlap=(round(mode.torsion_overlap, _OVERLAP_DECIMALS) if mode.torsion_overlap is not None else None),
        )
        for mode in projection.modes
    )
    return projection.status.value, modes


def reanalyse_calculation(
    session: Session,
    calculation: Calculation,
    *,
    max_deviation_cm1: float = DEFAULT_MAX_DEVIATION_CM1,
    max_omega2_deviation_cm2: float | None = None,
) -> HessianReanalysis:
    """Load one calculation's Hessian, frame, masses and list, and compare.

    Reads only. The refusal statuses this layer adds are the ones the
    database can produce; the numeric ones come from
    :func:`compare_spectrum`.
    """

    stored = [
        (int(mode.mode_index), float(mode.frequency_cm1))
        for mode in session.scalars(
            select(CalculationFreqMode)
            .where(CalculationFreqMode.calculation_id == calculation.id)
            .order_by(CalculationFreqMode.mode_index)
        ).all()
    ]
    hessian = session.scalar(select(CalculationHessian).where(CalculationHessian.calculation_id == calculation.id))

    if hessian is None:
        return _with_identity(
            HessianReanalysis(
                status=ReanalysisStatus.hessian_not_stored,
                stored_count=len(stored) if stored else None,
                stored_imaginary_count=(sum(1 for _, f in stored if f < 0.0) if stored else None),
            ),
            calculation,
        )
    if not stored:
        return _with_identity(
            HessianReanalysis(status=ReanalysisStatus.frequency_list_missing, natoms=hessian.natoms),
            calculation,
        )

    atoms = list(
        session.scalars(
            select(GeometryAtom).where(GeometryAtom.geometry_id == hessian.geometry_id).order_by(GeometryAtom.atom_index)
        ).all()
    )
    if not atoms or len(atoms) != hessian.natoms:
        return _with_identity(
            HessianReanalysis(status=ReanalysisStatus.geometry_incomplete, natoms=hessian.natoms),
            calculation,
        )

    masses: list[float] = []
    for atom in atoms:
        mass = atomic_mass(atom.element, atom.isotope_mass_number)
        if mass is None or mass <= 0.0:
            return _with_identity(
                HessianReanalysis(status=ReanalysisStatus.masses_unresolved, natoms=hessian.natoms),
                calculation,
            )
        masses.append(mass)

    coordinates = np.array([[atom.x, atom.y, atom.z] for atom in atoms], dtype=float)
    software_name = None
    release = calculation.software_release
    if release is not None and release.software is not None:
        software_name = release.software.name
    print_format, basis = resolve_print_format(hessian.source, software_name)
    try:
        matrix = unpack_lower_triangle(hessian.lower_triangle_hartree_bohr2, hessian.natoms)
        result = compare_spectrum(
            matrix,
            coordinates,
            masses,
            stored,
            print_format=print_format,
            print_format_basis=basis,
            max_deviation_cm1=max_deviation_cm1,
            max_omega2_deviation_cm2=max_omega2_deviation_cm2,
        )
    except ValueError:
        return _with_identity(
            HessianReanalysis(status=ReanalysisStatus.geometry_incomplete, natoms=hessian.natoms),
            calculation,
        )

    if result.analysed:
        from dataclasses import replace

        projection_status, imaginary = _imaginary_block(session, calculation.id)
        result = replace(result, projection_status=projection_status, imaginary_modes=imaginary)
    return _with_identity(result, calculation)


def reanalysis_scope(session: Session, *, calculation_ref: str | None = None) -> list[Calculation]:
    """Every calculation with a stored Hessian **or** a stored frequency list.

    The union is the denominator the manuscript needs: a frequency
    calculation with no Hessian is exactly the record that must be
    counted as not checkable rather than left out.
    """

    statement = select(Calculation)
    if calculation_ref is not None:
        if not calculation_ref.startswith("calc_"):
            raise ValueError(
                f"calculation_ref must be a public ref (calc_...), not {calculation_ref!r}: "
                "database ids are not an interface"
            )
        statement = statement.where(Calculation.public_ref == calculation_ref)
    else:
        has_modes = select(CalculationFreqMode.calculation_id).where(
            CalculationFreqMode.calculation_id == Calculation.id
        )
        has_hessian = select(CalculationHessian.calculation_id).where(
            CalculationHessian.calculation_id == Calculation.id
        )
        statement = statement.where(has_modes.exists() | has_hessian.exists())
    rows = list(session.scalars(statement).all())
    rows.sort(key=lambda calc: (calc.public_ref or "", calc.id))
    return rows


def _record_dict(result: HessianReanalysis, *, include_modes: bool) -> dict:
    record = {
        "calculation_ref": result.calculation_ref,
        "calculation_type": result.calculation_type,
        "species_entry_ref": result.species_entry_ref,
        "transition_state_entry_ref": result.transition_state_entry_ref,
        "status": result.status.value,
        "natoms": result.natoms,
        "hessian_print_format": result.hessian_print_format,
        "print_format_basis": result.print_format_basis,
        "rigid_body_dimension": result.rigid_body_dimension,
        "is_linear": result.is_linear,
        "max_rigid_body_curvature_cm1": result.max_rigid_body_curvature_cm1,
        "stored_count": result.stored_count,
        "recovered_count": result.recovered_count,
        "matched_count": result.matched_count,
        "within_tolerance_count": result.within_tolerance_count,
        "within_tolerance": result.within_tolerance,
        "max_abs_deviation_cm1": result.max_abs_deviation_cm1,
        "rms_abs_deviation_cm1": result.rms_abs_deviation_cm1,
        "max_omega2_deviation_cm2": result.max_omega2_deviation_cm2,
        "stored_order_violations": result.stored_order_violations,
        "stored_imaginary_count": result.stored_imaginary_count,
        "recovered_imaginary_count": result.recovered_imaginary_count,
        "projection_status": result.projection_status,
        "imaginary_modes": [
            {
                "mode_index": mode.mode_index,
                "stored_frequency_cm1": mode.stored_frequency_cm1,
                "declared_disposition": mode.declared_disposition,
                "determination": mode.determination,
                "not_determined_reason": mode.not_determined_reason,
                "agreement": mode.agreement,
                "rigid_body_overlap": mode.rigid_body_overlap,
                "torsion_overlap": mode.torsion_overlap,
            }
            for mode in result.imaginary_modes
        ],
    }
    if include_modes:
        record["modes"] = [
            {
                "mode_index": mode.mode_index,
                "stored_frequency_cm1": mode.stored_frequency_cm1,
                "recovered_frequency_cm1": mode.recovered_frequency_cm1,
                "deviation_cm1": mode.deviation_cm1,
                "omega2_deviation_cm2": mode.omega2_deviation_cm2,
                "omega2_allowance_cm2": mode.omega2_allowance_cm2,
                "tolerance_cm1": mode.tolerance_cm1,
                "within_tolerance": mode.within_tolerance,
                "nearest_match": mode.nearest_match,
            }
            for mode in result.modes
        ]
    return record


def hessian_reanalysis(
    session: Session,
    *,
    calculation_ref: str | None = None,
    max_deviation_cm1: float = DEFAULT_MAX_DEVIATION_CM1,
    max_omega2_deviation_cm2: float | None = None,
    include_modes: bool = True,
) -> dict:
    """The generator: every record in scope, as a canonical dictionary.

    Ordered by public ref, no wall-clock values, floats rounded to fixed
    decimals -- ``json.dumps(result, sort_keys=True)`` of two runs on the
    same database is byte-identical. Registered by the paper generator
    registry under the name ``hessian_reanalysis``.

    :param session: Open read session; nothing is written.
    :param calculation_ref: One calculation by public ref (``calc_...``;
        a database id is refused), or ``None`` for :func:`reanalysis_scope`.
    :param max_deviation_cm1: Term 1 of the per-mode bound.
    :param max_omega2_deviation_cm2: Flat override for term 2; ``None``
        derives it per mode from the print format.
    :param include_modes: Whether each analysed record lists every mode.
        The per-record summary fields are present either way.
    """

    targets = reanalysis_scope(session, calculation_ref=calculation_ref)
    results = [
        reanalyse_calculation(
            session,
            calc,
            max_deviation_cm1=max_deviation_cm1,
            max_omega2_deviation_cm2=max_omega2_deviation_cm2,
        )
        for calc in targets
    ]
    results.sort(key=lambda r: (r.calculation_ref or "", r.status.value))

    by_status = {status.value: 0 for status in ReanalysisStatus}
    for result in results:
        by_status[result.status.value] += 1
    analysed = [r for r in results if r.analysed]
    within = [r for r in analysed if r.within_tolerance]
    deviations = [r.max_abs_deviation_cm1 for r in analysed if r.max_abs_deviation_cm1 is not None]
    omega2 = [r.max_omega2_deviation_cm2 for r in analysed if r.max_omega2_deviation_cm2 is not None]

    return {
        "generator": "hessian_reanalysis",
        "tolerance": {
            "max_deviation_cm1": max_deviation_cm1,
            "max_omega2_deviation_cm2": (
                None if max_omega2_deviation_cm2 is None else round(max_omega2_deviation_cm2, _CM2_DECIMALS)
            ),
            "omega2_allowance": (
                "per mode: first-order eigenvalue sensitivity sum_ij |u_i||u_j| e_ij to the per-element "
                "half-ULP e of the Hessian's print format, u = v / sqrt(m)"
                if max_omega2_deviation_cm2 is None
                else "flat override from the command line"
            ),
            "rule": (
                "|recovered - stored| <= max_deviation_cm1 + (sqrt(stored^2 + omega2_allowance_cm2) - |stored|)"
            ),
            "derivation": {
                "frequency_print_half_ulp_cm1": FREQUENCY_PRINT_HALF_ULP_CM1,
                "print_formats": {
                    HessianPrintFormat.gaussian_log.value: "6 significant figures",
                    HessianPrintFormat.molpro_log.value: "7 decimals",
                    HessianPrintFormat.orca_hess.value: "11 significant figures",
                },
                "fallback_print_format": COARSEST_PRINT_FORMAT.value,
                "omega2_per_unit_eigenvalue_cm2": round(OMEGA2_PER_UNIT_EIGENVALUE_CM2, _CM2_DECIMALS),
            },
        },
        "method": {
            "masses": "isotopic; geometry_atom.isotope_mass_number NULL means the most abundant isotope",
            "rigid_body_projection": "exact, before diagonalisation (solve_vibrational_modes)",
            "pairing": "stored and recovered spectra sorted ascending (signed) and paired by position",
            "units": "hartree/bohr^2 in, cm^-1 out",
        },
        "scope": {
            "calculation_count": len(results),
            "by_status": by_status,
            "analysed_count": len(analysed),
            "within_tolerance_count": len(within),
            "exceeding_count": len(analysed) - len(within),
            "modes_compared": sum(r.stored_count or 0 for r in analysed),
            "max_abs_deviation_cm1": max(deviations) if deviations else None,
            "max_omega2_deviation_cm2": max(omega2) if omega2 else None,
            "records_with_order_violations": sum(1 for r in analysed if r.stored_order_violations),
            "imaginary_declaration_conflicts": sum(
                1 for r in analysed for m in r.imaginary_modes if m.agreement == "conflicts"
            ),
        },
        "records": [_record_dict(result, include_modes=include_modes) for result in results],
    }
