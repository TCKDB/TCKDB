"""A stored machine-review recipe holds exactly one rubric, so an unrelated rubric never restales it (issue #553).

Currency compares a review's stored ``rubric`` ref with the active one. Every consumer therefore stamps -- and later
compares against -- the one rubric ref for its own check, never a set:

* reviewer family, per record type: ``active_rubric_for_record_type`` (or, owner-aware,
  ``active_rubric_for_fragment``);
* scientific-check family, per advisory check and runner: ``AdvisoryResult.rubric_ref``;
* the external-Cp runner: ``cp.run_and_record`` / ``cp.cp_comparison_currency``.

The rule used to be a convention about filtering a dict; it is now structural, because the recipe has a single ``rubric``
field. These tests keep it that way: they store each consumer's ref, register a rubric none of them owns (the shape of
every later addition) and assert every stored ref still compares current, and that bumping only *one* rubric restales only
the reviews made under that rubric.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from tckdb_schemas.fragments.version import VersionRef

from app.services.consistency import gibbs, hess, isotope_identity, kinetics, kirchhoff, wide_basin
from app.services.consistency import thermo as thermo_check
from app.services.consistency.core import AdvisoryResult, currency
from app.services.consistency.service import invoke
from app.services.external_comparison import cp
from app.services.machine_review.admin_trigger import (
    SUPPORTED_RECORD_TYPES,
    active_rubric_for_fragment,
    active_rubric_for_record_type,
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
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT
from app.services.machine_review.rereview import (
    MachineReviewReReviewDecision,
    plan_record_machine_rereview,
)
from app.services.machine_review.schemas import MachineReviewStatus
from app.services.trust.models import TrustFragment
from app.services.trust.rubrics import (
    EXTERNAL_CP_COMPARISON,
    GIBBS_SELF_CONSISTENCY,
    HESS_CONSISTENCY,
    ISOTOPE_IDENTITY_CONSISTENCY,
    KIRCHHOFF_CONSISTENCY,
    RUBRICS,
    THERMO_CONSISTENCY,
    THERMO_KINETICS_CONSISTENCY,
    WIDE_BASIN_CONSISTENCY,
)
from tests.services._rubric_registry import bump, register_unrelated

_DIGEST = MachineReviewContextDigest(context_hash="c" * 64, context_schema_version=1)
_PROMPT = ACTIVE_MACHINE_REVIEW_PROMPT

#: What each reviewer-family record type must store: exactly its own rubric (every rubric is at version 1 except the
#: transition-state one, at 2: its qualifying refs are listed in the structure administrative key, version 2).
_REVIEWER_RECIPES = {
    "calculation": VersionRef(name="computed_calculation", version=1),
    "kinetics": VersionRef(name="computed_kinetics", version=1),
    "thermo": VersionRef(name="computed_thermo", version=1),
    "statmech": VersionRef(name="computed_statmech", version=1),
    "transport": VersionRef(name="computed_transport", version=1),
    "transition_state_entry": VersionRef(name="computed_transition_state", version=2),
}

#: Owner-scoped reviewer rubrics: selected per record by the rubric that evaluated it, so they are keyed by rubric
#: name, not by record type.
_OWNER_SCOPED_REVIEWER_RECIPES = {
    "computed_ts_statmech": VersionRef(name="computed_ts_statmech", version=1),
}

#: What each scientific-check runner must store: exactly its own rubric.
_ADVISORY_RECIPES = {
    (thermo_check.RUNNER, THERMO_CONSISTENCY): VersionRef(name="thermo_consistency", version=1),
    (kinetics.RUNNER, THERMO_KINETICS_CONSISTENCY): VersionRef(name="thermo_kinetics_consistency", version=1),
    (gibbs.RUNNER, GIBBS_SELF_CONSISTENCY): VersionRef(name="gibbs_self_consistency", version=1),
    (hess.RUNNER, HESS_CONSISTENCY): VersionRef(name="hess_consistency", version=1),
    (isotope_identity.RUNNER, ISOTOPE_IDENTITY_CONSISTENCY): VersionRef(name="isotope_identity_consistency", version=1),
    (kirchhoff.RUNNER, KIRCHHOFF_CONSISTENCY): VersionRef(name="kirchhoff_consistency", version=1),
    (wide_basin.RUNNER, WIDE_BASIN_CONSISTENCY): VersionRef(name="wide_basin_consistency", version=1),
    (cp.RUNNER, EXTERNAL_CP_COMPARISON): VersionRef(name="external_cp_comparison", version=1),
}


def _classify(stored_rubric: VersionRef, active_rubric: VersionRef, *, prompt=_PROMPT):
    stored = StoredMachineReviewProjection(
        record_type="thermo", record_id=1, reviewed_at=datetime(2026, 9, 1),
        context_schema_version=_DIGEST.context_schema_version, context_hash=_DIGEST.context_hash,
        prompt=prompt, rubric=stored_rubric,
    )
    return classify_machine_review_currency(
        [stored], current_context=_DIGEST, active_prompt=prompt, active_rubric=active_rubric,
    )


def test_the_expected_tables_cover_every_consumer():
    """Premise: the parametrized cases below are not empty and miss no consumer."""
    assert set(_REVIEWER_RECIPES) == set(SUPPORTED_RECORD_TYPES)
    assert len(_REVIEWER_RECIPES) == 6
    owned = {
        ref.name
        for ref in (
            *_REVIEWER_RECIPES.values(),
            *_OWNER_SCOPED_REVIEWER_RECIPES.values(),
            *_ADVISORY_RECIPES.values(),
        )
    }
    assert owned == {ref.name for ref in RUBRICS.refs()}


@pytest.mark.parametrize("record_type", sorted(_REVIEWER_RECIPES))
def test_reviewer_recipe_survives_an_unrelated_rubric(record_type, monkeypatch):
    stored = active_rubric_for_record_type(record_type)
    assert stored == _REVIEWER_RECIPES[record_type]

    unrelated = register_unrelated(monkeypatch)
    assert unrelated in {ref.name for ref in RUBRICS.refs()}

    active = active_rubric_for_record_type(record_type)
    assert active == _REVIEWER_RECIPES[record_type]
    result = _classify(stored, active)
    assert (result.state, result.stale_reasons) == (MachineReviewCurrencyState.current, ())


@pytest.mark.parametrize("record_type", sorted(_REVIEWER_RECIPES))
def test_bumping_one_rubric_restales_only_its_own_reviews(record_type, monkeypatch):
    """The rubric of one record type changing never touches another record type's reviews."""
    stored = {rt: active_rubric_for_record_type(rt) for rt in _REVIEWER_RECIPES}
    bump(monkeypatch, _REVIEWER_RECIPES[record_type].name, _REVIEWER_RECIPES[record_type].version + 1)
    for rt, ref in stored.items():
        result = _classify(ref, active_rubric_for_record_type(rt))
        if rt == record_type:
            assert (result.state, result.stale_reasons) == (
                MachineReviewCurrencyState.stale, (MachineReviewStaleReason.rubric_versions_mismatch,))
        else:
            assert (result.state, result.stale_reasons) == (MachineReviewCurrencyState.current, ())


def test_the_owner_scoped_reviewer_table_is_not_empty():
    assert _OWNER_SCOPED_REVIEWER_RECIPES


@pytest.mark.parametrize("rubric_name", sorted(_OWNER_SCOPED_REVIEWER_RECIPES))
def test_owner_scoped_reviewer_recipe_survives_an_unrelated_rubric(rubric_name, monkeypatch):
    """A transition-state-owned statmech review is stamped with its own one rubric, so it too is not restaled."""
    fragment = TrustFragment(
        trust_status="partial", evidence={"rubric": rubric_name, "rubric_version": 1}
    )
    stored = active_rubric_for_fragment("statmech", fragment)
    assert stored == _OWNER_SCOPED_REVIEWER_RECIPES[rubric_name]

    register_unrelated(monkeypatch)

    active = active_rubric_for_fragment("statmech", fragment)
    assert active == _OWNER_SCOPED_REVIEWER_RECIPES[rubric_name]
    result = _classify(stored, active)
    assert (result.state, result.stale_reasons) == (MachineReviewCurrencyState.current, ())


@pytest.mark.parametrize("runner,rubric", list(_ADVISORY_RECIPES), ids=lambda v: getattr(v, "name", v))
def test_scientific_check_recipe_survives_an_unrelated_rubric(runner, rubric, monkeypatch):
    def result():
        return AdvisoryResult(target=None, runner=runner, rubric=rubric, findings=(), inputs_json="{}")

    stored = result().rubric_ref
    assert stored == _ADVISORY_RECIPES[(runner, rubric)]

    register_unrelated(monkeypatch)

    active = result().rubric_ref
    assert active == _ADVISORY_RECIPES[(runner, rubric)]
    classified = _classify(stored, active, prompt=result().prompt)
    assert (classified.state, classified.stale_reasons) == (MachineReviewCurrencyState.current, ())


def test_persisted_reviews_survive_an_unrelated_rubric(db_session, monkeypatch):
    """End to end through the real write and currency paths of all three families.

    A reviewer-family thermo review, a D1 advisory row and an external-Cp row
    (via both the advisory service and ``cp.run_and_record``) are persisted with
    the recipe as it stands, then an unrelated rubric is registered and each is
    re-classified with its consumer's own live currency call.
    """
    from tests.services.test_phase_d_persistence import request, setup_records

    thermo, _, _ = setup_records(db_session)
    create_record_machine_review_row(
        db_session, record_type="thermo", record_id=thermo.id, context_digest=_DIGEST,
        prompt=_PROMPT, rubric=active_rubric_for_record_type("thermo"),
        review=RecordMachineReview(record_type="thermo", record_ref=thermo.public_ref, record_id=thermo.id,
                                   status=MachineReviewStatus.machine_screened_pass,
                                   reviewed_at=datetime(2026, 9, 1)),
    )
    d1, d1_row = invoke(db_session, commit=True, **request(thermo, "thermo"))
    cp_row = cp.run_and_record(db_session, thermo.id)
    db_session.flush()
    assert (d1_row.rubric_name, d1_row.rubric_version) == ("thermo_consistency", 1)
    assert (cp_row.rubric_name, cp_row.rubric_version) == ("external_cp_comparison", 1)
    assert (cp_row.prompt_name, cp_row.prompt_version, cp_row.model) == (
        "external_cp_comparison", 1, "external_cp_comparison")

    def states():
        plan = plan_record_machine_rereview(
            db_session, record_type="thermo", record_id=thermo.id, current_context=_DIGEST,
            active_prompt=_PROMPT, active_rubric=active_rubric_for_record_type("thermo"))
        return (plan.decision, currency(db_session, d1).state.value,
                cp.cp_comparison_currency(db_session, thermo.id).state.value)

    expected = (MachineReviewReReviewDecision.skip_current, "current", "current")
    assert states() == expected
    register_unrelated(monkeypatch)
    assert states() == expected
