"""The actual-protocol and structure-determination declarations, as the wire package states them.

Each test pins one refusal or one distinction a later task-aware energy selection relies on: a fact stated
with the wrong state/value pairing, an unstated fact staying unstated (never defaulted), an unknown field or
version, a determination that contradicts itself, and a pin that names a calculation nothing declared.
"""

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.local_key_codes import W_CALCULATION_KEY_UNDECLARED
from tckdb_schemas.structure_declarations import (
    W_STRUCTURE_DECLARATION_VERSION_UNSUPPORTED,
    W_STRUCTURE_DETERMINATION_INVALID,
    ActualProtocolDeclaration,
    StructureDeterminationDeclaration,
    assert_structure_pin_keys_declared,
    structure_determination_error,
)

SOURCE = {"origin": "producer_declared", "producer": "ARC"}
WTR = {"name": "ARC", "version": "1.1.0"}


def _coded(exc_info) -> CodedValidationError:
    return exc_info.value.errors()[0]["ctx"]["error"]


def _protocol(**extra) -> dict:
    return {"version": 1, "source": SOURCE, "solvation": {"state": "known", "kind": "gas_phase"}, **extra}


def _determination(**extra) -> dict:
    base = {
        "key": "d1",
        "target_kind": "conformer_basin",
        "quantity": "electronic_energy",
        "evaluated_geometry": {"calculation_key": "opt"},
        "sources": [
            {"role": "energy", "calculation_key": "sp"},
            {"role": "curvature", "calculation_key": "freq"},
        ],
        "workflow_tool_release": WTR,
    }
    base.update(extra)
    return base


class TestActualProtocolDeclaration:
    def test_a_stated_fact_round_trips_without_filling_the_others(self):
        declared = ActualProtocolDeclaration.model_validate(_protocol(electronic_state={"state": "known", "root": 0}))
        stored = declared.model_dump(mode="json", exclude_none=True)
        assert stored["electronic_state"] == {"state": "known", "root": 0}
        # Not stated stays not stated: no default gas phase, spin treatment or core treatment is invented.
        for unstated in ("spin_treatment", "relativistic_treatment", "core_treatment", "effective_core_potential"):
            assert unstated not in stored
        assert ActualProtocolDeclaration.model_validate(stored) == declared

    def test_not_applicable_is_a_claim_distinct_from_not_stated(self):
        declared = ActualProtocolDeclaration.model_validate(
            _protocol(effective_core_potential={"state": "not_applicable"})
        )
        assert declared.effective_core_potential is not None
        assert declared.effective_core_potential.state.value == "not_applicable"
        assert ActualProtocolDeclaration.model_validate(_protocol()).effective_core_potential is None

    @pytest.mark.parametrize(
        "fact",
        [
            {"state": "known"},
            {"state": "unknown", "value": "d3bj"},
            {"state": "not_applicable", "value": "d3bj"},
        ],
        ids=["known-without-value", "unknown-with-value", "not-applicable-with-value"],
    )
    def test_a_fact_states_its_value_exactly_when_known(self, fact):
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(_protocol(dispersion=fact))

    def test_a_root_is_stated_for_a_known_state_and_never_otherwise(self):
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(_protocol(electronic_state={"state": "known"}))
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(_protocol(electronic_state={"state": "unknown", "root": 1}))

    def test_names_are_lower_cased_so_one_claim_is_one_spelling(self):
        declared = ActualProtocolDeclaration.model_validate(
            _protocol(dispersion={"state": "known", "value": " D3BJ "})
        )
        assert declared.dispersion is not None and declared.dispersion.value == "d3bj"

    def test_a_gas_phase_environment_has_no_solvent_detail(self):
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(
                _protocol(solvation={"state": "known", "kind": "gas_phase", "detail": "water"})
            )

    def test_an_empty_declaration_is_refused(self):
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate({"version": 1, "source": SOURCE})

    def test_empty_list_is_the_claim_none_and_omitted_is_not_stated(self):
        none_material = ActualProtocolDeclaration.model_validate(_protocol(numerical_approximations=[]))
        assert none_material.numerical_approximations == []
        assert ActualProtocolDeclaration.model_validate(_protocol()).numerical_approximations is None

    @pytest.mark.parametrize("version", [2, 0, "1", 1.0, True])
    def test_only_the_integer_one_is_a_supported_version(self, version):
        with pytest.raises(ValidationError) as exc:
            ActualProtocolDeclaration.model_validate(_protocol(version=version))
        if version in (2, 0):
            assert _coded(exc).code == W_STRUCTURE_DECLARATION_VERSION_UNSUPPORTED

    def test_unknown_fields_are_refused_not_ignored(self):
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(_protocol(threads=8))

    def test_a_repeated_approximation_or_correction_is_refused(self):
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(
                _protocol(numerical_approximations=[{"kind": "density_fitting"}, {"kind": "density_fitting"}])
            )
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(
                _protocol(included_corrections=["zero_point_energy", "zero_point_energy"])
            )

    def test_a_parser_version_belongs_to_a_parser_derived_declaration(self):
        with pytest.raises(ValidationError):
            ActualProtocolDeclaration.model_validate(
                {"version": 1, "source": {"origin": "producer_declared", "parser_version": "1"}, "dispersion": {"state": "unknown"}}
            )


class TestStructureDeterminationDeclaration:
    def test_a_complete_determination_validates(self):
        declared = StructureDeterminationDeclaration.model_validate(_determination())
        assert [s.role.value for s in declared.sources] == ["energy", "curvature"]
        assert declared.evaluated_geometry.side.value == "output"

    def test_a_pin_names_a_key_or_a_ref_never_both_and_never_neither(self):
        with pytest.raises(ValidationError):
            StructureDeterminationDeclaration.model_validate(
                _determination(evaluated_geometry={"calculation_key": "a", "calculation_ref": "calc_x"})
            )
        with pytest.raises(ValidationError):
            StructureDeterminationDeclaration.model_validate(_determination(sources=[{"role": "energy"}]))

    def test_zero_kelvin_energy_needs_its_convention_and_the_others_refuse_one(self):
        convention = {"zero_point_treatment": "scaled_harmonic", "included_corrections": ["zero_point_energy"]}
        with pytest.raises(ValidationError) as exc:
            StructureDeterminationDeclaration.model_validate(_determination(quantity="zero_kelvin_energy"))
        assert _coded(exc).code == W_STRUCTURE_DETERMINATION_INVALID
        ok = StructureDeterminationDeclaration.model_validate(
            _determination(quantity="zero_kelvin_energy", energy_convention=convention)
        )
        assert ok.energy_convention is not None
        with pytest.raises(ValidationError) as exc:
            StructureDeterminationDeclaration.model_validate(_determination(energy_convention=convention))
        assert _coded(exc).code == W_STRUCTURE_DETERMINATION_INVALID
        with pytest.raises(ValidationError):
            StructureDeterminationDeclaration.model_validate(_determination(quantity=None, energy_convention=convention))

    def test_a_quantity_pins_the_calculation_that_supplies_it(self):
        with pytest.raises(ValidationError) as exc:
            StructureDeterminationDeclaration.model_validate(
                _determination(sources=[{"role": "curvature", "calculation_key": "freq"}])
            )
        assert _coded(exc).code == W_STRUCTURE_DETERMINATION_INVALID

    def test_an_evidence_only_determination_needs_no_energy_source(self):
        declared = StructureDeterminationDeclaration.model_validate(
            _determination(quantity=None, sources=[{"role": "curvature", "calculation_key": "freq"}])
        )
        assert declared.quantity is None

    def test_one_calculation_can_play_several_roles_but_not_one_role_twice(self):
        both = StructureDeterminationDeclaration.model_validate(
            _determination(
                sources=[
                    {"role": "energy", "calculation_key": "opt"},
                    {"role": "geometry_optimization", "calculation_key": "opt"},
                ]
            )
        )
        assert len(both.sources) == 2
        with pytest.raises(ValidationError):
            StructureDeterminationDeclaration.model_validate(
                _determination(
                    sources=[
                        {"role": "energy", "calculation_key": "opt"},
                        {"role": "energy", "calculation_key": "opt"},
                    ]
                )
            )

    def test_a_source_attribution_is_required_because_a_key_is_scoped_to_one(self):
        raw = _determination()
        raw.pop("workflow_tool_release")
        with pytest.raises(ValidationError) as exc:
            StructureDeterminationDeclaration.model_validate(raw)
        assert _coded(exc).code == W_STRUCTURE_DETERMINATION_INVALID

    def test_the_shared_rule_judges_a_payload_built_without_validation(self):
        built = StructureDeterminationDeclaration.model_construct(
            key="d", target_kind="geometry", quantity=None, energy_convention=None, sources=[],
            literature=None, workflow_tool_release=None,
        )
        assert structure_determination_error(built) is not None
        assert structure_determination_error(None) is None

    def test_a_pin_naming_an_undeclared_key_is_refused_with_the_shared_code(self):
        declared = StructureDeterminationDeclaration.model_validate(_determination())
        with pytest.raises(CodedValidationError) as exc:
            assert_structure_pin_keys_declared([declared], {"sp", "freq"})
        assert exc.value.code == W_CALCULATION_KEY_UNDECLARED
        assert exc.value.context["key"] == "opt"
        assert exc.value.context["declared_keys"] == ["freq", "sp"]
        assert_structure_pin_keys_declared([declared], {"sp", "freq", "opt"})
