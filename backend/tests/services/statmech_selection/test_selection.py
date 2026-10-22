"""Selection over real rows: qualify, decide and record in one snapshot, then replay the recorded decision offline."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.api.error_contract import CodedValueError
from app.db.models.common import RecordReviewStatus
from app.services.scientific_read.profile import reset_current_read_profile
from app.services.statmech_selection.manifest import (
    canonical,
    replay_matches,
    replay_statmech_decision,
    reproduce_statmech_ordering,
)
from app.services.statmech_selection.models import SelectionBounds
from app.services.statmech_selection.selection import select_statmech
from app.services.statmech_selection.vocabulary import Outcome
from tests.services.statmech_selection.test_service import _literature, build
from tests.services.structure_selection._support import curated


def preferred(scene, **changes):
    return scene.request(intent="protocol_preferred", **changes)


def select(scene, request=None, **kw):
    return select_statmech(scene.session, request=request or preferred(scene), owner_id=scene.world.entry.id, require_snapshot=False, **kw)


def test_a_selection_records_a_manifest_that_replays_from_itself(db_session):
    scene = build(db_session)
    result = select(scene)
    assert result.outcome is Outcome.sole_eligible_candidate
    assert result.decision.selection.sole_eligible_ref == result.qualification.candidates[0].candidate_ref
    assert result.manifest["outcome"] == "sole_eligible_candidate" and result.manifest["read_profile"]["profile"] == "exploratory"
    assert replay_matches(result.manifest)
    assert reproduce_statmech_ordering(result.manifest)["decision"] == result.manifest["decision"]
    assert replay_statmech_decision(result.manifest)["decision"] == result.manifest["decision"]


def test_a_qualify_selection_selects_nothing_and_still_records_its_manifest(db_session):
    scene = build(db_session)
    result = select(scene, scene.request())
    assert result.outcome is Outcome.qualified_models and result.decision.selection.sole_eligible_ref is None
    assert result.manifest["decision"]["selection"] == {
        "sole_eligible_ref": None, "administrative_ref": None, "basis": None, "limitation": None,
    }
    assert replay_matches(result.manifest)


def test_nothing_is_decided_from_a_refused_population_or_an_oversized_manifest(db_session):
    scene = build(db_session)
    with pytest.raises(CodedValueError) as caught:
        select(scene, replace(preferred(scene), bounds=SelectionBounds(manifest_bytes=500)))
    assert caught.value.code == "statmech_selection_bound_exceeded" and caught.value.context["bound"] == "manifest_bytes"
    with pytest.raises(CodedValueError) as caught:
        select(scene, replace(preferred(scene), bounds=SelectionBounds(candidates=0)))
    assert caught.value.context["bound"] == "candidates"


def test_imported_parameter_advisories_reach_the_decision_and_the_manifest_from_real_rows(db_session):
    scene = build(db_session, imported=_literature(db_session, doi="10.1000/imported"))
    result = select(scene)
    (candidate_ref,) = (c.candidate_ref for c in result.qualification.candidates)
    advisories = result.manifest["decision"]["advisories"][candidate_ref]
    assert "imported_parameters_not_revalidated:geometry" in advisories and "imported_parameters_not_revalidated:curvature" in advisories
    assert any("not re-judged" in n for n in result.manifest["decision"]["notices"])
    assert replay_matches(result.manifest)

    loose = select(build(db_session, imported=_literature(db_session)))
    assert loose.outcome is not Outcome.sole_eligible_candidate  # a title-only citation meets no role
    assert any("no DOI or ISBN" in n for n in loose.manifest["decision"]["notices"])


def test_under_the_curated_profile_the_manifest_redacts_what_the_floor_hides_and_still_replays(db_session):
    visible = build(db_session)
    from app.db.models.common import ScientificOriginKind, SubmissionRecordType
    from app.db.models.statmech import Statmech
    from tests.services.statmech_selection.test_service import set_review

    hidden = Statmech(species_entry_id=visible.world.entry.id, scientific_origin=ScientificOriginKind.computed)
    db_session.add(hidden)
    db_session.flush()
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=hidden.id, status=RecordReviewStatus.not_reviewed)
    token = curated()
    try:
        result = select(visible)
    finally:
        reset_current_read_profile(token)
    manifest = result.manifest
    assert manifest["read_profile"] == {"profile": "curated", "review_floor": "approved"}
    assert manifest["population"]["excluded_by_review"] == [] and manifest["population"]["total_candidates"] == 1
    assert hidden.public_ref not in canonical(manifest)
    assert replay_matches(manifest)


def test_a_manifest_from_real_rows_carries_no_database_id(db_session):
    manifest = select(build(db_session)).manifest

    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    assert not [k for k in keys(manifest) if k == "id" or k.endswith("_id")]
