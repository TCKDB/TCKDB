"""The H298 applicability assessor, on stored thermo rows.

Each test names one fact about a record and asserts the verdict and the reason, so a rule
that is relaxed or tightened shows up as a different reason code, not just a different count.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.models.common import (
    CalculationQuality,
    PhaseKind,
    ScientificOriginKind,
    ThermoCalculationRole,
    ThermoTargetKind,
)
from app.db.models.thermo import ThermoNASA, ThermoNASA9Interval
from app.services.thermo_selection.assessment import assess_candidate
from app.services.thermo_selection.models import Applicability as A
from app.services.thermo_selection.models import H298Request
from app.services.trust import evaluate_loaded_thermo
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    attach_thermo_nasa9,
    attach_thermo_points,
    attach_thermo_source_calculation,
    attach_thermo_wilhoit,
    make_calculation,
    make_conformer_group,
)
from tests.services.thermo_selection._support import make_thermo, species_entry_for

R_KJ = 8.31446261815324 / 1000.0
EQUILIBRIUM = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)


@pytest.fixture
def entry(db_session):
    return species_entry_for(db_session, "Methane")


def assess(session, thermo, request=EQUILIBRIUM, *, with_evidence=False):
    session.flush()
    session.expire(thermo)
    evidence = evaluate_loaded_thermo(thermo) if with_evidence else None
    return assess_candidate(thermo, request=request, evidence=evidence)


def codes(assessment):
    return [r.code.split(":")[0] for r in assessment.reasons]


def disable_reference_guard(session):
    session.execute(text("ALTER TABLE thermo DISABLE TRIGGER trg_guard_thermo_enthalpy_reference"))


# -- scalars -------------------------------------------------------------------------------------


def test_a_finite_scalar_answers_h298_with_no_interval_metadata(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=-74.6, tmin_k=None, tmax_k=None)
    a = assess(db_session, thermo)
    assert a.applicability is A.applicable and a.physically_eligible
    assert a.answer_representation == "h298" and a.value_kj_mol == -74.6
    assert not a.reasons and not a.blocking


def test_a_scalar_is_not_refused_because_the_record_range_excludes_298_15(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=-74.6, tmin_k=400.0, tmax_k=1000.0)
    assert assess(db_session, thermo).applicability is A.applicable


def test_a_nonfinite_stored_scalar_cannot_answer(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=float("nan"))
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible
    assert codes(a) == ["representation_defective"] and "nonfinite_stored_value" in a.reasons[0].code


def test_a_record_with_no_enthalpy_content_is_incompatible(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and codes(a) == ["no_h298_representation"]


# -- the enthalpy reference ------------------------------------------------------------------------


def test_a_scalar_without_a_declared_reference_stays_unresolved_never_guessed(db_session, entry):
    disable_reference_guard(db_session)  # a legacy row: h298 stored before the reference was declared
    thermo = make_thermo(db_session, entry, h298=-74.6, reference=None)
    a = assess(db_session, thermo)
    assert a.applicability is A.unresolved and not a.physically_eligible
    assert codes(a) == ["enthalpy_reference_not_declared"]
    assert a.value_kj_mol == -74.6  # the number is shown; it is just not usable for this quantity


def test_a_nasa_fit_without_a_declared_reference_stays_unresolved(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None, reference=None)
    attach_thermo_nasa(db_session, thermo=thermo)
    a = assess(db_session, thermo)
    assert a.applicability is A.unresolved and codes(a) == ["enthalpy_reference_not_declared"]


# -- exact points -----------------------------------------------------------------------------------


def test_an_enthalpy_point_at_exactly_298_15_k_answers(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[298.15, 400.0], h_kj_mol=[-74.8, -70.0])
    a = assess(db_session, thermo)
    assert a.applicability is A.applicable and a.answer_representation == "point" and a.value_kj_mol == -74.8


def test_points_that_do_not_include_298_15_are_not_interpolated(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[300.0, 400.0], h_kj_mol=[-74.7, -70.0])
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and codes(a) == ["no_enthalpy_point_at_298_15K"]


def test_a_cp_only_curve_cannot_supply_the_enthalpy(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[298.15, 400.0], cp_j_mol_k=[35.7, 40.0])
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and a.answer_representation is None


# -- NASA fits ---------------------------------------------------------------------------------------


def test_a_nasa7_fit_is_evaluated_at_298_15_k_by_the_existing_engine(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    attach_thermo_nasa(db_session, thermo=thermo, t_low=200.0, t_mid=1000.0, t_high=3000.0)
    a = assess(db_session, thermo)
    assert a.applicability is A.applicable and a.answer_representation == "nasa7"
    # H/R = a1*T + a6 with every other coefficient zero (the factory's fit).
    assert a.value_kj_mol == pytest.approx(R_KJ * (3.5 * 298.15 - 1000.0), rel=1e-9)


def test_a_nasa7_fit_whose_range_starts_above_298_15_does_not_answer_and_is_not_extrapolated(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    attach_thermo_nasa(db_session, thermo=thermo, t_low=300.0, t_mid=1000.0, t_high=3000.0)
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible
    assert codes(a) == ["domain_excludes_298_15K"] and "temperature_outside_fit_range" in a.reasons[0].code


def test_a_record_range_that_excludes_298_15_blocks_its_fit(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None, tmin_k=500.0, tmax_k=3000.0)
    attach_thermo_nasa(db_session, thermo=thermo)
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and "temperature_outside_record_range" in a.reasons[0].code


def test_a_nasa9_fit_covering_298_15_k_answers(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    attach_thermo_nasa9(db_session, thermo=thermo)
    a = assess(db_session, thermo)
    assert a.applicability is A.applicable and a.answer_representation == "nasa9"


def test_a_nasa9_gap_at_298_15_k_is_not_bridged(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    for index, (lo, hi) in enumerate([(200.0, 250.0), (300.0, 1000.0)], start=1):
        db_session.add(ThermoNASA9Interval(
            thermo_id=thermo.id, interval_index=index, t_min_k=lo, t_max_k=hi,
            a1=1.0, a2=2.0, a3=3.0, a4=4.0, a5=5.0, a6=6.0, a7=7.0, a8=8.0, a9=9.0))
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and "temperature_outside_fit_or_in_gap" in a.reasons[0].code


def test_an_incomplete_nasa7_row_is_defective_not_zero_filled(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    db_session.add(ThermoNASA(thermo_id=thermo.id, t_low=200.0, t_mid=1000.0, t_high=3000.0, a1=3.5))
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and "incomplete_nasa7" in a.reasons[0].code


def test_a_nonfinite_coefficient_is_defective(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    nasa = attach_thermo_nasa(db_session, thermo=thermo)
    nasa.a3 = float("nan")
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and codes(a) == ["representation_defective"]


def test_a_nonfinite_evaluation_is_refused(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    nasa = attach_thermo_nasa(db_session, thermo=thermo)
    nasa.a5 = 1e305  # finite coefficient whose T^5 term overflows a double
    a = assess(db_session, thermo)
    assert a.applicability is A.incompatible and "nonfinite_engine_result" in a.reasons[0].code


def test_a_wilhoit_only_record_is_unsupported_not_incompatible(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=None)
    attach_thermo_wilhoit(db_session, thermo=thermo)
    a = assess(db_session, thermo)
    assert a.applicability is A.unsupported and codes(a) == ["wilhoit_not_evaluated"]


def test_a_scalar_beside_a_wilhoit_fit_still_answers_and_the_fit_is_disclosed(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=-74.6)
    attach_thermo_wilhoit(db_session, thermo=thermo)
    a = assess(db_session, thermo)
    assert a.applicability is A.applicable and a.answer_representation == "h298"
    assert [r["representation"] for r in a.representations] == ["h298", "wilhoit"]


def test_every_stored_representation_is_disclosed_and_the_scalar_is_preferred(db_session, entry):
    thermo = make_thermo(db_session, entry, h298=-74.6)
    attach_thermo_nasa(db_session, thermo=thermo)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[298.15], h_kj_mol=[-74.5])
    a = assess(db_session, thermo)
    assert [r["representation"] for r in a.representations] == ["h298", "point", "nasa7"]
    assert a.answer_representation == "h298"


# -- phase, pressure, target ---------------------------------------------------------------------------


def test_an_unrecorded_phase_is_unresolved_and_another_phase_is_incompatible(db_session, entry):
    unrecorded = assess(db_session, make_thermo(db_session, entry, phase=None))
    aqueous = assess(db_session, make_thermo(db_session, entry, phase=PhaseKind.aqueous))
    assert unrecorded.applicability is A.unresolved and codes(unrecorded) == ["phase_not_recorded"]
    assert aqueous.applicability is A.incompatible and codes(aqueous) == ["phase_not_gas"]


def test_a_missing_reference_pressure_alone_does_not_invalidate_h298(db_session, entry):
    a = assess(db_session, make_thermo(db_session, entry, reference_pressure_bar=None))
    assert a.applicability is A.applicable
    assert "reference_pressure_not_recorded" in a.advisory


def test_an_undeclared_target_is_unresolved_and_is_not_inferred(db_session, entry):
    a = assess(db_session, make_thermo(db_session, entry, target=None))
    assert a.applicability is A.unresolved and codes(a) == ["thermodynamic_target_not_declared"]


def test_single_conformer_and_equilibrium_targets_stay_distinct(db_session, entry):
    group = make_conformer_group(db_session, entry)
    other = make_conformer_group(db_session, entry)
    ensemble = make_thermo(db_session, entry, target=ThermoTargetKind.equilibrium_ensemble)
    single = make_thermo(db_session, entry, target=ThermoTargetKind.single_conformer, group_id=group.id)
    want_ensemble = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)
    want_single = H298Request(target_kind=ThermoTargetKind.single_conformer, conformer_group_id=group.id)
    want_other = H298Request(target_kind=ThermoTargetKind.single_conformer, conformer_group_id=other.id)

    assert assess(db_session, ensemble, want_ensemble).applicability is A.applicable
    assert assess(db_session, single, want_single).applicability is A.applicable
    refused = assess(db_session, ensemble, want_single)
    assert refused.applicability is A.incompatible and "target_kind_mismatch" in refused.reasons[0].code
    refused = assess(db_session, single, want_ensemble)
    assert refused.applicability is A.incompatible and "target_kind_mismatch" in refused.reasons[0].code
    other_group = assess(db_session, single, want_other)
    assert other_group.applicability is A.incompatible and codes(other_group) == ["target_conformer_group_mismatch"]


def test_a_request_cannot_name_a_group_for_an_ensemble_or_omit_it_for_a_conformer():
    with pytest.raises(ValueError):
        H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble, conformer_group_id=1)
    with pytest.raises(ValueError):
        H298Request(target_kind=ThermoTargetKind.single_conformer)


def test_known_incompatibility_outranks_unsupported_and_unsupported_outranks_unresolved(db_session, entry):
    wilhoit_unrecorded_phase = make_thermo(db_session, entry, h298=None, phase=None)
    attach_thermo_wilhoit(db_session, thermo=wilhoit_unrecorded_phase)
    a = assess(db_session, wilhoit_unrecorded_phase)
    assert a.applicability is A.unsupported
    assert {r.applicability for r in a.reasons} == {A.unsupported, A.unresolved}

    wrong_target_no_reference = make_thermo(db_session, entry, target=ThermoTargetKind.single_conformer,
                                            group_id=make_conformer_group(db_session, entry).id)
    b = assess(db_session, wrong_target_no_reference)
    assert b.applicability is A.incompatible


# -- validation and attachments ------------------------------------------------------------------------


def test_an_experimental_record_without_computational_attachments_is_applicable(db_session, entry):
    thermo = make_thermo(db_session, entry, origin=ScientificOriginKind.experimental)
    a = assess(db_session, thermo, with_evidence=False)
    assert a.applicability is A.applicable and not a.blocking
    assert "evidence_rubric_not_applicable:experimental" in a.advisory


def test_a_computed_record_with_no_attached_calculations_is_disclosed_not_disqualified(db_session, entry):
    a = assess(db_session, make_thermo(db_session, entry), with_evidence=True)
    assert a.applicability is A.applicable and not a.blocking
    assert any(item.startswith("evidence_label:") for item in a.advisory)
    assert any(item.startswith("evidence_check_missing:") for item in a.advisory)


def test_a_hard_failed_required_source_calculation_blocks_a_computed_record(db_session, entry):
    thermo = make_thermo(db_session, entry)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    calc.quality = CalculationQuality.rejected
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=calc, role=ThermoCalculationRole.opt)
    a = assess(db_session, thermo, with_evidence=True)
    assert a.applicability is A.applicable  # physically able to answer ...
    assert a.blocking == ("evidence_hard_failed:source_calculation_hard_failed_for_required_role",)
    assert not a.physically_eligible  # ... but excluded by the blocking validation failure


def test_a_rejected_calculation_in_a_non_required_role_only_blocks_nothing(db_session, entry):
    thermo = make_thermo(db_session, entry)
    calc = make_calculation(db_session, species_entry_id=entry.id)
    calc.quality = CalculationQuality.rejected
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=calc, role=ThermoCalculationRole.sp)
    a = assess(db_session, thermo, with_evidence=True)
    assert not a.blocking and a.physically_eligible
