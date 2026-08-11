"""Normal-mode recovery and ADR 0012 projections, against real ESS output.

Every Hessian here came out of a real program and is already in
``backend/tests/fixtures``. That is deliberate: a synthetic matrix that
happens to diagonalise proves the linear algebra runs, and proves nothing
about whether the projections identify a torsion. Three molecules, chosen
for the shapes the corpus actually contains:

``Orca_TS_test.hess``
    Six atoms, F...CH3...Cl -- a rigid abstraction transition state with
    **no rotatable bond**, and the only fixture that also prints its own
    ``$normal_modes``, so the recovered eigenvectors can be checked
    against the program's rather than against themselves.

``molpro_TS_freq.out``
    Five atoms, a 1,2-H shift transition state. A stiff, well-converged
    reaction coordinate at -1998 cm-1 with essentially perfect rigid-body
    separation -- the clean end of the scale.

``freq_g09.log``
    Twelve atoms, an N-N-C-C-C chain with three rotatable bonds and an
    all-real spectrum whose lowest mode is a torsion. The torsion case,
    and the only one that can support or refute ADR 0012's 70%.
"""

from __future__ import annotations

import math
import pathlib

import numpy as np
import pytest

from app.chemistry.normal_modes import (
    FRAME_CONSISTENCY_TOLERANCE_CM1,
    RIGID_BODY_OVERLAP_THRESHOLD,
    TORSION_OVERLAP_THRESHOLD,
    ModeDetermination,
    atomic_mass,
    match_stored_frequency,
    perceive_bonds,
    project_mode,
    rigid_body_curvature_cm1,
    rigid_body_subspace,
    rotatable_bonds,
    solve_normal_modes,
    torsion_axes,
    unpack_lower_triangle,
)
from app.services.hessian_parsing import parse_hessian_from_artifact

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"

# Transcribed verbatim from the fixtures' own geometry blocks, in Angstrom.
# They are constants rather than parsed at test time because *which*
# orientation block a Hessian belongs to is exactly what these tests are
# about, and a parser that picks one silently would hide the question.

#: ``freq_g09.log``, "Input orientation" -- the frame the force-constant
#: block is printed in under ``IOp(2/9=2000)``.
GAUSSIAN_INPUT_ORIENTATION = (
    "N      1.503509     1.181696     1.176668",
    "N      1.558026    -0.051119     0.812490",
    "C      0.848246    -0.713718    -0.080418",
    "C     -0.221239    -0.012531    -0.837574",
    "C     -1.452743     0.330226     0.009404",
    "H      2.274585    -0.585879     1.305124",
    "H      1.080436    -1.761307    -0.183196",
    "H      0.192884     0.917407    -1.247550",
    "H     -0.513829    -0.633641    -1.685178",
    "H     -1.174130     0.950768     0.862394",
    "H     -2.184383     0.881402    -0.583268",
    "H     -1.929537    -0.575756     0.386536",
)

#: The same molecule from the same log, "Standard orientation" -- the
#: wrong frame for this matrix, and the silent failure the frame check
#: exists to catch.
GAUSSIAN_STANDARD_ORIENTATION = (
    "N      1.507860    -1.030328     0.252685",
    "N      1.334985     0.167760    -0.183356",
    "C      0.276110     0.953044    -0.136234",
    "C     -0.976246     0.462378     0.495905",
    "C     -1.703534    -0.608108    -0.326633",
    "H      2.164317     0.556481    -0.634152",
    "H      0.385619     1.919508    -0.600855",
    "H     -0.730122     0.042434     1.479486",
    "H     -1.637468     1.312800     0.667327",
    "H     -1.052621    -1.464913    -0.506868",
    "H     -2.588622    -0.963232     0.203328",
    "H     -2.018996    -0.208986    -1.291802",
)

#: ``molpro/hcco_radical/input.in`` -- a real collinear polyatomic. Every
#: atom sits on the z axis, so rotation about that axis is the exact zero
#: vector and there are five rigid-body degrees of freedom, not six.
HCCO_LINEAR_GEOMETRY = (
    "O      0.00000000    0.00000000    1.19728100",
    "C      0.00000000    0.00000000   -1.23490000",
    "C      0.00000000    0.00000000    0.02138900",
    "H      0.00000000    0.00000000   -2.29718500",
)

#: ``molpro_TS_freq.out``, "ATOMIC COORDINATES", converted from bohr.
MOLPRO_TS_GEOMETRY = (
    "O     -0.748847     0.000020    -0.094445",
    "C      0.635054    -0.000064     0.013863",
    "H      1.155184    -0.937951    -0.169661",
    "H      1.155204     0.938047    -0.168457",
    "H     -0.178948    -0.000680     0.979876",
)


def _split(lines: tuple[str, ...]) -> tuple[list[str], np.ndarray]:
    elements = [line.split()[0] for line in lines]
    coords = np.array([[float(v) for v in line.split()[1:4]] for line in lines])
    return elements, coords


def _analysis(elements, coords, matrix):
    masses = [atomic_mass(element, None) for element in elements]
    assert all(mass is not None for mass in masses)
    rigid = rigid_body_subspace(coords, masses)
    bonds = perceive_bonds(elements, coords)
    rotatable = rotatable_bonds(len(elements), bonds)
    axes = torsion_axes(coords, masses, bonds, rotatable, rigid)
    modes = solve_normal_modes(matrix, masses)
    return masses, rigid, bonds, rotatable, axes, modes


def _orca_ts():
    text = (FIXTURES / "orca" / "Orca_TS_test.hess").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=True)
    assert parsed is not None
    assert parsed.reference_coords_angstrom is not None
    elements = [row[0] for row in parsed.reference_coords_angstrom]
    coords = np.array([row[1:] for row in parsed.reference_coords_angstrom])
    matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)
    return elements, coords, matrix


def _orca_printed_normal_modes() -> tuple[list[float], np.ndarray]:
    """ORCA's own frequencies and Cartesian eigenvectors from the ``.hess``."""
    lines = (FIXTURES / "orca" / "Orca_TS_test.hess").read_text().splitlines()
    starts = {}
    for index, line in enumerate(lines):
        token = line.strip()
        if token.startswith("$"):
            starts.setdefault(token.split()[0], index)

    head = starts["$vibrational_frequencies"]
    count = int(lines[head + 1].split()[0])
    frequencies = [float(lines[head + 2 + k].split()[1]) for k in range(count)]

    head = starts["$normal_modes"]
    dimension = int(lines[head + 1].split()[0])
    vectors = np.zeros((dimension, dimension))
    cursor = head + 2
    while True:
        header = lines[cursor].split()
        if not header or not all(token.isdigit() for token in header):
            break
        columns = [int(token) for token in header]
        cursor += 1
        for _ in range(dimension):
            parts = lines[cursor].split()
            for column, value in zip(columns, parts[1:], strict=True):
                vectors[int(parts[0]), column] = float(value)
            cursor += 1
        if columns[-1] == dimension - 1:
            break
    return frequencies, vectors


# ---------------------------------------------------------------------------
# The recovery itself
# ---------------------------------------------------------------------------


def test_orca_hessian_reproduces_orca_own_reaction_coordinate_eigenvector():
    """The recovered eigenvector *is* the program's, not merely plausible.

    This is the claim ADR 0013 denies. ORCA prints both the matrix and the
    eigenvectors it got from diagonalising it, so recovering the second
    from the first is checkable end to end rather than self-consistent.
    The reaction coordinate agrees to seven figures in direction and to
    0.05% in frequency -- the residue being the mass convention, since the
    ``.hess`` carries average atomic weights (Cl 35.453) and TCKDB's
    ``isotope_mass_number IS NULL`` means the most abundant isotope.
    """
    elements, coords, matrix = _orca_ts()
    _, _, _, _, _, modes = _analysis(elements, coords, matrix)
    frequencies, printed = _orca_printed_normal_modes()

    reaction_coordinate = min(frequencies)
    assert reaction_coordinate == pytest.approx(-503.235928, abs=1e-3)

    match = match_stored_frequency(reaction_coordinate, modes)
    assert match.matched
    assert match.recovered_frequency_cm1 == pytest.approx(-503.5, abs=0.5)

    column = frequencies.index(reaction_coordinate)
    masses = np.array([atomic_mass(element, None) for element in elements])
    weighted = printed[:, column] * np.repeat(np.sqrt(masses), 3)
    weighted /= np.linalg.norm(weighted)
    overlap = abs(float(np.dot(weighted, match.mode.displacement)))
    assert overlap > 0.999


def test_molpro_hessian_reproduces_its_stored_imaginary_frequency():
    """A second program, a different packing, the same recovery."""
    text = (FIXTURES / "molpro" / "molpro_TS_freq.out").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=False)
    assert parsed is not None and parsed.natoms == 5
    elements, coords = _split(MOLPRO_TS_GEOMETRY)
    matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)
    _, _, _, _, _, modes = _analysis(elements, coords, matrix)

    # Molpro prints -1997.98; it uses isotopic masses, so the agreement is
    # an order of magnitude tighter than ORCA's.
    match = match_stored_frequency(-1997.98, modes)
    assert match.matched
    assert match.recovered_frequency_cm1 == pytest.approx(-1997.98, rel=1e-3)


# ---------------------------------------------------------------------------
# Rigid-body residue -- ADR 0012's first projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected_natoms"),
    [("orca", 6), ("molpro", 5), ("gaussian", 12)],
)
def test_rigid_body_directions_separate_from_vibrations_without_a_threshold(name, expected_natoms):
    """ADR 0012's 90% names a point inside an empty interval.

    Diagonalising an *unprojected* Hessian returns the six rigid-body
    directions as ordinary-looking modes: across these three real
    molecules they come out anywhere from -10.8 to +49.6 cm-1, which is
    squarely inside the range where a wavenumber threshold has to guess.
    Every one of them scores at least 0.998 rigid-body overlap and no
    genuine vibration scores above 0.01, so the *determination* separates
    them with three orders of magnitude to spare and no frequency
    threshold involved at all. That is the whole of ADR 0012's argument
    for preferring determinations to thresholds, measured.
    """
    if name == "orca":
        elements, coords, matrix = _orca_ts()
    else:
        source = (
            ("molpro", "molpro_TS_freq.out", MOLPRO_TS_GEOMETRY)
            if name == "molpro"
            else ("gaussian", "freq_g09.log", GAUSSIAN_INPUT_ORIENTATION)
        )
        text = (FIXTURES / source[0] / source[1]).read_text()
        parsed = parse_hessian_from_artifact(text, from_hess_file=False)
        assert parsed is not None
        elements, coords = _split(source[2])
        matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)
    assert len(elements) == expected_natoms

    _, rigid, _, rotatable, axes, modes = _analysis(elements, coords, matrix)
    assert rigid.dimension == 6

    projections = [project_mode(mode, rigid, axes) for mode in modes]
    residue = [
        (mode, projection)
        for mode, projection in zip(modes, projections, strict=True)
        if projection.determination is ModeDetermination.rigid_body_residue
    ]
    assert len(residue) == 6, "one per rigid-body degree of freedom"
    assert min(p.rigid_body_overlap for _, p in residue) > 0.998

    vibrations = [
        projection for projection in projections if projection.determination is not ModeDetermination.rigid_body_residue
    ]
    assert max(p.rigid_body_overlap for p in vibrations) < 0.01
    assert RIGID_BODY_OVERLAP_THRESHOLD == 0.90


def test_rigid_body_residue_spans_frequencies_a_threshold_cannot_separate():
    """The residue modes are not all near zero, which is the point.

    In ``Orca_TS_test.hess`` the six rigid-body directions land between
    -10.8 and +49.6 cm-1. ADR 0012's tau table would call the +49.6 one a
    vibration under every protocol row; the projection calls it what it is
    without consulting its frequency.
    """
    elements, coords, matrix = _orca_ts()
    _, rigid, _, _, axes, modes = _analysis(elements, coords, matrix)
    residue = [
        mode for mode in modes if project_mode(mode, rigid, axes).determination is ModeDetermination.rigid_body_residue
    ]
    frequencies = sorted(mode.frequency_cm1 for mode in residue)
    assert frequencies[0] < -10.0
    assert frequencies[-1] > 45.0


# ---------------------------------------------------------------------------
# Torsion -- ADR 0012's second projection
# ---------------------------------------------------------------------------


def test_gaussian_lowest_real_mode_is_determined_to_be_a_torsion():
    """A real 110 cm-1 mode is 98.6% a rotation about a real C-C bond.

    This is the fixture that can refute ADR 0012's 70%, and does not. The
    molecule has three rotatable bonds; its lowest mode projects onto one
    of them at 0.986 and the next most torsional mode in the whole
    spectrum reaches only 0.47, so 0.70 sits between them with a factor of
    two either way.
    """
    text = (FIXTURES / "gaussian" / "freq_g09.log").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=False)
    assert parsed is not None and parsed.natoms == 12
    elements, coords = _split(GAUSSIAN_INPUT_ORIENTATION)
    matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)
    _, rigid, _, rotatable, axes, modes = _analysis(elements, coords, matrix)

    assert rotatable == ((2, 3), (3, 4), (4, 5))
    assert len(axes) == 3

    lowest = min(
        (mode for mode in modes if mode.frequency_cm1 > 50.0),
        key=lambda mode: mode.frequency_cm1,
    )
    assert lowest.frequency_cm1 == pytest.approx(110.3, abs=0.5)

    projection = project_mode(lowest, rigid, axes)
    assert projection.determination is ModeDetermination.torsion
    assert projection.torsion_overlap > 0.98
    assert projection.best_torsion_bond == (3, 4)
    assert projection.rigid_body_overlap < 0.01

    others = [
        project_mode(mode, rigid, axes).torsion_overlap
        for mode in modes
        if mode is not lowest and mode.frequency_cm1 > 50.0
    ]
    assert max(others) < 0.50
    assert TORSION_OVERLAP_THRESHOLD == 0.70


@pytest.mark.parametrize("name", ["orca", "molpro"])
def test_a_rigid_transition_state_has_no_torsion_to_find(name):
    """The determination does not manufacture a torsion where none exists.

    Neither transition state has an acyclic bond with substituents on both
    sides, so there is no dihedral to rotate about, and the reaction
    coordinate comes back ``internal_vibration`` -- a positive statement
    that it is internal motion which is not a rotation about any bond,
    which is what a reaction coordinate should look like and is exactly
    what ADR 0012 wants a depositor's ``torsion`` declaration checked
    against.
    """
    if name == "orca":
        elements, coords, matrix = _orca_ts()
        expected = -503.5
    else:
        text = (FIXTURES / "molpro" / "molpro_TS_freq.out").read_text()
        parsed = parse_hessian_from_artifact(text, from_hess_file=False)
        assert parsed is not None
        elements, coords = _split(MOLPRO_TS_GEOMETRY)
        matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)
        expected = -1998.0

    _, rigid, _, rotatable, axes, modes = _analysis(elements, coords, matrix)
    assert rotatable == ()
    assert axes == []

    match = match_stored_frequency(expected, modes)
    assert match.matched
    projection = project_mode(match.mode, rigid, axes)
    assert projection.determination is ModeDetermination.internal_vibration
    assert projection.torsion_overlap == 0.0
    assert projection.rigid_body_overlap < 0.01


# ---------------------------------------------------------------------------
# The ways this refuses to answer
# ---------------------------------------------------------------------------


def test_wrong_orientation_is_caught_rather_than_answered():
    """Gaussian prints two orientations; taking the wrong one is silent.

    Against the standard orientation the projections still return numbers,
    and they are wrong: the residue modes' overlaps collapse from 0.998+
    to as low as 0.44 and the 110 cm-1 torsion falls from 0.986 to 0.04.
    The rigid-body curvature is what tells them apart -- 12.8 cm-1 in the
    right frame against 792.9 in the wrong one -- so a caller that gates
    on it converts a plausible wrong answer into an explicit refusal.
    """
    text = (FIXTURES / "gaussian" / "freq_g09.log").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=False)
    assert parsed is not None
    matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)

    correct_elements, correct_coords = _split(GAUSSIAN_INPUT_ORIENTATION)
    masses = [atomic_mass(element, None) for element in correct_elements]
    correct = rigid_body_curvature_cm1(matrix, masses, rigid_body_subspace(correct_coords, masses))
    assert max(abs(value) for value in correct) < FRAME_CONSISTENCY_TOLERANCE_CM1

    _, wrong_coords = _split(GAUSSIAN_STANDARD_ORIENTATION)
    wrong = rigid_body_curvature_cm1(matrix, masses, rigid_body_subspace(wrong_coords, masses))
    assert max(abs(value) for value in wrong) > FRAME_CONSISTENCY_TOLERANCE_CM1

    # ... and the answer it would have given is wrong, not merely noisy.
    wrong_rigid = rigid_body_subspace(wrong_coords, masses)
    wrong_axes = torsion_axes(
        wrong_coords,
        masses,
        perceive_bonds(correct_elements, wrong_coords),
        rotatable_bonds(12, perceive_bonds(correct_elements, wrong_coords)),
        wrong_rigid,
    )
    modes = solve_normal_modes(matrix, masses)
    lowest = min(
        (mode for mode in modes if mode.frequency_cm1 > 50.0),
        key=lambda mode: mode.frequency_cm1,
    )
    assert project_mode(lowest, wrong_rigid, wrong_axes).torsion_overlap < 0.10


def test_a_degenerate_pair_gets_no_projection():
    """Within a degenerate subspace the eigenvector is the diagonaliser's.

    ``Orca_TS_test.hess`` is C3v, so its bending and stretching modes come
    in pairs 0.05-0.09 cm-1 apart. Any rotation of such a pair diagonalises
    the Hessian equally well, so a per-mode overlap would be a property of
    LAPACK rather than of the molecule, and the honest answer is to refuse.
    """
    elements, coords, matrix = _orca_ts()
    _, _, _, _, _, modes = _analysis(elements, coords, matrix)

    degenerate = match_stored_frequency(173.474320, modes)
    assert degenerate.mode is not None
    assert degenerate.degenerate
    assert not degenerate.matched

    isolated = match_stored_frequency(-503.235928, modes)
    assert isolated.matched
    assert not isolated.degenerate


def test_a_frequency_the_hessian_does_not_reproduce_is_not_projected():
    """Matching is a claim that has to be checked, not an assumption.

    The Hessian and the frequency list are separately parsed facts about
    the same job. A stored frequency with no counterpart in the recovered
    spectrum means they are not facts about the same job, and projecting
    anyway would answer a question about a different calculation.
    """
    elements, coords, matrix = _orca_ts()
    _, _, _, _, _, modes = _analysis(elements, coords, matrix)
    assert not match_stored_frequency(-812.0, modes).matched


# ---------------------------------------------------------------------------
# Masses
# ---------------------------------------------------------------------------


def test_isotopic_substitution_moves_the_reaction_coordinate():
    """The per-atom mass column is honoured, and it changes the answer.

    ``geometry_atom.isotope_mass_number`` documents itself as "the
    per-atom mass that a downstream normal-mode analysis needs". This is
    that analysis. Deuterating the migrating hydrogen of the Molpro 1,2-H
    shift lowers its reaction coordinate towards the square-root-of-two
    limit a pure H motion would give, which is the physics and not a
    coincidence of the code.
    """
    text = (FIXTURES / "molpro" / "molpro_TS_freq.out").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=False)
    assert parsed is not None
    elements, coords = _split(MOLPRO_TS_GEOMETRY)
    matrix = unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms)

    light = [atomic_mass(element, None) for element in elements]
    # Atom 5 is the hydrogen bridging O and C: the one that migrates.
    heavy = list(light)
    heavy[4] = atomic_mass("H", 2)
    assert heavy[4] == pytest.approx(2.0141, abs=1e-3)

    light_rc = min(mode.frequency_cm1 for mode in solve_normal_modes(matrix, light))
    heavy_rc = min(mode.frequency_cm1 for mode in solve_normal_modes(matrix, heavy))

    ratio = abs(heavy_rc) / abs(light_rc)
    assert 1.0 / math.sqrt(2.0) < ratio < 1.0


def test_hydrogen_isotope_symbols_carry_their_own_mass():
    """``D`` names a nuclide, and mass-weighting has to know that.

    ``geometry_atom.element`` keeps the depositor's ``D``. An explicit
    ``isotope_mass_number`` still wins, because that column is the
    atom-resolved answer and the symbol is a spelling.
    """
    assert atomic_mass("H", None) == pytest.approx(1.00783, abs=1e-4)
    assert atomic_mass("D", None) == pytest.approx(2.01410, abs=1e-4)
    assert atomic_mass("T", None) == pytest.approx(3.01605, abs=1e-4)
    assert atomic_mass("D", 1) == pytest.approx(1.00783, abs=1e-4)
    assert atomic_mass("C", None) == pytest.approx(12.0, abs=1e-9)
    assert atomic_mass("C", 13) == pytest.approx(13.00335, abs=1e-4)
    assert atomic_mass("Zz", None) is None
    assert atomic_mass("C", 99) is None


def test_a_linear_molecule_has_five_rigid_body_degrees_of_freedom():
    """Rotation about a molecular axis is the exact zero vector.

    HCCO from the Molpro fixtures is genuinely collinear, so the rank of
    the six raw rigid-body generators is five and not six. Getting this
    wrong would not raise -- it would add a spurious basis direction and
    inflate every rigid-body overlap on every linear species in the
    corpus, which contains molecules down to two atoms.

    The singular values of the rotation block are the square roots of the
    principal moments of inertia, so the rank test and the usual linearity
    test are the same test.
    """
    elements, coords = _split(HCCO_LINEAR_GEOMETRY)
    masses = [atomic_mass(element, None) for element in elements]
    linear = rigid_body_subspace(coords, masses)
    assert linear.is_linear
    assert linear.dimension == 5

    # A diatomic is linear by construction; the same code path must hold.
    diatomic_masses = [atomic_mass("H", None), atomic_mass("H", None)]
    diatomic = rigid_body_subspace(np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]]), diatomic_masses)
    assert diatomic.is_linear
    assert diatomic.dimension == 5

    # Bending the same molecule restores the sixth.
    bent = np.array(coords, dtype=float)
    bent[3, 0] = 0.9
    assert rigid_body_subspace(bent, masses).dimension == 6


# The extremes measured by sweeping every live record the projections apply
# to -- 18 calculations with an imaginary mode and a stored Hessian, 6 to 27
# atoms, 0 to 7 rotatable bonds, on 2026-08-11. All 18 carry a genuine
# reaction coordinate, so they are the population most able to produce a
# false torsion, and these are the two that came closest.
CORPUS_HIGHEST_REACTION_COORDINATE_TORSION_OVERLAP = 0.344  # calculation 219
CORPUS_LARGEST_RIGID_BODY_CURVATURE_CM1 = 12.24  # calculation 453


def test_the_thresholds_sit_inside_the_gaps_that_were_measured():
    """Changing a threshold should have to argue with the evidence for it.

    Neither constant is free. ADR 0012 proposed both, and each is kept
    because a measurement put empty space on either side of it -- so a
    future edit that moves one into the occupied region is a change of
    scientific claim, not of taste, and should fail here rather than
    quietly re-decide every record in the corpus.

    The numbers below are the *closest approaches from real data*, not
    round figures: the stiffest non-torsion the live corpus produced, the
    one unambiguous torsion in the fixtures, and the noisiest correctly
    framed record. See ADR 0013 §"The corpus, swept".
    """
    # Torsion: measured gap [0.344, 0.986].
    assert (
        CORPUS_HIGHEST_REACTION_COORDINATE_TORSION_OVERLAP
        < TORSION_OVERLAP_THRESHOLD
        < 0.986
    ), (
        "the torsion threshold must separate the corpus's stiffest "
        "non-torsion from freq_g09.log's genuine one"
    )

    # Rigid body: measured gap [0.0022, 0.9985], three orders of magnitude.
    assert 0.0022 < RIGID_BODY_OVERLAP_THRESHOLD < 0.9985

    # Frame: correct frames reach 49.4 (fixtures) and 12.24 (corpus);
    # deliberately mis-framed ones start at 419.5.
    assert (
        CORPUS_LARGEST_RIGID_BODY_CURVATURE_CM1
        < 49.4
        < FRAME_CONSISTENCY_TOLERANCE_CM1
        < 419.5
    )


def test_unpack_lower_triangle_rejects_a_wrong_length():
    with pytest.raises(ValueError):
        unpack_lower_triangle([1.0, 2.0, 3.0], 2)
