"""Scale, modes, rotors, counting, ensembles, conventions and domains: the accounting a claim needs, checked one rule a test."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.db.models.common import RecordReviewStatus
from app.services.selection_kernel import Applicability
from app.services.statmech_selection.facts import ScaleFactorFacts, TorsionFacts
from tests.services.statmech_selection._support import (
    CG,
    FSF_ENTROPY,
    SMD,
    THERMAL,
    approved_calcs,
    assess,
    calc,
    codes,
    declared,
    make,
    member,
    record,
    req,
    src,
    subject,
)

A, INC, S, U = Applicability.applicable, Applicability.incompatible, Applicability.unsupported, Applicability.unresolved
_ = THERMAL

INTERNAL = make(THERMAL, task="canonical_model", observable="internal_partition")
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
ZPE = make(
    THERMAL,
    task="zpe_component",
    observable=...,
    conditions={"energy_reference": {"zero": "e0", "zpe": "included"}},
)
ZPE.pop("observable")
HEAT = make(THERMAL, observable="heat_capacity")
VIB = make(THERMAL, task="vibrational_component", conditions=...)
VIB.pop("observable")
VIB.pop("conditions")
ROT = make(THERMAL, task="rotational_component", conditions=...)
ROT.pop("observable")
ROT.pop("conditions")


def fsf(kind: str) -> object:
    return src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=ScaleFactorFacts(f"fsf_{kind}", kind, 0.97))


def sources_with(*extra):
    return (src("geom", "geometry", "calc_o"), src("curv", "curvature", "calc_f"), *extra)


# --- scale: unknown is not unity; purposes are not interchangeable ---------------------------------------------------------


@pytest.mark.parametrize("application", [..., "unknown"])
def test_an_unstated_or_unknown_scale_application_is_unresolved_never_unity(application):
    a = assess(declared(recipe={"scale_application": application, "scale_contributions": []}))
    assert a.applicability is U and "scale_application_unknown" in codes(a)


def test_an_explicit_not_applied_differs_from_absence():
    a = assess(declared(recipe={"scale_application": "not_applied", "scale_contributions": []}, sources=sources_with()))
    assert a.applicability is A, codes(a)
    contradicted = assess(declared(recipe={"scale_application": "not_applied", "scale_contributions": []}))
    assert INC == contradicted.applicability and "scale_declaration_conflict" in codes(contradicted)


def test_a_zpe_calibrated_scale_does_not_stand_in_for_an_entropy_claim():
    a = assess(declared(recipe={"scale_contributions": ["zero_point_energy"]}, sources=sources_with(fsf("zpe"))))
    assert a.applicability is INC and "scale_purpose_mismatch" in codes(a)


def test_a_second_factor_of_another_purpose_among_the_sources_is_not_ignored():
    extra = src("fsf2", "scale_factor", None, locator="scale_factor", scale_factor=ScaleFactorFacts("fsf_zpe", "zpe", 0.98))
    a = assess(declared(sources=sources_with(fsf("entropy"), extra)))
    assert a.applicability is INC and "scale_purpose_mismatch" in codes(a)


def test_the_factor_source_must_be_calibrated_for_the_purpose_it_is_applied_to():
    a = assess(declared(sources=sources_with(fsf("fundamental"))))
    assert a.applicability is INC and "scale_purpose_mismatch" in codes(a)
    assert "required_evidence_unavailable:scale_factor" in codes(assess(declared(sources=sources_with())))


def test_scaling_the_fundamentals_and_a_derived_quantity_too_is_double_scaling():
    a = assess(
        declared(
            recipe={"scale_contributions": ["fundamental", "vibrational_entropy"]},
            sources=sources_with(fsf("fundamental"), src("fsf2", "scale_factor", None, locator="scale_factor", scale_factor=FSF_ENTROPY)),
        )
    )
    assert a.applicability is INC and "counting_conflict:double_scaling" in codes(a)


def test_zero_point_energy_is_not_counted_twice():
    a = assess(declared(recipe={"energy_zero": "electronic", "zpe_included_in_energies": True}))
    assert a.applicability is INC and "energy_reference_conflict" in codes(a)


# --- modes and rotors ------------------------------------------------------------------------------------------------------


def rotor(index=1, **changes):
    return {"torsion_index": index, "representation": "free_rotor", "replaces_mode_indices": [], **changes}


TORSION = TorsionFacts(1, "free_rotor", 1, 3, None, None)


def with_torsion(recipe=None, **changes):
    return declared(recipe={"rotors": [rotor()], **(recipe or {})}, record=record(torsions=(TORSION,), statmech_treatment="rrho_1d"), **changes)


def test_a_flag_alone_is_not_a_mode_map_and_a_torsion_needs_its_rotor_entry():
    unstated = assess(declared(record=record(torsions=(TORSION,), statmech_treatment="rrho_1d")))
    assert unstated.applicability is U and "rotor_replacement_unstated" in codes(unstated)
    ok = assess(with_torsion())
    assert ok.applicability is A, codes(ok)
    flag_without_map = assess(with_torsion(), subj=None)
    assert flag_without_map.applicability is A
    projected_true = declared(
        recipe={"rotors": [rotor()]}, record=record(torsions=(TORSION,), statmech_treatment="rrho_1d", uses_projected_frequencies=True)
    )
    a = assess(projected_true)
    assert a.applicability is U and "required_recipe_unknown:projected_mode_indices" in codes(a)


def test_a_harmonic_mode_removed_twice_is_a_counting_conflict():
    both = with_torsion({"rotors": [rotor(replaces_mode_indices=[3])], "projected_mode_indices": [3]})
    assert "counting_conflict:mode_projected_and_replaced" in codes(assess(both))
    twice = declared(
        recipe={"rotors": [rotor(1, replaces_mode_indices=[3]), rotor(2, replaces_mode_indices=[3])]},
        record=record(torsions=(TORSION, replace(TORSION, torsion_index=2)), statmech_treatment="rrho_1d"),
    )
    assert "counting_conflict:mode_replaced_twice" in codes(assess(twice))
    flag = declared(recipe={"projected_mode_indices": [1]}, record=record(uses_projected_frequencies=False))
    assert "counting_conflict:projection_flag" in codes(assess(flag))


def test_an_invalidated_required_rotor_blocks_and_never_silently_becomes_rrho():
    bad = replace(TORSION, invalidated_reason="scan failed")
    a = assess(declared(recipe={"rotors": [rotor()]}, record=record(torsions=(bad,), statmech_treatment="rrho_1d")))
    assert a.applicability is INC and "required_rotor_invalidated" in codes(a)


def test_a_free_or_imported_rotor_needs_no_local_scan_but_a_local_scan_rotor_needs_its_scan():
    free = assess(with_torsion())
    assert free.applicability is A
    local = assess(with_torsion({"rotors": [rotor(representation="local_scan")]}))
    assert local.applicability is U and "required_evidence_unavailable:rotor_scan" in codes(local)
    scan_calc = calc("calc_scan", type="opt")
    with_scan = declared(
        recipe={"rotors": [rotor(representation="local_scan")]},
        record=record(torsions=(replace(TORSION, treatment_kind="hindered_rotor", scan_calculation_ref="calc_scan"),), statmech_treatment="rrho_1d"),
    )
    assert assess(with_scan, calcs=approved_calcs(scan_calc)).applicability is A
    assert "required_source_unusable:torsional_surface" in codes(assess(with_scan))  # the scan job is not available
    imported = with_torsion(
        {"rotors": [rotor(representation="imported_potential", potential_source_key="pot")]},
        sources=sources_with(fsf("entropy"), src("pot", "torsional_surface", None, locator="literature", literature_ref="lit_a")),
    )
    assert assess(imported).applicability is A


def test_an_unsupported_treatment_is_recognised_not_substituted():
    for changed in (
        declared(recipe={"vibrational_scheme": "anharmonic"}),
        declared(record=record(statmech_treatment="rrao")),
        with_torsion({"rotors": [rotor(dimension=2)]}),
    ):
        a = assess(changed)
        assert a.applicability is S and any(c.startswith("treatment_not_supported") for c in codes(a)), codes(a)


# --- statistical counting ---------------------------------------------------------------------------------------------------


def test_electronic_spin_is_not_counted_twice_through_both_the_levels_and_a_multiplier():
    a = assess(declared(recipe={"electronic_spin_multiplier": 2}))
    assert a.applicability is INC and "counting_conflict:electronic_spin" in codes(a)
    assert assess(declared(recipe={"electronic_levels_state": "not_counted", "electronic_spin_multiplier": 2})).applicability is INC  # not counted: refused for that


def test_the_optical_factor_is_counted_once_and_a_stated_multiplier_must_agree_with_the_record():
    assert assess(declared(recipe={"optical_multiplier": 1})).applicability is A
    a = assess(declared(recipe={"optical_multiplier": 2}))
    assert a.applicability is INC and "counting_conflict:optical_factor" in codes(a)


def test_a_factor_the_model_says_it_does_not_count_cannot_support_a_claim_that_needs_it():
    for field, name in (("external_symmetry_state", "external_symmetry"), ("optical_state", "optical_factor"), ("electronic_levels_state", "electronic_degeneracy")):
        a = assess(declared(recipe={field: "not_counted"}))
        assert a.applicability is INC and f"required_factor_not_counted:{name}" in codes(a), (field, codes(a))


def test_a_counted_factor_needs_the_number_it_counts():
    assert "required_recipe_unknown:external_symmetry" in codes(assess(declared(record=record(external_symmetry=None))))
    assert "required_recipe_unknown:electronic_levels" in codes(assess(declared(record=record(electronic_levels=()))))


def canonical(**changes):
    """A canonical-model candidate: the fundamentals are what a partition function is built from, so that is what is scaled."""
    recipe = {"scale_contributions": ["fundamental"], **changes.pop("recipe", {})}
    return declared(recipe=recipe, sources=changes.pop("sources", sources_with(fsf("fundamental"))), **changes)


def ensemble(*members, recipe=None):
    return canonical(
        scope="explicit_equilibrium_ensemble",
        recipe={"energy_zero": "e0", "zpe_included_in_energies": True, **(recipe or {})},
        members=members,
    )


ENSEMBLE_REQ = make(
    THERMAL,
    task="canonical_model",
    observable="internal_partition",
    target__scope={"kind": "explicit_equilibrium_ensemble", "ensemble_declaration_ref": SMD},
)


def test_an_ensemble_weighting_needs_compatible_offsets_and_multiplicities_a_basin_claim_does_not():
    ok = assess(ensemble(member("a"), member("b", relative_energy_kj_mol=2.5)), req(ENSEMBLE_REQ))
    assert ok.applicability is A, codes(ok)
    no_offset = assess(ensemble(member("a"), member("b", relative_energy_kj_mol=None)), req(ENSEMBLE_REQ))
    assert no_offset.applicability is U and "relative_offsets_missing" in codes(no_offset)
    no_zero = assess(ensemble(member("a"), recipe={"energy_zero": ...}), req(ENSEMBLE_REQ))
    assert "relative_offsets_missing" in codes(no_zero)
    unstated = assess(ensemble(member("a"), member("b", counting_multiplicity=None)), req(ENSEMBLE_REQ))
    assert "required_recipe_unknown:counting_multiplicity" in codes(unstated)  # never read as one
    # the same absent offset is irrelevant to a within-basin entropy claim
    assert assess(declared(recipe={"energy_zero": ...})).applicability is A


def test_a_member_whose_record_is_hidden_or_below_the_floor_cannot_be_counted_or_dropped():
    missing = member("b", review_status=None, statmech_ref=None)
    a = assess(ensemble(member("a"), missing), req(ENSEMBLE_REQ))
    assert a.applicability is U and "required_member_unavailable" in codes(a)
    low = member("b", review_status=RecordReviewStatus.not_reviewed)
    approved_only = frozenset({RecordReviewStatus.approved})
    b = assess(ensemble(member("a"), low), req(ENSEMBLE_REQ), effective=approved_only)
    assert b.applicability is U and "required_member_unavailable" in codes(b)
    assert codes(b) == ["required_member_unavailable"] and "sm_b" not in str(b.to_dict())  # the reason names no member


def test_a_rotor_whose_region_is_also_an_explicit_member_is_counted_twice():
    c = ensemble(member("a", region_key="r1"), recipe={"rotors": [rotor(region_key="r1")]})
    c = replace(c, record=record(torsions=(TORSION,), statmech_treatment="rrho_1d"))
    a = assess(c, req(ENSEMBLE_REQ))
    assert a.applicability is INC and "counting_conflict:rotor_member_overlap" in codes(a)


def test_a_declared_ensemble_ref_must_be_the_requested_one():
    other = replace(ensemble(member("a")).declaration, ref="smd_" + "z" * 26)
    a = assess(replace(ensemble(member("a")), declaration=other), req(ENSEMBLE_REQ))
    assert a.applicability is INC and "scope_incompatible" in codes(a)


# --- conventions -------------------------------------------------------------------------------------------------------------


def test_an_internal_claim_refuses_a_model_that_includes_translation_and_a_full_claim_an_internal_one():
    assert "model_includes_translation" in codes(assess(canonical(recipe={"translation_included": True}), req(INTERNAL)))
    full = {
        "translation_included": True,
        "translational_normalization": "standard_state_pressure",
        "reference_pressure_pa": 100000.0,
        "energy_zero": "e0",
        "zpe_included_in_energies": True,
    }
    ok = assess(canonical(recipe=full), req(FULL))
    assert ok.applicability is A, codes(ok)
    assert "model_is_internal_only" in codes(assess(canonical(recipe={"translation_included": False}), req(FULL)))
    assert "normalization_mismatch" in codes(assess(canonical(recipe={**full, "reference_pressure_pa": 101325.0}), req(FULL)))
    volume = {**full, "translational_normalization": "fixed_volume", "reference_pressure_pa": ..., "reference_volume_m3": 1.0}
    assert "normalization_mismatch" in codes(assess(canonical(recipe=volume), req(FULL)))
    unstated = {k: v for k, v in full.items() if k not in ("translational_normalization", "reference_pressure_pa")}
    assert "required_recipe_unknown:translational_normalization" in codes(assess(canonical(recipe=unstated), req(FULL)))


def test_a_zpe_interpretation_states_its_energy_reference_and_it_must_match():
    ok = assess(declared(recipe={"scale_contributions": ["zero_point_energy"]}, sources=sources_with(fsf("zpe"))), req(ZPE))
    assert ok.applicability is A, codes(ok)
    assert "energy_reference_mismatch" in codes(assess(declared(recipe={"energy_zero": "electronic", "zpe_included_in_energies": False, "scale_contributions": ["zero_point_energy"]}, sources=sources_with(fsf("zpe"))), req(ZPE)))
    assert "required_recipe_unknown:energy_reference" in codes(assess(declared(recipe={"energy_zero": ..., "scale_contributions": ["zero_point_energy"]}, sources=sources_with(fsf("zpe"))), req(ZPE)))


def test_the_requested_temperature_domain_must_lie_inside_a_stated_validity_range():
    inside = assess(declared(recipe={"validity_min_k": 200.0, "validity_max_k": 1000.0}))
    assert inside.applicability is A
    outside = assess(declared(recipe={"validity_min_k": 350.0, "validity_max_k": 1000.0}))
    assert outside.applicability is INC and "domain_not_covered" in codes(outside)
    unstated = assess(declared())
    assert unstated.applicability is A and "validity_domain_not_stated" in unstated.advisory
    assert "validity_domain_not_stated" in unstated.comparative_unknown  # it creates no edge and excludes nothing


def test_the_isotope_convention_must_be_the_models():
    assert "isotope_mismatch" in codes(assess(declared(recipe={"isotope_masses": "natural_abundance_average"})))


# --- atoms and imports need no fabricated jobs ------------------------------------------------------------------------------


def test_an_atom_is_qualified_without_a_frequency_a_scan_or_a_geometry_job():
    atom_subject = subject(is_atom=True)
    atom_record = record(rigid_rotor_kind="atom", n_rotational_constants=0, external_symmetry=None, optical_isomers=None, electronic_levels=((1, 0.0, 2),))
    c = declared(
        scope="atom",
        recipe={"external_symmetry_state": ..., "optical_state": ..., "scale_application": "not_applied", "scale_contributions": []},
        sources=(),
        record=atom_record,
    )
    atom_request = req(THERMAL, target__scope={"kind": "atom"})
    a = assess(c, atom_request, {}, subj=atom_subject)
    assert a.applicability is A, codes(a)
    rot = assess(c, req(ROT, target__scope={"kind": "atom"}), {}, subj=atom_subject)
    assert rot.applicability is A, codes(rot)


IMPORTED = (
    src("geom", "geometry", None, locator="literature", literature_ref="lit_a"),
    src("curv", "curvature", None, locator="literature", literature_ref="lit_b"),
    src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=FSF_ENTROPY),
)


def test_a_model_with_imported_parameters_needs_no_calculation_at_all():
    """Imported geometry and frequencies are supported provenance for their roles, not a local job to fabricate."""
    a = assess(declared(sources=IMPORTED), calcs={})
    assert a.applicability is A, codes(a)
    assert a.evidence.get("parameters") == ("lit_a", "lit_b")
    # disclosed, never silent: the source's own stationary-point statement is not re-validated here
    assert "imported_parameters_not_revalidated:geometry" in a.advisory
    assert "imported_parameters_not_revalidated:curvature" in a.advisory
    # the same models answer the component claims
    assert assess(declared(sources=IMPORTED), req(ROT), {}).applicability is A
    zpe = declared(recipe={"scale_contributions": ["zero_point_energy"]}, sources=(*IMPORTED[:2], fsf("zpe")))
    assert assess(zpe, req(ZPE), {}).applicability is A


def test_imported_parameters_only_stand_in_for_the_roles_they_are_declared_for():
    only_geometry = assess(declared(sources=(IMPORTED[0], IMPORTED[2])), calcs={})
    assert only_geometry.applicability is not A and "required_evidence_unavailable:curvature" in codes(only_geometry)
    only_curvature = assess(declared(sources=(IMPORTED[1], IMPORTED[2])), calcs={})
    assert only_curvature.applicability is not A and "required_evidence_unavailable:geometry" in codes(only_curvature)
    # a parameters-role source is attribution, not a geometry
    parameters = src("p", "parameters", None, locator="literature", literature_ref="lit_c")
    assert assess(declared(sources=(parameters, IMPORTED[2])), calcs={}).applicability is not A


def test_an_imported_source_meets_its_role_only_when_its_literature_is_identified():
    """A DOI or ISBN identifies the literature (the policy resolves them); a title-only citation is kept but unusable."""
    ok = assess(declared(sources=IMPORTED), calcs={})
    assert ok.applicability is A and "source_literature_unidentified:geometry" not in ok.advisory
    for role, index in (("geometry", 0), ("curvature", 1)):
        loose = list(IMPORTED)
        loose[index] = src(loose[index].source_key, role, None, locator="literature", literature_ref="lit_x", literature_identified=False)
        a = assess(declared(sources=tuple(loose)), calcs={})
        assert a.applicability is not A
        assert f"required_source_unusable:{role}" in codes(a)
        assert f"source_literature_unidentified:{role}" in a.advisory  # the public reason: it has no DOI or ISBN
        assert "lit_x" not in a.evidence.get("parameters", ())  # kept on the record, never counted as evidence


def test_a_rotor_potential_must_be_a_torsional_surface_source_and_identified():
    """Role isolation: a source of another role never stands in for the potential, whatever its key."""
    rotor_potential = {"rotors": [rotor(representation="imported_potential", potential_source_key="geom")]}
    # the reviewer's probes: a geometry-role literature source, and the local geometry optimisation
    as_geometry_literature = with_torsion(rotor_potential, sources=(src("geom", "geometry", None, locator="literature", literature_ref="lit_a"), IMPORTED[1], IMPORTED[2]))
    result = assess(as_geometry_literature, calcs={})
    assert "required_source_unusable:torsional_surface" in codes(result)
    as_local_optimisation = with_torsion(rotor_potential, sources=sources_with(fsf("entropy")))
    assert "required_source_unusable:torsional_surface" in codes(assess(as_local_optimisation))
    unidentified = with_torsion(
        {"rotors": [rotor(representation="imported_potential", potential_source_key="pot")]},
        sources=(*IMPORTED, src("pot", "torsional_surface", None, locator="literature", literature_ref="lit_d", literature_identified=False)),
    )
    unidentified_result = assess(unidentified, calcs={})
    assert "required_source_unusable:torsional_surface" in codes(unidentified_result)
    assert "source_literature_unidentified:torsional_surface" in unidentified_result.advisory


def test_an_imported_free_rotor_and_a_tabulated_potential_need_no_scan_job():
    free = with_torsion(sources=IMPORTED)
    assert assess(free, calcs={}).applicability is A, codes(assess(free, calcs={}))
    tabulated = with_torsion(
        {"rotors": [rotor(representation="imported_potential", potential_source_key="pot")]},
        sources=(*IMPORTED, src("pot", "torsional_surface", None, locator="literature", literature_ref="lit_d")),
    )
    result = assess(tabulated, calcs={})
    assert result.applicability is A, codes(result)
    missing = with_torsion({"rotors": [rotor(representation="imported_potential", potential_source_key="nope")]}, sources=IMPORTED)
    assert "required_source_unusable:torsional_surface" in codes(assess(missing, calcs={}))


def test_a_rotational_component_asks_nothing_of_temperature_energy_zero_or_scale():
    a = assess(declared(recipe={"scale_application": ..., "scale_contributions": [], "energy_zero": ...}), req(ROT))
    assert a.applicability is A, codes(a)
    assert CG and a.recipe is not None
