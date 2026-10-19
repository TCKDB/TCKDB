"""The transition-state rubric bump to version 5 (#723) restales the transition-state reviewer family only.

``computed_transition_state`` gained the zero-weight ``declared_saddle_target_qualifies`` warning and reads the
frequency result a declared saddle target pins, so a machine review stamped version 4 (or 3) was made under a check set
the record is no longer read by. This pins:

* a review stored under ``computed_transition_state_v4`` compares stale, with ``rubric_versions_mismatch`` (so does v3);
* every other reviewer family keeps its key, so a review stored under it still compares current;
* re-planning a transition-state review leaves the stored row, its recipe and its verdict exactly as they were.
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
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_DIGEST = MachineReviewContextDigest(context_hash="c" * 64, context_schema_version="v1")
_PROMPT = "machine_review_v1"
_V3 = {"computed_transition_state_v3": "3"}
_V4 = {"computed_transition_state_v4": "4"}
_V5 = {"computed_transition_state_v5": "5"}
_OTHER_FAMILIES = {
    "calculation": {"computed_calculation_v2": "2"},
    "kinetics": {"computed_kinetics_v2": "2"},
    "thermo": {"computed_thermo_v2": "2"},
    "statmech": {"computed_statmech_v2": "2"},
    "transport": {"computed_transport_v2": "2"},
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


def test_the_active_transition_state_recipe_is_version_5():
    assert active_rubric_versions_for_record_type("transition_state_entry") == _V5


@pytest.mark.parametrize("stored", [_V4, _V3], ids=["v4", "v3"])
def test_an_older_review_is_stale_for_the_rubric_version_alone(stored):
    result = _classify("transition_state_entry", stored, active_rubric_versions_for_record_type("transition_state_entry"))
    assert (result.state, result.stale_reasons) == (
        MachineReviewCurrencyState.stale, (MachineReviewStaleReason.rubric_versions_mismatch,)
    )


def test_a_v5_review_is_current():
    result = _classify("transition_state_entry", _V5, active_rubric_versions_for_record_type("transition_state_entry"))
    assert (result.state, result.stale_reasons) == (MachineReviewCurrencyState.current, ())


@pytest.mark.parametrize("record_type", sorted(_OTHER_FAMILIES))
def test_no_other_reviewer_family_is_restaled(record_type):
    active = active_rubric_versions_for_record_type(record_type)
    assert active == _OTHER_FAMILIES[record_type]
    assert _classify(record_type, dict(active), active).state is MachineReviewCurrencyState.current


def test_re_planning_a_stale_ts_review_rewrites_no_stored_row(db_session):
    def _entry(tag):
        return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))

    r1, p = _entry("TSV5R"), _entry("TSV5P")
    reaction = make_chem_reaction(db_session, reactants=[r1.species], products=[p.species], reversible=False)
    rxn_entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[r1], product_entries=[p])
    ts_entry = make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=rxn_entry)
    )
    create_record_machine_review_row(
        db_session,
        record_type="transition_state_entry",
        record_id=ts_entry.id,
        review=RecordMachineReview(
            record_type="transition_state_entry", record_ref=ts_entry.public_ref,
            status=MachineReviewStatus.machine_screened_pass, reviewed_at=datetime(2026, 9, 1), record_id=ts_entry.id,
        ),
        context_digest=_DIGEST,
        prompt_version=_PROMPT,
        rubric_versions=_V4,
    )
    db_session.flush()

    def snapshot():
        rows = db_session.scalars(
            select(RecordMachineReviewRow).where(RecordMachineReviewRow.record_id == ts_entry.id)
        ).all()
        return [(r.id, dict(r.rubric_versions_json), r.prompt_version, str(r.status)) for r in rows]

    before = snapshot()
    plan = plan_record_machine_rereview(
        db_session, record_type="transition_state_entry", record_id=ts_entry.id, current_context=_DIGEST,
        active_prompt_version=_PROMPT,
        active_rubric_versions=active_rubric_versions_for_record_type("transition_state_entry"),
    )
    assert plan.decision is MachineReviewReReviewDecision.run_stale
    assert snapshot() == before
    assert before and before[0][1] == _V4
