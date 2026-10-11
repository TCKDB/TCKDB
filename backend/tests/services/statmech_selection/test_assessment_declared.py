"""The statmech assessor over a declared model: each test pins one distinction the specification draws."""

from __future__ import annotations

import pytest

from app.services.selection_kernel import Applicability
from tests.services.statmech_selection._support import (
    FSF_ENTROPY,
    approved_calcs,
    assess,
    calc,
    codes,
    declared,
    finding_facts,
    freq,
    src,
)

A, INC, S, U = Applicability.applicable, Applicability.incompatible, Applicability.unsupported, Applicability.unresolved


def test_a_declared_basin_model_with_everything_it_needs_is_applicable_and_states_its_evidence():
    a = assess(declared())
    assert a.applicability is A and a.reasons == (), codes(a)
    assert a.evidence["curvature"] == ("calc_f",) and a.evidence["geometry"] == ("calc_o",)
    assert a.recipe is not None and a.recipe.cohort_key is not None and a.recipe.unestablished == ()


def test_an_entropy_claim_needs_no_energy_zero_but_every_statistical_factor_must_be_stated():
    assert assess(declared(recipe={"energy_zero": ..., "zpe_included_in_energies": ...})).applicability is A
    expected = {
        "external_symmetry_state": "required_recipe_unknown:external_symmetry",
        "optical_state": "required_recipe_unknown:optical_factor",
        "electronic_levels_state": "required_recipe_unknown:electronic_degeneracy",
        "isotope_masses": "required_recipe_unknown:isotope_masses",
        "translation_included": "required_recipe_unknown:translation",
    }
    for field, code in expected.items():
        a = assess(declared(recipe={field: ...}))
        assert a.applicability is U and code in codes(a), (field, codes(a))  # an omitted fact is unknown, never a default
        unknown = assess(declared(recipe={field: "unknown"} if field.endswith("_state") else {field: ...}))
        assert unknown.applicability is U, field


# --- representation and scope ----------------------------------------------------------------------------------------


def test_a_declaration_of_a_version_this_release_cannot_read_is_unsupported_not_applicable():
    base = declared()
    unreadable = declared(declaration=base.declaration.__class__(**{**base.declaration.__dict__, "schema_version": 2}))
    a = assess(unreadable)
    assert a.applicability is S and "declaration_version_unsupported" in codes(a)


def test_a_model_for_another_scope_is_incompatible_with_this_request():
    assert "scope_incompatible" in codes(assess(declared(scope="atom")))
    other_basin = declared()
    other_basin = declared(
        declaration=other_basin.declaration.__class__(**{**other_basin.declaration.__dict__, "conformer_group_ref": "cg_zzzzzzzzzzzzzzzzzzzzzzzzzz"})
    )
    a = assess(other_basin)
    assert a.applicability is INC and "scope_incompatible" in codes(a)


# --- availability: sources are judged at their own floor ---------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda c: {}, id="the-curvature-job-is-not-loaded-at-all"),
        pytest.param(lambda c: {"calc_f": freq("calc_f", review_status=__import__("app.db.models.common", fromlist=["x"]).RecordReviewStatus.rejected)}, id="rejected"),
        pytest.param(lambda c: {"calc_f": freq("calc_f", quality="rejected")}, id="rejected-quality"),
    ],
)
def test_a_source_that_is_hidden_rejected_or_below_the_floor_cannot_certify_and_names_no_record(mutate):
    calcs = approved_calcs()
    calcs.pop("calc_f")
    calcs.update(mutate(calcs))
    a = assess(declared(), calcs=calcs)
    assert a.applicability is U
    assert "required_source_unusable:curvature" in codes(a)
    assert "calc_f" not in " ".join(codes(a)) and "calc_f" not in str(a.advisory)  # one generic reason, no ref


def test_a_parents_approval_does_not_approve_a_source():
    """The owner record is approved; the pinned frequency job is not reviewed at all, and the floor is approved-only."""
    from app.db.models.common import RecordReviewStatus

    approved_only = frozenset({RecordReviewStatus.approved})
    calcs = approved_calcs()
    calcs["calc_f"] = freq("calc_f", review_status=RecordReviewStatus.not_reviewed)
    a = assess(declared(), calcs=calcs, effective=approved_only)
    assert a.applicability is U and "required_source_unusable:curvature" in codes(a)


def test_an_owner_record_below_the_floor_is_not_certified_even_when_all_its_sources_pass():
    from app.db.models.common import RecordReviewStatus

    a = assess(declared(review_status=RecordReviewStatus.not_reviewed), effective=frozenset({RecordReviewStatus.approved}))
    assert a.applicability is U and "required_evidence_unavailable:record" in codes(a)


# --- coherent role use ----------------------------------------------------------------------------------------------------


def test_a_curvature_witness_on_another_geometry_is_disclosed_and_does_not_certify():
    calcs = approved_calcs()
    calcs["calc_f"] = freq("calc_f", geometry="geom_other")
    a = assess(declared(), calcs=calcs)
    assert a.applicability is U
    assert "curvature_on_other_geometry" in a.advisory


def test_two_pinned_geometries_are_undeclared_alternatives_not_a_pick():
    other = calc("calc_o2", type="opt", output_geometry_refs=("geom_other",))
    calcs = approved_calcs(other)
    c = declared(sources=(src("g1", "geometry", "calc_o"), src("g2", "geometry", "calc_o2"), src("curv", "curvature", "calc_f")))
    a = assess(c, calcs=calcs)
    assert a.applicability is U and "multiple_geometry_alternatives" in codes(a)


def test_an_opt_calculation_with_a_hessian_is_a_curvature_witness_without_a_job_type_gate():
    from tests.services.statmech_selection._support import CurvatureFacts

    composite_like = calc(
        "calc_o", type="opt", energy=-76.4, curvature=CurvatureFacts(has_freq_result=True, n_imag=0)
    )
    sources = (
        src("geom", "geometry", "calc_o"),
        src("curv", "curvature", "calc_o"),
        src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=FSF_ENTROPY),
    )
    a = assess(declared(sources=sources), calcs={"calc_o": composite_like})
    assert a.applicability is A, codes(a)
    assert a.evidence["curvature"] == ("calc_o",)


# --- the stationary-point owner and findings ----------------------------------------------------------------------------


def test_an_imported_curvature_source_never_overrides_a_local_witness_that_contradicts_the_claim():
    from tests.services.statmech_selection._support import FSF_ENTROPY, CurvatureFacts, src

    curvature = CurvatureFacts(
        has_freq_result=True, n_imag=1, imag_freq_cm1=-500.0, imaginary_modes=((1, -500.0, None),), tau_cm1=50.0, tau_basis="default"
    )
    calcs = approved_calcs()
    calcs["calc_f"] = freq("calc_f", curvature=curvature)
    sources = (
        src("geom", "geometry", "calc_o"),
        src("curv", "curvature", "calc_f"),
        src("imp", "curvature", None, locator="literature", literature_ref="lit_b"),
        src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=FSF_ENTROPY),
    )
    a = assess(declared(sources=sources), calcs=calcs)
    assert a.applicability is INC and "curvature_contradicts_claim" in codes(a)


def test_an_imaginary_mode_above_tau_contradicts_a_minimum_claim_by_magnitude_not_count():
    from tests.services.statmech_selection._support import CurvatureFacts

    curvature = CurvatureFacts(
        has_freq_result=True, n_imag=1, imag_freq_cm1=-500.0, imaginary_modes=((1, -500.0, None),), tau_cm1=50.0, tau_basis="default"
    )
    calcs = approved_calcs()
    calcs["calc_f"] = freq("calc_f", curvature=curvature)
    a = assess(declared(), calcs=calcs)
    assert a.applicability is INC and "curvature_contradicts_claim" in codes(a)


def test_a_soft_imaginary_mode_below_tau_does_not_contradict_it():
    from tests.services.statmech_selection._support import CurvatureFacts

    curvature = CurvatureFacts(
        has_freq_result=True, n_imag=1, imag_freq_cm1=-20.0, imaginary_modes=((1, -20.0, None),), tau_cm1=50.0, tau_basis="default"
    )
    calcs = approved_calcs()
    calcs["calc_f"] = freq("calc_f", curvature=curvature)
    a = assess(declared(), calcs=calcs)
    assert "curvature_contradicts_claim" not in codes(a)


def test_a_confirmed_same_target_disproof_cannot_be_evaded_by_a_passing_older_job():
    """A live, authorized invalidation of the pinned frequency job blocks the claim, whatever else passes."""
    calcs = approved_calcs()
    calcs["calc_f"] = freq("calc_f", findings=(finding_facts("sfnd_1", kind="identity_incompatibility", subject_ref="calc_f"),))
    a = assess(declared(), calcs=calcs)
    assert a.applicability is INC and any(c.startswith("finding_invalidates") for c in codes(a))


def test_an_unrelated_failed_rerun_does_not_erase_a_coherent_model():
    """A newer failing job that the model does not pin is not loaded as evidence for it, so it changes nothing."""
    newer = freq("calc_f_new", n_imag=3, quality="rejected")
    calcs = approved_calcs(newer)
    assert assess(declared(), calcs=calcs).applicability is A


def test_a_curvature_invalidation_does_not_invalidate_a_separately_valid_recorded_energy():
    calcs = approved_calcs()
    calcs["calc_o"] = calc(
        "calc_o", type="opt", energy=-76.4, findings=(finding_facts("sfnd_1", kind="contradictory_characterization", subject_ref="calc_o"),)
    )
    # The contradictory characterization is about curvature; the optimisation is used here for geometry, not curvature.
    a = assess(declared(), calcs=calcs)
    assert not any(c.startswith("finding_invalidates") for c in codes(a))


def test_only_an_authorized_adjudication_about_the_same_subject_settles_a_finding():
    from tests.services.statmech_selection._support import finding_facts as ff

    bad = ff("sfnd_1", kind="identity_incompatibility", subject_ref="calc_f")
    settled = ff("sfnd_2", kind="adjudication", subject_ref="calc_f", verdict="does_not_invalidate", supersedes="sfnd_1")
    producer = ff("sfnd_3", kind="adjudication", subject_ref="calc_f", verdict="does_not_invalidate", supersedes="sfnd_1", authority="producer_assertion")
    elsewhere = ff("sfnd_4", kind="adjudication", subject_ref="calc_other", verdict="does_not_invalidate", supersedes="sfnd_1")
    for findings, expect in (((bad, settled), A), ((bad, producer), INC), ((bad, elsewhere), INC)):
        calcs = approved_calcs()
        calcs["calc_f"] = freq("calc_f", findings=findings)
        assert assess(declared(), calcs=calcs).applicability is expect, findings
