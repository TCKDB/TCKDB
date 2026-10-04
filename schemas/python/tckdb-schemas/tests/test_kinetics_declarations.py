"""The kinetics determination, applicability and protocol declarations, as the wire package states them.

Each test pins one refusal or one distinction a later applicability and method-aware
selection relies on: a declaration that contradicts itself, a declaration that contradicts the
column of the record it sits on, an unknown field or version, an unstated fact staying
unstated, and the one rule the workflows and the client builder share
(:func:`kinetics_declaration_error`) re-deriving every check from the payload itself.
"""

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.kinetics_declarations import (
    W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
    W_KINETICS_DECLARATION_INVALID,
    W_KINETICS_DECLARATION_VERSION_UNSUPPORTED,
    W_KINETICS_DETERMINATION_INVALID,
    KineticsApplicabilityDeclaration,
    KineticsDeterminationDeclaration,
    KineticsProtocolDeclaration,
    KineticsRecordFacts,
    StoredKineticsApplicabilityDeclaration,
    StoredKineticsProtocolDeclaration,
    kinetics_applicability_error,
    kinetics_declaration_context,
    kinetics_declaration_error,
    kinetics_record_facts,
)

H2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
N2 = {"smiles": "N#N", "charge": 0, "multiplicity": 1}
HE = {"smiles": "[He]", "charge": 0, "multiplicity": 1}


def _coded(exc_info) -> CodedValidationError:
    return exc_info.value.errors()[0]["ctx"]["error"]


def _applicability(**extra) -> dict:
    return {"version": 1, "claim_origin": "source_publication", **extra}


def _facts(**extra) -> KineticsRecordFacts:
    base = {
        "direction": "forward",
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "is_third_body": False,
        "n_reactants": 2,
        "pressure_context": None,
        "pressure_bar": None,
        "has_network_kinetics": False,
        "has_falloff": False,
        "has_third_body_efficiencies": False,
    }
    base.update(extra)
    return KineticsRecordFacts(**base)


# ---------------------------------------------------------------------------
# determination
# ---------------------------------------------------------------------------


def test_a_determination_by_key_states_its_target():
    declaration = KineticsDeterminationDeclaration(
        key="burke-2012-set-A", target_kind="whole_reaction", representation_role="complete"
    )
    assert declaration.key == "burke-2012-set-A"
    assert declaration.target_kind.value == "whole_reaction"


REF = "kdet_" + "a" * 26
TSE = "tse_" + "a" * 26


@pytest.mark.parametrize(
    "body",
    [
        {"representation_role": "complete"},
        {"determination_ref": REF, "key": "k", "target_kind": "whole_reaction", "representation_role": "complete"},
        {"determination_ref": REF, "key": "k", "representation_role": "complete"},
        {"determination_ref": REF, "target_kind": "whole_reaction", "representation_role": "complete"},
        {"determination_ref": REF, "transition_state_entry_ref": TSE, "representation_role": "complete"},
        {"key": "k", "representation_role": "complete"},
        {"key": "k", "target_kind": "whole_reaction", "transition_state_entry_ref": TSE, "representation_role": "complete"},
        {"key": "k", "target_kind": "whole_reaction", "network_ref": "net_x", "channel_key": "c", "representation_role": "complete"},
        {"key": "k", "target_kind": "resolved_channel", "representation_role": "complete"},
        {
            "key": "k",
            "target_kind": "resolved_channel",
            "transition_state_entry_ref": TSE,
            "network_ref": "net_x",
            "channel_key": "c",
            "representation_role": "complete",
        },
        {"key": "k", "target_kind": "resolved_channel", "network_ref": "net_x", "representation_role": "complete"},
        {"key": "k", "target_kind": "resolved_channel", "channel_key": "c", "representation_role": "complete"},
    ],
    ids=[
        "neither", "ref_and_key_and_kind", "ref_and_key", "ref_with_kind", "ref_with_locator", "key_without_kind",
        "whole_names_ts", "whole_names_channel", "channel_names_nothing", "channel_names_both",
        "half_a_channel", "other_half",
    ],
)
def test_a_determination_is_exactly_one_locator_with_a_target_that_names_what_its_kind_needs(body):
    with pytest.raises(ValidationError) as exc:
        KineticsDeterminationDeclaration(**body)
    assert _coded(exc).code == W_KINETICS_DETERMINATION_INVALID


def test_a_cited_determination_joins_by_ref_alone():
    declaration = KineticsDeterminationDeclaration(determination_ref=REF, representation_role="additive_component")
    assert declaration.target_kind is None
    assert declaration.representation_role.value == "additive_component"


def test_a_resolved_channel_names_a_transition_state_or_a_network_channel():
    assert KineticsDeterminationDeclaration(
        key="k", target_kind="resolved_channel", transition_state_entry_ref=TSE, representation_role="complete"
    )
    assert KineticsDeterminationDeclaration(
        key="k",
        target_kind="resolved_channel",
        network_ref="net_" + "a" * 26,
        channel_key="W1->P1",
        representation_role="complete",
    )


def test_an_unknown_representation_role_or_target_kind_is_refused():
    with pytest.raises(ValidationError):
        KineticsDeterminationDeclaration(key="k", target_kind="whole_reaction", representation_role="partial")
    with pytest.raises(ValidationError):
        KineticsDeterminationDeclaration(key="k", target_kind="channel", representation_role="complete")


# ---------------------------------------------------------------------------
# applicability: shape
# ---------------------------------------------------------------------------


def test_an_applicability_declaration_needs_a_claim_origin_and_a_claim():
    with pytest.raises(ValidationError):
        KineticsApplicabilityDeclaration(version=1, phase="gas")  # no claim_origin
    with pytest.raises(ValidationError):
        KineticsApplicabilityDeclaration(version=1, claim_origin="source_publication")  # nothing stated
    assert KineticsApplicabilityDeclaration(version=1, claim_origin="depositor_interpretation", phase="gas")


@pytest.mark.parametrize("version", [True, "1", 1.0, 2, 0])
def test_applicability_version_is_exactly_the_integer_one(version):
    with pytest.raises(ValidationError):
        KineticsApplicabilityDeclaration.model_validate({**_applicability(phase="gas"), "version": version})


def test_an_unsupported_applicability_version_carries_its_code():
    with pytest.raises(ValidationError) as exc:
        KineticsApplicabilityDeclaration.model_validate({**_applicability(phase="gas"), "version": 2})
    error = _coded(exc)
    assert error.code == W_KINETICS_DECLARATION_VERSION_UNSUPPORTED
    assert error.context["field"] == "applicability.version"


def test_unknown_applicability_fields_and_values_are_refused():
    for body in (
        _applicability(phase="gas", universally_valid=True),
        _applicability(phase="liquid"),
        _applicability(reaction_order=0),
        _applicability(reaction_order=5),
    ):
        with pytest.raises(ValidationError):
            KineticsApplicabilityDeclaration.model_validate(body)


def test_a_pressure_domain_is_for_pressure_dependent_models_with_both_bounds_ordered():
    ok = {"pressure_dependence": "pressure_dependent", "pressure_domain_min_bar": 0.01, "pressure_domain_max_bar": 100}
    assert KineticsApplicabilityDeclaration.model_validate(_applicability(**ok))
    for patch in (
        {"pressure_domain_max_bar": None},
        {"pressure_domain_min_bar": 10, "pressure_domain_max_bar": 1},
        {"pressure_dependence": "fixed_pressure"},
        {"pressure_dependence": None},
        {"pressure_domain_min_bar": 0},
        {"pressure_domain_max_bar": float("inf")},
    ):
        with pytest.raises(ValidationError):
            KineticsApplicabilityDeclaration.model_validate(_applicability(**{**ok, **patch}))


def _with_colliders(**fields):
    return KineticsApplicabilityDeclaration.model_validate(_applicability(**fields))


def test_colliders_state_what_their_kind_needs_and_nothing_else():
    assert _with_colliders(collider_kind="not_dependent")
    assert _with_colliders(collider_kind="specified_collider", colliders=[{"species": N2}])
    assert _with_colliders(collider_kind="composition_dependent", default_third_body_efficiency=1.0)
    for body in (
        {"collider_kind": "specified_collider"},
        {"collider_kind": "specified_collider", "colliders": [{"species": N2}, {"species": HE}]},
        {"collider_kind": "specified_collider", "colliders": [{"species": N2, "mole_fraction": 1.0}]},
        {"collider_kind": "specified_collider", "colliders": [{}]},
        {"collider_kind": "specified_collider", "colliders": [{"species_ref": "spc_a"}]},
        {"collider_kind": "not_dependent", "colliders": [{"species": N2}]},
        {"collider_kind": "composition_dependent", "colliders": [{"species": N2}]},
        {"collider_kind": "not_dependent", "default_third_body_efficiency": 1.0},
        {"collider_kind": "composition_dependent", "default_third_body_efficiency": -1.0},
        # Colliders without a kind state nothing a reader could place.
        {"phase": "gas", "colliders": [{"species": N2}]},
        {"phase": "gas", "default_third_body_efficiency": 1.0},
    ):
        with pytest.raises(ValidationError):
            _with_colliders(**body)


def _mixture(*fractions: float, species=(N2, HE, H2)) -> dict:
    return {
        "collider_kind": "fixed_mixture",
        "colliders": [{"species": species[i], "mole_fraction": fraction} for i, fraction in enumerate(fractions)],
    }


def test_a_mixture_sums_to_one_within_the_network_tolerance_and_is_never_renormalised():
    assert _with_colliders(**_mixture(0.79, 0.21))
    assert _with_colliders(**_mixture(0.79 + 5e-10, 0.21))  # inside 1e-9
    for body in (_mixture(0.79, 0.20), _mixture(0.79 + 5e-9, 0.21), _mixture(1.0), _mixture(0.5, 0.5, 0.5)):
        with pytest.raises(ValidationError):
            _with_colliders(**body)


def test_a_mixture_needs_a_fraction_for_every_component_and_none_twice():
    twice = {
        "collider_kind": "fixed_mixture",
        "colliders": [{"species": N2, "mole_fraction": 0.5}, {"species": N2, "mole_fraction": 0.5}],
    }
    missing = {"collider_kind": "fixed_mixture", "colliders": [{"species": N2, "mole_fraction": 1.0}, {"species": HE}]}
    for body in (twice, missing):
        with pytest.raises(ValidationError):
            _with_colliders(**body)
    for fraction in (0.0, -0.1, 1.5):
        with pytest.raises(ValidationError):
            _with_colliders(**_mixture(fraction, 1 - fraction))


def test_the_stored_form_names_species_by_ref_and_round_trips():
    stored = StoredKineticsApplicabilityDeclaration.model_validate(
        _applicability(
            collider_kind="fixed_mixture",
            colliders=[
                {"species_ref": "spc_" + "a" * 26, "mole_fraction": 0.79},
                {"species_ref": "spc_" + "b" * 26, "mole_fraction": 0.21},
            ],
        )
    )
    dumped = stored.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    assert dumped["colliders"][0] == {"species_ref": "spc_" + "a" * 26, "mole_fraction": 0.79}
    with pytest.raises(ValidationError):  # a species content block is not a stored form
        StoredKineticsApplicabilityDeclaration.model_validate(
            _applicability(collider_kind="specified_collider", colliders=[{"species": N2}])
        )


# ---------------------------------------------------------------------------
# applicability against the record's own columns
# ---------------------------------------------------------------------------


def _error(applicability: dict, facts: KineticsRecordFacts, **kw):
    return kinetics_applicability_error(KineticsApplicabilityDeclaration.model_validate(applicability), facts, **kw)


def test_an_unstated_column_is_not_a_contradiction():
    # A null pressure context contradicts nothing: the declaration is the only statement.
    assert _error(_applicability(pressure_dependence="independent"), _facts()) is None
    assert _error(_applicability(pressure_dependence="high_pressure_limit"), _facts()) is None


def _pressure(dependence: str) -> dict:
    return _applicability(pressure_dependence=dependence)


@pytest.mark.parametrize(
    "applicability,facts",
    [
        (_pressure("independent"), _facts(pressure_context="high_p_limit")),
        (_pressure("independent"), _facts(pressure_context="apparent_at_pressure", pressure_bar=1.0)),
        (_pressure("independent"), _facts(model_kind="plog")),
        (_pressure("high_pressure_limit"), _facts(model_kind="troe", has_falloff=True)),
        (_pressure("fixed_pressure"), _facts(model_kind="chebyshev", pressure_context="apparent_at_pressure", pressure_bar=1.0)),
        (_pressure("independent"), _facts(has_network_kinetics=True)),
        (_pressure("high_pressure_limit"), _facts(pressure_context="apparent_at_pressure", pressure_bar=1.0)),
        (_pressure("fixed_pressure"), _facts()),
        (_pressure("fixed_pressure"), _facts(pressure_context="apparent_at_pressure")),
        (_pressure("pressure_dependent"), _facts(pressure_context="high_p_limit")),
        (_pressure("pressure_dependent"), _facts(pressure_context="apparent_at_pressure", pressure_bar=1.0)),
        (_pressure("pressure_dependent"), _facts()),
    ],
    ids=[
        "independent_vs_hpl_context", "independent_vs_fixed_context", "independent_vs_plog", "hpl_vs_troe",
        "fixed_vs_chebyshev", "independent_vs_network", "hpl_vs_fixed_context", "fixed_without_context",
        "fixed_without_bar", "pd_vs_hpl_context", "pd_vs_fixed_context", "pd_vs_plain_arrhenius",
    ],
)
def test_pressure_claims_that_contradict_the_record_are_refused(applicability, facts):
    result = _error(applicability, facts)
    assert result is not None and result[0] == W_KINETICS_DECLARATION_CONTRADICTS_RECORD
    assert result[1].startswith("applicability.pressure_dependence")


@pytest.mark.parametrize(
    "applicability,facts",
    [
        (_pressure("independent"), _facts()),
        (_pressure("high_pressure_limit"), _facts(pressure_context="high_p_limit")),
        (_pressure("fixed_pressure"), _facts(pressure_context="apparent_at_pressure", pressure_bar=1.0)),
        (_pressure("pressure_dependent"), _facts(model_kind="plog")),
        (_pressure("pressure_dependent"), _facts(model_kind="chebyshev", pressure_context="pressure_dependent")),
        (_pressure("pressure_dependent"), _facts(has_network_kinetics=True)),
        (_pressure("pressure_dependent"), _facts(pressure_context="pressure_dependent")),
    ],
)
def test_pressure_claims_that_agree_with_the_record_are_accepted(applicability, facts):
    assert _error(applicability, facts) is None


def test_the_coefficient_basis_agrees_with_the_third_body_flag_and_the_collider():
    kernel = _applicability(coefficient_basis="third_body_kernel")
    assert _error(kernel, _facts(is_third_body=True, n_reactants=2)) is None
    assert _error(kernel, _facts(is_third_body=False)) is not None
    assert _error(kernel, _facts(is_third_body=True, model_kind="plog")) is not None
    elementary = _applicability(coefficient_basis="elementary_coefficient")
    assert _error(elementary, _facts(is_third_body=False)) is None
    assert _error(elementary, _facts(is_third_body=True)) is not None
    effective = _applicability(coefficient_basis="composition_effective_coefficient", **_mixture(0.79, 0.21))
    assert _error(effective, _facts()) is None
    wrong = _applicability(
        coefficient_basis="composition_effective_coefficient", collider_kind="composition_dependent"
    )
    assert _error(wrong, _facts(is_third_body=True)) is not None
    mixture_as_kernel = _applicability(coefficient_basis="third_body_kernel", **_mixture(0.79, 0.21))
    assert _error(mixture_as_kernel, _facts(is_third_body=True)) is not None


def test_collider_kinds_agree_with_the_third_body_treatment_of_the_record():
    assert _error(_applicability(collider_kind="not_dependent"), _facts()) is None
    assert _error(_applicability(collider_kind="not_dependent"), _facts(is_third_body=True)) is not None
    assert _error(_applicability(collider_kind="not_dependent"), _facts(has_falloff=True, model_kind="troe")) is not None
    assert _error(_applicability(collider_kind="not_dependent"), _facts(has_third_body_efficiencies=True)) is not None
    composition = _applicability(collider_kind="composition_dependent")
    assert _error(composition, _facts(is_third_body=True)) is None
    assert _error(composition, _facts(model_kind="troe", has_falloff=True)) is None
    assert _error(composition, _facts()) is not None
    # Unknown facts skip the rule rather than guessing.
    assert _error(composition, _facts(is_third_body=None, has_falloff=None, has_third_body_efficiencies=None)) is None


def test_the_declared_order_matches_the_reaction_and_a_third_body_adds_one():
    assert _error(_applicability(reaction_order=2), _facts(n_reactants=2)) is None
    assert _error(_applicability(reaction_order=3), _facts(n_reactants=2, is_third_body=True)) is None
    assert _error(_applicability(reaction_order=3), _facts(n_reactants=2)) is not None
    assert _error(_applicability(reaction_order=2), _facts(n_reactants=2, is_third_body=True)) is not None
    # A falloff record's coefficient keeps the order of its reactants.
    assert _error(_applicability(reaction_order=2), _facts(n_reactants=2, model_kind="troe", has_falloff=True, is_third_body=True)) is None
    # Order is unknown without a reactant count: nothing to compare.
    assert _error(_applicability(reaction_order=3), _facts(n_reactants=None)) is None


def test_the_order_follows_the_direction_the_coefficient_describes():
    # H2 -> 2 H: one reactant, two products.
    sides = {"n_reactants": 1, "n_products": 2}
    assert _error(_applicability(reaction_order=1), _facts(direction="forward", **sides)) is None
    assert _error(_applicability(reaction_order=2), _facts(direction="forward", **sides)) is not None
    assert _error(_applicability(reaction_order=2), _facts(direction="reverse", **sides)) is None
    assert _error(_applicability(reaction_order=1), _facts(direction="reverse", **sides)) is not None
    # A third body adds one on whichever side is the rate's own.
    assert _error(_applicability(reaction_order=3), _facts(direction="reverse", is_third_body=True, **sides)) is None
    # A net rate, or one that does not say, has no order to compare: skipped, never assumed.
    for direction in ("net", None):
        for order in (1, 2, 3):
            assert _error(_applicability(reaction_order=order), _facts(direction=direction, **sides)) is None
    # The product count is unknown for a request that does not carry one: nothing to compare.
    assert _error(_applicability(reaction_order=3), _facts(direction="reverse", n_reactants=1, n_products=None)) is None


def test_the_declared_scope_agrees_with_the_determination_target():
    declared = _applicability(scope="resolved_channel")
    assert _error(declared, _facts(), determination_target_kind="resolved_channel") is None
    result = _error(declared, _facts(), determination_target_kind="whole_reaction")
    assert result is not None and result[0] == W_KINETICS_DECLARATION_CONTRADICTS_RECORD
    assert result[1].startswith("applicability.scope")
    assert _error(declared, _facts()) is None  # no determination, nothing to compare


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


def test_a_protocol_must_state_something_and_distinguishes_no_departures_from_unstated():
    with pytest.raises(ValidationError):
        KineticsProtocolDeclaration(version=1)
    unstated = KineticsProtocolDeclaration(version=1, method_kind="experimental")
    none_declared = KineticsProtocolDeclaration(version=1, departures=[])
    named = KineticsProtocolDeclaration(version=1, departures=["geometry"])
    assert unstated.departures is None
    assert none_declared.departures == []
    assert len(named.departures) == 1
    stored = StoredKineticsProtocolDeclaration.model_validate({"version": 1, "departures": []})
    assert stored.model_dump(mode="json", exclude_none=True, exclude_defaults=True)["departures"] == []


@pytest.mark.parametrize("version", [True, "1", 1.0, 2])
def test_protocol_version_is_exactly_the_integer_one(version):
    with pytest.raises(ValidationError):
        KineticsProtocolDeclaration.model_validate({"version": version, "method_kind": "experimental"})


def test_an_unsupported_protocol_version_carries_its_code():
    with pytest.raises(ValidationError) as exc:
        KineticsProtocolDeclaration.model_validate({"version": 2, "method_kind": "experimental"})
    error = _coded(exc)
    assert error.code == W_KINETICS_DECLARATION_VERSION_UNSUPPORTED
    assert error.context["field"] == "protocol.version"


def test_protocol_refuses_inconsistent_unknown_and_empty_content():
    for body in (
        {"version": 1, "method_kind": "other"},
        {"version": 1, "method_kind": "experimental", "method_other_name": "x"},
        {"version": 1, "method_kind": "experimental", "unknown_block": {}},
        {"version": 1, "barrier_basis": "free_energy"},
        {"version": 1, "supporting_calculations": [{"purpose": "geometry"}]},
        {"version": 1, "supporting_calculations": [{"calculation_key": "a", "calculation_ref": "calc_x", "purpose": "geometry"}]},
        {"version": 1, "supporting_calculations": [{"calculation_key": "a", "purpose": "magic"}]},
        {"version": 1, "supporting_calculations": [{"calculation_key": "a", "purpose": "geometry"}] * 2},
    ):
        with pytest.raises(ValidationError):
            KineticsProtocolDeclaration.model_validate(body)
    assert KineticsProtocolDeclaration(version=1, method_kind="other", method_other_name="trajectory study")
    assert KineticsProtocolDeclaration(version=1, departures=["geometry", "tunneling"])


def test_the_same_calculation_may_serve_two_purposes():
    declaration = KineticsProtocolDeclaration(
        version=1,
        supporting_calculations=[
            {"calculation_key": "ts-sp", "purpose": "electronic_energy"},
            {"calculation_key": "ts-sp", "purpose": "geometry"},
        ],
    )
    assert len(declaration.supporting_calculations) == 2


# ---------------------------------------------------------------------------
# the shared producer rule re-derives everything from the payload
# ---------------------------------------------------------------------------

BASE = {
    "direction": "forward",
    "scientific_origin": "computed",
    "model_kind": "modified_arrhenius",
    "is_third_body": False,
    "reaction": {"reactants": [1, 2], "products": [1]},
}
DETERMINATION = {"key": "run-1", "target_kind": "whole_reaction", "representation_role": "complete"}


def test_a_payload_that_declares_nothing_is_never_refused():
    assert kinetics_declaration_error(BASE) is None
    assert kinetics_declaration_error({**BASE, "direction": None}) is None


def test_the_rule_runs_on_plain_dicts_and_on_unvalidated_models():
    # ``model_construct`` skips every validator; the rule re-derives each check from the data.
    unvalidated = KineticsDeterminationDeclaration.model_construct(
        determination_ref=None, key="k", target_kind=None, representation_role="complete"
    )
    code, _ = kinetics_declaration_error({**BASE, "determination": unvalidated}, has_source=True)
    assert code == W_KINETICS_DETERMINATION_INVALID
    bad_target = KineticsDeterminationDeclaration.model_construct(
        determination_ref=None,
        key="k",
        target_kind="whole_reaction",
        transition_state_entry_ref="tse_x",
        representation_role="complete",
    )
    code, _ = kinetics_declaration_error({**BASE, "determination": bad_target}, has_source=True)
    assert code == W_KINETICS_DETERMINATION_INVALID
    bad_role = KineticsDeterminationDeclaration.model_construct(
        determination_ref="kdet_x", key=None, target_kind=None, representation_role="partial"
    )
    code, _ = kinetics_declaration_error({**BASE, "determination": bad_role}, has_source=True)
    assert code == W_KINETICS_DETERMINATION_INVALID


def test_joining_a_determination_needs_a_direction_and_a_source():
    code, message = kinetics_declaration_error({**BASE, "direction": None, "determination": DETERMINATION}, has_source=True)
    assert code == W_KINETICS_DETERMINATION_INVALID
    assert kinetics_declaration_context(code, message) == {"field": "determination", "missing": "direction"}
    code, message = kinetics_declaration_error({**BASE, "determination": DETERMINATION}, has_source=False)
    assert code == W_KINETICS_DETERMINATION_INVALID
    assert kinetics_declaration_context(code, message) == {"field": "determination", "missing": "source"}
    # The layer that cannot tell (a bundle item; the bundle root knows the source) leaves it alone.
    assert kinetics_declaration_error({**BASE, "determination": DETERMINATION}) is None
    assert kinetics_declaration_error({**BASE, "determination": DETERMINATION}, has_source=True) is None


def test_the_rule_revalidates_applicability_and_protocol_from_raw_data():
    code, message = kinetics_declaration_error({**BASE, "applicability": {**_applicability(phase="gas"), "version": 2}})
    assert code == W_KINETICS_DECLARATION_VERSION_UNSUPPORTED
    assert kinetics_declaration_context(code, message)["field"] == "applicability"
    code, _ = kinetics_declaration_error({**BASE, "applicability": {"version": 1, "phase": "gas"}})  # no origin
    assert code == W_KINETICS_DECLARATION_INVALID
    code, message = kinetics_declaration_error({**BASE, "protocol": {"version": 2, "method_kind": "experimental"}})
    assert code == W_KINETICS_DECLARATION_VERSION_UNSUPPORTED
    assert kinetics_declaration_context(code, message)["field"] == "protocol"
    code, _ = kinetics_declaration_error({**BASE, "protocol": {"version": 1}})
    assert code == W_KINETICS_DECLARATION_INVALID
    constructed = KineticsApplicabilityDeclaration.model_construct(
        version=True, phase="gas", claim_origin="source_publication"
    )
    code, _ = kinetics_declaration_error({**BASE, "applicability": constructed})
    assert code == W_KINETICS_DECLARATION_VERSION_UNSUPPORTED


def test_the_rule_applies_the_column_contradictions_through_the_payload_facts():
    payload = {**BASE, "applicability": _applicability(pressure_dependence="fixed_pressure")}
    code, message = kinetics_declaration_error(payload)
    assert code == W_KINETICS_DECLARATION_CONTRADICTS_RECORD
    assert "pressure_bar" in message
    assert kinetics_declaration_context(code, message)["field"] == "applicability"
    ok = {**payload, "pressure_context": "apparent_at_pressure", "pressure_bar": 1.0}
    assert kinetics_declaration_error(ok) is None
    scope = {**BASE, "determination": DETERMINATION, "applicability": _applicability(scope="resolved_channel")}
    code, _ = kinetics_declaration_error(scope, has_source=True)
    assert code == W_KINETICS_DECLARATION_CONTRADICTS_RECORD


@pytest.mark.parametrize(
    "kind,origin,mismatched",
    [
        ("experimental", "computed", True),
        ("experimental", "estimated", True),
        ("experimental", "experimental", False),
        ("saddle_point_tst", "experimental", True),
        ("master_equation", "estimated", True),
        ("saddle_point_tst", "computed", False),
        ("other", "experimental", False),
    ],
)
def test_a_methods_kind_must_agree_with_the_records_origin(kind, origin, mismatched):
    protocol = {"version": 1, "method_kind": kind, **({"method_other_name": "x"} if kind == "other" else {})}
    result = kinetics_declaration_error({**BASE, "scientific_origin": origin, "protocol": protocol})
    if mismatched:
        assert result is not None and result[0] == W_KINETICS_DECLARATION_CONTRADICTS_RECORD
        assert kinetics_declaration_context(*result)["field"] == "protocol"
    else:
        assert result is None


def test_record_facts_read_a_standalone_request_and_a_bundle_block_alike():
    standalone = kinetics_record_facts(
        {**BASE, "falloff": None, "third_body_efficiencies": [], "pressure_context": "high_p_limit"}
    )
    assert (standalone.n_reactants, standalone.has_falloff, standalone.has_third_body_efficiencies) == (2, False, False)
    assert standalone.pressure_context == "high_p_limit"
    bundle = kinetics_record_facts(
        {"reactant_keys": ["a", "b", "c"], "product_keys": ["d"], "model_kind": "modified_arrhenius"}
    )
    # A bundle fit carries no falloff block and no efficiencies: those facts are False, not unknown.
    assert (bundle.n_reactants, bundle.has_falloff, bundle.has_third_body_efficiencies) == (3, False, False)
    with_falloff = kinetics_record_facts({**BASE, "model_kind": "troe", "falloff": {"low_a": 1.0}})
    assert with_falloff.has_falloff is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("reaction_order", "2"),
        ("pressure_domain_min_bar", "0.1"),
        ("pressure_domain_max_bar", "10"),
        ("default_third_body_efficiency", "1.0"),
    ],
)
def test_a_numeric_field_is_never_coerced_from_a_string(field, value):
    base = _applicability(
        collider_kind="composition_dependent",
        pressure_dependence="pressure_dependent",
        pressure_domain_min_bar=0.1,
        pressure_domain_max_bar=10.0,
        default_third_body_efficiency=1.0,
        reaction_order=2,
    )
    KineticsApplicabilityDeclaration.model_validate(base)  # the declaration itself is accepted as built
    with pytest.raises(ValidationError):
        KineticsApplicabilityDeclaration.model_validate({**base, field: value})


def test_a_mole_fraction_is_never_coerced_from_a_string():
    good = _applicability(**_mixture(0.79, 0.21))
    KineticsApplicabilityDeclaration.model_validate(good)
    bad = _applicability(**_mixture(0.79, 0.21))
    bad["colliders"][0]["mole_fraction"] = "0.79"
    with pytest.raises(ValidationError):
        KineticsApplicabilityDeclaration.model_validate(bad)
