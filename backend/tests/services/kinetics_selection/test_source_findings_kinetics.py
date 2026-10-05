"""Supplied kinetics and a confirmed finding about the record's own source calculations.

The same rule as H298 (``structure_selection.source_findings``): a live, supported finding about a source calculation, or
the geometry its role reads, in a role the record uses it for, excludes the record from the selection; a finding about
another target changes nothing; a curvature finding bans the frequency role and not a separately valid energy; an
authorized same-subject adjudication restores the record for fresh decisions and leaves the history stored.
"""

from __future__ import annotations

from app.db.models.calculation import CalculationGeometryValidation
from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    StructureFindingKind,
    StructureFindingVerdict,
    ValidationStatus,
)
from app.db.models.kinetics import KineticsSourceCalculation
from app.services.selection_kernel import Applicability as A
from tests.services.kinetics_selection.test_service import by_ref, declared, determination, run
from tests.services.scientific_read._factories import (
    attach_input_geometry,
    attach_opt_result,
    attach_output_geometry,
    attach_sp_result,
    make_calculation,
    make_geometry,
)
from tests.services.structure_selection._support import make_world

K = StructureFindingKind
V = StructureFindingVerdict
R = KineticsCalculationRole


class Sources:
    def __init__(self, session, world):
        self.session = session
        self.world = world
        self.findings = make_world(session)  # the finding builder only

    def energy(self, role=R.ts_energy):
        geometry = make_geometry(self.session)
        calc = make_calculation(self.session, type=CalculationType.sp, species_entry_id=self.world.h.id)
        attach_sp_result(self.session, calculation=calc, electronic_energy_hartree=-76.4)
        attach_input_geometry(self.session, calculation=calc, geometry=geometry)
        return calc, geometry, role

    def optimised(self, role=R.ts_energy):
        geometry = make_geometry(self.session)
        calc = make_calculation(self.session, type=CalculationType.opt, species_entry_id=self.world.h.id)
        attach_opt_result(self.session, calculation=calc, final_energy_hartree=-76.4, converged=True)
        attach_output_geometry(self.session, calculation=calc, geometry=geometry)
        return calc, geometry, role

    def link(self, k, calc, role):
        self.session.add(KineticsSourceCalculation(kinetics_id=k.id, calculation_id=calc.id, role=role))
        self.session.flush()


def record(session, world, key, sources, *, links):
    """An approved, fully declared kinetics row whose source links are written before it is approved (it is then frozen)."""
    det = determination(session, world, key)

    def attach(k):
        for calc, role in links:
            sources.link(k, calc, role)

    return declared(session, world, det, children=attach)


def only(result):
    (assessment,) = result.assessments
    return assessment


def test_a_confirmed_identity_failure_on_the_transition_state_energy_source_excludes_the_record(db_session, world):
    sources = Sources(db_session, world)
    calc, _, role = sources.energy(R.ts_energy)
    k = record(db_session, world, "d", sources, links=[(calc, role)])
    sources.findings.finding(calculation=calc, kind=K.identity_incompatibility)
    assessment = only(run(db_session, world.entry))
    assert not assessment.physically_eligible
    assert assessment.blocking == ("source_finding:ts_energy:finding_invalidates:identity_incompatibility:authorized_adjudication",)
    assert assessment.kinetics_ref == k.public_ref


def test_a_finding_about_an_unrelated_calculation_changes_nothing(db_session, world):
    sources = Sources(db_session, world)
    calc, _, role = sources.energy()
    record(db_session, world, "d", sources, links=[(calc, role)])
    stranger = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.h.id)
    sources.findings.finding(calculation=stranger, kind=K.identity_incompatibility)
    assessment = only(run(db_session, world.entry))
    assert assessment.physically_eligible and assessment.blocking == ()


def test_a_curvature_finding_bans_the_frequency_source_but_not_an_energy_taken_from_the_same_calculation(db_session, world):
    sources = Sources(db_session, world)
    calc, _, _ = sources.optimised()
    energy_user = record(db_session, world, "e", sources, links=[(calc, R.reactant_energy)])
    curvature_user = record(db_session, world, "f", sources, links=[(calc, R.freq)])
    sources.findings.finding(calculation=calc, kind=K.contradictory_characterization)
    result = run(db_session, world.entry)
    assessments = by_ref(result)
    assert assessments[energy_user.public_ref].physically_eligible
    assert not assessments[curvature_user.public_ref].physically_eligible
    assert any("contradictory_characterization" in b for b in assessments[curvature_user.public_ref].blocking)


def test_an_authorized_adjudication_restores_the_record_and_the_disproof_stays_stored(db_session, world):
    sources = Sources(db_session, world)
    calc, _, role = sources.energy()
    record(db_session, world, "d", sources, links=[(calc, role)])
    disproof = sources.findings.finding(calculation=calc, kind=K.identity_incompatibility)
    assert not only(run(db_session, world.entry)).physically_eligible
    sources.findings.finding(calculation=calc, kind=K.adjudication, verdict=V.does_not_invalidate, supersedes=disproof)
    assert only(run(db_session, world.entry)).physically_eligible
    db_session.refresh(disproof)
    assert disproof.verdict is V.invalidates


def test_an_unresolved_finding_leaves_the_record_unresolved(db_session, world):
    sources = Sources(db_session, world)
    calc, _, role = sources.energy()
    record(db_session, world, "d", sources, links=[(calc, role)])
    sources.findings.finding(calculation=calc, kind=K.identity_incompatibility, verdict=V.unresolved)
    assessment = only(run(db_session, world.entry))
    assert assessment.applicability is A.unresolved and assessment.blocking == ()
    assert "source_finding:ts_energy:finding_unresolved:identity_incompatibility" in {r.code for r in assessment.reasons}


def test_the_geometry_heuristic_alone_does_not_exclude_and_a_confirmed_geometry_finding_does(db_session, world):
    sources = Sources(db_session, world)
    calc, geometry, role = sources.energy()
    record(db_session, world, "d", sources, links=[(calc, role)])
    db_session.add(
        CalculationGeometryValidation(
            calculation_id=calc.id, species_smiles="[H]", is_isomorphic=False, rmsd=0.9, n_mappings=1,
            validation_status=ValidationStatus.fail, validation_reason="probe",
        )
    )
    db_session.flush()
    assert only(run(db_session, world.entry)).physically_eligible  # advisory only (trust contract v2)
    sources.findings.finding(geometry=geometry, kind=K.identity_incompatibility)
    assessment = only(run(db_session, world.entry))
    assert not assessment.physically_eligible
    assert assessment.blocking == ("source_finding:ts_energy:finding_invalidates:identity_incompatibility:authorized_adjudication",)


def test_a_computed_record_names_the_evidence_rubric_version_that_produced_its_verdict(db_session, world):
    """Structured as well in the manifest's ``assessment_semantics`` block; this is the per-record advisory (reviewer M6)."""
    sources = Sources(db_session, world)
    calc, _, role = sources.energy()
    record(db_session, world, "d", sources, links=[(calc, role)])
    assessment = only(run(db_session, world.entry))
    assert "evidence_rubric:computed_kinetics@2" in assessment.advisory


def test_links_that_supply_no_structural_role_are_not_consulted(db_session, world):
    sources = Sources(db_session, world)
    calc, _, _ = sources.energy()
    record(db_session, world, "d", sources, links=[(calc, R.fit_source)])
    sources.findings.finding(calculation=calc, kind=K.identity_incompatibility)
    assert only(run(db_session, world.entry)).physically_eligible


def test_k2_two_determinations_one_with_a_geometry_fail_now_compete_and_the_flagged_one_says_so(db_session, world):
    """Reviewer case K2. Main: the flagged determination was excluded, leaving ``sole_eligible_candidate``. Now both are
    eligible and no rule ranks them: ``incomparable_alternatives``, nothing selected, with the geometry warning disclosed on
    the flagged record's assessment."""
    from app.services.kinetics_selection.rules import default_rules
    from tests.services.kinetics_selection.test_selection import select

    sources = Sources(db_session, world)
    clean_calc, _, clean_role = sources.energy()
    flagged_calc, _, flagged_role = sources.energy()
    clean = record(db_session, world, "clean", sources, links=[(clean_calc, clean_role)])
    flagged = record(db_session, world, "flagged", sources, links=[(flagged_calc, flagged_role)])
    db_session.add(
        CalculationGeometryValidation(
            calculation_id=flagged_calc.id, species_smiles="[H]", is_isomorphic=False, rmsd=0.9, n_mappings=1,
            validation_status=ValidationStatus.fail, validation_reason="probe",
        )
    )
    db_session.flush()
    result = select(db_session, world.entry, rules=default_rules())
    assert result.outcome.value == "incomparable_alternatives" and result.selected_determination_ref is None
    advisory = {a.kinetics_ref: a.advisory for a in result.assessment.assessments}
    assert any("geometry_validation_not_failed_for_source_calculations" in i for i in advisory[flagged.public_ref])
    assert not any("geometry_validation_not_failed_for_source_calculations" in i for i in advisory[clean.public_ref])
