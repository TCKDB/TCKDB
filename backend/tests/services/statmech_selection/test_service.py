"""The qualification service over real rows: authorized loading, hidden dependencies, bounds, the snapshot, the population."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.common import (
    EnergyCorrectionApplicationRole,
    EnergyUnit,
    FrequencyScaleKind,
    RecordReviewStatus,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechModelScopeKind,
    StatmechModelSourceRole,
    StructureDeterminationTargetKind,
    SubmissionRecordType,
)
from app.db.models.energy_correction import AppliedEnergyCorrection, FrequencyScaleFactor
from app.db.models.record_review import RecordReview
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelSource
from app.services.read_snapshot import SnapshotNotConsistentError
from app.services.scientific_read.profile import reset_current_read_profile
from app.services.statmech_selection.models import SelectionBounds
from app.services.statmech_selection.service import qualify_target
from tests.services.scientific_read._factories import (
    attach_dependency,
    attach_statmech_electronic_level,
    attach_statmech_source_calculation,
    make_conformer_group,
    make_conformer_observation,
    make_literature,
    make_statmech,
    set_review,
)
from tests.services.statmech_selection._support import BASE_RECIPE, THERMAL, make, req
from tests.services.statmech_selection.test_request import TS
from tests.services.structure_selection._support import curated, make_ts_world, make_world

APPROVED = RecordReviewStatus.approved


@dataclass
class Scene:
    session: Any
    world: Any
    group: Any
    host: Statmech
    freq: Any
    opt: Any

    def request(self, **changes):
        base = make(THERMAL, target__species_entry_ref=self.world.entry.public_ref, target__scope__conformer_group_ref=self.group.public_ref)
        return req(base, **changes)

    def qualify(self, request=None, **kw):
        return qualify_target(self.session, request=request or self.request(), owner_id=self.world.entry.id, require_snapshot=False, **kw)


_VALUES = itertools.count(1)


def scale_factor(session, world, kind=FrequencyScaleKind.entropy, value=None) -> FrequencyScaleFactor:
    value = value if value is not None else 0.5 + next(_VALUES) / 1000
    row = FrequencyScaleFactor(level_of_theory_id=world.lot.id, scale_kind=kind, value=value)
    session.add(row)
    session.flush()
    return row


def restatus(session, record_type, record_id, status) -> None:
    """Change an existing review row's status (a record has exactly one review row)."""
    review = session.scalar(select(RecordReview).where(RecordReview.record_type == record_type, RecordReview.record_id == record_id))
    review.status = status
    session.flush()


def build(
    db_session, *, declared=True, freq_status=APPROVED, host_status=APPROVED, recipe=None, anchor=False, chain=0, imported=None
) -> Scene:
    world = make_world(db_session)
    group = make_conformer_group(db_session, world.entry)
    observation = make_conformer_observation(db_session, conformer_group=group) if anchor else None
    opt = world.opt("opt", -76.4, observation=observation)
    freq = world.freq("freq", frequencies=(100.0, 200.0, 300.0), status=freq_status, observation=observation)
    previous = freq
    for index in range(chain):
        parent = world.opt(f"chain_{index}", -76.4, observation=observation)
        attach_dependency(db_session, parent=parent, child=previous)
        previous = parent
    fsf = scale_factor(db_session, world)
    host = make_statmech(db_session, species_entry=world.entry, optical_isomers=1, external_symmetry=2, frequency_scale_factor_id=fsf.id)
    host.rigid_rotor_kind = RigidRotorKind.asymmetric_top
    host.rotational_constant_a_cm1, host.rotational_constant_b_cm1, host.rotational_constant_c_cm1 = 3.0, 2.0, 1.0
    host.uses_projected_frequencies = False
    attach_statmech_electronic_level(db_session, statmech=host, level_index=1, energy_cm1=0.0, degeneracy=1)
    attach_statmech_source_calculation(db_session, statmech=host, calculation=opt, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(db_session, statmech=host, calculation=freq, role=StatmechCalculationRole.freq)
    if declared:
        declaration = StatmechModelDeclaration(
            statmech_id=host.id,
            schema_version=1,
            scope_kind=StatmechModelScopeKind.basin,
            conformer_group_id=group.id,
            literature_id=make_literature(db_session).id,
            recipe={**dict(BASE_RECIPE), **(recipe or {})},
            content_hash="d" * 64,
        )
        db_session.add(declaration)
        db_session.flush()
        for key, role, kwargs in (
            ("geom", StatmechModelSourceRole.geometry, {"literature_id": imported.id} if imported else {"calculation_id": opt.id}),
            ("curv", StatmechModelSourceRole.curvature, {"literature_id": imported.id} if imported else {"calculation_id": freq.id}),
            ("fsf", StatmechModelSourceRole.scale_factor, {"frequency_scale_factor_id": fsf.id}),
        ):
            db_session.add(StatmechModelSource(declaration_id=declaration.id, source_key=key, role=role, **kwargs))
    else:
        db_session.add(
            AppliedEnergyCorrection(
                target_species_entry_id=world.entry.id,
                source_calculation_id=freq.id,
                frequency_scale_factor_id=fsf.id,
                application_role=EnergyCorrectionApplicationRole.entropy_contribution,
                value=0.01,
                value_unit=EnergyUnit.hartree,
            )
        )
    db_session.flush()
    world.settle()
    if host_status is not None:
        set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=host.id, status=host_status)
    return Scene(db_session, world, group, host, freq, opt)


# --- the happy paths -------------------------------------------------------------------------------------------------------


def _literature(db_session, **identifiers):
    from app.db.models.common import LiteratureKind
    from app.db.models.literature import Literature

    row = Literature(kind=LiteratureKind.article, title="A paper", year=2020, **identifiers)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.mark.parametrize("identifiers", [{"doi": "10.1000/xyz123"}, {"isbn": "9780306406157"}], ids=["doi", "isbn"])
def test_imported_literature_with_a_doi_or_an_isbn_meets_the_role_with_no_local_job(db_session, identifiers):
    scene = build(db_session, imported=_literature(db_session, **identifiers))
    (assessment,) = scene.qualify().assessments
    assert assessment.applicability.value == "applicable", [r.code for r in assessment.reasons]
    assert "imported_parameters_not_revalidated:geometry" in assessment.advisory


def test_imported_literature_without_a_doi_or_an_isbn_is_kept_but_unusable(db_session):
    scene = build(db_session, imported=_literature(db_session))
    (assessment,) = scene.qualify().assessments
    reasons = [r.code for r in assessment.reasons]
    assert assessment.applicability.value != "applicable"
    assert "required_source_unusable:geometry" in reasons and "required_source_unusable:curvature" in reasons
    assert "source_literature_unidentified:geometry" in assessment.advisory
    blank = build(db_session, imported=_literature(db_session, doi="  ", isbn=""))
    assert blank.qualify().assessments[0].applicability.value != "applicable"  # whitespace is not an identifier


def test_a_declared_basin_model_is_loaded_and_qualified_from_real_rows(db_session):
    scene = build(db_session)
    result = scene.qualify()
    assert result.summary.outcome == "qualified_models"
    (assessment,) = result.assessments
    assert assessment.kind.value == "declared_model" and assessment.candidate_ref.startswith("smd_")
    assert assessment.evidence["curvature"] == (scene.freq.public_ref,)
    assert result.visible_candidates == 1 and result.excluded_count == 0
    assert result.subject.entry_ref == scene.world.entry.public_ref and result.subject.is_atom is False
    assert [c.calculation_ref for c in result.calculations] and "calc_" in result.calculations[0].calculation_ref


def test_an_undeclared_record_is_a_legacy_candidate_read_as_stored(db_session):
    scene = build(db_session, declared=False, anchor=True)
    result = scene.qualify()
    (assessment,) = result.assessments
    assert assessment.kind.value == "legacy_record" and assessment.candidate_ref == scene.host.public_ref
    assert assessment.applicability.value == "applicable", [r.code for r in assessment.reasons]
    assert "legacy_record_read_as_stored" in assessment.advisory


def test_an_unanchored_legacy_record_is_not_tied_to_the_requested_basin(db_session):
    scene = build(db_session, declared=False, anchor=False)
    (assessment,) = scene.qualify().assessments
    assert assessment.applicability.value == "unresolved" and "scope_unestablished" in [r.code for r in assessment.reasons]


def test_a_record_that_declares_a_model_is_one_candidate_not_two(db_session):
    scene = build(db_session)
    kinds = [c.kind.value for c in scene.qualify().candidates]
    assert kinds == ["declared_model"]


# --- availability and visibility ---------------------------------------------------------------------------------------------


def test_a_source_below_the_floor_cannot_certify_and_no_record_is_named(db_session):
    scene = build(db_session, freq_status=RecordReviewStatus.not_reviewed)
    request = scene.request(min_review_status="approved")
    result = scene.qualify(request)
    (assessment,) = result.assessments
    assert assessment.applicability.value == "unresolved" and "required_source_unusable:curvature" in [r.code for r in assessment.reasons]
    assert scene.freq.public_ref not in str(assessment.to_dict())


def test_under_the_curated_profile_a_hidden_record_is_not_counted_listed_or_named(db_session):
    visible = build(db_session)
    hidden = Statmech(species_entry_id=visible.world.entry.id, scientific_origin=ScientificOriginKind.computed)
    db_session.add(hidden)
    db_session.flush()
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=hidden.id, status=RecordReviewStatus.not_reviewed)
    token = curated()
    try:
        result = visible.qualify()
    finally:
        reset_current_read_profile(token)
    assert result.total_candidates == 1 and result.excluded_count == 0 and result.excluded_by_review == ()
    assert hidden.public_ref not in str(result.summary.to_dict()) + str([c.candidate_ref for c in result.candidates])
    exploratory = visible.qualify(visible.request(min_review_status="approved"))
    assert exploratory.total_candidates == 2 and exploratory.excluded_count == 1
    assert exploratory.excluded_by_review[0]["candidate_ref"] == hidden.public_ref


def test_a_terminal_status_is_excluded_with_its_reason_and_is_never_an_unexplained_winner(db_session):
    scene = build(db_session, host_status=RecordReviewStatus.rejected)
    result = scene.qualify()
    assert result.summary.outcome == "no_candidates" and result.excluded_count == 1
    assert result.excluded_by_review[0]["reason"] == "terminal_review_status"
    # a declared candidate is named by its declaration everywhere, the exclusion list included
    assert result.excluded_by_review[0]["candidate_ref"].startswith("smd_")
    legacy = build(db_session, declared=False, host_status=RecordReviewStatus.rejected).qualify()
    assert legacy.excluded_by_review[0]["candidate_ref"].startswith("sm_")


def test_an_entry_below_the_callers_floor_excludes_its_records_at_every_grain(db_session):
    scene = build(db_session)
    restatus(db_session, SubmissionRecordType.species_entry, scene.world.entry.id, RecordReviewStatus.not_reviewed)
    result = scene.qualify(scene.request(min_review_status="approved"))
    assert result.visible_candidates == 0 and result.excluded_by_review[0]["reason"] == "owner_below_review_floor"


def test_an_unknown_target_is_a_404(db_session):
    scene = build(db_session)
    with pytest.raises(NotFoundError):
        qualify_target(db_session, request=scene.request(), owner_id=10**12, require_snapshot=False)


# --- the population --------------------------------------------------------------------------------------------------------


def test_an_explicit_population_names_candidates_and_an_unknown_hidden_or_foreign_one_reads_the_same(db_session):
    scene = build(db_session)
    other = build(db_session)
    declared_ref = scene.qualify().candidates[0].candidate_ref
    from app.services.statmech_selection.models import Population
    from app.services.statmech_selection.vocabulary import PopulationKind

    ok = scene.qualify(replace(scene.request(), population=Population(kind=PopulationKind.model_revisions, model_refs=(declared_ref,))))
    assert [a.candidate_ref for a in ok.assessments] == [declared_ref]
    foreign = other.qualify().candidates[0].candidate_ref
    answers = set()
    for ref in ("smd_" + "a" * 26, foreign, scene.host.public_ref):  # nothing, another target's, a declared record's own ref
        with pytest.raises(NotFoundError) as caught:
            scene.qualify(replace(scene.request(), population=Population(kind=PopulationKind.model_revisions, model_refs=(ref,))))
        answers.add((caught.value.code, str(caught.value).split("(")[0]))
    assert len(answers) == 1 and next(iter(answers))[0] == "unknown_statmech_candidate_ref"


def test_a_release_population_is_a_coded_refusal_not_a_silent_substitution(db_session):
    from app.services.statmech_selection.models import Population
    from app.services.statmech_selection.vocabulary import PopulationKind

    scene = build(db_session)
    request = replace(scene.request(), population=Population(kind=PopulationKind.release, release_ref="rel_" + "a" * 26))
    with pytest.raises(CodedValueError) as caught:
        scene.qualify(request)
    assert caught.value.code == "statmech_selection_unsupported"


def test_a_deferred_task_is_answered_without_loading_a_candidate_and_without_substitution(db_session):
    scene = build(db_session)
    body = make(THERMAL, task="density_of_states", observable="density_of_states", target__species_entry_ref=scene.world.entry.public_ref, target__scope__conformer_group_ref=scene.group.public_ref)
    body.pop("conditions")
    result = scene.qualify(req(body))
    assert result.summary.outcome == "unsupported_task" and result.assessments == () and result.candidates == ()


# --- bounds: at the limit is decided, past it is a refusal with no prefix winner -------------------------------------------


def test_the_candidate_bound_decides_at_the_limit_and_refuses_beyond_it(db_session):
    scene = build(db_session)
    second = Statmech(species_entry_id=scene.world.entry.id, scientific_origin=ScientificOriginKind.computed)
    db_session.add(second)
    db_session.flush()
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=second.id, status=APPROVED)
    request = scene.request()
    at_limit = replace(request, bounds=SelectionBounds(candidates=2))
    assert scene.qualify(at_limit).visible_candidates == 2
    with pytest.raises(CodedValueError) as caught:
        scene.qualify(replace(request, bounds=SelectionBounds(candidates=1)))
    assert caught.value.code == "statmech_selection_bound_exceeded"
    assert caught.value.context["bound"] == "candidates" and caught.value.context["visible"] == 2 and caught.value.context["limit"] == 1


def test_the_nested_row_bound_counts_what_the_population_needs(db_session):
    scene = build(db_session)
    needed = scene.qualify().nested_rows
    assert needed > 0
    assert scene.qualify(replace(scene.request(), bounds=SelectionBounds(nested_rows=needed))).nested_rows == needed
    with pytest.raises(CodedValueError) as caught:
        scene.qualify(replace(scene.request(), bounds=SelectionBounds(nested_rows=needed - 1)))
    assert caught.value.context["bound"] == "nested_rows" and caught.value.context["visible"] == needed


def test_the_recipes_own_arrays_are_nested_rows(db_session):
    base = build(db_session).qualify().nested_rows
    recipe = {
        "scale_contributions": ["vibrational_entropy", "vibrational_heat_capacity"],
        "projected_mode_indices": [1, 2, 3],
        "excluded_mode_indices": [4],
        "rotors": [
            {"torsion_index": 1, "representation": "free_rotor", "replaces_mode_indices": [5, 6]},
            {"torsion_index": 2, "representation": "free_rotor", "replaces_mode_indices": []},
        ],
    }
    scene = build(db_session, recipe=recipe)
    # base already counts the single default scale contribution; the rest are 1 + 3 + 1 + 2 rotors + 2 replaced modes
    assert scene.qualify().nested_rows - base == (2 - 1) + 3 + 1 + 2 + 2


def test_an_unfinished_dependency_traversal_is_a_refusal_not_a_complete_closure(db_session):
    scene = build(db_session, chain=3)
    with pytest.raises(CodedValueError) as caught:
        scene.qualify(replace(scene.request(), bounds=SelectionBounds(dependency_depth=1)))
    # this service's own code, not the structure service's that the shared walk raises
    assert caught.value.code == "statmech_selection_bound_exceeded"
    assert caught.value.context["bound"] == "dependency_depth"
    assert scene.qualify().summary.outcome == "qualified_models"  # the default depth closes the same chain


# --- the snapshot ----------------------------------------------------------------------------------------------------------


def test_the_service_insists_on_a_read_only_repeatable_read_snapshot_unless_told_otherwise(db_session):
    scene = build(db_session)
    with pytest.raises(SnapshotNotConsistentError):
        qualify_target(db_session, request=scene.request(), owner_id=scene.world.entry.id)  # the harness holds READ COMMITTED
    result = scene.qualify()
    assert result.snapshot_isolation == "read committed"  # the level actually in force is always reported


# --- a transition-state owner -----------------------------------------------------------------------------------------------


def build_ts_scene(db_session) -> SimpleNamespace:
    """A transition state entry with one approved TS-owned declared model; ``.body`` is a valid wire request for it."""
    ts = make_ts_world(db_session)
    freq = ts.freq("tf", frequencies=(-1500.0, 100.0), reaction_coordinate_mode_index=1, structural_flag=False)
    opt = ts.opt("topt", -76.0)
    determination = ts.determination(
        [("geometry_optimization", opt), ("curvature", freq)], target=StructureDeterminationTargetKind.saddle_point, quantity=None
    )
    host = Statmech(transition_state_entry_id=ts.entry.id, scientific_origin=ScientificOriginKind.computed, external_symmetry=1, optical_isomers=1)
    db_session.add(host)
    db_session.flush()
    fsf = scale_factor(db_session, ts, FrequencyScaleKind.fundamental, 0.9 + next(_VALUES) / 10000)
    attach_statmech_electronic_level(db_session, statmech=host, level_index=1, energy_cm1=0.0, degeneracy=1)
    declaration = StatmechModelDeclaration(
        statmech_id=host.id,
        schema_version=1,
        scope_kind=StatmechModelScopeKind.ts_determination,
        structure_determination_id=determination.id,
        literature_id=make_literature(db_session).id,
        recipe={
            **BASE_RECIPE,
            "scale_contributions": ["fundamental"],
            "dividing_surface": "conventional_saddle_point",
            "reaction_coordinate_excluded": True,
        },
        content_hash="e" * 64,
    )
    db_session.add(declaration)
    db_session.flush()
    for key, role, kwargs in (
        ("geom", StatmechModelSourceRole.geometry, {"calculation_id": opt.id}),
        ("curv", StatmechModelSourceRole.curvature, {"calculation_id": freq.id}),
        ("fsf", StatmechModelSourceRole.scale_factor, {"frequency_scale_factor_id": fsf.id}),
    ):
        db_session.add(StatmechModelSource(declaration_id=declaration.id, source_key=key, role=role, **kwargs))
    db_session.flush()
    ts.settle()
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=host.id, status=APPROVED)
    body = make(
        TS,
        target__transition_state_entry_ref=ts.entry.public_ref,
        target__scope__determination_ref=determination.public_ref,
    )
    return SimpleNamespace(world=ts, entry=ts.entry, determination=determination, host=host, declaration=declaration, body=body)


def test_a_ts_owned_declaration_is_qualified_on_a_real_ts_owner(db_session):
    scene = build_ts_scene(db_session)
    request = req(scene.body)
    result = qualify_target(db_session, request=request, owner_id=scene.entry.id, require_snapshot=False)
    assert result.subject.kind.value == "transition_state_entry" and result.subject.is_atom is False
    (assessment,) = result.assessments
    assert assessment.applicability.value == "applicable", [r.code for r in assessment.reasons]
    assert result.summary.outcome == "qualified_models"

