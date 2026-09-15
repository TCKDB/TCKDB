"""Tests for the v2 machine-review runner (``machine_review.run``).

The runner is the entry point the machine-review stack did not have: it
resolves a provider, builds the context, asks for a review, and records one
audit event. Everything worth pinning about it is a *negative*:

* it writes one row and only one row -- never ``submission.status``, never
  moderation, never ``record_review`` (human trust), never a curator task,
  never a scientific record;
* it never lets an exception past, whatever the provider does;
* it never lets a secret into the failure text it stores.

The one thing it *is* allowed to raise is a missing submission, because that
is the caller naming something that does not exist rather than the reviewer
failing.

No network and no real model: every provider here is a local stub, and the
runner is given it explicitly. The configured factory is exercised only
through ``Settings`` objects that make it fail or return the disabled
provider -- no test path can reach a transport.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.config import Settings
from app.api.errors import NotFoundError
from app.db.models.common import (
    SubmissionActorKind,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionStatus,
)
from app.db.models.machine_review_curator_task import MachineReviewCuratorTask
from app.db.models.record_review import RecordReview
from app.db.models.submission import SubmissionAuditEvent
from app.services.machine_review.providers.fake import (
    build_fake_machine_review_provider,
    make_failed_result,
    make_pass_result,
    make_warning_result,
)
from app.services.machine_review.run import run_machine_review_for_submission
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewStatus,
)
from app.services.submission import create_submission

# Off mode is what a default deployment runs; using it for the injected-provider
# tests proves the injected provider wins over configuration rather than
# happening to agree with it.
_OFF = Settings(ai_review_assistant_mode="off")


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #


class _RaisingProvider:
    """A provider whose call fails the way a transport does."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("upstream endpoint unreachable")

    def review_submission(self, context):
        """Raise, as a broken transport or a refused answer does."""
        raise self._exc


class _WrongTypeProvider:
    """A provider that returns something other than a validated v2 result.

    Not a hypothetical: the protocol is structural, so any object with a
    ``review_submission`` satisfies it at type-check time, and a provider that
    forgot to parse would return a raw ``dict`` that looks plausible.
    """

    def __init__(self, value=None) -> None:
        self._value = (
            value
            if value is not None
            else {
                "schema_version": MACHINE_REVIEW_V2_SCHEMA_VERSION,
                "status": "machine_screened_pass",
                "used_rag": False,
            }
        )

    def review_submission(self, context):
        """Return an unvalidated payload instead of the contract type."""
        return self._value


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _seed_submission(db_session: Session, user_id: int):
    """Create one pending submission to review."""
    submission = create_submission(
        db_session,
        created_by=user_id,
        submission_kind=SubmissionKind.thermo,
        title="machine-review runner",
        summary="compact",
    )
    db_session.flush()
    return submission


def _all_events(db_session: Session, submission_id: int) -> list[SubmissionAuditEvent]:
    """Every audit event on a submission, in order -- not just review ones.

    Deliberately unfiltered: a test that only counted ``llm_precheck_recorded``
    rows could not see the runner writing an approval event as well.
    ``create_submission`` already leaves one row here, so this is only ever
    compared as a before/after delta.
    """
    return list(
        db_session.scalars(
            select(SubmissionAuditEvent)
            .where(SubmissionAuditEvent.submission_id == submission_id)
            .order_by(SubmissionAuditEvent.id.asc())
        )
    )


def _review_events(db_session: Session, submission_id: int) -> list[SubmissionAuditEvent]:
    """Just the machine-review/precheck events, in order."""
    return [
        event
        for event in _all_events(db_session, submission_id)
        if event.event_kind is SubmissionAuditEventKind.llm_precheck_recorded
    ]


def _count(db_session: Session, model) -> int:
    """Row count for a whole table."""
    return db_session.scalar(select(func.count()).select_from(model)) or 0


def _moderation_snapshot(submission) -> tuple:
    """Every field that decides a submission's fate, plus the precheck columns.

    ``mark_precheck_result`` writes ``llm_precheck_*`` on the submission *and*
    moves its status; machine review must do neither, so the columns that
    helper owns are in the snapshot too.
    """
    return (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
        submission.llm_precheck_label,
        submission.llm_precheck_model,
        submission.llm_precheck_summary,
        submission.llm_precheck_at,
    )


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


def test_run_records_one_v2_audit_event(db_session, _api_test_user):
    """A successful review lands as exactly one v2-marked audit event."""
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=build_fake_machine_review_provider(make_pass_result()),
        settings_obj=_OFF,
    )

    assert outcome.status is MachineReviewStatus.machine_screened_pass
    assert outcome.audit_event_recorded is True
    assert outcome.failure_reason is None

    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].event_kind is SubmissionAuditEventKind.llm_precheck_recorded
    assert events[0].actor_kind is SubmissionActorKind.llm
    assert events[0].details_json["schema_version"] == MACHINE_REVIEW_V2_SCHEMA_VERSION
    assert events[0].details_json["status"] == "machine_screened_pass"
    # Advisory events carry no status transition: that is what makes them safe
    # to write without a curator's involvement.
    assert events[0].from_status is None
    assert events[0].to_status is None


def test_run_reports_findings_count_summary_and_identity(db_session, _api_test_user):
    """The outcome carries what a caller (and the admin route) needs."""
    submission = _seed_submission(db_session, _api_test_user)
    fixed = make_warning_result(record_type="calculation", record_ref="7")

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=build_fake_machine_review_provider(fixed),
        settings_obj=_OFF,
    )

    assert outcome.status is MachineReviewStatus.machine_screened_warning
    assert outcome.findings_count == len(fixed.findings) == 1
    assert outcome.summary == fixed.summary
    # Identity comes from the provider's own stamp, which the provider takes
    # from configuration -- the runner does not re-derive or overwrite it.
    assert outcome.model == fixed.model
    assert outcome.provider == fixed.provider


def test_run_passes_the_submissions_context_to_the_provider(db_session, _api_test_user):
    """The provider is asked about *this* submission, with its records attached.

    Without this, a runner that built an empty context would still pass every
    other test in this file: the fake provider answers regardless.
    """
    submission = _seed_submission(db_session, _api_test_user)
    seen = []

    class _Recording:
        def review_submission(self, context):
            seen.append(context)
            return make_pass_result()

    run_machine_review_for_submission(
        db_session, submission.id, provider=_Recording(), settings_obj=_OFF
    )

    assert len(seen) == 1
    assert seen[0].submission_id == submission.id
    assert seen[0].precheck_context is not None
    assert seen[0].precheck_context.submission_id == submission.id
    assert seen[0].precheck_context.title == "machine-review runner"


# --------------------------------------------------------------------------- #
# Writes ONLY the audit event (the load-bearing invariant)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(build_fake_machine_review_provider(), id="successful_review"),
        pytest.param(_RaisingProvider(), id="failed_review"),
    ],
)
def test_run_writes_only_the_audit_event(db_session, _api_test_user, provider):
    """Nothing but one ``submission_audit_event`` row changes.

    Both a succeeding and a failing run, because the failure path builds its
    own result and records it -- a different code path with the same duty.
    """
    submission = _seed_submission(db_session, _api_test_user)
    before_moderation = _moderation_snapshot(submission)
    before_reviews = _count(db_session, RecordReview)
    before_tasks = _count(db_session, MachineReviewCuratorTask)
    before_events = len(_all_events(db_session, submission.id))

    run_machine_review_for_submission(
        db_session, submission.id, provider=provider, settings_obj=_OFF
    )

    db_session.refresh(submission)
    assert _moderation_snapshot(submission) == before_moderation
    assert submission.status is SubmissionStatus.pending
    # Human review and the curator queue are separate axes; an advisory run
    # touches neither.
    assert _count(db_session, RecordReview) == before_reviews
    assert _count(db_session, MachineReviewCuratorTask) == before_tasks
    assert len(_all_events(db_session, submission.id)) == before_events + 1


# --------------------------------------------------------------------------- #
# Never a raise past the boundary
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(RuntimeError("upstream endpoint unreachable"), id="transport"),
        pytest.param(TimeoutError("timed out after 30s"), id="timeout"),
        pytest.param(ValueError("Expecting value: line 1 column 1"), id="malformed"),
        pytest.param(KeyError("choices"), id="unexpected_body"),
        pytest.param(NotFoundError("Submission not found."), id="notfound_from_provider"),
    ],
)
def test_provider_failure_becomes_an_advisory_failed_result(
    db_session, _api_test_user, exc
):
    """Every provider failure is converted, recorded, and returned -- not raised.

    ``NotFoundError`` is in the list on purpose: the runner raises that type
    itself for a missing submission, and a provider raising it after the
    submission has already been loaded must not be mistaken for one.
    """
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=_RaisingProvider(exc),
        settings_obj=_OFF,
    )

    assert outcome.status is MachineReviewStatus.machine_review_failed
    assert outcome.audit_event_recorded is True
    assert outcome.failure_reason is not None
    assert type(exc).__name__ in outcome.failure_reason
    assert outcome.findings_count == 0

    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].details_json["status"] == "machine_review_failed"
    assert events[0].details_json["schema_version"] == MACHINE_REVIEW_V2_SCHEMA_VERSION


def test_configuration_error_becomes_an_advisory_failed_result(
    db_session, _api_test_user, monkeypatch
):
    """A half-configured cloud deployment fails advisory, not loudly.

    Cloud mode with no model configured is what the factory refuses; the run
    still returns a result and still journals why.
    """
    monkeypatch.delenv("LLM_PRECHECK_API_KEY", raising=False)
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        settings_obj=Settings(
            ai_review_assistant_mode="cloud",
            llm_precheck_model=None,
            llm_precheck_api_key_env="LLM_PRECHECK_API_KEY",
        ),
    )

    assert outcome.status is MachineReviewStatus.machine_review_failed
    assert outcome.audit_event_recorded is True
    assert "could not be configured" in (outcome.failure_reason or "")
    assert len(_review_events(db_session, submission.id)) == 1


def test_unimplemented_local_mode_becomes_an_advisory_failed_result(
    db_session, _api_test_user
):
    """Local mode still raises ``NotImplementedError``; the run absorbs it."""
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        settings_obj=Settings(
            ai_review_assistant_mode="local",
            llm_precheck_model="some-local-model",
            llm_precheck_base_url="http://localhost:1234/v1",
        ),
    )

    assert outcome.status is MachineReviewStatus.machine_review_failed
    assert "NotImplementedError" in (outcome.failure_reason or "")
    assert outcome.audit_event_recorded is True


def test_provider_returning_the_wrong_type_becomes_a_failed_result(
    db_session, _api_test_user
):
    """An unvalidated payload is refused, not re-parsed into the contract.

    The runner adds no second parse boundary: a provider that hands back a raw
    ``dict`` (however plausible) failed to cross
    ``parse_machine_review_v2_payload``, and the runner says so rather than
    doing that work leniently on its behalf.
    """
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=_WrongTypeProvider(),
        settings_obj=_OFF,
    )

    assert outcome.status is MachineReviewStatus.machine_review_failed
    assert "dict" in (outcome.failure_reason or "")
    assert outcome.audit_event_recorded is True
    # Nothing from the unvalidated payload survived into the stored event.
    events = _review_events(db_session, submission.id)
    assert len(events) == 1
    assert events[0].details_json["status"] == "machine_review_failed"


def test_enormous_failure_text_is_clipped_not_raised(db_session, _api_test_user):
    """A huge exception message cannot break the failure path it describes.

    ``summary`` is capped at 2000 characters, so an unclipped failure text
    would raise a ``ValidationError`` while the runner was in the middle of
    handling a failure -- the one place it is not allowed to raise.
    """
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=_RaisingProvider(RuntimeError("x" * 50_000)),
        settings_obj=_OFF,
    )

    assert outcome.status is MachineReviewStatus.machine_review_failed
    assert outcome.summary is not None
    assert len(outcome.summary) <= 2000
    assert outcome.summary.endswith("[truncated]")
    assert outcome.audit_event_recorded is True


def test_a_provider_may_report_its_own_failure(db_session, _api_test_user):
    """A provider that returns a valid ``machine_review_failed`` is honoured.

    It did not raise, so its payload is recorded as-is; the runner still
    reports a ``failure_reason`` so a caller need not special-case the status.
    """
    submission = _seed_submission(db_session, _api_test_user)
    fixed = make_failed_result(summary="Model declined to review: policy.")

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=build_fake_machine_review_provider(fixed),
        settings_obj=_OFF,
    )

    assert outcome.status is MachineReviewStatus.machine_review_failed
    assert outcome.failure_reason == fixed.summary
    assert outcome.audit_event_recorded is True


# --------------------------------------------------------------------------- #
# Identity and secrets
# --------------------------------------------------------------------------- #


def test_failed_result_stamps_identity_from_configuration(db_session, _api_test_user):
    """On the failure path there is no payload, so identity comes from config.

    The model name is the one the deployment configured, and the provider name
    is the class that failed -- neither is read from anything the model said,
    because on this path the model said nothing.
    """
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=_RaisingProvider(),
        settings_obj=Settings(
            ai_review_assistant_mode="off",
            llm_precheck_model="vendor/some-model-v3",
        ),
    )

    assert outcome.model == "vendor/some-model-v3"
    assert outcome.provider == "_RaisingProvider"


def test_api_key_never_reaches_the_failure_text(
    db_session, _api_test_user, monkeypatch
):
    """A key echoed by a client library is redacted before it is stored.

    The failure text comes from a third-party exception and is written into a
    durable audit event and returned over HTTP. This is the last point at
    which a leaked credential can be stopped.
    """
    secret = "sk-test-0123456789abcdef"
    monkeypatch.setenv("TCKDB_TEST_MR_KEY", secret)
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session,
        submission.id,
        provider=_RaisingProvider(
            RuntimeError(f"401 Unauthorized for key {secret} at /v1/chat")
        ),
        settings_obj=Settings(
            ai_review_assistant_mode="off",
            llm_precheck_api_key_env="TCKDB_TEST_MR_KEY",
        ),
    )

    assert secret not in (outcome.failure_reason or "")
    assert secret not in (outcome.summary or "")
    assert "[redacted]" in (outcome.failure_reason or "")

    stored = _review_events(db_session, submission.id)[0]
    assert secret not in str(stored.details_json)
    assert secret not in (stored.summary or "")


# --------------------------------------------------------------------------- #
# Off mode and the missing submission
# --------------------------------------------------------------------------- #


def test_off_mode_returns_not_run_and_writes_nothing(db_session, _api_test_user):
    """Off mode is the one outcome with no event; absence already means not_run."""
    submission = _seed_submission(db_session, _api_test_user)

    outcome = run_machine_review_for_submission(
        db_session, submission.id, settings_obj=_OFF
    )

    assert outcome.status is MachineReviewStatus.not_run
    assert outcome.audit_event_recorded is False
    assert outcome.failure_reason is None
    assert _review_events(db_session, submission.id) == []
    # And nothing else was written either: only the creation event remains.
    assert len(_all_events(db_session, submission.id)) == 1


def test_missing_submission_raises_not_found(db_session):
    """The single exception to "never raises": a submission that is not there.

    :class:`~app.api.errors.NotFoundError` rather than a failed result, because
    there is no submission to attach an advisory result to -- and the API layer
    already renders this type as a 404.
    """
    with pytest.raises(NotFoundError):
        run_machine_review_for_submission(db_session, 10_000_017, settings_obj=_OFF)


def test_missing_submission_writes_no_event(db_session, _api_test_user):
    """The 404 path records nothing, for anyone."""
    before = _count(db_session, SubmissionAuditEvent)

    with pytest.raises(NotFoundError):
        run_machine_review_for_submission(
            db_session,
            10_000_019,
            provider=build_fake_machine_review_provider(),
            settings_obj=_OFF,
        )

    assert _count(db_session, SubmissionAuditEvent) == before
