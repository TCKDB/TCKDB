"""D0/D2/D7 live currency, isolation and dry-run contracts."""
import json
from datetime import datetime

import pytest
from sqlalchemy import event, func, select

from app.db.models.common import ObservedStateBasis, ObservedUncertaintyKind
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.services.consistency import engine
from app.services.consistency.core import currency, latest_recorded, thermo_inputs
from app.services.consistency.service import compare, current, invoke
from app.services.external_comparison import cp
from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, public_rubric_name
from app.services.machine_review.rereview import (
    MachineReviewReReviewDecision,
    plan_record_machine_rereview,
)
from app.services.machine_review.schemas import MachineReviewStatus as ServiceMachineReviewStatus
from tests.services.scientific_read._factories import attach_thermo_nasa, attach_thermo_points
from tests.services.test_external_cp_comparison import _make_external_source_record, _make_observation, _make_thermo


def setup_records(session):
    thermo = _make_thermo(session, smiles="CCO")
    attach_thermo_nasa(session, thermo=thermo)
    attach_thermo_points(session, thermo=thermo, temperatures_k=[300.0, 400.0])
    custody = _make_external_source_record(session, key="phase-d-custody")
    observation = _make_observation(session, thermo=thermo, temperature_k=300.0, scalar_value=90.0,
                                    external_source_record=custody)
    return thermo, observation, custody


def request(thermo, check="external-cp"):
    return {"check": check, "target_ref": thermo.public_ref}


def count_reviews(session):
    return session.scalar(select(func.count()).select_from(RecordMachineReviewRow))


def test_dry_run_and_engine_failure_never_stage_reviews(db_session, monkeypatch):
    thermo, _, _ = setup_records(db_session)
    before = count_reviews(db_session)
    for check in ("external-cp", "thermo"):
        result, row = invoke(db_session, **request(thermo, check))
        assert row is None and result.findings
        assert not db_session.new and not db_session.dirty
    assert count_reviews(db_session) == before
    def fail():
        raise engine.ConfigurationError("missing engine")
    monkeypatch.setattr(engine, "cantera", fail)
    with pytest.raises(engine.ConfigurationError):
        invoke(db_session, commit=True, **request(thermo))
    assert count_reviews(db_session) == before


def test_each_check_appends_one_review_and_changes_no_scientific_state(db_session):
    thermo, observation, _ = setup_records(db_session)
    before = thermo_inputs(thermo)
    count_before = count_reviews(db_session)
    flushed = []
    def audit(session, *_):
        flushed.extend(type(row).__name__ for row in session.new)
        assert not session.dirty and not session.deleted
    event.listen(db_session, "before_flush", audit)
    try:
        for check in ("external-cp", "thermo"):
            result, row = invoke(db_session, commit=True, **request(thermo, check))
            assert row.record_id == thermo.id
            assert all(f.severity.value == "info" for f in result.findings)
            assert result.findings
    finally:
        event.remove(db_session, "before_flush", audit)
    assert flushed == ["RecordMachineReviewRow", "RecordMachineReviewRow"]
    assert count_reviews(db_session) == count_before + 2
    assert thermo_inputs(thermo) == before
    assert observation.scalar_value == 90.0


def test_checks_and_rubrics_have_independent_currency(db_session, monkeypatch):
    thermo, _, _ = setup_records(db_session)
    a, first_a = invoke(db_session, commit=True, **request(thermo))
    b, first_b = invoke(db_session, commit=True, **request(thermo, "thermo"))
    assert currency(db_session, a).state.value == "current"
    assert currency(db_session, b).state.value == "current"
    _, second_a = invoke(db_session, commit=True, **request(thermo))
    assert second_a.id != first_a.id
    assert latest_recorded(db_session, a).id == second_a.id
    assert latest_recorded(db_session, b).id == first_b.id
    assert currency(db_session, b).state.value == "current"
    monkeypatch.setitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, public_rubric_name(a.rubric), "999")
    assert currency(db_session, a).state.value == "stale"
    assert currency(db_session, b).state.value == "current"


@pytest.mark.parametrize("location,field,value", [
    ("nasa", "a1", 4.0), ("nasa", "a7", 8.0), ("nasa", "t_high", 7000.0),
    ("thermo", "reference_pressure_bar", 1.01325), ("thermo", "phase", None),
    ("thermo", "s298_uncertainty_j_mol_k", 2.0), ("thermo", "tmax_k", 4000.0),
    ("observation", "scalar_value", 91.0), ("observation", "scalar_unit", "unknown"),
    ("observation", "pressure_bar", 2.0), ("observation", "state_basis", None),
    ("observation", "method_note", "revised method"),
    ("custody", "content_sha256", "b" * 64), ("custody", "mapping_version", "2"),
    ("custody", "parser_version", "2"), ("source", "source_release", "new-release"),
    ("point", "cp_j_mol_k", 80.0),
])
def test_every_material_cp_input_changes_live_currency(db_session, location, field, value):
    thermo, observation, custody = setup_records(db_session)
    row = cp.run_and_record(db_session, thermo.id)
    assert cp.cp_comparison_currency(db_session, thermo.id).state.value == "current"
    targets = {"thermo": thermo, "nasa": thermo.nasa, "point": thermo.points[0],
               "observation": observation, "custody": custody, "source": custody.external_source}
    setattr(targets[location], field, value)
    with db_session.no_autoflush:
        live = compare(db_session, **request(thermo))
        assert live.digest.context_hash != row.context_hash
        assert current(db_session, **request(thermo)).state.value == "stale"
        assert latest_recorded(db_session, live).id == row.id


def test_uncertainty_magnitude_missing_and_supplied_and_currency(db_session):
    thermo, observation, _ = setup_records(db_session)
    before = compare(db_session, **request(thermo))
    assert json.loads(before.findings[0].message)["scalar_uncertainty"] is None
    observation.scalar_uncertainty = 2.5
    observation.uncertainty_kind = ObservedUncertaintyKind.expanded
    observation.uncertainty_coverage_factor = 2.0
    after = compare(db_session, **request(thermo))
    payload = json.loads(after.findings[0].message)
    assert payload["scalar_uncertainty"] == 2.5
    assert payload["uncertainty_coverage_factor"] == 2.0
    assert after.digest != before.digest


def test_hash_ignores_clocks_and_collection_order(db_session):
    thermo, observation, custody = setup_records(db_session)
    a = compare(db_session, **request(thermo))
    b = compare(db_session, **request(thermo, "thermo"), temperature_grid=[400, 300, 400])
    thermo.points.reverse()
    thermo.updated_at = datetime(2020, 1, 1)
    custody.retrieved_at = datetime(2020, 1, 1)
    assert compare(db_session, **request(thermo)).digest == a.digest
    assert compare(db_session, **request(thermo, "thermo"), temperature_grid=[300, 400]).digest == b.digest


def test_cli_uses_public_refs_dry_runs_and_commits_once(db_session, monkeypatch, capsys):
    from app.api import deps
    from scripts import run_consistency_check as cli
    thermo, _, _ = setup_records(db_session)
    commits = []
    class SessionProxy:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def __getattr__(self, name):
            return getattr(db_session, name)
        def commit(self):
            commits.append(True)
        def rollback(self):
            pass
    monkeypatch.setattr(deps, "SessionLocal", SessionProxy)
    args = ["--check", "thermo", "--target-ref", thermo.public_ref]
    before = count_reviews(db_session)
    assert cli.main(args) == 0
    assert count_reviews(db_session) == before and not commits
    assert json.loads(capsys.readouterr().out)["committed"] is False
    assert cli.main([*args, "--commit"]) == 0
    assert count_reviews(db_session) == before + 1 and commits == [True]
    assert json.loads(capsys.readouterr().out)["target_ref"] == thermo.public_ref
    assert cli.main(["--check", "thermo", "--target-ref", str(thermo.id)]) == 1


def test_d3_public_mapping_dry_run_persistence_and_currency(db_session):
    from app.db.models.common import (
        ArrheniusAUnits,
        KineticsDegeneracyConvention,
        KineticsDirection,
        PhaseKind,
        PressureContext,
    )
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_kinetics,
        make_reaction_entry,
        make_species,
        make_species_entry,
        make_thermo_scalar,
    )
    species = [make_species(db_session, smiles="[H][H]"), make_species(db_session, smiles="[H]", multiplicity=2)]
    entries = [make_species_entry(db_session, species=s) for s in species]
    reaction = make_chem_reaction(db_session, reactants=[species[0]], products=[species[1], species[1]])
    reaction_entry = make_reaction_entry(db_session, reaction=reaction,
                                        reactant_entries=[entries[0]], product_entries=[entries[1], entries[1]])
    thermos = []
    for entry in entries:
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        thermo.phase, thermo.reference_pressure_bar = PhaseKind.gas, 1.0
        attach_thermo_nasa(db_session, thermo=thermo)
        thermos.append(thermo)
    forward = make_kinetics(db_session, reaction_entry=reaction_entry, direction=KineticsDirection.forward,
                            a_units=ArrheniusAUnits.per_s, pressure_context=PressureContext.high_p_limit)
    reverse = make_kinetics(db_session, reaction_entry=reaction_entry, direction=KineticsDirection.reverse,
                            a_units=ArrheniusAUnits.m3_mol_s, pressure_context=PressureContext.high_p_limit)
    for rate in (forward, reverse):
        rate.degeneracy_convention = KineticsDegeneracyConvention.already_applied
    db_session.flush()
    args = {"check": "thermo-kinetics", "target_ref": forward.public_ref, "reverse_kinetics_ref": reverse.public_ref,
                "thermo_mapping": {e.public_ref: t.public_ref for e, t in zip(entries, thermos, strict=True)}, "temperature_grid": [500]}
    before = count_reviews(db_session)
    result, row = invoke(db_session, **args)
    assert row is None and count_reviews(db_session) == before
    numerical = [json.loads(f.message) for f in result.findings if "k_forward" in f.message]
    assert len(numerical) == 1
    _, row = invoke(db_session, commit=True, **args)
    assert row.record_type.value == "kinetics" and row.record_id == forward.id
    assert count_reviews(db_session) == before + 1
    assert current(db_session, **args).state.value == "current"
    reverse.a *= 2
    assert current(db_session, **args).state.value == "stale"


def test_missing_scalar_is_a_visible_unavailable_comparison():
    from app.db.models.molecular_property_observation import MolecularPropertyObservation
    observation = MolecularPropertyObservation(public_ref="mpo_missing", scalar_unit="J/mol/K", temperature_k=300,
                                                state_basis=ObservedStateBasis.ideal_gas, scalar_value=None)
    result = cp._compare_one(observation, "point", lambda temperature: (25.0, True, None))
    assert result.comparability_reason == "missing_or_nonfinite_observation_scalar"
    assert result.residual_j_mol_k is None


def test_nonfinite_inputs_remain_hashable_and_unavailable_without_flushing(db_session):
    thermo, observation, _ = setup_records(db_session)
    baseline = compare(db_session, **request(thermo))
    observation.scalar_value = float("nan")
    direct = cp.compare_thermo_with_cp_observations(db_session, thermo.id)
    assert direct.comparisons[0].comparability_reason == "missing_or_nonfinite_observation_scalar"
    result = compare(db_session, **request(thermo))
    assert result.digest != baseline.digest
    assert json.loads(result.findings[0].message)["residual_j_mol_k"] is None
    assert "nonfinite_float" in result.inputs_json
    assert observation in db_session.dirty


# --------------------------------------------------------------------------- #
# Review round 2, part E: the two new advisory checks (D1 thermo-consistency
# and D3 thermo-kinetics) must not restale a record's current reviewer-family
# review, the exact defect that bit the external-Cp check in Phase C (see
# test_external_cp_comparison.py::
# test_recording_a_cp_comparison_does_not_restale_the_reviewer_familys_current_review).
# Both new checks share the same append-only ``record_machine_review`` and
# the same ``get_record_machine_review_currency_for_record`` classifier, so
# the same class of bug -- a differently-keyed row read as "the latest" and
# demoting a genuine reviewer pass -- was equally possible here and had no
# regression test pinning it shut.
# --------------------------------------------------------------------------- #


def _seed_reviewer_review(session, *, record_type, record_id, record_ref):
    """A fake "current" reviewer-family (LLM) review, recorded before either
    new check ever runs -- mirrors test_external_cp_comparison.py's fixture.
    """
    digest = MachineReviewContextDigest(context_hash="e" * 64, context_schema_version="v1")
    prompt_version = "reviewer_prompt_v9"
    rubric_versions = {"computed_thermo_v1": "1"}
    create_record_machine_review_row(
        session,
        record_type=record_type,
        record_id=record_id,
        review=RecordMachineReview(
            record_type=record_type,
            record_ref=record_ref,
            status=ServiceMachineReviewStatus.machine_screened_pass,
            reviewed_at=datetime(2026, 9, 1, 0, 0, 0),
            record_id=record_id,
        ),
        context_digest=digest,
        prompt_version=prompt_version,
        rubric_versions=rubric_versions,
    )
    session.flush()
    return digest, prompt_version, rubric_versions


def test_thermo_consistency_check_does_not_restale_the_reviewer_familys_current_review(db_session):
    thermo, _, _ = setup_records(db_session)
    digest, prompt_version, rubric_versions = _seed_reviewer_review(
        db_session, record_type="thermo", record_id=thermo.id, record_ref=thermo.public_ref,
    )

    def _plan():
        return plan_record_machine_rereview(
            db_session, record_type="thermo", record_id=thermo.id,
            current_context=digest, active_prompt_version=prompt_version,
            active_rubric_versions=rubric_versions,
        )

    assert _plan().decision is MachineReviewReReviewDecision.skip_current

    _, row = invoke(db_session, commit=True, **request(thermo, "thermo"))
    db_session.flush()
    assert row is not None

    assert _plan().decision is MachineReviewReReviewDecision.skip_current


def test_thermo_kinetics_check_does_not_restale_the_reviewer_familys_current_review_on_the_kinetics_record(
    db_session,
):
    from app.db.models.common import (
        ArrheniusAUnits,
        KineticsDegeneracyConvention,
        KineticsDirection,
        PhaseKind,
        PressureContext,
    )
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_kinetics,
        make_reaction_entry,
        make_species,
        make_species_entry,
        make_thermo_scalar,
    )

    species = [make_species(db_session, smiles="[H][H]"), make_species(db_session, smiles="[H]", multiplicity=2)]
    entries = [make_species_entry(db_session, species=s) for s in species]
    reaction = make_chem_reaction(db_session, reactants=[species[0]], products=[species[1], species[1]])
    reaction_entry = make_reaction_entry(db_session, reaction=reaction,
                                         reactant_entries=[entries[0]], product_entries=[entries[1], entries[1]])
    thermos = []
    for entry in entries:
        thermo = make_thermo_scalar(db_session, species_entry=entry)
        thermo.phase, thermo.reference_pressure_bar = PhaseKind.gas, 1.0
        attach_thermo_nasa(db_session, thermo=thermo)
        thermos.append(thermo)
    forward = make_kinetics(db_session, reaction_entry=reaction_entry, direction=KineticsDirection.forward,
                            a_units=ArrheniusAUnits.per_s, pressure_context=PressureContext.high_p_limit)
    reverse = make_kinetics(db_session, reaction_entry=reaction_entry, direction=KineticsDirection.reverse,
                            a_units=ArrheniusAUnits.m3_mol_s, pressure_context=PressureContext.high_p_limit)
    for rate in (forward, reverse):
        rate.degeneracy_convention = KineticsDegeneracyConvention.already_applied
    db_session.flush()

    digest, prompt_version, rubric_versions = _seed_reviewer_review(
        db_session, record_type="kinetics", record_id=forward.id, record_ref=forward.public_ref,
    )

    def _plan():
        return plan_record_machine_rereview(
            db_session, record_type="kinetics", record_id=forward.id,
            current_context=digest, active_prompt_version=prompt_version,
            active_rubric_versions=rubric_versions,
        )

    assert _plan().decision is MachineReviewReReviewDecision.skip_current

    args = {"check": "thermo-kinetics", "target_ref": forward.public_ref, "reverse_kinetics_ref": reverse.public_ref,
            "thermo_mapping": {e.public_ref: t.public_ref for e, t in zip(entries, thermos, strict=True)},
            "temperature_grid": [500]}
    _, row = invoke(db_session, commit=True, **args)
    db_session.flush()
    assert row is not None and row.record_id == forward.id

    assert _plan().decision is MachineReviewReReviewDecision.skip_current


def test_no_observations_is_visible_and_large_engine_values_are_unavailable(db_session):
    thermo = _make_thermo(db_session, smiles="CC")
    attach_thermo_nasa(db_session, thermo=thermo)
    result = compare(db_session, **request(thermo))
    assert len(result.findings) == 1
    assert json.loads(result.findings[0].message)["reason"] == "no_cp_observations"
    _make_observation(db_session, thermo=thermo, temperature_k=300, scalar_value=25)
    thermo.nasa.a1 = 1e308
    result = compare(db_session, **request(thermo))
    assert json.loads(result.findings[0].message)["comparability_reason"] == "nonfinite_engine_result"
    assert result.digest.context_hash


# --------------------------------------------------------------------------- #
# D4 Gibbs self-consistency (``--check gibbs-self``). The record is persisted
# through the real thermo upload workflow (``upload_thermo``), with a stored
# G on every point so the check has something to evaluate.
# --------------------------------------------------------------------------- #

_GIBBS_LOW = (3.0, 2e-3, -4e-7, 3e-11, -1e-15, -9000.0, 7.0)
_GIBBS_HIGH = (3.4, 1.1e-3, -2e-7, 1e-11, -3e-16, -8800.0, 5.0)


def gibbs_records(session):
    from tests.services.test_phase_d_gibbs import nasa7_payload, upload_thermo
    return upload_thermo(
        session, smiles="CCO", enthalpy_reference_kind="formation_298k", reference_pressure_bar=1.0,
        nasa=nasa7_payload(_GIBBS_LOW, _GIBBS_HIGH),
        points=[{"temperature_k": 500.0, "h_kj_mol": -50.0, "s_j_mol_k": 200.0, "g_kj_mol": -150.0},
                {"temperature_k": 1500.0, "h_kj_mol": 10.0, "s_j_mol_k": 300.0, "g_kj_mol": -440.0}],
    )


def gibbs_request(thermo):
    return {"check": "gibbs-self", "target_ref": thermo.public_ref}


def _evaluated(result):
    rows = [json.loads(f.message) for f in result.findings]
    evaluated = [r for r in rows if r["reason"] is None]
    return rows, evaluated


def test_gibbs_dry_run_stages_nothing(db_session):
    thermo = gibbs_records(db_session)
    before = count_reviews(db_session)
    result, row = invoke(db_session, **gibbs_request(thermo))
    rows, evaluated = _evaluated(result)
    assert row is None
    assert len(rows) == 4 and len(evaluated) == 4  # two stored G x (point, nasa7)
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    assert count_reviews(db_session) == before


def test_gibbs_commit_appends_one_review_and_changes_no_scientific_state(db_session):
    thermo = gibbs_records(db_session)
    before = thermo_inputs(thermo)
    count_before = count_reviews(db_session)
    flushed = []

    def audit(session, *_):
        flushed.extend(type(row).__name__ for row in session.new)
        assert not session.dirty and not session.deleted

    event.listen(db_session, "before_flush", audit)
    try:
        result, row = invoke(db_session, commit=True, **gibbs_request(thermo))
        db_session.flush()
    finally:
        event.remove(db_session, "before_flush", audit)
    rows, evaluated = _evaluated(result)
    assert len(rows) == 4 and len(evaluated) == 4
    assert flushed == ["RecordMachineReviewRow"]
    assert count_reviews(db_session) == count_before + 1
    assert (row.record_type.value, row.record_id, row.model, row.prompt_version) == (
        "thermo", thermo.id, "gibbs_self_consistency", "gibbs_self_consistency")
    assert row.rubric_versions_json == {"gibbs_self_consistency_v1": "1"}
    assert thermo_inputs(thermo) == before
    assert current(db_session, **gibbs_request(thermo)).state.value == "current"


def test_gibbs_check_does_not_restale_the_reviewer_review_or_d1_rows(db_session, monkeypatch):
    thermo = gibbs_records(db_session)
    digest, prompt_version, rubric_versions = _seed_reviewer_review(
        db_session, record_type="thermo", record_id=thermo.id, record_ref=thermo.public_ref,
    )
    d1, _ = invoke(db_session, commit=True, **request(thermo, "thermo"))
    db_session.flush()

    def _plan():
        return plan_record_machine_rereview(
            db_session, record_type="thermo", record_id=thermo.id,
            current_context=digest, active_prompt_version=prompt_version,
            active_rubric_versions=rubric_versions,
        )

    assert _plan().decision is MachineReviewReReviewDecision.skip_current
    assert currency(db_session, d1).state.value == "current"
    d4, first = invoke(db_session, commit=True, **gibbs_request(thermo))
    _, second = invoke(db_session, commit=True, **gibbs_request(thermo))
    db_session.flush()
    assert first.id != second.id and latest_recorded(db_session, d4).id == second.id
    assert _plan().decision is MachineReviewReReviewDecision.skip_current
    assert currency(db_session, d1).state.value == "current"
    assert latest_recorded(db_session, d1).id != second.id
    # Bumping D4's own rubric stales D4 and nothing else.
    monkeypatch.setitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, public_rubric_name(d4.rubric), "999")
    assert currency(db_session, d4).state.value == "stale"
    assert currency(db_session, d1).state.value == "current"
    assert _plan().decision is MachineReviewReReviewDecision.skip_current


def test_adding_the_gibbs_rubric_restales_no_stored_review(db_session, monkeypatch):
    """Record every existing kind of thermo review in a world WITHOUT the D4 key,
    then add the key back: nothing recorded before it existed may go stale.
    """
    from app.services.machine_review.admin_trigger import active_rubric_versions_for_record_type
    from app.services.trust.rubrics import GIBBS_SELF_CONSISTENCY_V1

    key = public_rubric_name(GIBBS_SELF_CONSISTENCY_V1)
    thermo, _, _ = setup_records(db_session)
    with_key = dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS)
    monkeypatch.delitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, key)
    without_key = dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS)
    assert set(with_key) - set(without_key) == {key}  # premise: the full recipe really changes

    reviewer_recipe_before = active_rubric_versions_for_record_type("thermo")
    digest = MachineReviewContextDigest(context_hash="f" * 64, context_schema_version="v1")
    create_record_machine_review_row(
        db_session, record_type="thermo", record_id=thermo.id,
        review=RecordMachineReview(record_type="thermo", record_ref=thermo.public_ref,
                                   status=ServiceMachineReviewStatus.machine_screened_pass,
                                   reviewed_at=datetime(2026, 9, 1), record_id=thermo.id),
        context_digest=digest, prompt_version="machine_review_v1", rubric_versions=reviewer_recipe_before,
    )
    recorded = {check: invoke(db_session, commit=True, **request(thermo, check))[0]
                for check in ("thermo", "external-cp")}
    db_session.flush()
    assert len(recorded) == 2
    assert all(currency(db_session, r).state.value == "current" for r in recorded.values())

    monkeypatch.setitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, key, with_key[key])
    assert dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS) == with_key
    reviewer_recipe_after = active_rubric_versions_for_record_type("thermo")
    assert reviewer_recipe_after == reviewer_recipe_before
    plan = plan_record_machine_rereview(
        db_session, record_type="thermo", record_id=thermo.id, current_context=digest,
        active_prompt_version="machine_review_v1", active_rubric_versions=reviewer_recipe_after,
    )
    assert plan.decision is MachineReviewReReviewDecision.skip_current
    states = {check: currency(db_session, r).state.value for check, r in recorded.items()}
    assert states == {"thermo": "current", "external-cp": "current"}


@pytest.mark.parametrize("location,field,value", [
    ("thermo", "enthalpy_reference_kind", None),
    ("point", "h_kj_mol", -51.0), ("point", "s_j_mol_k", 201.0), ("point", "g_kj_mol", -149.0),
    ("nasa", "a6", -9001.0), ("nasa", "b6", -8801.0),
])
def test_every_material_gibbs_input_changes_live_currency(db_session, location, field, value):
    thermo = gibbs_records(db_session)
    _, row = invoke(db_session, commit=True, **gibbs_request(thermo))
    db_session.flush()
    assert current(db_session, **gibbs_request(thermo)).state.value == "current"
    targets = {"thermo": thermo, "nasa": thermo.nasa, "point": thermo.points[0]}
    assert getattr(targets[location], field) != value
    setattr(targets[location], field, value)
    with db_session.no_autoflush:
        live = compare(db_session, **gibbs_request(thermo))
        assert live.digest.context_hash != row.context_hash
        assert current(db_session, **gibbs_request(thermo)).state.value == "stale"
        assert latest_recorded(db_session, live).id == row.id


def test_gibbs_hash_ignores_clocks_and_point_order(db_session):
    thermo = gibbs_records(db_session)
    baseline = compare(db_session, **gibbs_request(thermo))
    thermo.points.reverse()
    thermo.updated_at = datetime(2020, 1, 1)
    with db_session.no_autoflush:
        assert compare(db_session, **gibbs_request(thermo)).digest == baseline.digest


def test_gibbs_service_refuses_neighbours_and_grids_and_the_cli_dry_runs(db_session, monkeypatch, capsys):
    from app.api import deps
    from scripts import run_consistency_check as cli
    thermo = gibbs_records(db_session)
    with pytest.raises(ValueError, match="gibbs-self"):
        compare(db_session, **gibbs_request(thermo), temperature_grid=[500.0])
    with pytest.raises(ValueError, match="gibbs-self"):
        compare(db_session, **gibbs_request(thermo), comparison_thermo_ref=thermo.public_ref)
    commits = []

    class SessionProxy:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __getattr__(self, name):
            return getattr(db_session, name)

        def commit(self):
            commits.append(True)

        def rollback(self):
            pass

    monkeypatch.setattr(deps, "SessionLocal", SessionProxy)
    before = count_reviews(db_session)
    assert cli.main(["--check", "gibbs-self", "--target-ref", thermo.public_ref]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["committed"] is False and output["check"] == "gibbs-self"
    assert len(output["findings"]) == 4
    assert count_reviews(db_session) == before and not commits


# --------------------------------------------------------------------------- #
# D5 Kirchhoff: dry run, one append, no restale, material-input currency.
# Rows come from the thermo upload workflow (tests/services/test_phase_d_kirchhoff.py).
# --------------------------------------------------------------------------- #

KIRCHHOFF_KEY = "kirchhoff_consistency_v1"


def _kirchhoff_record(session):
    from tests.services.test_phase_d_foundation import LOW, _h_nasa7_kj
    from tests.services.test_phase_d_kirchhoff import nasa7_block, upload
    return upload(session, nasa=nasa7_block(), h298_kj_mol=_h_nasa7_kj(LOW, 298.15) - 1.5,
                  points=[{"temperature_k": 600.0, "h_kj_mol": _h_nasa7_kj(LOW, 600.0) + 5.0}])


def test_kirchhoff_dry_run_stages_nothing_and_commit_appends_one_row(db_session):
    thermo = _kirchhoff_record(db_session)
    before, inputs = count_reviews(db_session), thermo_inputs(thermo)
    result, row = invoke(db_session, **request(thermo, "kirchhoff"))
    assert row is None and not db_session.new and not db_session.dirty
    assert count_reviews(db_session) == before
    measured = [p for p in (json.loads(f.message) for f in result.findings) if "residual" in p]
    # 3 anchor pairs x {298.15, 600} + 1 increment (from 600; no point at 298.15) + 1 jump.
    assert len(measured) == 8
    # h298-nasa7 at 298.15, point-nasa7 at 600, and the NASA-7 jump.
    assert len([p for p in measured if p["reason"] is None]) == 3
    flushed = []

    def audit(session, *_):
        flushed.extend(type(r).__name__ for r in session.new)
        assert not session.dirty and not session.deleted

    event.listen(db_session, "before_flush", audit)
    try:
        committed, row = invoke(db_session, commit=True, **request(thermo, "kirchhoff"))
        db_session.flush()
    finally:
        event.remove(db_session, "before_flush", audit)
    assert flushed == ["RecordMachineReviewRow"]
    assert count_reviews(db_session) == before + 1
    assert (row.record_id, row.model, row.provider) == (thermo.id, "kirchhoff_consistency", "tckdb.scientific_checks")
    assert row.rubric_versions_json == {KIRCHHOFF_KEY: "1"}
    assert all(f.severity.value == "info" for f in committed.findings)
    assert thermo_inputs(thermo) == inputs


def test_adding_the_kirchhoff_rubric_restales_no_stored_review(db_session, monkeypatch):
    """The plan's claim, measured: rows recorded under the pre-D5 recipe stay current.

    Every reviewer-family recipe is filtered to its own record type, and every
    scientific-check row is keyed by its own rubric, so the new key reaches
    no stored row's currency. Rows are recorded with the key absent (the
    recipe as it was before D5), then classified with it present.
    """
    from app.services.machine_review.admin_trigger import (
        SUPPORTED_RECORD_TYPES,
        active_rubric_versions_for_record_type,
    )
    assert KIRCHHOFF_KEY in ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS
    with_key = {rt: active_rubric_versions_for_record_type(rt) for rt in SUPPORTED_RECORD_TYPES}
    assert len(with_key) == 6
    assert all(KIRCHHOFF_KEY not in recipe for recipe in with_key.values())

    thermo, _, _ = setup_records(db_session)
    monkeypatch.delitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, KIRCHHOFF_KEY)
    assert {rt: active_rubric_versions_for_record_type(rt) for rt in SUPPORTED_RECORD_TYPES} == with_key
    digest = MachineReviewContextDigest(context_hash="e" * 64, context_schema_version="v1")
    reviewer_recipe = active_rubric_versions_for_record_type("thermo")
    create_record_machine_review_row(
        db_session, record_type="thermo", record_id=thermo.id, context_digest=digest,
        prompt_version="reviewer_prompt_v9", rubric_versions=reviewer_recipe,
        review=RecordMachineReview(record_type="thermo", record_ref=thermo.public_ref, record_id=thermo.id,
                                   status=ServiceMachineReviewStatus.machine_screened_pass,
                                   reviewed_at=datetime(2026, 9, 1)),
    )
    d1, _ = invoke(db_session, commit=True, **request(thermo, "thermo"))
    cp.run_and_record(db_session, thermo.id)
    db_session.flush()
    monkeypatch.undo()
    assert KIRCHHOFF_KEY in ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS

    def states():
        plan = plan_record_machine_rereview(
            db_session, record_type="thermo", record_id=thermo.id, current_context=digest,
            active_prompt_version="reviewer_prompt_v9",
            active_rubric_versions=active_rubric_versions_for_record_type("thermo"))
        return (plan.decision, currency(db_session, d1).state.value,
                cp.cp_comparison_currency(db_session, thermo.id).state.value)

    expected = (MachineReviewReReviewDecision.skip_current, "current", "current")
    assert states() == expected
    k, _ = invoke(db_session, commit=True, **request(thermo, "kirchhoff"))
    db_session.flush()
    assert states() == expected
    assert currency(db_session, k).state.value == "current"


@pytest.mark.parametrize("location,field,value", [
    ("nasa", "a6", 25.0), ("nasa", "b6", 125.0), ("nasa", "t_mid", 900.0),
    ("point", "h_kj_mol", 1.0), ("thermo", "h298_kj_mol", 1.0), ("thermo", "enthalpy_reference_kind", None),
    ("thermo", "reference_pressure_bar", 2.0), ("thermo", "phase", None), ("thermo", "tmax_k", 2500.0),
    ("thermo", "h298_uncertainty_kj_mol", 0.5), ("entry", "isotope_key", "[2H]C"),
])
def test_every_material_kirchhoff_input_changes_live_currency(db_session, location, field, value):
    thermo = _kirchhoff_record(db_session)
    args = request(thermo, "kirchhoff")
    _, row = invoke(db_session, commit=True, **args)
    db_session.flush()
    assert current(db_session, **args).state.value == "current"
    targets = {"thermo": thermo, "nasa": thermo.nasa, "point": thermo.points[0], "entry": thermo.species_entry}
    with db_session.no_autoflush:
        setattr(targets[location], field, value)
        live = compare(db_session, **args)
        assert live.digest.context_hash != row.context_hash
        assert current(db_session, **args).state.value == "stale"
