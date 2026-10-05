"""H298 under the trust-contract correction: which live answers change, and which must not.

The correction (computed rubrics at the next version): an automated geometry-validation ``fail`` is a curator-attention
signal (see ``CalculationGeometryValidation``), not a hard failure. A record is no longer ``hard_failed`` because a source
calculation's automated identity check said ``fail``, so the H298 assessor, which blocks on ``evidence.hard_fail_reason``,
no longer excludes it for that reason alone. Rejected calculation quality, broken artifact custody and the other
structural hard fails still block exactly as before.

Each scenario states the main-branch answer and the answer now. The H298 golden (``test_h298_golden.py``) is synthetic
candidate-level data and is unaffected; these are the end-to-end probes on stored rows.
"""

from __future__ import annotations

import pytest

from app.db.models.calculation import CalculationGeometryValidation
from app.db.models.common import CalculationQuality, ThermoCalculationRole, ThermoTargetKind, ValidationStatus
from app.services.thermo_selection import H298Request, Outcome, select_h298
from tests.services.scientific_read._factories import attach_thermo_source_calculation, make_calculation
from tests.services.thermo_selection._support import make_thermo, protocol, species_entry_for

EQUILIBRIUM = H298Request(target_kind=ThermoTargetKind.equilibrium_ensemble)


@pytest.fixture
def methane(db_session):
    return species_entry_for(db_session, "Methane")


def _source(session, entry, thermo, *, role=ThermoCalculationRole.opt, geometry=None, rejected=False):
    calc = make_calculation(session, species_entry_id=entry.id)
    if rejected:
        calc.quality = CalculationQuality.rejected
    if geometry is not None:
        session.add(
            CalculationGeometryValidation(
                calculation_id=calc.id,
                species_smiles="C",
                is_isomorphic=geometry is not ValidationStatus.fail,
                rmsd=0.5,
                n_mappings=1,
                validation_status=geometry,
                validation_reason="probe",
            )
        )
    attach_thermo_source_calculation(session, thermo=thermo, calculation=calc, role=role)
    session.flush()
    return calc


def _run(session, entry):
    session.flush()
    session.expire_all()
    return select_h298(session, species_entry_id=entry.id, request=EQUILIBRIUM)


def _blocking(result, thermo):
    return next(a for a in result.assessments if a.thermo_ref == thermo.public_ref).blocking


def test_p1_a_sole_record_whose_only_flaw_is_an_automated_geometry_fail_is_now_selectable(db_session, methane):
    """Main: no_applicable_candidate (blocked, source_calculation_hard_failed_for_required_role). Now: selectable."""
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    _source(db_session, methane, thermo, geometry=ValidationStatus.fail)
    result = _run(db_session, methane)
    assert result.outcome is Outcome.sole_eligible_candidate and result.selected_ref == thermo.public_ref
    assert _blocking(result, thermo) == ()


def test_p2_a_geometry_fail_no_longer_lets_a_competitor_win_by_exclusion(db_session, methane):
    """Main: the G4 with the geometry fail was excluded, so the G3 was the sole eligible record. Now: E1 prefers the G4."""
    g4 = make_thermo(db_session, methane, proto=protocol("g4"), age_days=400)
    g3 = make_thermo(db_session, methane, proto=protocol("g3"), age_days=1)
    _source(db_session, methane, g4, geometry=ValidationStatus.fail)
    result = _run(db_session, methane)
    assert result.outcome is Outcome.policy_preferred and result.selected_ref == g4.public_ref
    assert _blocking(result, g3) == ()


def test_p3_a_rejected_quality_source_still_blocks_even_with_a_geometry_fail(db_session, methane):
    """No change: quality=rejected is a confirmed unusable calculation."""
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    _source(db_session, methane, thermo, geometry=ValidationStatus.fail, rejected=True)
    result = _run(db_session, methane)
    assert result.outcome is Outcome.no_applicable_candidate
    assert _blocking(result, thermo) == ("evidence_hard_failed:source_calculation_hard_failed_for_required_role",)


@pytest.mark.parametrize("status", [ValidationStatus.warning, ValidationStatus.passed])
def test_p4_a_geometry_warning_and_a_pass_are_unchanged(db_session, methane, status):
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    _source(db_session, methane, thermo, geometry=status)
    result = _run(db_session, methane)
    assert result.selected_ref == thermo.public_ref
    assert _blocking(result, thermo) == ()


def test_p5_a_geometry_fail_on_a_non_required_role_never_blocked_and_still_does_not(db_session, methane):
    """No change: only opt and freq roles are required for H298 evidence."""
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    _source(db_session, methane, thermo, role=ThermoCalculationRole.sp, geometry=ValidationStatus.fail)
    result = _run(db_session, methane)
    assert result.selected_ref == thermo.public_ref and _blocking(result, thermo) == ()


def test_p6_the_geometry_fail_stays_visible_as_advisory_evidence(db_session, methane):
    """The signal is demoted, not erased: the assessment discloses it so the answer cannot read as a clean check."""
    thermo = make_thermo(db_session, methane, proto=protocol("g4"))
    _source(db_session, methane, thermo, geometry=ValidationStatus.fail)
    result = _run(db_session, methane)
    advisory = next(a for a in result.assessments if a.thermo_ref == thermo.public_ref).advisory
    assert any(item.startswith("evidence_check_warning:") and "geometry_validation" in item for item in advisory), advisory
    # ...and the assessment names the trust-contract version that produced its verdict.
    assert "evidence_rubric:computed_thermo@2" in advisory, advisory
