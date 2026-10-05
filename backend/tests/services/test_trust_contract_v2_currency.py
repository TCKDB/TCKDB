"""The trust-contract correction restales exactly the six reviewer families it changed, and rewrites nothing.

The computed rubrics for calculation, kinetics, thermo, statmech and transport moved to version 2 and the transition
state rubric to version 3 (an automated geometry-validation fail is advisory; the saddle frequency contradiction is
judged over every source result). A machine review stamped with the old rubric version is therefore genuinely stale:
it was made under rules the record is no longer read by. This pins three things:

* each of those six reviewer-family recipes now compares stale against a review stored with the previous version, with
  the one stale reason ``rubric_versions_mismatch`` (so the mechanism is the existing versioned-recipe currency, not a
  new flag);
* nothing else restales: every scientific-check family and the external-Cp runner keeps its key, so a review stored
  under it still compares current;
* a stored review row is never rewritten: planning a re-review of a record whose review is stale leaves the stored
  row, its recipe and its verdict exactly as they were and only says that a new review should run.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app.db.models.record_machine_review import RecordMachineReviewRow
from app.services.machine_review.admin_trigger import active_rubric_versions_for_record_type
from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.currency import (
    MachineReviewCurrencyState,
    MachineReviewStaleReason,
    StoredMachineReviewProjection,
    classify_machine_review_currency,
)
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.rereview import MachineReviewReReviewDecision, plan_record_machine_rereview
from app.services.machine_review.schemas import MachineReviewStatus
from tests.services.test_machine_review_recipe_filtering import _ADVISORY_RECIPES
from tests.services.test_phase_d_persistence import setup_records

_DIGEST = MachineReviewContextDigest(context_hash="c" * 64, context_schema_version="v1")
_PROMPT = "machine_review_v1"

#: What each reviewer family stored before this change: the previous rubric version.
_PREVIOUS = {
    "calculation": {"computed_calculation_v1": "1"},
    "kinetics": {"computed_kinetics_v1": "1"},
    "thermo": {"computed_thermo_v1": "1"},
    "statmech": {"computed_statmech_v1": "1"},
    "transport": {"computed_transport_v1": "1"},
    "transition_state_entry": {"computed_transition_state_v2": "2"},
}
#: What it stores now.
_NOW = {
    "calculation": {"computed_calculation_v2": "2"},
    "kinetics": {"computed_kinetics_v2": "2"},
    "thermo": {"computed_thermo_v2": "2"},
    "statmech": {"computed_statmech_v2": "2"},
    "transport": {"computed_transport_v2": "2"},
    "transition_state_entry": {"computed_transition_state_v3": "3"},
}


def _classify(record_type, stored, active):
    projection = StoredMachineReviewProjection(
        record_type=record_type, record_id=1, reviewed_at=datetime(2026, 9, 1),
        context_schema_version=_DIGEST.context_schema_version, context_hash=_DIGEST.context_hash,
        prompt_version=_PROMPT, rubric_versions=stored,
    )
    return classify_machine_review_currency(
        [projection], current_context=_DIGEST, active_prompt_version=_PROMPT, active_rubric_versions=active
    )


def test_the_premise_six_families_changed_and_each_pair_differs():
    assert set(_PREVIOUS) == set(_NOW) and len(_NOW) == 6
    for record_type in _NOW:
        assert active_rubric_versions_for_record_type(record_type) == _NOW[record_type]
        assert _PREVIOUS[record_type] != _NOW[record_type]


@pytest.mark.parametrize("record_type", sorted(_NOW))
def test_a_review_stored_under_the_previous_rubric_version_is_stale_for_that_reason_alone(record_type):
    result = _classify(record_type, _PREVIOUS[record_type], active_rubric_versions_for_record_type(record_type))
    assert (result.state, result.stale_reasons) == (
        MachineReviewCurrencyState.stale, (MachineReviewStaleReason.rubric_versions_mismatch,)
    )


@pytest.mark.parametrize("record_type", sorted(_NOW))
def test_a_review_stored_under_the_new_version_is_current(record_type):
    active = active_rubric_versions_for_record_type(record_type)
    result = _classify(record_type, dict(active), active)
    assert (result.state, result.stale_reasons) == (MachineReviewCurrencyState.current, ())


@pytest.mark.parametrize("recipe", sorted(_ADVISORY_RECIPES.values(), key=lambda r: sorted(r)), ids=lambda r: next(iter(r)))
def test_no_scientific_check_family_or_the_external_cp_runner_is_restaled(recipe):
    """These keep their own key and version, so a review stored under it still compares current."""
    assert _classify("thermo", dict(recipe), dict(recipe)).state is MachineReviewCurrencyState.current


def test_planning_a_re_review_of_a_stale_record_rewrites_no_stored_row(db_session):
    thermo, _, _ = setup_records(db_session)
    create_record_machine_review_row(
        db_session,
        record_type="thermo",
        record_id=thermo.id,
        review=RecordMachineReview(
            record_type="thermo", record_ref=thermo.public_ref, status=MachineReviewStatus.machine_screened_pass,
            reviewed_at=datetime(2026, 9, 1), record_id=thermo.id,
        ),
        context_digest=_DIGEST,
        prompt_version=_PROMPT,
        rubric_versions=_PREVIOUS["thermo"],
    )
    db_session.flush()

    def snapshot():
        rows = db_session.scalars(
            select(RecordMachineReviewRow).where(RecordMachineReviewRow.record_id == thermo.id)
        ).all()
        return [(r.id, dict(r.rubric_versions_json), r.prompt_version, str(r.status)) for r in rows]

    before = snapshot()
    plan = plan_record_machine_rereview(
        db_session, record_type="thermo", record_id=thermo.id, current_context=_DIGEST,
        active_prompt_version=_PROMPT, active_rubric_versions=active_rubric_versions_for_record_type("thermo"),
    )
    assert plan.decision is MachineReviewReReviewDecision.run_stale
    assert snapshot() == before  # the old review stands as history, with its own recipe and verdict
    assert before and before[0][1] == _PREVIOUS["thermo"]
