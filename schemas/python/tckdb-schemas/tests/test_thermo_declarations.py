"""The thermo target and protocol declarations, as the wire package states them.

Each test pins one refusal or one distinction a later method-aware comparison
relies on: a declaration that contradicts itself, an unknown field or version,
the difference between "no departures stated" and "none declared", and the
rule that a bundle names things by local key while the standalone route names
them by ref.
"""

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import ThermoTargetKind
from tckdb_schemas.thermo import ThermoStateFields
from tckdb_schemas.thermo_declarations import (
    THERMO_PROTOCOL_VERSIONS,
    W_THERMO_DECLARATION_INVALID,
    W_THERMO_PROTOCOL_VERSION_UNSUPPORTED,
    W_THERMO_TARGET_GROUP_NOT_ALLOWED,
    W_THERMO_TARGET_GROUP_REQUIRED,
    StoredThermoProtocolDeclaration,
    ThermoProtocolDeclaration,
    ThermoTargetDeclaration,
    thermo_declaration_error,
    thermo_target_error,
)

GROUP_REF = "cg_" + "a" * 26


def _coded(exc_info) -> CodedValidationError:
    """The coded refusal a validator raised, however pydantic wrapped it."""
    return exc_info.value.errors()[0]["ctx"]["error"]


# ---------------------------------------------------------------------------
# target
# ---------------------------------------------------------------------------


def test_equilibrium_target_names_no_group():
    target = ThermoTargetDeclaration(kind="equilibrium_ensemble")
    assert target.kind is ThermoTargetKind.equilibrium_ensemble
    assert target.conformer_group_ref is None


def test_single_conformer_target_with_a_ref_or_a_key_is_accepted():
    assert ThermoTargetDeclaration(kind="single_conformer", conformer_group_ref=GROUP_REF)
    assert ThermoTargetDeclaration(kind="single_conformer", conformer_key="c0")


@pytest.mark.parametrize(
    "body,code",
    [
        ({"kind": "single_conformer"}, W_THERMO_TARGET_GROUP_REQUIRED),
        (
            {"kind": "single_conformer", "conformer_group_ref": GROUP_REF, "conformer_key": "c0"},
            W_THERMO_TARGET_GROUP_REQUIRED,
        ),
        ({"kind": "equilibrium_ensemble", "conformer_group_ref": GROUP_REF}, W_THERMO_TARGET_GROUP_NOT_ALLOWED),
        ({"kind": "equilibrium_ensemble", "conformer_key": "c0"}, W_THERMO_TARGET_GROUP_NOT_ALLOWED),
    ],
    ids=["single_without_group", "group_named_twice", "equilibrium_with_ref", "equilibrium_with_key"],
)
def test_a_self_contradicting_target_is_refused_with_a_code(body, code):
    with pytest.raises(ValidationError) as caught:
        ThermoTargetDeclaration(**body)
    assert _coded(caught).code == code
    # The same rule, called directly, says the same thing: this is what the
    # workflows and the client builder run on a payload that skipped validation.
    assert thermo_target_error(body)[0] == code


def test_an_unrecognised_kind_on_an_unvalidated_target_is_refused_with_a_code():
    for kind in ("ground_state", "Equilibrium_Ensemble", None):
        code, message = thermo_target_error({"kind": kind})
        assert code == W_THERMO_DECLARATION_INVALID
        assert "equilibrium_ensemble" in message


def test_an_unknown_target_field_or_kind_is_refused():
    with pytest.raises(ValidationError):
        ThermoTargetDeclaration(kind="equilibrium_ensemble", population="boltzmann")
    with pytest.raises(ValidationError):
        ThermoTargetDeclaration(kind="ground_state")


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------

G4 = {
    "version": 1,
    "recipe": {"name": "g4", "recipe_version": "Curtiss 2007"},
    "formation_reference": {"derivation": "atomization", "reference_data_source": "atct"},
    "thermal_approximation": {"ensemble_representation": "lowest_conformer", "internal_motion": "harmonic"},
    "departures": [],
}


def test_a_full_protocol_round_trips_through_the_stored_form():
    wire = ThermoProtocolDeclaration.model_validate(G4)
    stored = StoredThermoProtocolDeclaration.model_validate(wire.model_dump(mode="json", exclude_none=True))
    assert stored.model_dump(mode="json", exclude_none=True)["recipe"] == {"name": "g4", "recipe_version": "Curtiss 2007"}
    assert stored.departures == []


def test_an_unknown_protocol_field_is_refused():
    with pytest.raises(ValidationError) as caught:
        ThermoProtocolDeclaration.model_validate({**G4, "basis_set_quality": "high"})
    assert caught.value.errors()[0]["type"] == "extra_forbidden"
    with pytest.raises(ValidationError) as caught:
        ThermoProtocolDeclaration.model_validate({**G4, "recipe": {"name": "g4", "vendor": "x"}})
    assert caught.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("version", [0, 2, 99, -1])
def test_an_unsupported_version_is_refused_with_a_code(version):
    assert version not in THERMO_PROTOCOL_VERSIONS
    with pytest.raises(ValidationError) as caught:
        ThermoProtocolDeclaration.model_validate({**G4, "version": version})
    error = _coded(caught)
    assert error.code == W_THERMO_PROTOCOL_VERSION_UNSUPPORTED
    assert error.context["supported_versions"] == [1]


def test_the_version_is_required():
    body = {key: value for key, value in G4.items() if key != "version"}
    with pytest.raises(ValidationError) as caught:
        ThermoProtocolDeclaration.model_validate(body)
    assert caught.value.errors()[0]["type"] == "missing"


def test_a_protocol_that_states_nothing_is_refused():
    with pytest.raises(ValidationError, match="at least one of"):
        ThermoProtocolDeclaration.model_validate({"version": 1})


def test_departures_distinguishes_not_stated_from_none_declared():
    """Only an explicit empty list says "no departures"; omission says nothing."""
    omitted = ThermoProtocolDeclaration.model_validate({"version": 1, "recipe": {"name": "g4"}})
    declared_none = ThermoProtocolDeclaration.model_validate(
        {"version": 1, "recipe": {"name": "g4"}, "departures": []}
    )
    assert omitted.departures is None
    assert declared_none.departures == []
    # And the serialised form keeps the difference, which is what gets stored.
    assert "departures" not in omitted.model_dump(mode="json", exclude_none=True)
    assert declared_none.model_dump(mode="json", exclude_none=True)["departures"] == []
    # An explicit empty list is a statement on its own.
    assert ThermoProtocolDeclaration.model_validate({"version": 1, "departures": []}).departures == []


def test_other_recipe_needs_its_name_and_a_named_recipe_refuses_one():
    with pytest.raises(ValidationError, match="other_name is required"):
        ThermoProtocolDeclaration.model_validate({"version": 1, "recipe": {"name": "other"}})
    with pytest.raises(ValidationError, match="only allowed when"):
        ThermoProtocolDeclaration.model_validate({"version": 1, "recipe": {"name": "g4", "other_name": "G4X"}})
    assert ThermoProtocolDeclaration.model_validate({"version": 1, "recipe": {"name": "other", "other_name": "CBS-QB3"}})


@pytest.mark.parametrize("name", ["g3", "g4", "g4mp2", "g4_complete"])
def test_the_four_recipes_a_comparison_must_tell_apart_are_distinct_values(name):
    assert ThermoProtocolDeclaration.model_validate({"version": 1, "recipe": {"name": name}}).recipe.name.value == name


def test_other_reference_data_needs_a_detail_and_a_thermal_block_needs_a_statement():
    with pytest.raises(ValidationError, match="reference_data_detail is required"):
        ThermoProtocolDeclaration.model_validate(
            {"version": 1, "formation_reference": {"derivation": "isodesmic", "reference_data_source": "other"}}
        )
    with pytest.raises(ValidationError, match="must state"):
        ThermoProtocolDeclaration.model_validate({"version": 1, "thermal_approximation": {}})


def test_the_target_and_its_representation_are_separate_declarations():
    """The intended ensemble is the target; what represents it is the approximation."""
    fields = ThermoProtocolDeclaration.model_fields
    assert "thermal_approximation" in fields
    assert "thermodynamic_target" not in fields
    approximation = ThermoProtocolDeclaration.model_validate(
        {"version": 1, "thermal_approximation": {"ensemble_representation": "lowest_conformer"}}
    )
    target = ThermoTargetDeclaration(kind="equilibrium_ensemble")
    # An equilibrium target represented by one conformer is a coherent pair.
    assert target.kind is ThermoTargetKind.equilibrium_ensemble
    assert approximation.thermal_approximation.ensemble_representation.value == "lowest_conformer"


def test_a_supporting_calculation_is_a_key_or_a_ref_never_both_or_neither():
    for bad in ({}, {"calculation_key": "k", "calculation_ref": "calc_" + "a" * 26}):
        with pytest.raises(ValidationError):
            ThermoProtocolDeclaration.model_validate({"version": 1, "supporting_calculations": [bad]})
    with pytest.raises(ValidationError, match="must not repeat"):
        ThermoProtocolDeclaration.model_validate(
            {"version": 1, "supporting_calculations": [{"calculation_key": "k"}, {"calculation_key": "k"}]}
        )


def test_the_stored_form_carries_refs_only():
    stored = {"version": 1, "supporting_calculations": [{"calculation_ref": "calc_" + "a" * 26}]}
    assert StoredThermoProtocolDeclaration.model_validate(stored)
    with pytest.raises(ValidationError):
        StoredThermoProtocolDeclaration.model_validate(
            {"version": 1, "supporting_calculations": [{"calculation_key": "opt0"}]}
        )


# ---------------------------------------------------------------------------
# the shared rule
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")
def test_the_shared_rule_judges_a_payload_that_skipped_validation():
    unvalidated = ThermoProtocolDeclaration.model_construct(version=99)
    code, _ = thermo_declaration_error({"protocol": unvalidated})
    assert code == W_THERMO_PROTOCOL_VERSION_UNSUPPORTED

    nested_bad = ThermoProtocolDeclaration.model_construct(version=1, recipe={"name": "g5"})
    code, message = thermo_declaration_error({"protocol": nested_bad})
    assert code == W_THERMO_DECLARATION_INVALID
    assert "recipe" in message

    code, _ = thermo_declaration_error(
        {"thermodynamic_target": ThermoTargetDeclaration.model_construct(kind=ThermoTargetKind.single_conformer)}
    )
    assert code == W_THERMO_TARGET_GROUP_REQUIRED
    assert thermo_declaration_error({}) is None
    assert thermo_declaration_error({"protocol": G4, "thermodynamic_target": {"kind": "equilibrium_ensemble"}}) is None


# ---------------------------------------------------------------------------
# bundle state
# ---------------------------------------------------------------------------


def _state(**extra):
    return ThermoStateFields.model_validate({"scientific_origin": "computed", **extra})


def test_a_bundle_block_carries_both_declarations_and_leaves_them_null_when_omitted():
    assert _state().thermodynamic_target is None and _state().protocol is None
    state = _state(thermodynamic_target={"kind": "single_conformer", "conformer_key": "c0"}, protocol=G4)
    assert state.thermodynamic_target.conformer_key == "c0"
    assert state.protocol.recipe.name.value == "g4"


def test_a_bundle_names_by_key_and_refuses_a_public_ref():
    with pytest.raises(ValidationError, match="not accepted inside a bundle"):
        _state(thermodynamic_target={"kind": "single_conformer", "conformer_group_ref": GROUP_REF})
    with pytest.raises(ValidationError, match="not accepted inside a bundle"):
        _state(protocol={"version": 1, "supporting_calculations": [{"calculation_ref": "calc_" + "a" * 26}]})
    assert _state(protocol={"version": 1, "supporting_calculations": [{"calculation_key": "opt0"}]})
