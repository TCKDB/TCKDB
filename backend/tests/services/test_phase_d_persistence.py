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
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, public_rubric_name
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
