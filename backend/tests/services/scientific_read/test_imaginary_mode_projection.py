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
from app.db.models.calculation import CalculationParameter, CalculationParameterVocab
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


# ---------------------------------------------------------------------------
# What a refusal says instead of nothing (ADR 0012's stored tau)
# ---------------------------------------------------------------------------
#
# The statuses above are honest and they are also a shrug: a reader who
# asks about the corpus records with an imaginary mode and no Hessian
# learns only that nothing was checked. TCKDB is not silent about those
# modes -- ADR 0012 judged every one of them by magnitude at upload,
# against a tau resolved from the protocol that produced them, and
# persisted the tau, its basis and the resulting structural flag. These
# tests are about returning that judgement beside the refusal, and about
# the line it must not cross: reporting a magnitude is not assigning a
# mode, and nothing here may call a mode spurious.


def _seed_parameter(session, *, calculation, canonical_key, canonical_value):
    """Insert one ``calculation_parameter`` row, seeding its vocab first.

    ``calculation_parameter.canonical_key`` carries an FK to
    ``calculation_parameter_vocab``, so a test that sets one must seed
    the vocabulary row it points at.
    """
    if session.get(CalculationParameterVocab, canonical_key) is None:
        session.add(CalculationParameterVocab(canonical_key=canonical_key))
        session.flush()
    session.add(
        CalculationParameter(
            calculation_id=calculation.id,
            raw_key=canonical_key,
            raw_value=canonical_value,
            canonical_key=canonical_key,
            canonical_value=canonical_value,
        )
    )
    session.flush()


def _judged_record_without_a_hessian(session, **freq_kwargs):
    """ADR 0012's motivating record, judged and deposited without a matrix.

    -1300, -42 and -13 cm-1 is the case ADR 0012 opens with. No live
    record reaches this path: ``n_imag`` is only ever 0 or 1 across the
    whole corpus, so ``imaginary_disposition`` is null everywhere and a
    record with an extra imaginary mode has to be constructed.
    """
    calc = _calculation(session)
    attach_freq_result(
        session,
        calculation=calc,
        frequencies_cm1=[-1300.0, -42.0, -13.0, 620.0],
        **freq_kwargs,
    )
    return calc


def test_a_record_with_no_hessian_reports_the_tau_it_was_judged_against(db_session):
    """"Not determinable" stops being a shrug.

    The projection still refuses -- there is no matrix, and no amount of
    stored provenance conjures one. What it now carries is the judgement
    that *was* made: the tau applied, which row of ADR 0012's protocol
    table chose it, and the structural flag that followed.
    """
    calc = _judged_record_without_a_hessian(
        db_session,
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=15.0,
        imaginary_mode_tau_basis="analytic_tight",
        imaginary_mode_structural_flag=True,
        imaginary_dispositions=[None, ImaginaryModeDisposition.torsion, None, None],
    )
    result = build_imaginary_mode_projection(db_session, calc.id)

    assert result.status is ProjectionStatus.hessian_not_stored
    assert result.modes == ()

    tau = result.tau_context
    assert tau is not None
    assert tau.tau_cm1 == 15.0
    assert tau.tau_basis == "analytic_tight"
    assert tau.structural_flag is True
    assert tau.reaction_coordinate_mode_index == 1


def test_the_tau_block_ranks_by_magnitude_and_names_only_the_designation(db_session):
    """Ranked magnitudes, and one mode named -- by the depositor.

    ADR 0012's tiers turn on the *ordering* of magnitudes against the
    designated reaction coordinate, so the ordering is what the block
    reports, and it is a re-ordering rather than a pass-through: the
    modes are deliberately stored here in an order the frequency list
    might genuinely produce, with the reaction coordinate second and the
    smallest residue first. The only mode the block calls anything is
    the one the depositor designated; the rest carry a magnitude and a
    comparison and no label.
    """
    calc = _calculation(db_session)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-13.0, -1300.0, -42.0, 620.0],
        reaction_coordinate_mode_index=2,
        imaginary_mode_tau_cm1=15.0,
        imaginary_mode_tau_basis="analytic_tight",
        imaginary_mode_structural_flag=True,
    )
    tau = build_imaginary_mode_projection(db_session, calc.id).tau_context
    assert tau is not None

    assert [mode.magnitude_cm1 for mode in tau.modes] == [1300.0, 42.0, 13.0]
    assert [mode.mode_index for mode in tau.modes] == [2, 3, 1]
    assert [mode.frequency_cm1 for mode in tau.modes] == [-1300.0, -42.0, -13.0]

    designated = [mode.is_designated_reaction_coordinate for mode in tau.modes]
    assert designated == [True, False, False]

    # -42 is 2.8x above the noise floor of a clean protocol and -13 is
    # not; that comparison is the whole of what tau decides.
    assert [mode.at_or_above_tau for mode in tau.modes] == [True, True, False]


def test_the_tau_block_reports_a_magnitude_and_refuses_to_assign_a_mode(db_session):
    """The line ADR 0012 draws, asserted as a property of the payload.

    A torsion at -300 cm-1 and a genuine second reaction coordinate at
    -300 cm-1 are the same number in a frequency list, which is the
    entire gap the projection exists to close. So a block that could not
    take a projection must not fill the hole with a verdict: no field
    here names a mode spurious, an artefact, or noise, and the only
    classification on the wire is the one the depositor declared.
    """
    calc = _judged_record_without_a_hessian(
        db_session,
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="protocol_not_recorded",
        imaginary_mode_structural_flag=False,
        imaginary_dispositions=[None, ImaginaryModeDisposition.unassigned, None, None],
    )
    tau = build_imaginary_mode_projection(db_session, calc.id).tau_context
    assert tau is not None

    # Every classification-shaped value is either the depositor's own
    # declaration or absent. The ranked modes carry no determination
    # field at all -- there was nothing to determine.
    assert [mode.declared_disposition for mode in tau.modes] == [
        None,
        ImaginaryModeDisposition.unassigned,
        None,
    ]
    assert not hasattr(tau.modes[0], "determination")

    forbidden = ("spurious", "artefact", "artifact", "is_real", "is_noise")
    rendered = repr(tau.modes).lower()
    for word in forbidden:
        assert word not in rendered, f"the ranked modes should not say {word!r}"

    # And the block says so out loud, on every payload, because the
    # inference a reader is most likely to draw is the one the data
    # cannot support.
    assert "cannot separate a spurious mode from a real one" in tau.interpretation_limit
    assert tau.interpretation_limit.isascii()


def test_the_recorded_protocol_that_set_tau_is_reported_absences_included(db_session):
    """Which recorded provenance chose the threshold, including what was missing.

    ADR 0012: an unrecorded Hessian method takes the conservative row
    "whatever else is present", so the parameter that is *absent* is
    frequently the one that decided tau. Listing only the recorded keys
    would hide it.
    """
    calc = _judged_record_without_a_hessian(
        db_session,
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="protocol_not_recorded",
        imaginary_mode_structural_flag=False,
    )
    _seed_parameter(db_session, calculation=calc, canonical_key="grid.quality", canonical_value="ultrafine")
    _seed_parameter(db_session, calculation=calc, canonical_key="opt.convergence", canonical_value="tight")

    tau = build_imaginary_mode_projection(db_session, calc.id).tau_context
    assert tau is not None
    assert [(p.canonical_key, p.canonical_value) for p in tau.protocol_parameters] == [
        ("freq.hessian_method", None),
        ("grid.quality", "ultrafine"),
        ("opt.convergence", "tight"),
    ]

    # A tight grid and a tight optimisation did not buy the 15 cm-1 line,
    # because the frequency job's own method was never recorded. The
    # stored basis says so, and the block reports the stored basis rather
    # than re-resolving one from these three rows.
    assert tau.tau_basis == "protocol_not_recorded"
    assert tau.tau_cm1 == 50.0


def test_the_stored_basis_wins_over_the_parameters_and_the_disagreement_shows(db_session):
    """tau is read back, never re-resolved.

    ADR 0012 stores tau so that "a later parser improvement cannot
    silently re-decide every historical record". A record judged at
    50 cm-1 whose parameters now say analytic/tight is exactly that
    case: the block reports the 50 it was judged at, alongside the
    parameters that would now suggest 15, and leaves the reader to
    notice. Re-resolving here would be the defect the storage
    requirement exists to prevent.
    """
    calc = _judged_record_without_a_hessian(
        db_session,
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="protocol_not_recorded",
        imaginary_mode_structural_flag=False,
    )
    _seed_parameter(db_session, calculation=calc, canonical_key="freq.hessian_method", canonical_value="analytic")
    _seed_parameter(db_session, calculation=calc, canonical_key="grid.quality", canonical_value="ultrafine")
    _seed_parameter(db_session, calculation=calc, canonical_key="opt.convergence", canonical_value="tight")

    tau = build_imaginary_mode_projection(db_session, calc.id).tau_context
    assert tau is not None
    assert tau.tau_cm1 == 50.0
    assert tau.tau_basis == "protocol_not_recorded"
    assert [p.canonical_value for p in tau.protocol_parameters] == ["analytic", "ultrafine", "tight"]
    # -42 was below the tau this record was judged at and would be above
    # a re-resolved one. The stored judgement is what is reported.
    assert [mode.at_or_above_tau for mode in tau.modes] == [True, False, False]


def test_a_record_judged_before_adr_0012_reports_no_tau_rather_than_a_default(db_session):
    """A missing tau is a missing tau, not 50.

    ADR 0012's migration "backfills nothing, because there is nothing
    true to backfill". The read surface has to hold the same line: a
    record deposited before the decision was never judged under it, and
    substituting the protocol-not-recorded row here would manufacture a
    judgement that was never made.
    """
    calc = _judged_record_without_a_hessian(db_session)
    tau = build_imaginary_mode_projection(db_session, calc.id).tau_context

    assert tau is not None
    assert tau.tau_cm1 is None
    assert tau.tau_basis is None
    assert tau.structural_flag is None
    assert tau.reaction_coordinate_mode_index is None
    assert [mode.at_or_above_tau for mode in tau.modes] == [None, None, None]
    assert [mode.is_designated_reaction_coordinate for mode in tau.modes] == [False, False, False]


def test_an_assumed_basis_renders_verbatim_and_stays_distinguishable(db_session):
    """ADR 0012's 2026-09-04 amendment, read back.

    An assumed basis is read straight off the stored row exactly like
    every other basis -- this block does no basis-specific branching --
    but it must remain *distinguishable* from a recorded one: the basis
    itself says "assumed_analytic_default" rather than "analytic_default",
    and the recorded ``freq.hessian_method`` parameter beside it is
    genuinely absent (``None``), because nothing about the assumption
    manufactures a parameter row that was never observed.
    """
    calc = _judged_record_without_a_hessian(
        db_session,
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=30.0,
        imaginary_mode_tau_basis="assumed_analytic_default",
        imaginary_mode_structural_flag=False,
    )

    tau = build_imaginary_mode_projection(db_session, calc.id).tau_context
    assert tau is not None
    assert tau.tau_basis == "assumed_analytic_default"
    assert tau.tau_basis != "analytic_default"
    assert tau.tau_cm1 == 30.0
    assert [(p.canonical_key, p.canonical_value) for p in tau.protocol_parameters] == [
        ("freq.hessian_method", None),
        ("grid.quality", None),
        ("opt.convergence", None),
    ]


def test_every_way_of_failing_to_determine_carries_the_tau_judgement(db_session):
    """Not just the no-Hessian case.

    A matrix bound to the wrong frame is as undetermined as no matrix at
    all, and the reader is owed the same answer.
    """
    parsed = _gaussian_hessian()
    calc = _calculation(db_session)
    geometry = make_geometry(db_session, natoms=parsed.natoms)
    attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=[row[0] for row in GAUSSIAN_STANDARD_ORIENTATION],
        coords=[list(row[1:]) for row in GAUSSIAN_STANDARD_ORIENTATION],
    )
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-1300.0, -42.0, 620.0],
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=30.0,
        imaginary_mode_tau_basis="analytic_default",
        imaginary_mode_structural_flag=True,
    )
    attach_hessian(
        db_session,
        calculation=calc,
        geometry=geometry,
        natoms=parsed.natoms,
        lower_triangle=parsed.lower_triangle_hartree_bohr2,
    )

    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.status is ProjectionStatus.rigid_body_curvature_too_large
    assert result.tau_context is not None
    assert result.tau_context.tau_cm1 == 30.0
    assert [mode.at_or_above_tau for mode in result.tau_context.modes] == [True, True]


def test_a_determined_projection_carries_no_tau_block(db_session):
    """A determination beats a threshold, so the threshold stands down.

    ADR 0012 is explicit that the projections "should be implemented
    before tau is tuned, because a determination beats a threshold
    wherever one is available". Returning both would invite a reader to
    average a measurement with a tolerance. The response for a record
    that carries a Hessian is unchanged by this feature.
    """
    calc = _orca_record(
        db_session,
        dispositions=[ImaginaryModeDisposition.torsion, None, None, None],
    )
    result = build_imaginary_mode_projection(db_session, calc.id)

    assert result.status is ProjectionStatus.determined
    assert result.tau_context is None


@pytest.mark.parametrize(
    "frequencies",
    [
        pytest.param([331.2, 1074.8], id="no_imaginary_modes"),
        pytest.param([], id="no_frequency_modes"),
    ],
)
def test_a_record_with_nothing_to_judge_carries_no_tau_block(db_session, frequencies):
    """tau judges imaginary modes. With none, there is nothing to say."""
    calc = _calculation(db_session)
    if frequencies:
        attach_freq_result(
            db_session,
            calculation=calc,
            frequencies_cm1=frequencies,
            imaginary_mode_tau_cm1=15.0,
            imaginary_mode_tau_basis="analytic_tight",
        )
    result = build_imaginary_mode_projection(db_session, calc.id)
    assert result.status is not ProjectionStatus.determined
    assert result.tau_context is None
