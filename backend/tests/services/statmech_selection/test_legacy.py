"""What a legacy statmech record's stored facts establish for each claim. Unknown stays unknown; nothing is defaulted."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.statmech_selection.legacy import PROFILES, LegacyFacts, Readiness, classify, requirements
from app.services.statmech_selection.vocabulary import OwnerKind

SPECIES = OwnerKind.species_entry
TS = OwnerKind.transition_state_entry


def species_row(**kw) -> LegacyFacts:
    base = {
        "owner_kind": SPECIES,
        "scientific_origin": "computed",
        "rigid_rotor_kind": "asymmetric_top",
        "n_rotational_constants": 3,
        "external_symmetry": 2,
        "optical_isomers": 1,
        "statmech_treatment": "rrho",
        "uses_projected_frequencies": False,
        "has_scale_factor": True,
        "scale_factor_kind": "fundamental",
        "n_electronic_levels": 1,
        "n_curvature_sources": 1,
        "n_source_geometries": 1,
    }
    return LegacyFacts(**{**base, **kw})


def ts_row(**kw) -> LegacyFacts:
    return species_row(owner_kind=TS, determination_tie="single", reaction_coordinate_designated=True, **kw)


def state(profile: str, f: LegacyFacts) -> Readiness:
    return classify(profile, f)[0]


def test_a_fully_stated_record_is_usable_for_the_internal_claims_and_never_for_the_ones_it_cannot_hold():
    f = species_row()
    for profile in ("rotational_component", "vibrational_component", "within_basin_thermal", "canonical_model.internal_partition"):
        assert state(profile, f) is Readiness.usable, profile
    # The record cannot state an energy-reference convention or a translational normalisation: never usable.
    assert state("zpe_component", f) is Readiness.ambiguous
    assert state("canonical_model.full_partition", f) is Readiness.ambiguous
    assert requirements("canonical_model.full_partition", f)["translational_normalization"] == "missing"
    assert requirements("zpe_component", f)["energy_reference_convention"] == "missing"


def test_no_legacy_record_ever_establishes_a_full_model_or_a_zpe_convention():
    for f in (species_row(), species_row(rigid_rotor_kind="atom", n_rotational_constants=0)):
        assert state("canonical_model.full_partition", f) is not Readiness.usable
        assert state("zpe_component", f) is not Readiness.usable


def test_an_absent_scale_factor_is_unknown_application_not_unity():
    f = species_row(has_scale_factor=False, scale_factor_kind=None)
    assert requirements("vibrational_component", f)["scale_application"] == "missing"
    assert state("vibrational_component", f) is Readiness.ambiguous


def test_a_zpe_claim_with_a_scale_factor_of_another_purpose_is_ambiguous_not_matching():
    assert requirements("zpe_component", species_row(scale_factor_kind="fundamental"))["scale_application"] == "ambiguous"
    assert requirements("zpe_component", species_row(scale_factor_kind="zpe"))["scale_application"] == "present"


def test_an_unknown_optical_factor_or_missing_electronic_levels_keep_the_thermal_claims_open():
    assert requirements("within_basin_thermal", species_row(optical_isomers=None))["optical_factor"] == "missing"
    assert state("within_basin_thermal", species_row(optical_isomers=None)) is Readiness.ambiguous
    assert state("within_basin_thermal", species_row(n_electronic_levels=0)) is Readiness.ambiguous
    # An absent factor is not one: the rotational component does not need it.
    assert state("rotational_component", species_row(optical_isomers=None)) is Readiness.usable


def test_several_source_geometries_are_undeclared_alternatives_not_a_pick():
    f = species_row(n_source_geometries=2)
    assert requirements("rotational_component", f)["geometry"] == "ambiguous"
    assert state("rotational_component", f) is Readiness.ambiguous
    assert requirements("rotational_component", species_row(n_source_geometries=0))["geometry"] == "missing"


def test_curvature_is_discovered_on_any_source_not_only_a_frequency_role_link():
    # n_curvature_sources counts every source carrying a frequency or Hessian result, whatever its job type.
    assert requirements("vibrational_component", species_row(n_curvature_sources=1))["curvature_source"] == "present"
    assert requirements("vibrational_component", species_row(n_curvature_sources=0))["curvature_source"] == "missing"


def test_a_torsion_bearing_record_with_unknown_projection_or_an_invalidated_rotor_is_ambiguous():
    base = {"statmech_treatment": "rrho_1d", "n_torsions": 1}
    unknown_projection = species_row(uses_projected_frequencies=None, **base)
    assert requirements("within_basin_thermal", unknown_projection)["projection_status"] == "ambiguous"
    invalidated = species_row(n_torsions_invalidated=1, **base)
    assert requirements("within_basin_thermal", invalidated)["rotor_treatment"] == "ambiguous"
    no_rows = species_row(statmech_treatment="rrho_1d", n_torsions=0)
    assert requirements("within_basin_thermal", no_rows)["rotor_treatment"] == "missing"
    clean = species_row(**base)
    assert requirements("within_basin_thermal", clean)["rotor_treatment"] == "present"
    unscanned = species_row(n_torsions_hindered_without_scan=1, **base)
    assert requirements("within_basin_thermal", unscanned)["rotor_treatment"] == "ambiguous"


def test_an_atom_needs_no_frequency_scan_or_constants():
    atom = species_row(
        rigid_rotor_kind="atom", n_rotational_constants=0, external_symmetry=None, n_curvature_sources=0,
        n_source_geometries=0, has_scale_factor=False, scale_factor_kind=None, statmech_treatment=None,
    )
    assert requirements("vibrational_component", atom)["curvature_source"] == "not_required"
    assert state("rotational_component", atom) is Readiness.usable
    assert state("vibrational_component", atom) is Readiness.usable
    assert state("within_basin_thermal", atom) is Readiness.usable


def test_an_imported_record_needs_no_local_geometry_job():
    imported = species_row(scientific_origin="experimental", n_source_geometries=0)
    assert requirements("rotational_component", imported)["geometry"] == "not_required"
    assert state("rotational_component", imported) is Readiness.usable


def test_a_record_that_says_nothing_of_a_claim_is_absent_not_ambiguous():
    empty = LegacyFacts(owner_kind=SPECIES, scientific_origin="computed")
    assert state("rotational_component", empty) is Readiness.absent
    assert state("vibrational_component", empty) is Readiness.absent
    assert requirements("vibrational_component", empty)["projection_status"] == "not_required"  # nothing to project
    assert state("within_basin_thermal", empty) is Readiness.absent  # a vacuous requirement is not a stated fact
    assert state("within_basin_thermal", replace(empty, optical_isomers=1)) is Readiness.ambiguous


def test_rotor_kind_and_constants_must_agree():
    assert requirements("rotational_component", species_row(n_rotational_constants=2))["rotor"] == "ambiguous"
    assert requirements("rotational_component", species_row(rigid_rotor_kind="linear", n_rotational_constants=1))["rotor"] == "present"
    assert requirements("rotational_component", species_row(rigid_rotor_kind=None, is_linear=True, n_rotational_constants=1))["rotor"] == "present"
    assert requirements("rotational_component", species_row(rigid_rotor_kind=None, n_rotational_constants=3))["rotor"] == "ambiguous"
    assert requirements("rotational_component", species_row(rigid_rotor_kind=None, n_rotational_constants=0))["rotor"] == "missing"
    assert requirements("rotational_component", species_row(external_symmetry=None))["external_symmetry"] == "missing"


def test_owner_kind_gates_the_ts_claim_and_the_species_claims_but_not_the_components():
    assert state("conventional_ts_transverse_model", species_row()) is Readiness.not_applicable
    assert state("within_basin_thermal", ts_row()) is Readiness.not_applicable
    assert state("canonical_model.full_partition", ts_row()) is Readiness.not_applicable
    assert state("rotational_component", ts_row()) is Readiness.usable
    assert state("vibrational_component", ts_row()) is Readiness.usable


def test_a_ts_record_needs_a_designated_reaction_coordinate_and_one_determination_tie():
    f = ts_row()
    assert state("conventional_ts_transverse_model", f) is Readiness.usable
    assert state("conventional_ts_transverse_model", replace(f, reaction_coordinate_designated=False)) is Readiness.ambiguous
    assert state("conventional_ts_transverse_model", replace(f, reaction_coordinate_designated=None)) is Readiness.ambiguous
    assert requirements("conventional_ts_transverse_model", replace(f, determination_tie="multiple"))["determination_tie"] == "ambiguous"
    assert requirements("conventional_ts_transverse_model", replace(f, determination_tie="none"))["determination_tie"] == "missing"
    # No optical factor is required of a saddle point.
    assert "optical_factor" not in requirements("conventional_ts_transverse_model", f)


def test_every_profile_is_total_over_both_owner_kinds():
    for owner in OwnerKind:
        f = LegacyFacts(owner_kind=owner, scientific_origin="computed")
        for profile in PROFILES:
            readiness, states = classify(profile, f)
            assert isinstance(readiness, Readiness)
            assert readiness is Readiness.not_applicable or states


def test_an_unknown_profile_is_refused():
    with pytest.raises(ValueError):
        requirements("canonical_model", species_row())
