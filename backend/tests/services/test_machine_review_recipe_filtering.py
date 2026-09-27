"""A stored machine-review recipe must be the filtered view (issue #553).

Currency compares a review's stored ``rubric_versions`` for exact equality with
the active one. Every consumer therefore stamps -- and later compares against --
only the one key for its own rubric out of ``ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS``:

* reviewer family, per record type: ``active_rubric_versions_for_record_type``;
* scientific-check family, per advisory check and runner: ``AdvisoryResult.recipe``;
* the external-Cp runner: ``cp.run_and_record`` / ``cp.cp_comparison_currency``.

These tests store each filtered recipe, then add a rubric key none of them owns
(the shape of every Phase D addition), and assert every stored recipe still
compares current. A consumer that stored the unfiltered dict turns them red --
the control in each test shows that dict *does* go stale under the same change.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.services.consistency import gibbs, hess, kinetics, kirchhoff
from app.services.consistency import thermo as thermo_check
from app.services.consistency.core import AdvisoryResult, currency
from app.services.consistency.service import invoke
from app.services.external_comparison import cp
from app.services.machine_review.admin_trigger import (
    SUPPORTED_RECORD_TYPES,
    active_rubric_versions_for_record_type,
)
from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.currency import (
    MachineReviewCurrencyState,
    MachineReviewStaleReason,
    StoredMachineReviewProjection,
    classify_machine_review_currency,
)
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS
from app.services.machine_review.rereview import (
    MachineReviewReReviewDecision,
    plan_record_machine_rereview,
)
from app.services.machine_review.schemas import MachineReviewStatus
from app.services.trust.rubrics import (
    EXTERNAL_CP_COMPARISON_V2,
    GIBBS_SELF_CONSISTENCY_V1,
    HESS_CONSISTENCY_V1,
    KIRCHHOFF_CONSISTENCY_V1,
    THERMO_CONSISTENCY_V1,
    THERMO_KINETICS_CONSISTENCY_V1,
)

#: A rubric key no current consumer owns -- stands in for the next check added.
UNRELATED_KEY = "unrelated_future_check_v1"

_DIGEST = MachineReviewContextDigest(context_hash="c" * 64, context_schema_version="v1")
_PROMPT = "machine_review_v1"

#: What each reviewer-family record type must store: exactly its own rubric.
_REVIEWER_RECIPES = {
    "calculation": {"computed_calculation_v1": "1"},
    "kinetics": {"computed_kinetics_v1": "1"},
    "thermo": {"computed_thermo_v1": "1"},
    "statmech": {"computed_statmech_v1": "1"},
    "transport": {"computed_transport_v1": "1"},
    "transition_state_entry": {"computed_transition_state_v2": "2"},
}

#: What each scientific-check runner must store: exactly its own rubric.
_ADVISORY_RECIPES = {
    (thermo_check.RUNNER, THERMO_CONSISTENCY_V1): {"thermo_consistency_v1": "1"},
    (kinetics.RUNNER, THERMO_KINETICS_CONSISTENCY_V1): {"thermo_kinetics_consistency_v1": "1"},
    (gibbs.RUNNER, GIBBS_SELF_CONSISTENCY_V1): {"gibbs_self_consistency_v1": "1"},
    (hess.RUNNER, HESS_CONSISTENCY_V1): {"hess_consistency_v1": "1"},
    (kirchhoff.RUNNER, KIRCHHOFF_CONSISTENCY_V1): {"kirchhoff_consistency_v1": "1"},
    (cp.RUNNER_VERSION, EXTERNAL_CP_COMPARISON_V2): {"external_cp_comparison_v2": "2"},
}


def _classify(stored_rubric_versions, active_rubric_versions, *, prompt=_PROMPT):
    stored = StoredMachineReviewProjection(
        record_type="thermo", record_id=1, reviewed_at=datetime(2026, 9, 1),
        context_schema_version=_DIGEST.context_schema_version, context_hash=_DIGEST.context_hash,
        prompt_version=prompt, rubric_versions=stored_rubric_versions,
    )
    return classify_machine_review_currency(
        [stored], current_context=_DIGEST, active_prompt_version=prompt,
        active_rubric_versions=active_rubric_versions,
    )


def test_the_expected_tables_cover_every_consumer():
    """Premise: the parametrized cases below are not empty and miss no consumer."""
    assert set(_REVIEWER_RECIPES) == set(SUPPORTED_RECORD_TYPES)
    assert len(_REVIEWER_RECIPES) == 6
    owned = {key for recipe in (*_REVIEWER_RECIPES.values(), *_ADVISORY_RECIPES.values()) for key in recipe}
    assert owned == set(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS)
    assert UNRELATED_KEY not in ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS


@pytest.mark.parametrize("record_type", sorted(_REVIEWER_RECIPES))
def test_reviewer_recipe_survives_an_unrelated_rubric(record_type, monkeypatch):
    stored = active_rubric_versions_for_record_type(record_type)
    assert stored == _REVIEWER_RECIPES[record_type]
    unfiltered = dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS)

    monkeypatch.setitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, UNRELATED_KEY, "1")

    active = active_rubric_versions_for_record_type(record_type)
    assert active == _REVIEWER_RECIPES[record_type]
    result = _classify(stored, active)
    assert (result.state, result.stale_reasons) == (MachineReviewCurrencyState.current, ())
    # Control: had the consumer stored the unfiltered dict, the same change restales it.
    control = _classify(unfiltered, dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS))
    assert (control.state, control.stale_reasons) == (
        MachineReviewCurrencyState.stale, (MachineReviewStaleReason.rubric_versions_mismatch,))


@pytest.mark.parametrize("runner,rubric", list(_ADVISORY_RECIPES), ids=lambda v: getattr(v, "name", v))
def test_scientific_check_recipe_survives_an_unrelated_rubric(runner, rubric, monkeypatch):
    def result():
        return AdvisoryResult(target=None, runner=runner, rubric=rubric, findings=(), inputs_json="{}")

    stored = result().recipe
    assert stored == _ADVISORY_RECIPES[(runner, rubric)]
    unfiltered = dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS)

    monkeypatch.setitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, UNRELATED_KEY, "1")

    active = result().recipe
    assert active == _ADVISORY_RECIPES[(runner, rubric)]
    classified = _classify(stored, active, prompt=runner)
    assert (classified.state, classified.stale_reasons) == (MachineReviewCurrencyState.current, ())
    control = _classify(unfiltered, dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS), prompt=runner)
    assert (control.state, control.stale_reasons) == (
        MachineReviewCurrencyState.stale, (MachineReviewStaleReason.rubric_versions_mismatch,))


def test_persisted_reviews_survive_an_unrelated_rubric(db_session, monkeypatch):
    """End to end through the real write and currency paths of all three families.

    A reviewer-family thermo review, a D1 advisory row and an external-Cp row
    (via both the advisory service and ``cp.run_and_record``) are persisted with
    the recipe as it stands, then an unrelated key is added and each is
    re-classified with its consumer's own live currency call.
    """
    from tests.services.test_phase_d_persistence import request, setup_records

    thermo, _, _ = setup_records(db_session)
    create_record_machine_review_row(
        db_session, record_type="thermo", record_id=thermo.id, context_digest=_DIGEST,
        prompt_version=_PROMPT, rubric_versions=active_rubric_versions_for_record_type("thermo"),
        review=RecordMachineReview(record_type="thermo", record_ref=thermo.public_ref, record_id=thermo.id,
                                   status=MachineReviewStatus.machine_screened_pass,
                                   reviewed_at=datetime(2026, 9, 1)),
    )
    d1, d1_row = invoke(db_session, commit=True, **request(thermo, "thermo"))
    cp_row = cp.run_and_record(db_session, thermo.id)
    db_session.flush()
    assert d1_row.rubric_versions_json == {"thermo_consistency_v1": "1"}
    assert cp_row.rubric_versions_json == {"external_cp_comparison_v2": "2"}

    def states():
        plan = plan_record_machine_rereview(
            db_session, record_type="thermo", record_id=thermo.id, current_context=_DIGEST,
            active_prompt_version=_PROMPT, active_rubric_versions=active_rubric_versions_for_record_type("thermo"))
        return (plan.decision, currency(db_session, d1).state.value,
                cp.cp_comparison_currency(db_session, thermo.id).state.value)

    expected = (MachineReviewReReviewDecision.skip_current, "current", "current")
    assert states() == expected
    before = dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS)
    monkeypatch.setitem(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, UNRELATED_KEY, "1")
    assert set(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS) - set(before) == {UNRELATED_KEY}
    assert states() == expected
