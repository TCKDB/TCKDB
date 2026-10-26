"""Transition-state models, legacy records read as stored, and the population summary."""

from __future__ import annotations

from dataclasses import replace

from app.db.models.common import RecordReviewStatus
from app.services.selection_kernel import Applicability
from app.services.statmech_selection.assessment import request_support, summarize
from app.services.statmech_selection.facts import DeterminationFacts
from app.services.statmech_selection.vocabulary import OwnerKind
from tests.services.statmech_selection._support import (
    APPROVED,
    CG,
    EFFECTIVE,
    G1,
    SDET,
    SMD,
    TSE,
    CurvatureFacts,
    approved_calcs,
    assess,
    calc,
    codes,
    declared,
    freq,
    legacy,
    make,
    record,
    req,
    subject,
    ts_declared,
    ts_req,
)
from tests.services.statmech_selection.test_request import THERMAL

A, INC, S, U = Applicability.applicable, Applicability.incompatible, Applicability.unsupported, Applicability.unresolved


def ts_subject():
    return subject(kind=OwnerKind.transition_state_entry, entry_ref=TSE, stationary_point_kind=None, electronic_state_kind=None, is_atom=False)


def saddle_calcs(**curvature):
    base = {
        "has_freq_result": True,
        "n_imag": 1,
        "imag_freq_cm1": -1500.0,
        "imaginary_modes": ((1, -1500.0, "reaction_coordinate"),),
        "reaction_coordinate_mode_index": 1,
        "tau_cm1": 50.0,
        "tau_basis": "default",
        "structural_flag": False,
    }
    base.update(curvature)
    return {"calc_o": calc("calc_o", type="opt", energy=-76.0), "calc_f": freq("calc_f", curvature=CurvatureFacts(**base))}


def assess_ts(candidate=None, calcs=None, request=None):
    return assess(candidate or ts_declared(), request or ts_req(), saddle_calcs() if calcs is None else calcs, subj=ts_subject())


# --- transition-state models ------------------------------------------------------------------------------------------------


def test_a_conventional_ts_transverse_model_on_a_real_ts_owner_is_applicable_without_irc_or_a_species_owner():
    a = assess_ts()
    assert a.applicability is A, codes(a)
    assert a.evidence["curvature"] == ("calc_f",)  # nothing demands an IRC, a raw imaginary-mode count or a species


def test_the_first_order_claim_is_judged_by_the_shared_owner_not_by_counting_imaginary_modes():
    # Two imaginary modes, the second below tau: the owner's warning tier, not a contradiction of a first-order claim.
    soft = saddle_calcs(n_imag=2, imaginary_modes=((1, -1500.0, "reaction_coordinate"), (2, -20.0, None)))
    assert assess_ts(calcs=soft).applicability is A
    # The extra mode is above tau: a higher-order saddle, recognised and not evaluated for a conventional model.
    higher = saddle_calcs(n_imag=2, imaginary_modes=((1, -1500.0, "reaction_coordinate"), (2, -300.0, None)))
    a = assess_ts(calcs=higher)
    assert a.applicability is S and "higher_order_saddle_flagged" in codes(a)
    none = assess_ts(calcs=saddle_calcs(n_imag=0, imag_freq_cm1=None, imaginary_modes=(), reaction_coordinate_mode_index=None))
    assert none.applicability is INC and "curvature_contradicts_claim" in codes(none)


def test_an_undesignated_reaction_coordinate_with_several_modes_is_unresolved_not_a_failure():
    calcs = saddle_calcs(n_imag=2, imaginary_modes=((1, -1500.0, None), (2, -300.0, None)), reaction_coordinate_mode_index=None)
    a = assess_ts(calcs=calcs)
    assert a.applicability is U and a.applicability is not INC


def test_the_dividing_surface_and_the_coordinate_exclusion_are_the_models_to_state():
    base = ts_declared()
    for recipe, expected in (
        ({"dividing_surface": ...}, "required_recipe_unknown:dividing_surface"),
        ({"reaction_coordinate_excluded": ...}, "required_recipe_unknown:reaction_coordinate_excluded"),
    ):
        a = assess_ts(ts_declared(recipe=recipe))
        assert a.applicability is U and expected in codes(a)
    assert assess_ts(ts_declared(recipe={"reaction_coordinate_excluded": False})).applicability is INC
    variational = assess_ts(ts_declared(recipe={"dividing_surface": "variational"}))
    assert variational.applicability is S and "dividing_surface_not_supported" in codes(variational)
    assert base.determination is not None


def test_the_models_determination_must_be_the_requested_one_and_available():
    other = replace(ts_declared(), determination=DeterminationFacts("sdet_" + "z" * 26, TSE, "saddle_point", G1, APPROVED))
    other = replace(other, declaration=replace(other.declaration, determination_ref="sdet_" + "z" * 26))
    assert "scope_incompatible" in codes(assess_ts(other))
    hidden = replace(ts_declared(), determination=DeterminationFacts(SDET, TSE, "saddle_point", G1, None))
    a = assess_ts(hidden)
    assert a.applicability is U and "required_evidence_unavailable:determination" in codes(a)
    elsewhere = replace(ts_declared(), determination=DeterminationFacts(SDET, TSE, "saddle_point", "geom_elsewhere", APPROVED))
    assert "scope_incompatible" in codes(assess_ts(elsewhere)) or assess_ts(elsewhere).applicability is not A


def test_a_ts_model_whose_reaction_coordinate_disagrees_with_the_owners_designation_is_refused():
    agree = assess_ts(ts_declared(recipe={"reaction_coordinate_mode_index": 1}))
    assert agree.applicability is A, codes(agree)
    disagree = assess_ts(ts_declared(recipe={"reaction_coordinate_mode_index": 2}))
    assert disagree.applicability is INC and "reaction_coordinate_mismatch" in codes(disagree)
    # Nothing to compare when the owner designated none: unresolved elsewhere, not a mismatch here.
    undesignated = assess_ts(ts_declared(recipe={"reaction_coordinate_mode_index": 2}), saddle_calcs(reaction_coordinate_mode_index=None))
    assert "reaction_coordinate_mismatch" not in codes(undesignated)


# --- legacy records read as stored -----------------------------------------------------------------------------------------


def test_a_legacy_record_that_states_everything_the_claim_needs_is_applicable_and_says_it_was_read_as_stored():
    a = assess(legacy())
    assert a.applicability is A, codes(a)
    assert "legacy_record_read_as_stored" in a.advisory
    assert a.recipe is not None and a.recipe.cohort_key is None  # a legacy record has no cohort: it creates no preference edge


def test_a_legacy_record_is_never_given_membership_or_a_scope_it_does_not_have():
    ensemble_request = req(
        THERMAL,
        task="canonical_model",
        observable="internal_partition",
        target__scope={"kind": "explicit_equilibrium_ensemble", "ensemble_declaration_ref": SMD},
    )
    a = assess(legacy(), ensemble_request)
    assert a.applicability is INC and "scope_incompatible" in codes(a)
    unanchored = assess(legacy(anchor_group_refs=()))
    assert unanchored.applicability is U and "scope_unestablished" in codes(unanchored)
    two = assess(legacy(anchor_group_refs=(CG, "cg_" + "z" * 26)))
    assert two.applicability is U and "scope_unestablished" in codes(two)
    elsewhere = assess(legacy(anchor_group_refs=("cg_" + "z" * 26,)))
    assert elsewhere.applicability is INC and "scope_incompatible" in codes(elsewhere)


def test_a_legacy_record_with_a_missing_or_ambiguous_fact_keeps_the_reason_the_classifier_gave():
    missing = assess(legacy(facts={"optical_isomers": None}))
    assert missing.applicability is U and "required_recipe_unknown:optical_factor" in codes(missing)
    ambiguous = assess(legacy(facts={"applied_scale_kinds": frozenset(), "has_scale_factor": True}))
    assert ambiguous.applicability is U and "ambiguous_fact:scale_application" in codes(ambiguous)
    wrong_purpose = assess(legacy(facts={"applied_scale_kinds": frozenset({"zpe"})}))
    assert "ambiguous_fact:scale_application" in codes(wrong_purpose)


def test_a_legacy_anharmonic_or_density_of_states_treatment_is_recognised_not_substituted():
    a = assess(legacy(record=record(statmech_treatment="rrao", source_links=(("opt", "calc_o"), ("freq", "calc_f")))))
    assert a.applicability is S and "treatment_not_supported:anharmonic" in codes(a)


def test_a_legacy_curvature_witness_is_any_linked_calculation_with_a_frequency_or_hessian_result():
    only_opt_with_curvature = {
        "calc_o": calc("calc_o", type="opt", curvature=CurvatureFacts(has_freq_result=True, n_imag=0)),
    }
    a = assess(legacy(links=(("opt", "calc_o"),)), calcs=only_opt_with_curvature)
    assert a.applicability is A, codes(a)
    assert a.evidence["curvature"] == ("calc_o",)


def test_a_linked_calculation_that_is_not_available_is_one_generic_reason():
    a = assess(legacy(), calcs={"calc_o": calc("calc_o", type="opt")})
    assert a.applicability is U and "required_source_unusable:linked_calculation" in codes(a)


def test_a_superseded_record_carries_its_correction_notice_into_the_assessment():
    notice = {"superseded_by": "sm_next", "current": "sm_next", "chain_length": 1}
    a = assess(legacy(supersession=notice))
    assert a.supersession == notice and "superseded_record" in a.advisory


# --- the request and the population summary --------------------------------------------------------------------------------


def test_a_deferred_task_is_answered_unsupported_with_nothing_substituted():
    body = make(THERMAL, task="density_of_states", observable="density_of_states", conditions=...)
    body.pop("conditions")
    request = req(body)
    assert request_support(request) == ("task_deferred:density_of_states",)
    summary = summarize(request, ())
    assert summary.outcome == "unsupported_task" and summary.qualified_refs == ()


def test_unsupported_conditions_are_the_requests_not_a_candidates():
    for change, expected in (
        ({"conditions__environment": {"kind": "solution"}}, "environment_not_supported:solution"),
        ({"target__isotope_convention": "natural_abundance_average"}, "isotope_convention_unsupported:natural_abundance_average"),
        ({"target__stereochemical_scope": "declared_stereoisomer_set"}, "stereochemical_scope_unsupported:declared_stereoisomer_set"),
    ):
        request = req(THERMAL, **change)
        assert request_support(request) == (expected,), change
        assert summarize(request, (assess(declared(), request),)).outcome == "unsupported_task"


def population(*candidates):
    return tuple(candidates)


def summarize_of(request, *assessments):
    return summarize(request, assessments)


def test_no_candidates_is_its_own_outcome():
    assert summarize_of(req()).outcome == "no_candidates"


def test_qualified_models_lists_them_and_scopes_the_answer_to_the_eligible_population():
    ok = assess(declared())
    unresolved = assess(declared(recipe={"scale_application": ...}, candidate_ref="smd_other", id_rank=2))
    scoped = summarize_of(req(THERMAL, coverage_requirement="eligible_population"), ok, unresolved)
    assert scoped.outcome == "qualified_models" and scoped.qualified_refs == (ok.candidate_ref,)
    assert scoped.undecided_refs == (unresolved.candidate_ref,) and scoped.notes


def test_the_entire_requested_domain_is_not_certified_while_an_alternative_is_undecided():
    ok = assess(declared())
    unresolved = assess(declared(recipe={"scale_application": ...}, candidate_ref="smd_other", id_rank=2))
    full = summarize_of(req(), ok, unresolved)  # entire_requested_domain is the default
    assert full.outcome == "missing_required_data"  # the relevant unresolved outcome
    assert full.qualified_refs == (ok.candidate_ref,)  # the qualified subset stays visible
    only_known_bad = summarize_of(req(), ok, assess(declared(scope="atom", candidate_ref="smd_x", id_rank=2)))
    assert only_known_bad.outcome == "qualified_models"  # a decided incompatibility does not block certification of the rest


def test_when_nothing_qualifies_the_outcome_reads_the_categories_in_the_stated_precedence():
    incompatible = assess(declared(scope="atom"))
    missing = assess(declared(recipe={"scale_application": ...}, candidate_ref="smd_m", id_rank=2))
    unresolved_shape = assess(legacy(anchor_group_refs=(), candidate_ref="sm_u", id_rank=3))
    unsupported = assess(declared(recipe={"vibrational_scheme": "anharmonic"}, candidate_ref="smd_s", id_rank=4))
    conflict_calcs = approved_calcs()
    conflict_calcs["calc_f"] = freq("calc_f", curvature=CurvatureFacts(has_freq_result=True, n_imag=0))
    request = req()
    assert summarize_of(request, incompatible).outcome == "no_applicable_candidate"
    assert summarize_of(request, incompatible, missing).outcome == "missing_required_data"
    assert summarize_of(request, incompatible, missing, unresolved_shape).outcome == "unresolved_applicability"
    assert summarize_of(request, unsupported).outcome == "unsupported_model"
    assert summarize_of(request, unsupported, incompatible).outcome == "unsupported_model"  # only known incompatibility besides
    assert summarize_of(request, unsupported, missing).outcome == "unresolved_applicability"  # mixed: not only missing inputs
    conflicted = assess(declared(candidate_ref="smd_c", id_rank=5), calcs={**approved_calcs(), "calc_f": freq("calc_f", findings=(__import__("tests.services.statmech_selection._support", fromlist=["x"]).finding_facts("sfnd_u", kind="identity_incompatibility", subject_ref="calc_f", verdict="unresolved"),))})
    assert "finding_unresolved:identity_incompatibility" in codes(conflicted)
    assert summarize_of(request, incompatible, missing, unsupported, conflicted).outcome == "evidence_conflict"


def test_every_candidate_keeps_all_its_reasons_when_categories_coexist():
    a = assess(declared(recipe={"scale_application": ..., "optical_state": ..., "vibrational_scheme": "anharmonic"}))
    assert {"scale_application_unknown", "required_recipe_unknown:optical_factor", "treatment_not_supported:anharmonic"} <= set(codes(a))
    assert a.applicability is S  # unsupported outranks unresolved on the candidate; every reason is still listed
    assert RecordReviewStatus.approved is APPROVED


# --- the declared determination under the evidence rule (ADR 0023, step 5) -----------------------------------------------------

#: The statuses the evidence rule admits under curated with no caller floor: everything but rejected and deprecated.
CURATED_EFFECTIVE = frozenset({RecordReviewStatus.approved})
CURATED_EVIDENCE = frozenset({RecordReviewStatus.approved, RecordReviewStatus.under_review, RecordReviewStatus.not_reviewed})


def assess_under_curated(determination):
    from app.services.statmech_selection.assessment import assess_candidate

    candidate = replace(ts_declared(), determination=determination)
    return assess_candidate(
        candidate,
        request=ts_req(),
        subject=ts_subject(),
        calculations=saddle_calcs(),
        effective_statuses=CURATED_EFFECTIVE,
        evidence_statuses=CURATED_EVIDENCE,
    )


def determination(**changes):
    return replace(DeterminationFacts(SDET, TSE, "saddle_point", G1, APPROVED), **changes)


def test_a_declared_determination_whose_group_is_unreviewed_is_used_and_whose_group_is_withheld_is_not():
    """The determination is linked evidence of the statmech record: unreviewed is used, rejected or deprecated is withheld.

    Kills: ignoring the group (a withheld group changing nothing) and holding the record floor on it (an unreviewed group
    making the determination unavailable).
    """
    used = assess_under_curated(determination(group_review_status=RecordReviewStatus.not_reviewed, observation_review_status=RecordReviewStatus.under_review))
    assert used.applicability is A, codes(used)
    for status in (RecordReviewStatus.rejected, RecordReviewStatus.deprecated):
        for field in ("group_review_status", "observation_review_status"):
            withheld = assess_under_curated(determination(**{field: status}))
            assert withheld.applicability is U and "required_evidence_unavailable:determination" in codes(withheld), field


def test_a_species_entry_owner_never_gates_its_determination_but_a_transition_state_entry_does():
    """Species and reaction entries are scoping parents (ADR 0023); a transition-state entry is a record (Q3).

    Kills: gating a determination on a species owner's status under curated; and not gating on a TS entry's.
    """
    below = RecordReviewStatus.not_reviewed
    species_owned = assess_under_curated(determination(review_status=below, owner_gates=False))
    assert species_owned.applicability is A, codes(species_owned)
    ts_owned = assess_under_curated(determination(review_status=below, owner_gates=True))
    assert ts_owned.applicability is U and "required_evidence_unavailable:determination" in codes(ts_owned)


def test_an_exploratory_determination_is_judged_exactly_as_before():
    """Without a floor the facts carry no conformer statuses and the owner gates, so the old rule is the only rule."""
    from app.services.statmech_selection.assessment import assess_candidate

    candidate = replace(ts_declared(), determination=determination(review_status=RecordReviewStatus.rejected))
    a = assess_candidate(
        candidate, request=ts_req(), subject=ts_subject(), calculations=saddle_calcs(), effective_statuses=EFFECTIVE
    )
    assert a.applicability is U and "required_evidence_unavailable:determination" in codes(a)
