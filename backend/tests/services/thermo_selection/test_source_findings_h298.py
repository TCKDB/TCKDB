"""H298 selection and a confirmed finding about the product's own source (the other half of the trust correction).

The legacy trust badge no longer hard-fails on an automated geometry heuristic. What a *confirmed* invalidation does is
decided here, where the question is asked: the H298 assessor reads live, supported ``StructureEvidenceFinding`` rows for
the record's source calculations in the roles it uses them for. Each case is a stored product with stored sources and
findings, run through ``select_h298``.
"""

from __future__ import annotations

import pytest

from app.db.models.calculation import CalculationGeometryValidation
from app.db.models.common import (
    CalculationType,
    StructureFindingKind,
    StructureFindingVerdict,
    StructureSourceRole,
    ThermoCalculationRole,
    ThermoTargetKind,
    ValidationStatus,
)
from app.services.thermo_selection import H298Request, Outcome, select_h298
from tests.services.scientific_read._factories import (
    attach_input_geometry,
    attach_opt_result,
    attach_output_geometry,
    attach_sp_result,
    attach_thermo_source_calculation,
    make_calculation,
    make_geometry,
)
from tests.services.structure_selection._support import make_world
from tests.services.thermo_selection._support import make_thermo, protocol, species_entry_for

K = StructureFindingKind
V = StructureFindingVerdict
EQUILIBRIUM = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)


@pytest.fixture
def methane(db_session):
    return species_entry_for(db_session, "Methane")


class Bench:
    """A species entry the findings can be written against, plus the means to give a thermo record sources."""

    def __init__(self, session, entry):
        self.session = session
        self.entry = entry
        self.world = make_world(session)  # owns the finding builder only; the products live on ``entry``

    def opt(self, thermo, *, role=ThermoCalculationRole.opt):
        geometry = make_geometry(self.session)
        calc = make_calculation(self.session, type=CalculationType.opt, species_entry_id=self.entry.id)
        attach_opt_result(self.session, calculation=calc, final_energy_hartree=-76.4, converged=True)
        attach_output_geometry(self.session, calculation=calc, geometry=geometry)
        attach_thermo_source_calculation(self.session, thermo=thermo, calculation=calc, role=role)
        return calc, geometry

    def sp(self, thermo):
        geometry = make_geometry(self.session)
        calc = make_calculation(self.session, type=CalculationType.sp, species_entry_id=self.entry.id)
        attach_sp_result(self.session, calculation=calc, electronic_energy_hartree=-76.4)
        attach_input_geometry(self.session, calculation=calc, geometry=geometry)
        attach_thermo_source_calculation(self.session, thermo=thermo, calculation=calc, role=ThermoCalculationRole.sp)
        return calc, geometry

    def finding(self, **kw):
        return self.world.finding(**kw)

    def run(self):
        self.session.flush()
        self.session.expire_all()
        return select_h298(self.session, species_entry_id=self.entry.id, request=EQUILIBRIUM)


@pytest.fixture
def bench(db_session, methane):
    return Bench(db_session, methane)


def blocking(result, thermo):
    return next(a for a in result.assessments if a.thermo_ref == thermo.public_ref).blocking


def test_a_confirmed_identity_failure_on_the_optimisation_source_excludes_the_record(db_session, methane, bench):
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    opt, _ = bench.opt(thermo)
    bench.finding(calculation=opt, kind=K.identity_incompatibility)
    result = bench.run()
    assert result.outcome is Outcome.no_applicable_candidate
    assert blocking(result, thermo) == ("source_finding:opt:finding_invalidates:identity_incompatibility:authorized_adjudication",)


def test_the_excluded_record_cannot_win_and_its_competitor_is_then_sole_eligible(db_session, methane, bench):
    g4 = make_thermo(db_session, methane, proto=protocol("g4"), age_days=400)
    g3 = make_thermo(db_session, methane, proto=protocol("g3"), age_days=1)
    opt, _ = bench.opt(g4)
    bench.finding(calculation=opt, kind=K.identity_incompatibility)
    result = bench.run()
    assert result.outcome is Outcome.sole_eligible_candidate and result.selected_ref == g3.public_ref


def test_a_finding_about_an_unrelated_calculation_leaves_the_record_selectable(db_session, methane, bench):
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    bench.opt(thermo)
    stranger = make_calculation(db_session, type=CalculationType.opt, species_entry_id=methane.id)
    bench.finding(calculation=stranger, kind=K.identity_incompatibility)
    result = bench.run()
    assert result.selected_ref == thermo.public_ref and blocking(result, thermo) == ()


def test_a_curvature_finding_bans_the_frequency_role_but_not_the_recorded_energy_on_the_same_calculation(
    db_session, methane, bench
):
    as_energy = make_thermo(db_session, methane, proto=protocol("g4"))
    as_curvature = make_thermo(db_session, methane, proto=protocol("g3"))
    shared, geometry = bench.opt(as_energy, role=ThermoCalculationRole.sp)  # used for the recorded energy only
    attach_thermo_source_calculation(db_session, thermo=as_curvature, calculation=shared, role=ThermoCalculationRole.freq)
    bench.finding(calculation=shared, kind=K.contradictory_characterization)
    result = bench.run()
    assert blocking(result, as_energy) == ()  # its energy is separately valid
    assert any("contradictory_characterization" in code for code in blocking(result, as_curvature))
    assert result.selected_ref == as_energy.public_ref


def test_a_role_invalidation_of_the_energy_role_excludes_only_records_that_use_that_role(db_session, methane, bench):
    energy_user = make_thermo(db_session, methane, proto=protocol("g4"))
    curvature_user = make_thermo(db_session, methane, proto=protocol("g3"))
    shared, _ = bench.opt(energy_user, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(db_session, thermo=curvature_user, calculation=shared, role=ThermoCalculationRole.freq)
    bench.finding(calculation=shared, kind=K.role_invalidation, role=StructureSourceRole.energy)
    result = bench.run()
    assert blocking(result, energy_user) and not blocking(result, curvature_user)


def test_an_authorized_adjudication_restores_the_record_for_fresh_decisions_and_history_stays(db_session, methane, bench):
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    opt, _ = bench.opt(thermo)
    disproof = bench.finding(calculation=opt, kind=K.identity_incompatibility)
    assert bench.run().outcome is Outcome.no_applicable_candidate
    bench.finding(calculation=opt, kind=K.adjudication, verdict=V.does_not_invalidate, supersedes=disproof)
    result = bench.run()
    assert result.selected_ref == thermo.public_ref
    db_session.refresh(disproof)
    assert disproof.verdict is V.invalidates  # stored history, unchanged


def test_an_unresolved_finding_leaves_the_record_unresolved_not_excluded_as_failed(db_session, methane, bench):
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    opt, _ = bench.opt(thermo)
    bench.finding(calculation=opt, kind=K.identity_incompatibility, verdict=V.unresolved)
    result = bench.run()
    assessment = next(a for a in result.assessments if a.thermo_ref == thermo.public_ref)
    assert assessment.applicability.value == "unresolved" and assessment.blocking == ()
    assert result.outcome is Outcome.no_applicable_candidate and result.unresolved_refs == (thermo.public_ref,)


def test_the_geometry_heuristic_alone_does_not_exclude_but_the_confirmed_finding_does_and_says_why(db_session, methane, bench):
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    opt, geometry = bench.opt(thermo)
    db_session.add(
        CalculationGeometryValidation(
            calculation_id=opt.id, species_smiles="C", is_isomorphic=False, rmsd=0.9, n_mappings=1,
            validation_status=ValidationStatus.fail, validation_reason="probe",
        )
    )
    assert bench.run().selected_ref == thermo.public_ref  # heuristic only: advisory (trust contract v2)
    bench.finding(geometry=geometry, kind=K.identity_incompatibility)
    result = bench.run()
    assert blocking(result, thermo) == ("source_finding:opt:finding_invalidates:identity_incompatibility:authorized_adjudication",)
    assert result.outcome is Outcome.no_applicable_candidate


def test_a_geometry_finding_on_the_input_of_an_optimisation_is_not_a_finding_about_its_product(db_session, methane, bench):
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    opt, _ = bench.opt(thermo)
    guess = make_geometry(db_session)
    attach_input_geometry(db_session, calculation=opt, geometry=guess)
    bench.finding(geometry=guess, kind=K.identity_incompatibility)  # the starting guess was wrong; the optimised result is read
    assert bench.run().selected_ref == thermo.public_ref
