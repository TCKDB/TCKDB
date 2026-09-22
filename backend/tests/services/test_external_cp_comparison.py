"""Tests for the review-tier external-Cp-comparison runner (Phase C-E4).

Before this runner existed, ``CheckTier.review`` (``app/scientific_checks/
__init__.py``) had zero declared members and nothing in TCKDB compared a
computed thermo record against an external observation -- confirmed by
``git show main:backend/app/scientific_checks/declarations.py | grep -c
'CheckTier.review'`` returning 0. This module is the red-to-green test for
that gap: a species entry with a computed NASA-7 thermo and three
``heat_capacity_cp`` observation rows yields one ``record_machine_review``
row with three findings.

Every test below is written to fail under a specific mutation (noted in each
docstring), per the house rule against vacuous tests: a check that always
passes proves nothing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

import pytest
from sqlalchemy import select

from app.db.models.common import (
    ExternalSourceRecordKind,
    MolecularPropertyKind,
    ObservedStateBasis,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
    PhaseKind,
    ScientificOriginKind,
)
from app.db.models.external_source import ExternalSource, ExternalSourceRecord
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.thermo import Thermo
from app.services.external_comparison.cp import (
    ExternalCpComparisonConfigurationError,
    compare_thermo_with_cp_observations,
    latest_cp_comparison_for_thermo,
    run_and_record,
)
from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS
from app.services.machine_review.rereview import (
    MachineReviewReReviewDecision,
    plan_record_machine_rereview,
)
from app.services.machine_review.schemas import MachineReviewStatus as ServiceMachineReviewStatus
from app.services.trust.rubrics import EXTERNAL_CP_COMPARISON_V2
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    attach_thermo_nasa9,
    attach_thermo_points,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

_R = 8.314462618  # J/(mol*K), CODATA molar gas constant.

# The factory's default NASA-7 (see attach_thermo_nasa): a1..a5 = 3.5,0,0,0,0
# (low range, T in [200, 1000)) and b1..b5 = 3.2,0,0,0,0 (high range, T in
# [1000, 6000]). Both are T-independent, so the hand-calculated Cp is exact
# at any temperature in each range -- not just at one sampled point.
_CP_LOW_RANGE_J_MOL_K = 3.5 * _R
_CP_HIGH_RANGE_J_MOL_K = 3.2 * _R


def _make_thermo(session, *, smiles: str, tmin_k: float = 200.0, tmax_k: float = 6000.0) -> Thermo:
    species = make_species(session, smiles=smiles)
    entry = make_species_entry(session, species=species)
    thermo = make_thermo_scalar(
        session,
        species_entry=entry,
        scientific_origin=ScientificOriginKind.computed,
        tmin_k=tmin_k,
        tmax_k=tmax_k,
    )

    # Only ``phase`` is set here: heat-capacity applicability requires the
    # gas phase but never a reference pressure (decided 2026-09-23, see
    # engine.gas_state_reason's ``quantity="cp"`` branch) -- Cp is
    # pressure-independent. Leaving reference_pressure_bar unset by default
    # is deliberate: it exercises that every Cp comparison in this file
    # works without one, matching this runner's pre-D2 applicability.
    thermo.phase = PhaseKind.gas
    session.flush()
    return thermo


def _make_observation(
    session,
    *,
    thermo: Thermo,
    temperature_k: float,
    scalar_value: float,
    state_basis: ObservedStateBasis = ObservedStateBasis.ideal_gas,
    pressure_bar: float | None = None,
    uncertainty_kind: ObservedUncertaintyKind | None = None,
    scalar_uncertainty: float | None = None,
    uncertainty_coverage_factor: float | None = None,
    uncertainty_assessor: ObservedUncertaintyAssessor | None = None,
    method_note: str | None = None,
    external_source_record: ExternalSourceRecord | None = None,
) -> MolecularPropertyObservation:
    obs = MolecularPropertyObservation(
        species_entry_id=thermo.species_entry_id,
        scientific_origin=ScientificOriginKind.experimental,
        property_kind=MolecularPropertyKind.heat_capacity_cp,
        scalar_value=scalar_value,
        scalar_unit="J/mol/K",
        scalar_uncertainty=scalar_uncertainty,
        temperature_k=temperature_k,
        pressure_bar=pressure_bar,
        state_basis=state_basis,
        uncertainty_kind=uncertainty_kind,
        uncertainty_coverage_factor=uncertainty_coverage_factor,
        uncertainty_assessor=uncertainty_assessor,
        method_note=method_note,
        external_source_record_id=(
            external_source_record.id if external_source_record is not None else None
        ),
    )
    session.add(obs)
    session.flush()
    return obs


def _make_external_source_record(session, *, key: str) -> ExternalSourceRecord:
    source = ExternalSource(source_name="NIST ThermoML Archive", source_release="test-release")
    session.add(source)
    session.flush()
    record = ExternalSourceRecord(
        external_source_id=source.id,
        record_kind=ExternalSourceRecordKind.thermoml_article,
        source_uri="https://trc.nist.gov/ThermoML/test.xml",
        source_record_key=key,
        retrieved_at=datetime(2026, 9, 1, 12, 0, 0),
        content_sha256="a" * 64,
        content_length=1234,
        raw_uri="artifacts/test",
        parser_name="thermoml_cp_parser",
        parser_version="1.0.0",
        mapping_version="1.0.0",
    )
    session.add(record)
    session.flush()
    return record


# --------------------------------------------------------------------------- #
# The reproduction test: one thermo + three observations -> one row, three
# findings.
# --------------------------------------------------------------------------- #


def test_computed_thermo_with_three_cp_observations_yields_one_row_three_findings(db_session):
    thermo = _make_thermo(db_session, smiles="c1ccccc1")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)
    _make_observation(db_session, thermo=thermo, temperature_k=400.0, scalar_value=95.0)
    _make_observation(db_session, thermo=thermo, temperature_k=500.0, scalar_value=105.0)

    row = run_and_record(db_session, thermo.id)
    db_session.flush()

    assert isinstance(row, RecordMachineReviewRow)
    assert row.record_id == thermo.id
    assert len(row.findings_json) == 3

    count = db_session.scalar(
        select(RecordMachineReviewRow).where(RecordMachineReviewRow.record_id == thermo.id)
    )
    assert count is not None


# --------------------------------------------------------------------------- #
# HIGH finding, review round 2: a Cp-comparison row must never restale a
# thermo's existing reviewer-family (LLM) review. Before
# app.services.machine_review.query.MachineReviewRecordFamily existed,
# get_record_machine_review_currency_for_record loaded every persisted row
# for (record_type, record_id) with no regard for provider. A Cp row's own
# recipe (model=external_cp_comparison_v1, provider=tckdb.scientific_checks)
# never matches the reviewer recipe, and run_and_record always stamps a
# reviewed_at newer than any prior row -- so the Cp row was always read as
# the latest row, always classified stale, and always demoted the thermo's
# genuine current reviewer review to historical. This test pins the
# behaviour end to end through the actual planner
# (plan_record_machine_rereview), not just the classifier, and must assert
# skip_current both before and after run_and_record.
# --------------------------------------------------------------------------- #


def test_recording_a_cp_comparison_does_not_restale_the_reviewer_familys_current_review(
    db_session,
):
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)

    reviewer_digest = MachineReviewContextDigest(context_hash="c" * 64, context_schema_version="v1")
    reviewer_prompt = "reviewer_prompt_v9"
    reviewer_rubrics = {"computed_thermo_v1": "1"}

    # A fake "current" reviewer-family (LLM) review for this same thermo,
    # recorded before the Cp check ever runs.
    create_record_machine_review_row(
        db_session,
        record_type="thermo",
        record_id=thermo.id,
        review=RecordMachineReview(
            record_type="thermo",
            record_ref=thermo.public_ref,
            status=ServiceMachineReviewStatus.machine_screened_pass,
            reviewed_at=datetime(2026, 9, 1, 0, 0, 0),
            record_id=thermo.id,
        ),
        context_digest=reviewer_digest,
        prompt_version=reviewer_prompt,
        rubric_versions=reviewer_rubrics,
    )
    db_session.flush()

    def _plan():
        return plan_record_machine_rereview(
            db_session,
            record_type="thermo",
            record_id=thermo.id,
            current_context=reviewer_digest,
            active_prompt_version=reviewer_prompt,
            active_rubric_versions=reviewer_rubrics,
        )

    assert _plan().decision is MachineReviewReReviewDecision.skip_current

    # Run and record the unrelated, deterministic external-Cp-comparison
    # check for the same thermo -- this must not touch reviewer currency.
    run_and_record(db_session, thermo.id)
    db_session.flush()

    assert _plan().decision is MachineReviewReReviewDecision.skip_current


def test_latest_cp_comparison_for_thermo_ignores_a_newer_reviewer_family_row(db_session):
    """The Cp helper reads its own family; it must not return a reviewer row.

    Companion to the restaling test above, from the other direction: a
    reviewer-family row recorded AFTER a Cp comparison (newer by
    ``reviewed_at``) must not make ``latest_cp_comparison_for_thermo`` return
    that reviewer row or ``None`` -- it must keep returning the Cp row, since
    the two families are read independently by ``family``.
    """
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)

    assert latest_cp_comparison_for_thermo(db_session, thermo.id) is None

    cp_row = run_and_record(db_session, thermo.id)
    db_session.flush()

    # A newer reviewer-family row for the same thermo.
    create_record_machine_review_row(
        db_session,
        record_type="thermo",
        record_id=thermo.id,
        review=RecordMachineReview(
            record_type="thermo",
            record_ref=thermo.public_ref,
            status=ServiceMachineReviewStatus.machine_screened_pass,
            reviewed_at=datetime(2099, 1, 1, 0, 0, 0),
            record_id=thermo.id,
        ),
        context_digest=MachineReviewContextDigest(context_hash="d" * 64, context_schema_version="v1"),
        prompt_version="reviewer_prompt_v9",
        rubric_versions={"computed_thermo_v1": "1"},
    )
    db_session.flush()

    latest_cp = latest_cp_comparison_for_thermo(db_session, thermo.id)
    assert latest_cp is not None
    assert latest_cp.id == cp_row.id


# --------------------------------------------------------------------------- #
# NASA-7 Cp at a known T matches a hand-computed value.
# Mutation: perturb one coefficient -> the hand-calc no longer matches -> red.
# Mutation: use the wrong J/kmol->J/mol conversion -> off by 1000x -> red.
# --------------------------------------------------------------------------- #


def test_nasa7_cp_matches_hand_calculated_polynomial_value(db_session):
    thermo = _make_thermo(db_session, smiles="CC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=0.0)

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    assert result.representation == "nasa7"
    (comparison,) = result.comparisons
    assert comparison.cp_computed_j_mol_k == pytest.approx(_CP_LOW_RANGE_J_MOL_K, rel=1e-9)


def test_nasa7_cp_in_high_temperature_range_matches_hand_calculated_value(db_session):
    thermo = _make_thermo(db_session, smiles="CCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=1500.0, scalar_value=0.0)

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    (comparison,) = result.comparisons
    assert comparison.cp_computed_j_mol_k == pytest.approx(_CP_HIGH_RANGE_J_MOL_K, rel=1e-9)


def test_j_per_kmol_k_to_j_per_mol_k_conversion_constant_is_one_over_1000():
    """Direct guard on the named unit constant (mutation: swap 1000 for 1 -> red)."""
    from app.services.external_comparison.cp import _J_PER_KMOL_K_TO_J_PER_MOL_K

    assert _J_PER_KMOL_K_TO_J_PER_MOL_K == pytest.approx(1.0 / 1000.0)
    # And exercised end-to-end: a deliberately wrong constant would make this fail.
    assert 29071.02 * _J_PER_KMOL_K_TO_J_PER_MOL_K == pytest.approx(29.07102, rel=1e-6)


# --------------------------------------------------------------------------- #
# NASA-9.
# --------------------------------------------------------------------------- #


def test_nasa9_cp_matches_hand_calculated_polynomial_value(db_session):
    thermo = _make_thermo(db_session, smiles="CCCC")
    intervals = attach_thermo_nasa9(db_session, thermo=thermo)
    # attach_thermo_nasa9's first interval: a1..a7 = 1,2,3,4,5,6,7; T in [200, 1000).
    t = 500.0
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    cp_over_r = a[0] * t**-2 + a[1] * t**-1 + a[2] + a[3] * t + a[4] * t**2 + a[5] * t**3 + a[6] * t**4
    expected = cp_over_r * _R
    _make_observation(db_session, thermo=thermo, temperature_k=t, scalar_value=0.0)

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    assert result.representation == "nasa9"
    (comparison,) = result.comparisons
    assert comparison.cp_computed_j_mol_k == pytest.approx(expected, rel=1e-9)
    assert len(intervals) == 2


# --------------------------------------------------------------------------- #
# Point representation compares only at an exact match.
# Mutation: interpolate between neighboring points -> red (this test pins
# "no exact match" to not_comparable, which an interpolating implementation
# would violate).
# --------------------------------------------------------------------------- #


def test_point_representation_compares_only_at_exact_temperature_match(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCC")
    attach_thermo_points(
        db_session,
        thermo=thermo,
        temperatures_k=[300.0, 400.0, 500.0],
        cp_j_mol_k=[90.0, 100.0, 110.0],
    )
    _make_observation(db_session, thermo=thermo, temperature_k=400.0, scalar_value=99.0)
    _make_observation(db_session, thermo=thermo, temperature_k=350.0, scalar_value=95.0)  # no exact point

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    assert result.representation == "point"
    by_t = {c.temperature_k: c for c in result.comparisons}

    exact = by_t[400.0]
    assert exact.comparability == "comparable"
    assert exact.cp_computed_j_mol_k == pytest.approx(100.0)
    assert exact.residual_j_mol_k == pytest.approx(100.0 - 99.0)

    no_match = by_t[350.0]
    assert no_match.comparability == "not_comparable"
    assert no_match.comparability_reason == "no_exact_matching_point"
    assert no_match.cp_computed_j_mol_k is None
    assert no_match.residual_j_mol_k is None


# --------------------------------------------------------------------------- #
# real_gas rows are comparable with non_ideality: unquantified, carrying the
# observed pressure, and still report a residual.
# Mutation: mark them not_comparable instead -> red.
# --------------------------------------------------------------------------- #


def test_real_gas_observation_is_comparable_with_unquantified_non_ideality_and_pressure(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(
        db_session,
        thermo=thermo,
        temperature_k=298.15,
        scalar_value=80.0,
        state_basis=ObservedStateBasis.real_gas,
        pressure_bar=5.0,
    )

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    (comparison,) = result.comparisons
    assert comparison.state_basis == "real_gas"
    assert comparison.non_ideality == "unquantified"
    assert comparison.pressure_bar == pytest.approx(5.0)
    assert comparison.comparability == "comparable"
    assert comparison.residual_j_mol_k is not None
    assert comparison.residual_j_mol_k == pytest.approx(_CP_LOW_RANGE_J_MOL_K - 80.0)


def test_null_reference_pressure_still_yields_a_cp_residual(db_session):
    """Decision (2026-09-23): reference pressure cannot change a heat
    capacity, so a thermo with no recorded ``reference_pressure_bar`` must
    still produce a comparable Cp residual (the pre-D2 behaviour, restored
    here after D2 briefly required a pressure for every applicability
    check). ``_make_thermo`` sets no reference pressure by default -- see
    its docstring -- so this only asserts the value explicitly stays None
    and the comparison still succeeds.

    Mutation: reintroduce a reference-pressure requirement into
    ``engine.gas_state_reason(..., quantity="cp")`` -> ``comparability``
    goes to ``not_comparable`` and this goes red.
    """
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCCCCCCCCCC")
    assert thermo.reference_pressure_bar is None
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    (comparison,) = result.comparisons
    assert comparison.comparability == "comparable"
    assert comparison.residual_j_mol_k == pytest.approx(_CP_LOW_RANGE_J_MOL_K - 80.0)


def test_null_reference_pressure_leaves_entropy_comparison_unavailable(db_session):
    """Companion to the Cp test above: entropy still needs a reference
    pressure (it is baked into the entropy coefficient), so the D1
    thermo-consistency check must keep reporting it unavailable when the
    thermo carries no ``reference_pressure_bar`` -- even though the same
    record's Cp comparisons now succeed (decision, 2026-09-23).
    """
    from app.services.consistency.thermo import compare_thermo

    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCCCCCCCCCCC")
    assert thermo.reference_pressure_bar is None
    thermo.s298_j_mol_k = 200.0
    attach_thermo_nasa(db_session, thermo=thermo)
    db_session.flush()

    result = compare_thermo(thermo, temperature_grid=[298.15])
    rows = [json.loads(f.message) for f in result.findings if '"quantity"' in f.message]
    s_rows = [r for r in rows if r["quantity"] == "s"]
    assert s_rows
    assert all(r["reason"] == "missing_or_invalid_reference_pressure" for r in s_rows)
    assert all(r["residual"] is None for r in s_rows)


def test_ideal_gas_observation_carries_no_non_ideality_flag(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(
        db_session,
        thermo=thermo,
        temperature_k=298.15,
        scalar_value=80.0,
        state_basis=ObservedStateBasis.ideal_gas,
    )
    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    (comparison,) = result.comparisons
    assert comparison.non_ideality is None
    assert comparison.comparability == "comparable"


# --------------------------------------------------------------------------- #
# Temperature outside the NASA fit range -> not_comparable.
# Mutation: extrapolate anyway -> red.
# --------------------------------------------------------------------------- #


def test_temperature_outside_nasa_fit_range_is_not_comparable(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo, t_low=200.0, t_mid=1000.0, t_high=2000.0)
    _make_observation(db_session, thermo=thermo, temperature_k=2500.0, scalar_value=80.0)

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    (comparison,) = result.comparisons
    assert comparison.comparability == "not_comparable"
    assert comparison.comparability_reason == "temperature_outside_fit_range"
    assert comparison.t_in_range is False
    assert comparison.cp_computed_j_mol_k is None
    assert comparison.residual_j_mol_k is None


# --------------------------------------------------------------------------- #
# No mutation of thermo or observation columns after a run.
# Mutation: have the runner set a status/flag on the observation or thermo
# row -> red (this test reads every touched column back and diffs).
# --------------------------------------------------------------------------- #


def test_run_and_record_mutates_no_thermo_or_observation_column(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    obs = _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)

    before_thermo = {c.name: getattr(thermo, c.name) for c in Thermo.__table__.columns}
    before_obs = {
        c.name: getattr(obs, c.name) for c in MolecularPropertyObservation.__table__.columns
    }

    run_and_record(db_session, thermo.id)
    db_session.flush()
    db_session.expire(thermo)
    db_session.expire(obs)

    after_thermo = {c.name: getattr(thermo, c.name) for c in Thermo.__table__.columns}
    after_obs = {c.name: getattr(obs, c.name) for c in MolecularPropertyObservation.__table__.columns}

    assert after_thermo == before_thermo
    assert after_obs == before_obs


# --------------------------------------------------------------------------- #
# The machine-review row carries the rubric version and a context hash that
# changes when an observation is added.
# Mutation: hard-code a constant hash -> red.
# --------------------------------------------------------------------------- #


def test_record_carries_rubric_version_and_context_hash_changes_with_observations(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)

    row_one = run_and_record(db_session, thermo.id)
    db_session.flush()

    rubric_key = f"{EXTERNAL_CP_COMPARISON_V2.name}_v{EXTERNAL_CP_COMPARISON_V2.version}"
    assert row_one.rubric_versions_json == {
        rubric_key: ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS[rubric_key]
    }
    assert row_one.context_schema_version

    _make_observation(db_session, thermo=thermo, temperature_k=400.0, scalar_value=90.0)
    row_two = run_and_record(db_session, thermo.id)
    db_session.flush()

    assert row_two.context_hash != row_one.context_hash
    assert len(row_two.findings_json) == 2


# --------------------------------------------------------------------------- #
# Cantera absent -> configuration error, no row.
# --------------------------------------------------------------------------- #


def test_missing_cantera_raises_configuration_error_and_writes_no_row(db_session, monkeypatch):
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)

    monkeypatch.setitem(sys.modules, "cantera", None)

    before = db_session.scalar(
        select(RecordMachineReviewRow).where(RecordMachineReviewRow.record_id == thermo.id)
    )
    assert before is None

    with pytest.raises(ExternalCpComparisonConfigurationError):
        run_and_record(db_session, thermo.id)

    after = db_session.scalar(
        select(RecordMachineReviewRow).where(RecordMachineReviewRow.record_id == thermo.id)
    )
    assert after is None


def test_point_representation_never_imports_cantera(db_session, monkeypatch):
    """A tabulated-point thermo has no Cantera dependency at all."""
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCC")
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[300.0], cp_j_mol_k=[90.0])
    _make_observation(db_session, thermo=thermo, temperature_k=300.0, scalar_value=88.0)

    monkeypatch.setitem(sys.modules, "cantera", None)
    row = run_and_record(db_session, thermo.id)
    assert len(row.findings_json) == 1


# --------------------------------------------------------------------------- #
# Findings carry the custody ref and every required field, decodable from the
# canonical JSON message.
# --------------------------------------------------------------------------- #


def test_finding_message_decodes_to_every_required_field_including_custody_ref(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    custody = _make_external_source_record(db_session, key="10.1016/j.jct.2013.08.022#P1/V1")
    _make_observation(
        db_session,
        thermo=thermo,
        temperature_k=298.15,
        scalar_value=80.0,
        uncertainty_kind=ObservedUncertaintyKind.expanded,
        scalar_uncertainty=1.5,
        uncertainty_coverage_factor=2.0,
        uncertainty_assessor=ObservedUncertaintyAssessor.source_author,
        method_note="statistical thermodynamics",
        external_source_record=custody,
    )

    row = run_and_record(db_session, thermo.id)
    (finding,) = row.findings_json
    detail = json.loads(finding["message"])

    assert detail["observation_ref"].startswith("mpo_")
    assert detail["scalar_uncertainty"] == 1.5
    assert detail["external_source_record_ref"] == "10.1016/j.jct.2013.08.022#P1/V1"
    assert detail["temperature_k"] == pytest.approx(298.15)
    assert detail["cp_observed_j_mol_k"] == pytest.approx(80.0)
    assert detail["cp_computed_j_mol_k"] == pytest.approx(_CP_LOW_RANGE_J_MOL_K, rel=1e-9)
    assert detail["uncertainty_kind"] == "expanded"
    assert detail["uncertainty_coverage_factor"] == pytest.approx(2.0)
    assert detail["uncertainty_assessor"] == "source_author"
    assert detail["representation"] == "nasa7"
    assert detail["comparability"] == "comparable"
    assert detail["non_ideality"] is None
    assert finding["severity"] == "info"
    assert f"observation:{detail['observation_ref']}" in finding["evidence_keys"]


def test_a_long_custody_key_does_not_blow_the_findings_message_size_limit(db_session):
    """A 2000-char ``source_record_key`` must not make the whole run raise.

    MEDIUM finding, review round 2: ``source_record_key``
    (``ExternalSourceRecord``) is an unbounded ``Text`` column, and before
    this fix it was embedded, untruncated, into the JSON ``message`` twice
    (as both ``observation_ref`` and ``external_source_record_ref``).
    ``MachineReviewFinding.message`` is capped at 1000 chars
    (``app.services.machine_review.schemas``), and the only existing
    fallback (dropping ``method_note``) could not save a message that was
    already over the limit from the ref fields alone -- so a sufficiently
    long custody key made ``_finding_from_comparison`` raise a pydantic
    ``ValidationError`` instead of recording a finding, silently killing an
    otherwise-successful comparison run. Mutation: remove ``_bounded_ref``
    from ``_finding_payload`` -> this test goes red with a raised
    ``ValidationError`` (or, run against the pre-fix code, does exactly
    that).
    """
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    long_key = "10.1016/j.jct.2013.08.022#" + "P" * 2000
    custody = _make_external_source_record(db_session, key=long_key)
    obs = _make_observation(
        db_session,
        thermo=thermo,
        temperature_k=298.15,
        scalar_value=80.0,
        external_source_record=custody,
    )

    # Must not raise (the pre-fix behaviour) and must persist a row.
    row = run_and_record(db_session, thermo.id)
    (finding,) = row.findings_json
    assert len(finding["message"].encode("utf-8")) <= 1000

    detail = json.loads(finding["message"])
    # observation_ref is always the observation's own short public ref
    # (never the custody key), so it is untouched by the long key -- the
    # long key only reaches external_source_record_ref, and that copy
    # inside message is bounded with a truncation marker.
    assert detail["observation_ref"] == obs.public_ref
    assert len(detail["external_source_record_ref"]) < len(long_key)
    assert detail["external_source_record_ref"] != long_key
    # ...but evidence_keys still carries the full, untruncated key, so exact
    # matching against the custody row's own key is never degraded.
    assert f"observation:{detail['observation_ref']}" in finding["evidence_keys"]
    assert f"external_source_record:{long_key}" in finding["evidence_keys"]


def test_observation_without_custody_gets_its_public_ref_never_the_db_id(db_session):
    """``observation_ref`` is ``obs.public_ref`` -- opaque, never the row id.

    Pre-D2 this was a content-derived label built from temperature and
    value (``heat_capacity_cp@{t}K={v}``); the D2 rewrite switched to the
    observation's own ``mpo_``-prefixed public ref (Phase C-E5), which is
    an opaque random token, not content-derived -- this test (and its
    name) pin that current behaviour rather than the stale pre-D2 claim.
    """
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    obs = _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=80.0)

    result = compare_thermo_with_cp_observations(db_session, thermo.id)
    (comparison,) = result.comparisons
    assert comparison.external_source_record_ref is None
    assert comparison.observation_ref.startswith("mpo_")
    assert comparison.observation_ref == obs.public_ref
    assert str(obs.id) not in comparison.observation_ref


# --------------------------------------------------------------------------- #
# The status token is derived (not invented) and is never not_run / failed --
# it is NOT, despite the name a residual of 50% still reading "pass" would
# suggest, an approval signal in the everyday sense of that word. See
# test_status_pins_the_shared_derivation_and_is_not_an_accuracy_verdict below
# for the documented limitation this leaves.
# --------------------------------------------------------------------------- #


def test_status_pins_the_shared_derivation_and_is_not_an_accuracy_verdict(db_session):
    """Pin what the status token actually is, and record its known limitation.

    MEDIUM finding, review round 2: ``run_and_record`` derives its status via
    the shared ``derive_machine_review_status`` (``app.services.machine_review
    .derivation``), the same function every reviewer-family review uses.
    Every Cp finding is ``severity=info`` (no ratio is ever judged here --
    see the module docstring), and that shared function's documented rule
    (``derivation.py`` §"Notes for the implementer") is "info-only findings
    are still a pass". So a thermo whose computed Cp is wildly off from an
    observation -- 50% off, say -- still stamps ``machine_screened_pass``.

    This is a genuine, currently-unresolved limitation, not a bug this
    change fixes: ``MachineReviewStatus``
    (``app.db.models.common.MachineReviewStatus``) has no advisory/
    info-only/not-assessed token distinct from ``machine_screened_pass`` to
    use instead, and inventing one is a new enum value, which requires a
    migration and is out of scope here (and would fork this check's status
    semantics from every other machine-review consumer's, which is worse).
    So this test does NOT claim the status is safe to read as "no accuracy
    concern" -- it pins the current, shared, and honestly-limited behaviour,
    and this docstring is where that limitation is recorded until an
    advisory token exists. A prior version of this test was named
    ``test_status_is_not_an_approval_signal``, which claimed the opposite of
    what the assertions below actually show.
    """
    from app.services.machine_review.schemas import MachineReviewStatus

    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCCC")
    attach_thermo_nasa(db_session, thermo=thermo)
    # Observed at half the computed value -- a 100%-of-observed residual, to
    # make the "still reads pass despite a huge residual" limitation
    # concrete rather than hypothetical.
    _make_observation(db_session, thermo=thermo, temperature_k=298.15, scalar_value=_CP_LOW_RANGE_J_MOL_K * 0.5)

    row = run_and_record(db_session, thermo.id)
    detail = json.loads(row.findings_json[0]["message"])
    assert detail["comparability"] == "comparable"
    assert abs(detail["residual_j_mol_k"]) > 0.9 * detail["cp_observed_j_mol_k"]

    # The known limitation: a residual this large still reads "pass".
    assert row.status is MachineReviewStatus.machine_screened_pass
    assert row.status is not MachineReviewStatus.not_run
    assert row.status is not MachineReviewStatus.machine_review_failed


# --------------------------------------------------------------------------- #
# Preconditions that are not findings.
# --------------------------------------------------------------------------- #


def test_non_computed_thermo_raises_value_error_not_a_finding(db_session):
    species = make_species(db_session, smiles="CCCCCCCCCCCCCCCC")
    entry = make_species_entry(db_session, species=species)
    thermo = make_thermo_scalar(
        db_session, species_entry=entry, scientific_origin=ScientificOriginKind.experimental
    )
    with pytest.raises(ValueError, match="computed"):
        compare_thermo_with_cp_observations(db_session, thermo.id)


def test_thermo_with_no_representation_raises_value_error(db_session):
    thermo = _make_thermo(db_session, smiles="CCCCCCCCCCCCCCCCC")
    with pytest.raises(ValueError, match="no NASA-7, NASA-9, or tabulated-point"):
        compare_thermo_with_cp_observations(db_session, thermo.id)
