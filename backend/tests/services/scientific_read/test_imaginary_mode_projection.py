"""The read-time imaginary-mode projections, end to end through the DB.

The physics is covered in ``tests/services/test_normal_modes.py``. What
these tests are about is the layer above it: what the block says when a
projection succeeds, what it says when it cannot be taken, and what
happens when the determination and the depositor's declaration disagree.

Every Hessian and every geometry below is real -- parsed out of the ESS
output already in ``backend/tests/fixtures`` -- because a zero matrix has
no normal modes and a made-up one proves nothing about whether the
projection recognises a torsion.
"""

from __future__ import annotations

import pathlib

import pytest

from app.chemistry.normal_modes import ModeDetermination
from app.db.models.common import CalculationType, ImaginaryModeDisposition
from app.services.hessian_parsing import parse_hessian_from_artifact
from app.services.scientific_read.imaginary_mode_projection import (
    DeclarationAgreement,
    ProjectionStatus,
    build_imaginary_mode_projection,
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

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "fixtures"

# ``freq_g09.log``'s input orientation -- the frame its force-constant
# block is printed in. Kept in step with ``tests/services/test_normal_modes.py``.
GAUSSIAN_INPUT_ORIENTATION = [
    ("N", 1.503509, 1.181696, 1.176668),
    ("N", 1.558026, -0.051119, 0.812490),
    ("C", 0.848246, -0.713718, -0.080418),
    ("C", -0.221239, -0.012531, -0.837574),
    ("C", -1.452743, 0.330226, 0.009404),
    ("H", 2.274585, -0.585879, 1.305124),
    ("H", 1.080436, -1.761307, -0.183196),
    ("H", 0.192884, 0.917407, -1.247550),
    ("H", -0.513829, -0.633641, -1.685178),
    ("H", -1.174130, 0.950768, 0.862394),
    ("H", -2.184383, 0.881402, -0.583268),
    ("H", -1.929537, -0.575756, 0.386536),
]

# The same log's standard orientation: the same molecule in a frame the
# matrix was not computed in.
GAUSSIAN_STANDARD_ORIENTATION = [
    ("N", 1.507860, -1.030328, 0.252685),
    ("N", 1.334985, 0.167760, -0.183356),
    ("C", 0.276110, 0.953044, -0.136234),
    ("C", -0.976246, 0.462378, 0.495905),
    ("C", -1.703534, -0.608108, -0.326633),
    ("H", 2.164317, 0.556481, -0.634152),
    ("H", 0.385619, 1.919508, -0.600855),
    ("H", -0.730122, 0.042434, 1.479486),
    ("H", -1.637468, 1.312800, 0.667327),
    ("H", -1.052621, -1.464913, -0.506868),
    ("H", -2.588622, -0.963232, 0.203328),
    ("H", -2.018996, -0.208986, -1.291802),
]


def _orca_transition_state():
    """The 6-atom F...CH3...Cl transition state, matrix and frame together."""
    text = (FIXTURES / "orca" / "Orca_TS_test.hess").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=True)
    assert parsed is not None and parsed.reference_coords_angstrom is not None
    return parsed


def _gaussian_hessian():
    text = (FIXTURES / "gaussian" / "freq_g09.log").read_text()
    parsed = parse_hessian_from_artifact(text, from_hess_file=False)
    assert parsed is not None and parsed.natoms == 12
    return parsed


def _calculation(session):
    species = make_species(session, smiles="C", inchi_key=next_inchi_key("PROJ"))
    entry = make_species_entry(session, species)
    return make_calculation(session, type=CalculationType.freq, species_entry_id=entry.id)


def _orca_record(session, *, frequencies=None, dispositions=None, hessian=True):
    """An ORCA-TS calculation with its real matrix, frame and spectrum."""
    parsed = _orca_transition_state()
    calc = _calculation(session)
    geometry = make_geometry(session, natoms=parsed.natoms)
    attach_geometry_atoms(
        session,
        geometry=geometry,
        symbols=[row[0] for row in parsed.reference_coords_angstrom],
        coords=[list(row[1:]) for row in parsed.reference_coords_angstrom],
    )
    attach_input_geometry(session, calculation=calc, geometry=geometry)
    # ORCA's own vibrational frequencies, minus the six exact zeros it
    # prints for the modes it projected out.
    attach_freq_result(
        session,
        calculation=calc,
        frequencies_cm1=frequencies
        or [
            -503.235928,
            331.230278,
            1074.813628,
            3107.075736,
        ],
        imaginary_dispositions=dispositions,
    )
    if hessian:
        attach_hessian(
            session,
            calculation=calc,
            geometry=geometry,
            natoms=parsed.natoms,
            lower_triangle=parsed.lower_triangle_hartree_bohr2,
        )
    return calc


# ---------------------------------------------------------------------------
# A projection that succeeds
# ---------------------------------------------------------------------------


def test_a_transition_state_reaction_coordinate_is_determined_not_thresholded(
    db_session,
):
    """The block answers ADR 0012's question about a real record.

    The reaction coordinate of this transition state is 0.0000 rigid-body
    and 0.0000 torsion, so it is internal motion that is not a rotation
    about any bond -- which is what a reaction coordinate should be, and
    is a determination rather than an inference from its magnitude.
    """
    calc = _orca_record(db_session)
    result = build_imaginary_mode_projection(db_session, calc.id)

    assert result.status is ProjectionStatus.determined
    assert result.natoms == 6
    assert result.rigid_body_dimension == 6
    assert result.is_linear is False
    assert result.rotatable_bonds == ()
    assert len(result.modes) == 1

    mode = result.modes[0]
    assert mode.frequency_cm1 == pytest.approx(-503.235928)
    assert mode.recovered_frequency_cm1 == pytest.approx(-503.5, abs=0.5)
    assert mode.determination is ModeDetermination.internal_vibration
    assert mode.rigid_body_overlap < 0.01
    assert mode.torsion_overlap == 0.0
    assert mode.agreement is DeclarationAgreement.not_declared
    assert result.conflict_count == 0

    # The thresholds ride along so a reader can re-decide from the raw
    # overlaps without re-running anything.
    assert result.rigid_body_overlap_threshold == 0.90
    assert result.torsion_overlap_threshold == 0.70


def test_an_unprojected_low_mode_is_determined_to_be_rigid_body_residue(
    db_session,
):
    """The case ADR 0012 says a threshold cannot decide, decided.

    -7.15 cm-1 is a genuine eigenvalue of ``freq_g09.log``'s stored
    Hessian -- one of the six rigid-body directions, which Gaussian
    projected out before printing its own frequency list but a code (or a
    pipeline) that does not project would report. Against every row of
    ADR 0012's tau table it is "below tau, sign indeterminate". Projected,
    it is 0.9999 rigid-body motion and the question does not arise.
    """
    parsed = _gaussian_hessian()
    calc = _calculation(db_session)
    geometry = make_geometry(db_session, natoms=12)
    attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=[row[0] for row in GAUSSIAN_INPUT_ORIENTATION],
        coords=[list(row[1:]) for row in GAUSSIAN_INPUT_ORIENTATION],
    )
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-7.15, 110.29, 201.28],
    )
    attach_hessian(
        db_session,
        calculation=calc,
        geometry=geometry,
        natoms=12,
        lower_triangle=parsed.lower_triangle_hartree_bohr2,
    )

    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.status is ProjectionStatus.determined
    assert result.rotatable_bonds == ((2, 3), (3, 4), (4, 5))
    assert len(result.modes) == 1

    mode = result.modes[0]
    assert mode.determination is ModeDetermination.rigid_body_residue
    assert mode.rigid_body_overlap > 0.99
    assert mode.torsion_overlap < 0.01


# ---------------------------------------------------------------------------
# When a determination and a declaration disagree
# ---------------------------------------------------------------------------


def test_a_declared_torsion_the_projection_excludes_is_reported_as_a_conflict(
    db_session,
):
    """The case this whole feature exists for.

    A depositor declaring ``torsion`` on a genuine reaction coordinate is
    exactly what ADR 0013 says TCKDB cannot check -- "a depositor who
    writes ``torsion`` on a genuine second reaction coordinate has
    falsified a record". It can now be checked. This transition state has
    no rotatable bond at all, so the declaration is excluded rather than
    merely unsupported.

    Both readings survive in the payload and neither is preferred: the
    declaration stays exactly as deposited, the determination sits beside
    it, and ``agreement`` names the disagreement. Under ADR 0008 that is
    the most a projection may do -- it is an expectation about the record,
    not a definition, so it informs and does not block.
    """
    calc = _orca_record(
        db_session,
        dispositions=[ImaginaryModeDisposition.torsion, None, None, None],
    )
    result = build_imaginary_mode_projection(db_session, calc.id)

    assert result.status is ProjectionStatus.determined
    mode = result.modes[0]
    assert mode.declared_disposition is ImaginaryModeDisposition.torsion
    assert mode.determination is ModeDetermination.internal_vibration
    assert mode.agreement is DeclarationAgreement.conflicts
    assert result.conflict_count == 1


def test_a_declaration_the_projection_confirms_agrees(db_session):
    """The other half of the same measurement, on the same molecule."""
    parsed = _gaussian_hessian()
    calc = _calculation(db_session)
    geometry = make_geometry(db_session, natoms=12)
    attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=[row[0] for row in GAUSSIAN_INPUT_ORIENTATION],
        coords=[list(row[1:]) for row in GAUSSIAN_INPUT_ORIENTATION],
    )
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-7.15, 110.29],
        imaginary_dispositions=[ImaginaryModeDisposition.rigid_body_residue, None],
    )
    attach_hessian(
        db_session,
        calculation=calc,
        geometry=geometry,
        natoms=12,
        lower_triangle=parsed.lower_triangle_hartree_bohr2,
    )

    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.modes[0].agreement is DeclarationAgreement.agrees
    assert result.conflict_count == 0


@pytest.mark.parametrize(
    "declared",
    [
        ImaginaryModeDisposition.ring_pucker,
        ImaginaryModeDisposition.intermolecular,
        ImaginaryModeDisposition.symmetry_breaking,
        ImaginaryModeDisposition.unassigned,
    ],
)
def test_a_declaration_outside_the_projections_vocabulary_is_inconclusive(db_session, declared):
    """The measurement states its own limits instead of overreaching.

    The projections can positively identify rigid-body residue and
    torsion. They cannot tell a ring pucker from an intermolecular mode
    from a symmetry-breaking one -- all three land in
    ``internal_vibration`` -- so against those declarations they report
    ``inconclusive`` rather than manufacturing agreement they have not
    earned or a conflict they cannot support.
    """
    calc = _orca_record(db_session, dispositions=[declared, None, None, None])
    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.modes[0].agreement is DeclarationAgreement.inconclusive
    assert result.conflict_count == 0


# ---------------------------------------------------------------------------
# The ways it refuses, each distinguishable from every other
# ---------------------------------------------------------------------------


def test_no_hessian_reads_as_not_determinable_and_not_as_clean(db_session):
    """A record with no matrix is unchecked, not cleared.

    36% of the frequency calculations in the corpus carry no Hessian.
    If that came back as an empty mode list a reader would take it for
    "no residue found", which is the exact defect
    ``verify_artifact_integrity.py`` was rebuilt to close. The status is
    the finding.
    """
    calc = _orca_record(db_session, hessian=False)
    result = build_imaginary_mode_projection(db_session, calc.id)

    assert result.status is ProjectionStatus.hessian_not_stored
    assert result.modes == ()
    assert result.natoms is None
    assert result.status is not ProjectionStatus.no_imaginary_modes


def test_no_imaginary_modes_is_a_different_answer_from_no_hessian(db_session):
    """Nothing to project, and nothing wrong -- said in its own words."""
    calc = _orca_record(db_session, frequencies=[331.2, 1074.8, 3107.1])
    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.status is ProjectionStatus.no_imaginary_modes
    assert result.modes == ()


def test_no_frequency_modes_at_all_is_its_own_answer(db_session):
    calc = _calculation(db_session)
    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.status is ProjectionStatus.no_frequency_modes


def test_a_geometry_in_the_wrong_frame_is_refused_rather_than_projected(
    db_session,
):
    """The silent failure, made loud.

    Bind ``freq_g09.log``'s matrix to the standard orientation instead of
    the input orientation -- the same molecule, the same atom order, a
    different frame -- and the projections would still return numbers.
    They would be wrong. The rigid-body curvature is 792.9 cm-1 there
    against 12.8 in the right frame, and the block refuses.
    """
    parsed = _gaussian_hessian()
    calc = _calculation(db_session)
    geometry = make_geometry(db_session, natoms=12)
    attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=[row[0] for row in GAUSSIAN_STANDARD_ORIENTATION],
        coords=[list(row[1:]) for row in GAUSSIAN_STANDARD_ORIENTATION],
    )
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    attach_freq_result(db_session, calculation=calc, frequencies_cm1=[-7.15, 110.29])
    attach_hessian(
        db_session,
        calculation=calc,
        geometry=geometry,
        natoms=12,
        lower_triangle=parsed.lower_triangle_hartree_bohr2,
    )

    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.status is ProjectionStatus.rigid_body_curvature_too_large
    assert result.modes == ()
    assert abs(result.max_rigid_body_curvature_cm1) > 100.0


def test_a_geometry_whose_atom_count_disagrees_is_refused(db_session):
    """A Hessian bound to the wrong geometry is not projected part-way."""
    parsed = _gaussian_hessian()
    calc = _calculation(db_session)
    geometry = make_geometry(db_session, natoms=12)
    attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=[row[0] for row in GAUSSIAN_INPUT_ORIENTATION[:3]],
        coords=[list(row[1:]) for row in GAUSSIAN_INPUT_ORIENTATION[:3]],
    )
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    attach_freq_result(db_session, calculation=calc, frequencies_cm1=[-7.15])
    attach_hessian(
        db_session,
        calculation=calc,
        geometry=geometry,
        natoms=12,
        lower_triangle=parsed.lower_triangle_hartree_bohr2,
    )

    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.status is ProjectionStatus.geometry_incomplete
    assert result.modes == ()


def test_a_stored_frequency_the_matrix_does_not_reproduce_is_not_projected(
    db_session,
):
    """The pairing of a matrix with a frequency list is checked, not assumed.

    The block still reports ``determined`` -- something *was* measured --
    but the mode itself carries no determination and says why. A silent
    projection here would answer a question about a different job.
    """
    calc = _orca_record(db_session, frequencies=[-812.0, 331.230278])
    result = build_imaginary_mode_projection(db_session, calc.id)

    assert result.status is ProjectionStatus.determined
    mode = result.modes[0]
    assert mode.determination is None
    assert mode.not_determined_reason == "not_matched_in_recovered_spectrum"
    assert mode.rigid_body_overlap is None
    assert mode.recovered_frequency_cm1 is not None
