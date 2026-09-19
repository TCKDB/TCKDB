"""The deposited spectrum-from-Hessian check, against spectra known in advance.

Two synthetic molecules whose vibrational spectrum follows from the force
constants by hand -- a diatomic (one stretch, ``sqrt(k/mu)``) and a
symmetric linear triatomic (symmetric and antisymmetric stretch, doubly
degenerate bend) -- pin the recovery to closed-form answers, so a
comparison that passes here passes because the numbers are right rather
than because the same code produced both sides. The Gaussian fixture is
then the real-data case: a program's own printed list against its own
printed matrix.

Every refusal category is made to fire on a record built for it, the
empty scope exits 2, the JSON is byte-identical across two runs, and the
three mutations the plan names -- scale the Hessian by 1.01, drop the
rigid-body projection, swap two stored frequencies -- each change the
result in the way the report is supposed to show.
"""

from __future__ import annotations

import contextlib
import importlib.util
import math
import pathlib
import sys

import numpy as np
import pytest

from app.chemistry.normal_modes import (
    atomic_mass,
    rigid_body_subspace,
    solve_vibrational_modes,
    unpack_lower_triangle,
    wavenumber_from_eigenvalue,
)
from app.db.models.common import CalculationType, HessianSource
from app.services.hessian_parsing import parse_hessian_from_artifact
from app.services.hessian_reanalysis import (
    COARSEST_PRINT_FORMAT,
    DEFAULT_MAX_DEVIATION_CM1,
    FREQUENCY_PRINT_HALF_ULP_CM1,
    OMEGA2_PER_UNIT_EIGENVALUE_CM2,
    HessianPrintFormat,
    ReanalysisStatus,
    compare_spectrum,
    element_half_ulps,
    hessian_reanalysis,
    mode_omega2_allowance_cm2,
    mode_tolerance_cm1,
    reanalyse_calculation,
    resolve_print_format,
)
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_geometry_atoms,
    attach_hessian,
    attach_input_geometry,
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)
from tests.services.test_normal_modes import GAUSSIAN_INPUT_ORIENTATION, _split

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "validation" / "hessian_reanalysis_report.py"


# ---------------------------------------------------------------------------
# Synthetic molecules with closed-form spectra
# ---------------------------------------------------------------------------


def _pack(matrix: np.ndarray) -> list[float]:
    """Row-major lower triangle with diagonal -- ``calc_hessian``'s layout."""

    dim = matrix.shape[0]
    return [float(matrix[row, col]) for row in range(dim) for col in range(row + 1)]


def _diatomic(k: float = 0.33, d: float = 1.27):
    """H-Cl on the z axis with one bond force constant ``k`` (hartree/bohr^2).

    The only vibration is the stretch at ``sqrt(k / mu)``.
    """

    elements = ["H", "Cl"]
    coords = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, d]])
    matrix = np.zeros((6, 6))
    for a, b, sign in ((2, 2, 1.0), (5, 5, 1.0), (2, 5, -1.0), (5, 2, -1.0)):
        matrix[a, b] = sign * k
    m1, m2 = atomic_mass("H", None), atomic_mass("Cl", None)
    mu = m1 * m2 / (m1 + m2)
    expected = [wavenumber_from_eigenvalue(k / mu)]
    return elements, coords, matrix, expected


def _linear_triatomic(k: float = 1.0, c_bend: float = 0.05, d: float = 1.16):
    """O=C=O on the z axis: two bond springs ``k`` and a bending term ``c_bend``.

    Bond stretches: ``k [[1,-1,0],[-1,2,-1],[0,-1,1]]`` on the z components.
    Bending, per transverse axis: ``c_bend v v^T`` with ``v = (1, -2, 1)``.
    Closed-form mass-weighted eigenvalues, with ``m`` the end mass and
    ``M`` the centre mass:

    * bend (doubly degenerate): ``c_bend (2/m + 4/M)``
    * symmetric stretch:        ``k / m``
    * antisymmetric stretch:    ``k (1/m + 2/M)``
    """

    elements = ["O", "C", "O"]
    coords = np.array([[0.0, 0.0, -d], [0.0, 0.0, 0.0], [0.0, 0.0, d]])
    matrix = np.zeros((9, 9))
    stretch = k * np.array([[1.0, -1.0, 0.0], [-1.0, 2.0, -1.0], [0.0, -1.0, 1.0]])
    v = np.array([1.0, -2.0, 1.0])
    bend = c_bend * np.outer(v, v)
    for axis, block in ((0, bend), (1, bend), (2, stretch)):
        for i in range(3):
            for j in range(3):
                matrix[3 * i + axis, 3 * j + axis] = block[i, j]
    m, big_m = atomic_mass("O", None), atomic_mass("C", None)
    bend_w = wavenumber_from_eigenvalue(c_bend * (2.0 / m + 4.0 / big_m))
    expected = sorted(
        [
            bend_w,
            bend_w,
            wavenumber_from_eigenvalue(k / m),
            wavenumber_from_eigenvalue(k * (1.0 / m + 2.0 / big_m)),
        ]
    )
    return elements, coords, matrix, expected


def _masses(elements):
    masses = [atomic_mass(e, None) for e in elements]
    assert all(mass is not None for mass in masses)
    return masses


def _stored(frequencies, decimals: int = 4):
    """A stored list printed to ``decimals`` places, in ascending mode order."""

    return [(index, round(f, decimals)) for index, f in enumerate(sorted(frequencies), start=1)]


def _gaussian_fixture():
    text = (FIXTURES / "gaussian" / "freq_g09.log").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=False)
    assert parsed is not None
    elements, coords = _split(GAUSSIAN_INPUT_ORIENTATION)
    matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)
    printed = [float(x) for line in text.splitlines() if "Frequencies --" in line for x in line.split("--")[1].split()]
    assert len(printed) == 30
    return elements, coords, matrix, printed


# ---------------------------------------------------------------------------
# The tolerance is derived, not measured
# ---------------------------------------------------------------------------


def test_term_one_is_the_frequency_print_half_ulp():
    """Term 1 of the bound is the one stated constant, and the omega-squared
    bracket is the exact inverse of an omega-squared interval, not a
    first-order approximation of it."""

    assert DEFAULT_MAX_DEVIATION_CM1 == FREQUENCY_PRINT_HALF_ULP_CM1 == 0.005
    assert OMEGA2_PER_UNIT_EIGENVALUE_CM2 == pytest.approx(wavenumber_from_eigenvalue(1.0) ** 2)
    w, b = 110.0, 13.0
    assert mode_tolerance_cm1(w, omega2_allowance_cm2=b) - 0.005 == pytest.approx(math.sqrt(w * w + b) - w)
    assert mode_tolerance_cm1(3000.0, omega2_allowance_cm2=b) == pytest.approx(0.005 + b / 6000.0, rel=1e-3)
    assert mode_tolerance_cm1(20.0, omega2_allowance_cm2=0.0) == 0.005


def test_element_half_ulps_follow_the_print_format():
    """Six significant figures in D-format: ``0.410282D-01`` is good to
    5e-8; ``0.432137D+00`` to 5e-7; a printed zero to 5e-7. Molpro's seven
    decimals are flat; ORCA's eleven figures are five orders finer."""

    matrix = np.array([[0.0410282, 0.432137], [0.432137, 0.0]])
    gaussian = element_half_ulps(matrix, HessianPrintFormat.gaussian_log)
    assert gaussian[0, 0] == pytest.approx(5e-8)
    assert gaussian[0, 1] == pytest.approx(5e-7)
    assert gaussian[1, 1] == pytest.approx(5e-7)
    molpro = element_half_ulps(matrix, HessianPrintFormat.molpro_log)
    assert np.all(molpro == 5e-8)
    orca = element_half_ulps(matrix, HessianPrintFormat.orca_hess)
    assert orca[0, 1] == pytest.approx(5e-12)
    assert np.all(orca < gaussian)


def test_the_per_mode_allowance_is_the_eigenvector_weighted_sensitivity():
    """Term 2 is ``sum_ij |u_i||u_j| e_ij`` with ``u = v / sqrt(m)``, checked
    against a hand computation on the diatomic: the stretch eigenvector is
    the only mode, the Cartesian half-ULPs are known, and the answer is a
    closed form."""

    elements, coords, matrix, expected = _diatomic()
    masses = _masses(elements)
    rigid = rigid_body_subspace(coords, masses)
    (stretch,) = solve_vibrational_modes(matrix, masses, rigid)
    half = element_half_ulps(matrix, HessianPrintFormat.gaussian_log)
    root = np.repeat(np.sqrt(masses), 3)
    u = np.abs(stretch.displacement / root)
    by_hand = float(u @ half @ u) * OMEGA2_PER_UNIT_EIGENVALUE_CM2

    assert mode_omega2_allowance_cm2(stretch.displacement, masses, half) == pytest.approx(by_hand)
    # Only the four z-z elements are non-zero in the diatomic; the zeros
    # carry the coarsest half-ULP but the eigenvector has no x/y weight, so
    # they contribute nothing.
    assert by_hand > 0.0
    assert by_hand == pytest.approx(
        (0.5e-6 * (u[2] * u[2] + u[5] * u[5] + 2 * u[2] * u[5])) * OMEGA2_PER_UNIT_EIGENVALUE_CM2
    )


def test_the_allowance_exceeds_every_measured_deviation_and_ranks_the_modes_honestly():
    """On the Gaussian fixture every mode's measured omega-squared deviation
    is inside its own derived allowance, and the allowance is a property
    of the mode: the 3446 cm^-1 C-H stretch, whose eigenvector sits on the
    largest and most coarsely printed elements, is entitled to more than
    the 110 cm^-1 torsion. An allowance that ignored the eigenvector
    weighting would give every mode the same figure and fail the second
    assertion."""

    elements, coords, matrix, printed = _gaussian_fixture()
    result = compare_spectrum(matrix, coords, _masses(elements), _stored(printed))

    assert result.status is ReanalysisStatus.analysed
    assert result.within_tolerance is True
    for mode in result.modes:
        assert mode.omega2_deviation_cm2 < mode.omega2_allowance_cm2
        assert abs(mode.deviation_cm1) <= mode.tolerance_cm1
    by_stored = {round(m.stored_frequency_cm1, 4): m for m in result.modes}
    stretch = by_stored[3446.2841]
    torsion = by_stored[110.1603]
    assert stretch.omega2_allowance_cm2 > 2.0 * torsion.omega2_allowance_cm2
    assert 30.0 < stretch.omega2_allowance_cm2 < 40.0
    assert 8.0 < torsion.omega2_allowance_cm2 < 14.0
    assert len({m.omega2_allowance_cm2 for m in result.modes}) == len(result.modes)


def test_a_flat_omega_squared_override_replaces_the_per_mode_term():
    elements, coords, matrix, expected = _linear_triatomic()
    masses = _masses(elements)
    derived = compare_spectrum(matrix, coords, masses, _stored(expected))
    flat = compare_spectrum(matrix, coords, masses, _stored(expected), max_omega2_deviation_cm2=13.0)
    zero = compare_spectrum(matrix, coords, masses, _stored(expected), max_omega2_deviation_cm2=0.0)

    assert len({m.omega2_allowance_cm2 for m in derived.modes}) > 1
    assert {m.omega2_allowance_cm2 for m in flat.modes} == {13.0}
    assert all(m.tolerance_cm1 == 0.005 for m in zero.modes)


@pytest.mark.parametrize(
    ("source", "software", "expected_format", "fallback"),
    [
        (HessianSource.parsed_hess, "ORCA", HessianPrintFormat.orca_hess, False),
        (HessianSource.parsed_log, "Molpro", HessianPrintFormat.molpro_log, False),
        (HessianSource.parsed_log, "gaussian", HessianPrintFormat.gaussian_log, False),
        (HessianSource.parsed_log, "psi4", COARSEST_PRINT_FORMAT, True),
        (HessianSource.parsed_fchk, "gaussian", COARSEST_PRINT_FORMAT, True),
        (HessianSource.uploaded, None, COARSEST_PRINT_FORMAT, True),
        (None, None, COARSEST_PRINT_FORMAT, True),
    ],
)
def test_the_print_format_comes_from_provenance_or_falls_back_to_the_coarsest(source, software, expected_format, fallback):
    print_format, basis = resolve_print_format(source, software)
    assert print_format is expected_format
    assert basis.startswith("fallback") is fallback


# ---------------------------------------------------------------------------
# Closed-form spectra
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [_diatomic, _linear_triatomic], ids=["diatomic", "linear_triatomic"])
def test_a_closed_form_spectrum_is_recovered_within_the_bound(builder):
    elements, coords, matrix, expected = builder()
    result = compare_spectrum(matrix, coords, _masses(elements), _stored(expected))

    assert result.status is ReanalysisStatus.analysed
    assert result.is_linear is True
    assert result.rigid_body_dimension == 5
    assert result.stored_count == result.recovered_count == len(expected)
    assert result.matched_count == len(expected)
    assert result.within_tolerance_count == len(expected)
    assert result.within_tolerance is True
    # Printed to four decimals, so the residual is the print rounding alone.
    assert result.max_abs_deviation_cm1 <= 0.5e-4 + 1e-9
    for mode, analytic in zip(result.modes, sorted(expected), strict=True):
        assert mode.recovered_frequency_cm1 == pytest.approx(analytic, abs=1e-6)


def test_the_triatomic_bend_is_degenerate_and_still_paired():
    """Positional pairing is a bijection even through a degenerate pair."""

    elements, coords, matrix, expected = _linear_triatomic()
    result = compare_spectrum(matrix, coords, _masses(elements), _stored(expected))
    bends = [m for m in result.modes if abs(m.stored_frequency_cm1 - expected[0]) < 1e-3]
    assert len(bends) == 2
    assert all(m.nearest_match and m.within_tolerance for m in bends)


def test_the_gaussian_fixture_reproduces_its_own_printed_list():
    """Real data: Gaussian's printed frequencies against Gaussian's printed
    force constants, projected. This is the measurement the manuscript's
    sentence rests on, at fixture scale."""

    elements, coords, matrix, printed = _gaussian_fixture()
    result = compare_spectrum(matrix, coords, _masses(elements), _stored(printed))

    assert result.status is ReanalysisStatus.analysed
    assert result.stored_count == result.recovered_count == 30
    assert result.within_tolerance is True
    assert result.matched_count == 30
    assert result.stored_order_violations == 0
    assert result.max_abs_deviation_cm1 < 0.01
    assert result.hessian_print_format == COARSEST_PRINT_FORMAT.value


# ---------------------------------------------------------------------------
# Mutations: each must move the result the way the report is meant to show
# ---------------------------------------------------------------------------


def test_scaling_the_hessian_by_one_percent_fails_the_bound():
    elements, coords, matrix, expected = _linear_triatomic()
    result = compare_spectrum(matrix * 1.01, coords, _masses(elements), _stored(expected))

    assert result.status is ReanalysisStatus.analysed
    assert result.within_tolerance is False
    assert result.within_tolerance_count == 0
    # sqrt(1.01) - 1 ~ 0.5% on every mode, far outside the bound.
    assert result.max_abs_deviation_cm1 > 0.004 * max(expected)


def test_dropping_the_rigid_body_projection_changes_the_result():
    """The Gaussian fixture carries 12.8 cm^-1 of rigid-body curvature.
    Left in, it couples into the 110 cm^-1 torsion and that mode leaves
    the bound; projected out, every mode is inside it."""

    elements, coords, matrix, printed = _gaussian_fixture()
    masses = _masses(elements)
    projected = compare_spectrum(matrix, coords, masses, _stored(printed))
    unprojected = compare_spectrum(matrix, coords, masses, _stored(printed), project_rigid_body=False)

    assert projected.within_tolerance is True
    assert unprojected.within_tolerance is False
    assert unprojected.max_abs_deviation_cm1 > 10 * projected.max_abs_deviation_cm1
    lowest = min(unprojected.modes, key=lambda m: m.stored_frequency_cm1)
    assert lowest.stored_frequency_cm1 == pytest.approx(110.1603)
    assert not lowest.within_tolerance


def test_rigid_body_coupling_added_to_a_clean_hessian_is_invisible_only_when_projected():
    """The synthetic version of the same fact, where the expected answer is
    exact. A stored Hessian at an imperfect geometry couples a rotation to a
    vibration; put that coupling in by hand -- a symmetric off-diagonal
    block between one rotation and the bend, with zero curvature *along*
    the rotation so the frame check passes -- and the bend moves by
    ``c^2 / lambda`` only when the rotation is left in."""

    elements, coords, matrix, expected = _linear_triatomic()
    masses = _masses(elements)
    rigid = rigid_body_subspace(coords, masses)
    root_mass = np.repeat(np.sqrt(masses), 3)
    rotation = rigid.basis[-1]
    bend = solve_vibrational_modes(matrix, masses, rigid)[0].displacement
    bend_eigenvalue = (expected[0] / wavenumber_from_eigenvalue(1.0)) ** 2
    coupling = 0.1 * bend_eigenvalue
    mass_weighted = coupling * (np.outer(rotation, bend) + np.outer(bend, rotation))
    residue = mass_weighted * np.outer(root_mass, root_mass)

    projected = compare_spectrum(matrix + residue, coords, masses, _stored(expected))
    unprojected = compare_spectrum(matrix + residue, coords, masses, _stored(expected), project_rigid_body=False)

    assert projected.status is ReanalysisStatus.analysed
    assert abs(projected.max_rigid_body_curvature_cm1) < 1e-3  # numerically zero; the frame check passes
    assert projected.within_tolerance is True
    assert projected.max_abs_deviation_cm1 <= 0.5e-4 + 1e-6
    assert unprojected.status is ReanalysisStatus.analysed
    assert unprojected.within_tolerance is False
    assert unprojected.max_abs_deviation_cm1 > 1.0


def test_swapping_two_stored_frequencies_is_reported():
    elements, coords, matrix, expected = _linear_triatomic()
    stored = _stored(expected)
    swapped = list(stored)
    swapped[2], swapped[3] = (3, stored[3][1]), (4, stored[2][1])

    clean = compare_spectrum(matrix, coords, _masses(elements), stored)
    result = compare_spectrum(matrix, coords, _masses(elements), swapped)

    assert clean.stored_order_violations == 0
    assert result.stored_order_violations == 1
    # The spectrum itself is unchanged by a relabelling, and the report says so
    # rather than manufacturing a deviation out of an index swap.
    assert result.max_abs_deviation_cm1 == clean.max_abs_deviation_cm1
    assert [m.mode_index for m in result.modes] == [1, 2, 4, 3]


def test_a_partial_stored_list_is_a_mode_count_mismatch():
    elements, coords, matrix, expected = _linear_triatomic()
    result = compare_spectrum(matrix, coords, _masses(elements), _stored(expected[:2]))

    assert result.status is ReanalysisStatus.mode_count_mismatch
    assert result.stored_count == 2
    assert result.recovered_count == 4
    assert result.modes == ()
    assert result.within_tolerance is None


def test_a_wrong_frame_is_refused_rather_than_compared():
    elements, coords, matrix, expected = _linear_triatomic()
    angle = math.radians(30.0)
    rotation = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(angle), -math.sin(angle)], [0.0, math.sin(angle), math.cos(angle)]])
    result = compare_spectrum(matrix, coords @ rotation.T, _masses(elements), _stored(expected))

    assert result.status is ReanalysisStatus.rigid_body_curvature_too_large
    assert abs(result.max_rigid_body_curvature_cm1) > 100.0
    assert result.modes == ()


# ---------------------------------------------------------------------------
# Against the database
# ---------------------------------------------------------------------------


def _deposit(session, elements, coords, matrix, stored_frequencies, *, with_hessian=True, isotopes=None):
    species = make_species(session, inchi_key=next_inchi_key("HRAN"))
    entry = make_species_entry(session, species)
    calc = make_calculation(session, type=CalculationType.freq, species_entry_id=entry.id)
    geometry = make_geometry(session, natoms=len(elements))
    atoms = attach_geometry_atoms(session, geometry=geometry, symbols=list(elements), coords=coords.tolist())
    if isotopes is not None:
        for atom, mass_number in zip(atoms, isotopes, strict=True):
            atom.isotope_mass_number = mass_number
        session.flush()
    attach_input_geometry(session, calculation=calc, geometry=geometry)
    if stored_frequencies is not None:
        attach_freq_result(session, calculation=calc, frequencies_cm1=list(stored_frequencies))
    if with_hessian:
        attach_hessian(
            session,
            calculation=calc,
            geometry=geometry,
            natoms=len(elements),
            lower_triangle=_pack(matrix),
        )
    return calc


def test_a_deposited_record_is_analysed_from_the_database(db_session):
    elements, coords, matrix, expected = _diatomic()
    calc = _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected])

    result = reanalyse_calculation(db_session, calc)

    assert result.status is ReanalysisStatus.analysed
    assert result.calculation_ref == calc.public_ref
    assert result.calculation_type == "freq"
    assert result.species_entry_ref is not None
    assert result.within_tolerance is True
    assert result.projection_status == "no_imaginary_modes"
    assert result.stored_imaginary_count == 0 and result.recovered_imaginary_count == 0
    # The factory's Hessian is ``parsed_log`` with no software on the
    # calculation, so the format falls back to the coarsest and says so.
    assert result.hessian_print_format == COARSEST_PRINT_FORMAT.value
    assert result.print_format_basis.startswith("fallback")


@pytest.mark.parametrize(
    ("with_hessian", "frequencies", "isotopes", "expected_status"),
    [
        (False, "full", None, ReanalysisStatus.hessian_not_stored),
        (True, None, None, ReanalysisStatus.frequency_list_missing),
        (True, "partial", None, ReanalysisStatus.mode_count_mismatch),
        (True, "full", [99, 35], ReanalysisStatus.masses_unresolved),
    ],
    ids=["no_hessian", "no_frequency_list", "partial_list", "unknown_isotope"],
)
def test_each_refusal_category_fires(db_session, with_hessian, frequencies, isotopes, expected_status):
    elements, coords, matrix, expected = _linear_triatomic()
    stored = None if frequencies is None else ([round(f, 4) for f in expected] if frequencies == "full" else [round(expected[0], 4)])
    calc = _deposit(
        db_session,
        elements,
        coords,
        matrix,
        stored,
        with_hessian=with_hessian,
        isotopes=isotopes if isotopes is None else [isotopes[0], isotopes[1], isotopes[0]],
    )

    result = reanalyse_calculation(db_session, calc)

    assert result.status is expected_status
    assert result.calculation_ref == calc.public_ref
    assert result.modes == ()
    assert result.within_tolerance is None


def test_the_imaginary_determination_sits_beside_the_declaration(db_session):
    """A real transition state: the ORCA fixture's own frame and matrix,
    with its reaction coordinate stored as the depositor printed it."""

    text = (FIXTURES / "orca" / "Orca_TS_test.hess").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=True)
    assert parsed is not None and parsed.reference_coords_angstrom is not None
    elements = [row[0] for row in parsed.reference_coords_angstrom]
    coords = np.array([row[1:] for row in parsed.reference_coords_angstrom])
    matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)
    lines = text.splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("$vibrational_frequencies"))
    count = int(lines[head + 1].split()[0])
    printed = [float(lines[head + 2 + k].split()[1]) for k in range(count)]
    printed = [f for f in printed if f != 0.0]
    assert len(printed) == 12 and printed[0] < 0

    calc = _deposit(db_session, elements, coords, matrix, printed)
    result = reanalyse_calculation(db_session, calc)

    assert result.status is ReanalysisStatus.analysed
    assert result.stored_imaginary_count == 1 and result.recovered_imaginary_count == 1
    assert result.projection_status == "determined"
    (imaginary,) = result.imaginary_modes
    assert imaginary.stored_frequency_cm1 == pytest.approx(-503.235928)
    assert imaginary.determination == "internal_vibration"
    assert imaginary.declared_disposition is None
    assert imaginary.agreement == "not_declared"
    # ORCA's list uses average atomic weights; the C-Cl stretch lands ~1 cm^-1
    # off the isotopic-mass recovery and the report says so instead of hiding it.
    assert result.matched_count == 12
    assert result.within_tolerance is False
    assert 0.5 < result.max_abs_deviation_cm1 < 2.0


def test_the_generator_counts_every_record_and_is_byte_identical(db_session):
    elements, coords, matrix, expected = _diatomic()
    good = _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected])
    bare = _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected], with_hessian=False)

    first = hessian_reanalysis(db_session)
    second = hessian_reanalysis(db_session)

    import json

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["generator"] == "hessian_reanalysis"
    assert first["scope"]["calculation_count"] == 2
    assert first["scope"]["analysed_count"] == 1
    assert first["scope"]["by_status"]["hessian_not_stored"] == 1
    assert first["scope"]["within_tolerance_count"] == 1
    refs = [record["calculation_ref"] for record in first["records"]]
    assert refs == sorted(refs)
    assert {good.public_ref, bare.public_ref} == set(refs)
    assert first["tolerance"]["max_deviation_cm1"] == DEFAULT_MAX_DEVIATION_CM1
    assert first["tolerance"]["max_omega2_deviation_cm2"] is None
    # Every key is on an allowlist, so a new one -- a run stamp, a host
    # name, anything that would make two runs differ -- fails here until it
    # is added deliberately.
    assert set(first) == {"generator", "tolerance", "method", "scope", "records"}
    assert set(first["tolerance"]) == {"max_deviation_cm1", "max_omega2_deviation_cm2", "omega2_allowance", "rule", "derivation"}
    assert set(first["tolerance"]["derivation"]) == {
        "frequency_print_half_ulp_cm1",
        "print_formats",
        "fallback_print_format",
        "omega2_per_unit_eigenvalue_cm2",
    }
    assert set(first["method"]) == {"masses", "rigid_body_projection", "pairing", "units"}
    assert set(first["scope"]) == {
        "calculation_count",
        "by_status",
        "analysed_count",
        "within_tolerance_count",
        "exceeding_count",
        "modes_compared",
        "max_abs_deviation_cm1",
        "max_omega2_deviation_cm2",
        "records_with_order_violations",
        "imaginary_declaration_conflicts",
    }
    record_keys = {
        "calculation_ref",
        "calculation_type",
        "species_entry_ref",
        "transition_state_entry_ref",
        "status",
        "natoms",
        "hessian_print_format",
        "print_format_basis",
        "rigid_body_dimension",
        "is_linear",
        "max_rigid_body_curvature_cm1",
        "stored_count",
        "recovered_count",
        "matched_count",
        "within_tolerance_count",
        "within_tolerance",
        "max_abs_deviation_cm1",
        "rms_abs_deviation_cm1",
        "max_omega2_deviation_cm2",
        "stored_order_violations",
        "stored_imaginary_count",
        "recovered_imaginary_count",
        "projection_status",
        "imaginary_modes",
        "modes",
    }
    mode_keys = {
        "mode_index",
        "stored_frequency_cm1",
        "recovered_frequency_cm1",
        "deviation_cm1",
        "omega2_deviation_cm2",
        "omega2_allowance_cm2",
        "tolerance_cm1",
        "within_tolerance",
        "nearest_match",
    }
    for record in first["records"]:
        assert set(record) == record_keys
        for mode in record["modes"]:
            assert set(mode) == mode_keys


def test_a_single_calculation_is_named_by_public_ref_never_by_id(db_session):
    elements, coords, matrix, expected = _diatomic()
    calc = _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected])
    _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected])

    by_ref = hessian_reanalysis(db_session, calculation_ref=calc.public_ref)

    assert by_ref["scope"]["calculation_count"] == 1
    assert by_ref["records"][0]["calculation_ref"] == calc.public_ref
    with pytest.raises(ValueError, match="public ref"):
        hessian_reanalysis(db_session, calculation_ref=str(calc.id))


# ---------------------------------------------------------------------------
# The script: exit status and read-only-ness
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report_script():
    spec = importlib.util.spec_from_file_location("hessian_reanalysis_report", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    """The test's own session, wearing the shape ``main`` expects."""

    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return contextlib.nullcontext()


def _run_main(report_script, monkeypatch, db_session, argv):
    import app.api.deps as deps

    monkeypatch.setattr("sys.argv", ["hessian_reanalysis_report.py", *argv], raising=False)
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    return report_script.main()


def test_an_empty_scope_exits_two_and_has_no_silent_pass(db_session, report_script, monkeypatch, capsys):
    code = _run_main(report_script, monkeypatch, db_session, ["--all", "--quiet"])
    out = capsys.readouterr().out

    assert code == report_script.EXIT_NOTHING_ANALYSABLE
    assert "NOTHING ANALYSED" in out
    with pytest.raises(SystemExit):
        _run_main(report_script, monkeypatch, db_session, ["--all", "--allow-empty"])


def test_the_script_refuses_a_database_id(db_session, report_script, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_main(report_script, monkeypatch, db_session, ["--calculation-ref", "42", "--quiet"])
    assert exc.value.code == 2
    assert "public ref" in capsys.readouterr().err


def test_a_scope_where_everything_is_refused_also_exits_two(db_session, report_script, monkeypatch, capsys):
    elements, coords, matrix, expected = _diatomic()
    _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected], with_hessian=False)

    code = _run_main(report_script, monkeypatch, db_session, ["--all", "--quiet"])
    out = capsys.readouterr().out

    assert code == report_script.EXIT_NOTHING_ANALYSABLE
    assert "every one refused" in out


def test_a_clean_corpus_exits_zero_and_lists_the_record_without_a_hessian(
    db_session, report_script, monkeypatch, capsys, tmp_path
):
    elements, coords, matrix, expected = _diatomic()
    good = _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected])
    bare = _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected], with_hessian=False)
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"

    code = _run_main(
        report_script,
        monkeypatch,
        db_session,
        ["--all", "--json-out", str(json_path), "--markdown-out", str(md_path)],
    )
    out = capsys.readouterr().out

    assert code == report_script.EXIT_OK
    assert "within the bound" in out
    table = md_path.read_text()
    assert good.public_ref in table and bare.public_ref in table
    assert "hessian_not_stored" in table
    assert "2 calculation(s) in scope; 1 analysed" in table
    first = json_path.read_text()
    _run_main(report_script, monkeypatch, db_session, ["--all", "--json-out", str(json_path), "--quiet"])
    capsys.readouterr()
    assert json_path.read_text() == first


def test_a_record_outside_the_bound_exits_one_and_names_it(db_session, report_script, monkeypatch, capsys):
    elements, coords, matrix, expected = _diatomic()
    calc = _deposit(db_session, elements, coords, matrix * 1.01, [round(f, 4) for f in expected])

    code = _run_main(report_script, monkeypatch, db_session, ["--all", "--quiet"])
    out = capsys.readouterr().out

    assert code == report_script.EXIT_EXCEEDED
    assert calc.public_ref in out
    assert "exceed the bound" in out
    assert "worst mode 1" in out


def test_the_bound_can_be_widened_on_the_command_line(db_session, report_script, monkeypatch, capsys):
    elements, coords, matrix, expected = _diatomic()
    _deposit(db_session, elements, coords, matrix * 1.01, [round(f, 4) for f in expected])

    code = _run_main(report_script, monkeypatch, db_session, ["--all", "--quiet", "--max-deviation-cm1", "100"])
    capsys.readouterr()

    assert code == report_script.EXIT_OK


def test_the_report_writes_nothing(db_session, report_script, monkeypatch, capsys):
    elements, coords, matrix, expected = _diatomic()
    _deposit(db_session, elements, coords, matrix, [round(f, 4) for f in expected])
    db_session.flush()
    before = set(db_session.identity_map.values())

    _run_main(report_script, monkeypatch, db_session, ["--all", "--quiet"])
    capsys.readouterr()

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
    assert set(db_session.identity_map.values()) >= before
