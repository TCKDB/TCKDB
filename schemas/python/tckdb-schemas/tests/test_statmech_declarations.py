"""The statistical-model declaration, as the wire package states it.

Each test pins one refusal or one distinction the selection relies on: a fact left out staying out (never unity, never
projected, never counted), a record counted once, scope and pointer agreeing, a source naming exactly one locator, and a
key nothing declared.
"""

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.local_key_codes import W_STATMECH_CALCULATION_KEY_UNDECLARED
from tckdb_schemas.statmech_declarations import (
    W_STATMECH_MODEL_INVALID,
    W_STATMECH_MODEL_VERSION_UNSUPPORTED,
    ModelRecipe,
    StatmechModelDeclaration,
    assert_model_determination_key_declared,
    assert_model_pin_keys_declared,
)

WTR = {"name": "ARC", "version": "1.1.0"}
SM_A = "sm_abcdefghijklmnopqrstuvwxyz"
SM_B = "sm_bcdefghijklmnopqrstuvwxyza"
CG = "cg_abcdefghijklmnopqrstuvwxyz"
SDET = "sdet_abcdefghijklmnopqrstuvwxyz"


def _declaration(**extra) -> dict:
    base = {"version": 1, "scope_kind": "atom", "workflow_tool_release": WTR}
    base.update(extra)
    return base


def _coded(exc_info) -> CodedValidationError:
    return exc_info.value.errors()[0]["ctx"]["error"]


def _refused(fragment: str, **extra) -> None:
    with pytest.raises(ValidationError) as caught:
        StatmechModelDeclaration.model_validate(_declaration(**extra))
    assert fragment in str(caught.value), str(caught.value)


def test_a_minimal_declaration_states_only_what_it_states():
    declared = StatmechModelDeclaration.model_validate(_declaration())
    assert declared.recipe.model_dump(exclude_unset=True) == {}
    recipe = declared.recipe
    for omitted in (
        recipe.scale_application,
        recipe.vibrational_scheme,
        recipe.optical_state,
        recipe.optical_multiplier,
        recipe.electronic_spin_multiplier,
        recipe.external_symmetry_state,
        recipe.nuclear_spin,
        recipe.energy_zero,
        recipe.zpe_included_in_energies,
    ):
        assert omitted is None  # not stated; never unity, never counted, never projected


def test_a_version_this_server_does_not_read_is_refused_with_its_code():
    for version in (2, 0, "1", 1.0, True):
        with pytest.raises(ValidationError) as caught:
            StatmechModelDeclaration.model_validate(_declaration(version=version))
        assert _coded(caught).code == W_STATMECH_MODEL_VERSION_UNSUPPORTED


def test_attribution_is_required_and_a_fabricated_extra_field_is_refused():
    with pytest.raises(ValidationError):
        StatmechModelDeclaration.model_validate({"version": 1, "scope_kind": "atom"})
    _refused("Extra inputs", database_id=3)
    _refused("Extra inputs", recipe={"zpe_scale": 0.97})


def test_scale_contributions_go_with_an_applied_scale_and_only_with_it():
    ok = ModelRecipe.model_validate({"scale_application": "applied", "scale_contributions": ["zero_point_energy"]})
    assert ok.scale_contributions
    for bad in (
        {"scale_application": "applied"},
        {"scale_application": "unknown", "scale_contributions": ["fundamental"]},
        {"scale_contributions": ["fundamental"]},
        {"scale_application": "applied", "scale_contributions": ["fundamental", "fundamental"]},
    ):
        with pytest.raises(ValidationError):
            ModelRecipe.model_validate(bad)


def test_a_quasi_harmonic_scheme_states_its_cutoff_and_modes_are_distinct_and_one_based():
    with pytest.raises(ValidationError):
        ModelRecipe.model_validate({"vibrational_scheme": "quasi_harmonic"})
    assert ModelRecipe.model_validate({"vibrational_scheme": "quasi_harmonic", "low_frequency_cutoff_cm1": 100.0})
    for bad in (
        {"projected_mode_indices": [0]},
        {"projected_mode_indices": [1, 1]},
        {"projected_mode_indices": [1, 2], "excluded_mode_indices": [2]},
        {"validity_min_k": 300.0},
        {"validity_min_k": 600.0, "validity_max_k": 300.0},
        {"low_frequency_cutoff_cm1": float("inf")},
    ):
        with pytest.raises(ValidationError):
            ModelRecipe.model_validate(bad)


def test_a_rotor_names_the_potential_it_integrates_and_only_an_imported_one_does():
    rotors = [{"torsion_index": 1, "representation": "imported_potential", "potential_source_key": "pot"}]
    declared = StatmechModelDeclaration.model_validate(
        _declaration(
            recipe={"rotors": rotors},
            sources=[{"source_key": "pot", "role": "torsional_surface", "literature": {"kind": "article", "title": "T"}}],
        )
    )
    assert declared.recipe.rotors[0].potential_source_key == "pot"
    _refused("potential_source_key names a source", recipe={"rotors": rotors})
    for wrong in ("geometry", "curvature", "parameters", "absolute_energy"):
        _refused("role is torsional_surface", recipe={"rotors": rotors},
                 sources=[{"source_key": "pot", "role": wrong, "literature": {"kind": "article", "title": "T"}}])
    # the role check is not tied to one kind of source: a calculation-locator source with a wrong role is refused too
    for wrong in ("geometry", "curvature", "absolute_energy"):
        _refused("role is torsional_surface", recipe={"rotors": rotors},
                 sources=[{"source_key": "pot", "role": wrong, "calculation_key": "scan"}])
    assert StatmechModelDeclaration.model_validate(
        _declaration(recipe={"rotors": rotors}, sources=[{"source_key": "pot", "role": "torsional_surface", "calculation_key": "scan"}])
    ).recipe.rotors[0].potential_source_key == "pot"
    _refused("imported_potential", recipe={"rotors": [{"torsion_index": 1, "representation": "free_rotor", "potential_source_key": "p"}]})
    _refused("imported_potential", recipe={"rotors": [{"torsion_index": 1, "representation": "imported_potential"}]})
    _refused("names a torsion_index twice", recipe={"rotors": [rotors[0], {**rotors[0], "potential_source_key": "pot"}]},
             sources=[{"source_key": "pot", "role": "torsional_surface", "literature": {"kind": "article", "title": "T"}}])
    _refused("replaces_mode_indices", recipe={"rotors": [{"torsion_index": 1, "representation": "free_rotor", "replaces_mode_indices": [2, 2]}]})


def test_only_an_ensemble_has_members_and_a_record_is_a_member_once():
    members = [{"member_key": "a", "statmech_ref": SM_A}, {"member_key": "b", "statmech_ref": SM_B}]
    ok = StatmechModelDeclaration.model_validate(_declaration(scope_kind="explicit_equilibrium_ensemble", members=members))
    assert [m.counting_multiplicity for m in ok.members] == [None, None]  # not stated is not one
    _refused("has members", members=members)
    _refused("has members", scope_kind="explicit_equilibrium_ensemble")
    _refused("repeated record", scope_kind="explicit_equilibrium_ensemble", members=[members[0], {**members[0], "member_key": "c"}])
    _refused("member_key twice", scope_kind="explicit_equilibrium_ensemble", members=[members[0], {**members[1], "member_key": "a"}])
    _refused("pattern", scope_kind="explicit_equilibrium_ensemble", members=[{"member_key": "a", "statmech_ref": "17"}])
    _refused("greater than or equal to 1", scope_kind="explicit_equilibrium_ensemble", members=[{**members[0], "counting_multiplicity": 0}])
    _refused("finite number", scope_kind="explicit_equilibrium_ensemble", members=[{**members[0], "relative_energy_kj_mol": float("nan")}])


def test_scope_and_the_ref_it_needs_agree():
    assert StatmechModelDeclaration.model_validate(_declaration(scope_kind="basin", conformer_group_ref=CG))
    assert StatmechModelDeclaration.model_validate(_declaration(scope_kind="basin"))  # a conformer deposit's own basin
    _refused("belongs to a basin scope", conformer_group_ref=CG)
    _refused("pattern", scope_kind="basin", conformer_group_ref="spe_abcdefghijklmnopqrstuvwxyz")
    assert StatmechModelDeclaration.model_validate(_declaration(scope_kind="ts_determination", determination_ref=SDET))
    assert StatmechModelDeclaration.model_validate(_declaration(scope_kind="ts_determination", determination_key="saddle"))
    _refused("exactly one of determination_ref or determination_key", scope_kind="ts_determination")
    _refused("exactly one of determination_ref or determination_key", scope_kind="ts_determination", determination_ref=SDET, determination_key="k")
    _refused("exactly one of determination_ref or determination_key", determination_key="k")


def test_a_source_names_exactly_one_locator_and_the_roles_agree():
    lit = {"kind": "article", "title": "T"}
    ok = StatmechModelDeclaration.model_validate(
        _declaration(sources=[{"source_key": "a", "role": "curvature", "calculation_key": "freq"}, {"source_key": "b", "role": "parameters", "literature": lit}])
    )
    assert len(ok.sources) == 2
    _refused("exactly one", sources=[{"source_key": "a", "role": "curvature"}])
    _refused("exactly one", sources=[{"source_key": "a", "role": "curvature", "calculation_key": "f", "literature": lit}])
    _refused("scale_factor role", sources=[{"source_key": "a", "role": "scale_factor", "calculation_key": "f"}])
    _refused("geometry_ref goes with a calculation", sources=[{"source_key": "a", "role": "geometry", "literature": lit, "geometry_ref": "geom_abcdefghijklmnopqrstuvwxyz"}])
    _refused("source_key twice", sources=[{"source_key": "a", "role": "curvature", "calculation_key": "f"}] * 2)
    _refused("member_key names a member", sources=[{"source_key": "a", "role": "curvature", "calculation_key": "f", "member_key": "nope"}])


def test_an_undeclared_calculation_key_is_refused_with_the_shared_code_and_the_names_that_would_work():
    declared = StatmechModelDeclaration.model_validate(
        _declaration(sources=[{"source_key": "a", "role": "curvature", "calculation_key": "ghost"}])
    )
    assert_model_pin_keys_declared(declared, {"ghost"})
    assert_model_pin_keys_declared(None, set())
    with pytest.raises(CodedValidationError) as caught:
        assert_model_pin_keys_declared(declared, {"freq", "opt"})
    assert caught.value.code == W_STATMECH_CALCULATION_KEY_UNDECLARED
    assert caught.value.context["key"] == "ghost" and caught.value.context["declared_keys"] == ["freq", "opt"]


def test_a_determination_key_names_a_determination_of_the_same_deposit():
    declared = StatmechModelDeclaration.model_validate(_declaration(scope_kind="ts_determination", determination_key="saddle"))
    assert_model_determination_key_declared(declared, {"saddle"})
    assert_model_determination_key_declared(StatmechModelDeclaration.model_validate(_declaration()), set())
    with pytest.raises(CodedValidationError) as caught:
        assert_model_determination_key_declared(declared, {"other"})
    assert caught.value.code == W_STATMECH_MODEL_INVALID and caught.value.context["declared_keys"] == ["other"]


def test_a_full_model_states_its_translational_normalization_with_exactly_its_reference_value():
    ok = ModelRecipe.model_validate(
        {"translation_included": True, "translational_normalization": "standard_state_pressure", "reference_pressure_pa": 1e5}
    )
    assert ok.reference_pressure_pa == 1e5 and ok.reference_volume_m3 is None  # nothing else is filled in
    assert ModelRecipe.model_validate({"translation_included": False}).translational_normalization is None
    for bad in (
        {"translation_included": True, "translational_normalization": "fixed_volume"},
        {"translation_included": True, "translational_normalization": "fixed_volume", "reference_pressure_pa": 1e5},
        {"translation_included": True, "translational_normalization": "standard_state_pressure", "reference_pressure_pa": 1e5, "reference_volume_m3": 1.0},
        {"translation_included": False, "translational_normalization": "fixed_volume", "reference_volume_m3": 1.0},
        {"translational_normalization": "fixed_volume", "reference_volume_m3": 1.0},
        {"reference_pressure_pa": 1e5},
        {"translation_included": True, "translational_normalization": "fixed_volume", "reference_volume_m3": -1.0},
    ):
        with pytest.raises(ValidationError):
            ModelRecipe.model_validate(bad)


def test_the_reaction_coordinate_mode_is_named_only_when_the_coordinate_is_excluded():
    assert ModelRecipe.model_validate({"reaction_coordinate_excluded": True, "reaction_coordinate_mode_index": 1})
    assert ModelRecipe.model_validate({"reaction_coordinate_excluded": False}).reaction_coordinate_mode_index is None
    for bad in ({"reaction_coordinate_mode_index": 1}, {"reaction_coordinate_excluded": False, "reaction_coordinate_mode_index": 1}):
        with pytest.raises(ValidationError):
            ModelRecipe.model_validate(bad)
