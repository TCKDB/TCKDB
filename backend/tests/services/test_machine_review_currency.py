"""Unit tests for the pure machine-review currency classifier.

These prove the current/stale/historical/not_run model from
``backend/docs/specs/record_machine_review_policy.md`` §2/§3.5/§4: the latest
review (by the policy ordering) is current only when all four currency
dimensions match the active recipe, otherwise stale (with reasons); every
non-latest review is historical; no reviews means not_run.

The classifier under test is pure: no DB, no provider, no persistence. Tests
build :class:`StoredMachineReviewProjection` objects and a
:class:`MachineReviewContextDigest` directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.machine_review import (
    MachineReviewContextDigest,
    MachineReviewCurrencyState,
    MachineReviewStaleReason,
    StoredMachineReviewProjection,
    classify_machine_review_currency,
)

_T0 = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)

# The active recipe every test classifies against.
_ACTIVE_CONTEXT = MachineReviewContextDigest(
    context_hash="hash_current", context_schema_version="v1"
)
_ACTIVE_PROMPT = "prompt_v3"
_ACTIVE_RUBRICS = {"kinetics": "computed_kinetics_v1", "calc": "computed_calculation_v1"}


def _review(
    *,
    reviewed_at: datetime = _T0,
    id: int | None = 1,
    source_audit_event_id: int | None = 100,
    context_hash: str = "hash_current",
    context_schema_version: str = "v1",
    prompt_version: str = "prompt_v3",
    rubric_versions: dict[str, str] | None = None,
    record_type: str = "kinetics",
    record_id: int | str = 9001,
) -> StoredMachineReviewProjection:
    """Build a review projection that, with the defaults, is exactly current."""
    return StoredMachineReviewProjection(
        record_type=record_type,
        record_id=record_id,
        reviewed_at=reviewed_at,
        context_schema_version=context_schema_version,
        context_hash=context_hash,
        prompt_version=prompt_version,
        rubric_versions=rubric_versions if rubric_versions is not None else dict(_ACTIVE_RUBRICS),
        id=id,
        source_audit_event_id=source_audit_event_id,
    )


def _classify(reviews):
    return classify_machine_review_currency(
        reviews,
        current_context=_ACTIVE_CONTEXT,
        active_prompt_version=_ACTIVE_PROMPT,
        active_rubric_versions=_ACTIVE_RUBRICS,
    )


# --------------------------------------------------------------------------- #
# Empty input
# --------------------------------------------------------------------------- #


def test_no_reviews_is_not_run():
    """No reviews -> not_run, with no active review and no reasons."""
    result = _classify([])
    assert result.state is MachineReviewCurrencyState.not_run
    assert result.active_review is None
    assert result.historical_reviews == ()
    assert result.stale_reasons == ()


# --------------------------------------------------------------------------- #
# Single review: current vs each mismatch dimension
# --------------------------------------------------------------------------- #


def test_single_matching_review_is_current():
    """A single review matching every currency dimension is current."""
    review = _review()
    result = _classify([review])
    assert result.state is MachineReviewCurrencyState.current
    assert result.active_review is review
    assert result.historical_reviews == ()
    assert result.stale_reasons == ()


def test_context_hash_mismatch_is_stale():
    """A different context_hash makes the latest review stale."""
    result = _classify([_review(context_hash="hash_old")])
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (
        MachineReviewStaleReason.context_hash_mismatch,
    )


def test_context_schema_version_mismatch_is_stale():
    """A different context_schema_version makes the latest review stale."""
    result = _classify([_review(context_schema_version="v0")])
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (
        MachineReviewStaleReason.context_schema_version_mismatch,
    )


def test_prompt_version_mismatch_is_stale():
    """A different prompt_version makes the latest review stale."""
    result = _classify([_review(prompt_version="prompt_v2")])
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (
        MachineReviewStaleReason.prompt_version_mismatch,
    )


def test_rubric_versions_mismatch_is_stale():
    """A different rubric version makes the latest review stale."""
    result = _classify(
        [_review(rubric_versions={"kinetics": "computed_kinetics_v2",
                                  "calc": "computed_calculation_v1"})]
    )
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (
        MachineReviewStaleReason.rubric_versions_mismatch,
    )


def test_multiple_mismatches_report_all_reasons_in_fixed_order():
    """Several dimensions changing reports every reason, in the fixed order."""
    result = _classify(
        [
            _review(
                context_schema_version="v0",
                context_hash="hash_old",
                prompt_version="prompt_v2",
                rubric_versions={"kinetics": "computed_kinetics_v2"},
            )
        ]
    )
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (
        MachineReviewStaleReason.context_schema_version_mismatch,
        MachineReviewStaleReason.context_hash_mismatch,
        MachineReviewStaleReason.prompt_version_mismatch,
        MachineReviewStaleReason.rubric_versions_mismatch,
    )


# --------------------------------------------------------------------------- #
# Rubric-version canonical comparison
# --------------------------------------------------------------------------- #


def test_rubric_version_key_order_does_not_matter():
    """Same rubric mapping in a different key order is still current."""
    reordered = {"calc": "computed_calculation_v1", "kinetics": "computed_kinetics_v1"}
    result = _classify([_review(rubric_versions=reordered)])
    assert result.state is MachineReviewCurrencyState.current


def test_missing_rubric_key_is_stale():
    """A rubric mapping missing an active key is stale."""
    result = _classify([_review(rubric_versions={"kinetics": "computed_kinetics_v1"})])
    assert result.state is MachineReviewCurrencyState.stale
    assert MachineReviewStaleReason.rubric_versions_mismatch in result.stale_reasons


def test_extra_rubric_key_is_stale():
    """A rubric mapping with an extra key beyond the active set is stale."""
    result = _classify(
        [_review(rubric_versions={**_ACTIVE_RUBRICS, "thermo": "computed_thermo_v1"})]
    )
    assert result.state is MachineReviewCurrencyState.stale
    assert MachineReviewStaleReason.rubric_versions_mismatch in result.stale_reasons


# --------------------------------------------------------------------------- #
# Latest-selection ordering (policy §4)
# --------------------------------------------------------------------------- #


def test_latest_selected_by_reviewed_at():
    """The newest reviewed_at is the active review; the older is historical."""
    older = _review(reviewed_at=_T0, id=1, context_hash="hash_old")
    newer = _review(reviewed_at=_T0 + timedelta(hours=1), id=2, context_hash="hash_current")
    result = _classify([older, newer])
    assert result.active_review is newer
    assert result.historical_reviews == (older,)
    assert result.state is MachineReviewCurrencyState.current


def test_latest_tie_breaks_by_source_audit_event_id():
    """Equal reviewed_at: the higher source_audit_event_id is latest."""
    low = _review(reviewed_at=_T0, id=1, source_audit_event_id=100, context_hash="hash_old")
    high = _review(reviewed_at=_T0, id=2, source_audit_event_id=200, context_hash="hash_current")
    result = _classify([low, high])
    assert result.active_review is high
    assert result.historical_reviews == (low,)


def test_latest_final_tie_breaks_by_id():
    """Equal reviewed_at and source_audit_event_id: the higher id is latest."""
    low = _review(reviewed_at=_T0, id=10, source_audit_event_id=None, context_hash="hash_old")
    high = _review(reviewed_at=_T0, id=20, source_audit_event_id=None, context_hash="hash_current")
    result = _classify([low, high])
    assert result.active_review is high
    assert result.historical_reviews == (low,)


def test_none_source_audit_event_id_sorts_after_real_ids():
    """DESC NULLS LAST: a real source_audit_event_id outranks None at equal time."""
    real = _review(reviewed_at=_T0, id=1, source_audit_event_id=5, context_hash="hash_current")
    missing = _review(reviewed_at=_T0, id=2, source_audit_event_id=None, context_hash="hash_old")
    result = _classify([missing, real])
    # The review WITH an audit id is latest even though `missing` has a higher id,
    # because source_audit_event_id is the higher-priority tiebreak.
    assert result.active_review is real
    assert result.historical_reviews == (missing,)


# --------------------------------------------------------------------------- #
# Historical reviews and latest-stale dominance
# --------------------------------------------------------------------------- #


def test_non_latest_reviews_are_historical_even_if_their_hash_matches():
    """A current-hash older review is still historical, not the active candidate."""
    older_matching = _review(
        reviewed_at=_T0, id=1, context_hash="hash_current"
    )
    newer_stale = _review(
        reviewed_at=_T0 + timedelta(hours=1), id=2, context_hash="hash_old"
    )
    result = _classify([older_matching, newer_stale])
    # Latest is the stale newer review; the matching older one is historical only.
    assert result.active_review is newer_stale
    assert result.historical_reviews == (older_matching,)
    assert result.state is MachineReviewCurrencyState.stale


def test_latest_stale_makes_state_stale_even_if_older_is_current():
    """State follows the latest review, not the best-matching one (policy §4)."""
    current_old = _review(reviewed_at=_T0, id=1, context_hash="hash_current")
    stale_new = _review(
        reviewed_at=_T0 + timedelta(hours=2), id=2, context_hash="hash_old"
    )
    result = _classify([current_old, stale_new])
    assert result.state is MachineReviewCurrencyState.stale
    assert result.stale_reasons == (MachineReviewStaleReason.context_hash_mismatch,)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_classification_is_deterministic_across_input_order():
    """Shuffling the input never changes the active review or state."""
    a = _review(reviewed_at=_T0, id=1, source_audit_event_id=100, context_hash="hash_old")
    b = _review(reviewed_at=_T0 + timedelta(hours=1), id=2, source_audit_event_id=200,
                context_hash="hash_current")
    c = _review(reviewed_at=_T0 - timedelta(hours=1), id=3, source_audit_event_id=50,
                context_hash="hash_older")

    r1 = _classify([a, b, c])
    r2 = _classify([c, a, b])
    r3 = _classify([b, c, a])

    assert r1 == r2 == r3
    assert r1.active_review is b or r1.active_review == b
    assert r1.active_review.id == 2
    assert r1.state is MachineReviewCurrencyState.current
    # Historical is newest-first: a (T0) then c (T0 - 1h).
    assert tuple(h.id for h in r1.historical_reviews) == (1, 3)
