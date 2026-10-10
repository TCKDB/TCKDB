"""The statmech request contract: every cross-field rule refuses the combination it names, and a valid one round-trips."""

from __future__ import annotations

import copy
import math

import pytest
from pydantic import ValidationError

from app.schemas.reads.scientific_statmech_selection import StatmechSelectionRequest
from app.services.statmech_selection.models import SelectionBounds, StatmechRequest
from app.services.statmech_selection.vocabulary import (
    OUTCOME_PRECEDENCE,
    REASON_CODES,
    DeferredTask,
    Normalization,
    Outcome,
    Task,
)

SPE = "spe_abcdefghijklmnopqrstuvwxyz"
TSE = "tse_abcdefghijklmnopqrstuvwxyz"
CG = "cg_abcdefghijklmnopqrstuvwxyz"
SMD = "smd_abcdefghijklmnopqrstuvwxyz"
SMD2 = "smd_zzzzzzzzzzzzzzzzzzzzzzzzzz"
SDET = "sdet_abcdefghijklmnopqrstuvwxyz"

THERMAL: dict = {
    "intent": "qualify",
    "task": "within_basin_thermal",
    "observable": "entropy",
    "target": {
        "kind": "species_entry",
        "species_entry_ref": SPE,
        "scope": {"kind": "basin", "conformer_group_ref": CG},
        "isotope_convention": "entry_documented_masses",
        "electronic_scope": "entry_state",
        "stereochemical_scope": "entry_identity",
    },
    "conditions": {"temperature_domain_k": {"min": 298.15, "max": 500.0}, "normalization": "internal_only"},
}
TS: dict = {
    "intent": "qualify",
    "task": "conventional_ts_transverse_model",
    "observable": "transverse_partition",
    "target": {
        "kind": "transition_state_entry",
        "transition_state_entry_ref": TSE,
        "scope": {
            "kind": "ts_determination",
            "determination_ref": SDET,
            "dividing_surface": "conventional_saddle_point",
            "reaction_coordinate_excluded": True,
        },
        "isotope_convention": "entry_documented_masses",
        "electronic_scope": "entry_state",
        "stereochemical_scope": "entry_identity",
    },
    "conditions": {"temperature_domain_k": {"min": 300.0, "max": 1000.0}},
}


def make(base: dict, **changes) -> dict:
    body = copy.deepcopy(base)
    for path, value in changes.items():
        node = body
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[k]
        if value is _DROP:
            node.pop(keys[-1], None)
        else:
            node[keys[-1]] = value
    return body


_DROP = object()


def accepts(body: dict) -> StatmechRequest:
    return StatmechSelectionRequest.model_validate(body).service_request()


def refuses(body: dict, fragment: str) -> None:
    with pytest.raises(ValidationError) as caught:
        StatmechSelectionRequest.model_validate(body)
    assert fragment in str(caught.value), str(caught.value)


def test_a_valid_request_round_trips_through_its_manifest_form():
    for body in (THERMAL, TS):
        request = accepts(body)
        again = StatmechRequest.from_dict(request.to_dict())
        assert again == request
        assert again.to_dict() == request.to_dict()
        assert request.to_dict()["bounds"] == SelectionBounds().to_dict()


def test_a_component_task_implies_its_observable_and_an_internal_model_echoes_internal_only():
    component = accepts(make(THERMAL, task="rotational_component", observable=_DROP, conditions=_DROP))
    assert component.observable.value == "rotational_characterization"
    internal = accepts(make(THERMAL, task="canonical_model", observable="internal_partition", conditions__normalization=_DROP))
    assert internal.conditions.normalization is Normalization.internal_only
    assert internal.to_dict()["conditions"]["normalization"] == "internal_only"


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"observable": "internal_partition"}, "accepts observable"),
        ({"task": "canonical_model", "observable": "entropy"}, "accepts observable"),
        ({"observable": _DROP}, "states its observable"),
        ({"conditions__temperature_domain_k": _DROP}, "thermal"),
        ({"conditions__temperature_domain_k": {"min": 500.0, "max": 300.0}}, "ordered"),
        ({"conditions__temperature_domain_k": {"min": 0.0, "max": 300.0}}, "positive"),
        ({"conditions__temperature_domain_k": {"min": -5.0, "max": 300.0}}, "positive"),
        ({"conditions__normalization": "fixed_volume"}, "needs volume_m3"),
        ({"conditions__pressure_pa": 1e5}, "does not belong to normalization internal_only"),
        ({"conditions__energy_domain_kj_mol": {"min": 0.0, "max": 10.0}}, "states its energy_reference"),
        ({"intent": "protocol_preferred"}, "comparison_objective"),
        ({"comparison_objective": "physical_accuracy"}, "compares nothing"),
        ({"result_mode": "first"}, "contradictory"),
        ({"target__scope": {"kind": "ts_determination", "determination_ref": SDET}}, "takes a"),
        ({"target__scope": {"kind": "explicit_equilibrium_ensemble", "ensemble_declaration_ref": SMD}}, "takes a"),
        ({"target__scope": {"kind": "basin"}}, "conformer_group_ref"),
        ({"target__scope": {"kind": "basin", "conformer_group_ref": SPE}}, "pattern"),
        ({"target__species_entry_ref": TSE}, "pattern"),
        ({"bounds": {"candidates": 10_000}}, "Extra inputs"),
        ({"limit": 5}, "Extra inputs"),
        ({"offset": 5}, "Extra inputs"),
        ({"task": "within_basin_thermall"}, "Input should be"),
        ({"observable": "gibbs_energy"}, "accepts observable"),
        ({"contract_version": "statmech-selection-v2"}, "statmech-selection-v1"),
    ],
)
def test_a_contradictory_or_malformed_thermal_request_is_refused(changes, fragment):
    refuses(make(THERMAL, **changes), fragment)


def test_a_supported_task_refuses_an_energy_domain_even_with_its_reference():
    body = make(
        THERMAL,
        conditions__energy_domain_kj_mol={"min": 0.0, "max": 10.0},
        conditions__energy_reference={"zero": "e0", "zpe": "included"},
    )
    refuses(body, "deferred energy-resolved tasks")


def test_infinite_and_nan_numbers_are_refused():
    for bad in (math.inf, math.nan):
        body = make(THERMAL, conditions__temperature_domain_k={"min": 300.0, "max": bad})
        body["conditions"]["temperature_domain_k"]["max"] = bad
        with pytest.raises(ValidationError):
            StatmechSelectionRequest.model_validate(body)


def test_a_single_temperature_point_is_an_ordered_domain():
    assert accepts(make(THERMAL, conditions__temperature_domain_k={"min": 298.15, "max": 298.15}))


FULL = make(
    THERMAL,
    task="canonical_model",
    observable="full_partition",
    conditions={
        "temperature_domain_k": {"min": 300.0, "max": 600.0},
        "normalization": "standard_state_pressure",
        "pressure_pa": 100000.0,
        "energy_reference": {"zero": "e0", "zpe": "included"},
    },
)


def test_a_full_partition_model_states_its_translational_convention_and_its_energy_zero():
    request = accepts(FULL)
    assert request.conditions.pressure_pa == 100000.0
    refuses(make(FULL, conditions__normalization="internal_only", conditions__pressure_pa=_DROP), "translational normalization")
    refuses(make(FULL, conditions__normalization=_DROP, conditions__pressure_pa=_DROP), "translational normalization")
    refuses(make(FULL, conditions__energy_reference=_DROP), "energy_reference")
    refuses(make(FULL, conditions__pressure_pa=_DROP), "needs pressure_pa")
    refuses(make(FULL, conditions__volume_m3=1e-3), "does not belong to normalization")
    refuses(make(FULL, conditions__pressure_pa=-1.0), "positive")


def test_an_internal_partition_model_never_needs_and_never_accepts_a_pressure():
    body = make(THERMAL, task="canonical_model", observable="internal_partition")
    assert accepts(body)
    refuses(make(body, conditions__normalization="standard_state_pressure", conditions__pressure_pa=1e5), "internal contribution")
    refuses(make(body, conditions__pressure_pa=1e5), "does not belong to normalization internal_only")


def test_a_zpe_interpretation_states_its_energy_reference():
    body = make(THERMAL, task="zpe_component", observable=_DROP, conditions=_DROP)
    refuses(body, "energy_reference")
    ok = make(body, conditions={"energy_reference": {"zero": "electronic", "zpe": "excluded"}})
    assert accepts(ok).observable.value == "zero_point_energy_interpretation"


def test_a_component_claim_refuses_a_normalization_it_does_not_use():
    refuses(make(THERMAL, task="rotational_component", observable=_DROP), "component claim")


def test_ensemble_scope_needs_the_membership_declaration_not_a_filtered_context():
    body = make(
        THERMAL,
        task="canonical_model",
        observable="internal_partition",
        target__scope={"kind": "explicit_equilibrium_ensemble", "ensemble_declaration_ref": SMD},
    )
    assert accepts(body).target.scope.ensemble_declaration_ref == SMD
    refuses(make(body, target__scope={"kind": "explicit_equilibrium_ensemble"}), "ensemble_declaration_ref")
    refuses(make(body, target__scope={"kind": "explicit_equilibrium_ensemble", "ensemble_declaration_ref": CG}), "pattern")
    # within-basin thermal takes an atom or a basin only
    refuses(make(body, task="within_basin_thermal", observable="entropy"), "takes a")


def test_a_ts_transverse_request_needs_a_ts_owner_a_determination_a_dividing_surface_and_a_temperature_domain():
    assert accepts(TS)
    refuses(make(TS, target__scope__dividing_surface=_DROP), "dividing_surface")
    refuses(make(TS, target__scope__reaction_coordinate_excluded=False), "dividing_surface")
    refuses(make(TS, conditions=_DROP), "thermal")
    species_body = make(
        TS,
        target={**THERMAL["target"]},
    )
    refuses(species_body, "takes a")
    refuses(make(TS, observable="entropy"), "accepts observable")
    refuses(make(TS, target__scope={"kind": "atom"}), "takes a")


def test_protocol_preference_states_its_objective_and_a_fidelity_objective_its_pinned_reference():
    pref = make(THERMAL, intent="protocol_preferred", comparison_objective="expected_accuracy")
    assert accepts(pref).objective.value == "expected_accuracy"
    refuses(make(pref, comparison_objective="model_fidelity"), "reference_model_ref")
    assert accepts(make(pref, comparison_objective="model_fidelity", reference_model_ref=SMD)).reference_model_ref == SMD
    refuses(make(pref, reference_model_ref=SMD), "reference_model_ref")
    refuses(make(pref, comparison_objective="best"), "Input should be")


def test_populations_are_all_authorized_explicit_revisions_or_a_release_never_a_page_prefix():
    assert accepts(THERMAL).population.kind.value == "authorized_matching_models"
    explicit = make(THERMAL, population={"kind": "model_revisions", "model_refs": [SMD, SMD2]})
    assert accepts(explicit).population.model_refs == (SMD, SMD2)
    refuses(make(THERMAL, population={"kind": "model_revisions", "model_refs": []}), "at least 1")
    refuses(make(THERMAL, population={"kind": "model_revisions", "model_refs": [SMD, SMD]}), "named twice")
    refuses(make(THERMAL, population={"kind": "page", "limit": 10}), "does not match any of the expected tags")
    assert accepts(make(THERMAL, population={"kind": "release", "release_ref": "rel_abcdefghijklmnopqrstuvwxyz"})).population.release_ref


@pytest.mark.parametrize("task", [t.value for t in DeferredTask])
def test_a_recognised_deferred_task_is_a_valid_request_answered_later_with_unsupported_task(task):
    body = make(THERMAL, task=task, observable=_DROP, conditions=_DROP)
    if task == "higher_order_ts_model":
        body = make(TS, task=task, observable=_DROP, conditions=_DROP)
    request = accepts(body)
    assert request.deferred and request.task is DeferredTask(task)


def test_an_energy_domain_is_accepted_for_a_deferred_task_only_with_its_explicit_reference():
    body = make(THERMAL, task="density_of_states", observable="density_of_states", conditions=_DROP)
    ok = make(body, conditions={"energy_domain_kj_mol": {"min": 0.0, "max": 200.0}, "energy_reference": {"zero": "e0", "zpe": "included"}})
    assert accepts(ok).conditions.energy_domain_kj_mol == (0.0, 200.0)
    refuses(make(body, conditions={"energy_domain_kj_mol": {"min": 0.0, "max": 200.0}}), "energy_reference")
    refuses(make(body, conditions={"energy_domain_kj_mol": {"min": 200.0, "max": 0.0}, "energy_reference": {"zero": "e0", "zpe": "included"}}), "ordered")


def test_a_higher_order_ts_task_needs_a_ts_owner():
    body = make(THERMAL, task="higher_order_ts_model", observable=_DROP, conditions=_DROP)
    refuses(body, "transition_state_entry target")


def test_every_task_has_exactly_one_home_and_no_token_is_both_supported_and_deferred():
    assert not {t.value for t in Task} & {t.value for t in DeferredTask}


def test_outcomes_and_reason_codes_are_closed_lists_with_a_stated_precedence():
    assert {o.value for o in Outcome} >= {
        "qualified_models", "no_candidates", "no_applicable_candidate", "unresolved_applicability",
        "missing_required_data", "unsupported_task", "unsupported_model", "evidence_conflict",
        "unresolved_comparability", "policy_conflict", "sole_eligible_candidate", "policy_preferred",
        "incomparable_alternatives",
    }
    assert len(Outcome) == 13
    assert len(set(REASON_CODES)) == len(REASON_CODES)
    assert OUTCOME_PRECEDENCE[0].startswith("bounded_refusal") and OUTCOME_PRECEDENCE[-1].startswith("preference")


def test_the_normalised_request_refuses_an_ensemble_without_a_declaration_on_its_own():
    """The service-level rule holds without the wire schema: a filtered context is not membership."""
    from app.services.statmech_selection.models import Scope
    from app.services.statmech_selection.vocabulary import ScopeKind

    with pytest.raises(ValueError, match="membership declaration"):
        Scope(kind=ScopeKind.explicit_equilibrium_ensemble)
    with pytest.raises(ValueError, match="does not belong"):
        Scope(kind=ScopeKind.atom, conformer_group_ref=CG)
    with pytest.raises(ValueError, match="needs conformer_group_ref"):
        Scope(kind=ScopeKind.basin)


def test_bounds_are_the_published_defaults_and_not_a_wire_field():
    b = SelectionBounds()
    assert (b.candidates, b.nested_rows, b.dependency_depth, b.manifest_bytes) == (500, 25_000, 8, 10 * 1024 * 1024)
    assert "bounds" not in StatmechSelectionRequest.model_fields
    assert StatmechSelectionRequest.model_config["extra"] == "forbid"


def test_a_standard_state_entropy_needs_no_energy_reference():
    """S and Cp do not depend on the energy zero or on zero-point inclusion, so a request for them never states one."""
    body = make(
        THERMAL,
        conditions={
            "temperature_domain_k": {"min": 298.15, "max": 298.15},
            "normalization": "standard_state_pressure",
            "pressure_pa": 100000.0,
        },
    )
    request = accepts(body)
    assert request.conditions.energy_reference is None and request.conditions.pressure_pa == 100000.0
    refuses(make(body, conditions__pressure_pa=_DROP), "needs pressure_pa")
    assert accepts(make(body, observable="heat_capacity")).observable.value == "heat_capacity"


def test_a_component_claim_accepts_a_temperature_domain_it_does_not_require():
    """A component is qualified without a temperature (the spec's table says so) and accepts one if it is stated."""
    bare = make(THERMAL, task="vibrational_component", observable=_DROP, conditions=_DROP)
    assert accepts(bare).conditions.temperature_domain_k is None
    stated = make(bare, conditions={"temperature_domain_k": {"min": 300.0, "max": 600.0}})
    assert accepts(stated).conditions.temperature_domain_k == (300.0, 600.0)


@pytest.mark.parametrize(
    "task, observable, ok",
    [
        ("density_of_states", "density_of_states", True),
        ("density_of_states", "entropy", False),
        ("sum_of_states", "density_of_states", False),
        ("thermochemical_formation", "gibbs_energy", True),
        ("thermochemical_formation", "entropy", False),
        ("numerical_evaluation", "helmholtz_energy", True),
        ("numerical_evaluation", "population", False),
        ("prepared_population", "population", True),
        ("trapped_population", "enthalpy", False),
    ],
)
def test_a_deferred_task_still_validates_its_observable_pairing(task, observable, ok):
    body = make(THERMAL, task=task, observable=observable, conditions=_DROP)
    if ok:
        assert accepts(body).deferred
    else:
        refuses(body, "accepts observable")


@pytest.mark.parametrize(
    "ref",
    [
        "spe_abcdefghijklmnopqrstuvwxy",  # 25 characters
        "spe_abcdefghijklmnopqrstuvwxyz2",  # 27 characters
        "spe_ABCDEFGHIJKLMNOPQRSTUVWXYZ",  # not lowercase
        "spe_0123456789abcdefghijklmnop",  # digits outside the base32 alphabet
        "tse_abcdefghijklmnopqrstuvwxyz",  # another record type's prefix
        "17",  # a database id
    ],
)
def test_a_public_ref_has_its_registered_prefix_and_its_exact_base32_body(ref):
    refuses(make(THERMAL, target__species_entry_ref=ref), "pattern")


def test_the_ref_grammar_comes_from_the_public_ref_registry():
    from app.schemas.reads.scientific_statmech_selection import _ref_pattern
    from app.services.public_refs import PREFIXES, PUBLIC_REF_BODY_LEN

    assert PREFIXES["StatmechModelDeclaration"] == "smd"
    assert _ref_pattern("SpeciesEntry") == f"^{PREFIXES['SpeciesEntry']}_[a-z2-7]{{{PUBLIC_REF_BODY_LEN}}}$"
