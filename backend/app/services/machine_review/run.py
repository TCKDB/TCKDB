"""Run one machine review for one submission, and write down what happened.

Until this module existed the machine-review stack had every part but an entry
point. :func:`~app.services.machine_review.providers.factory.build_machine_review_provider`
built a real provider, :func:`~app.services.submission.record_machine_review_audit_event`
recorded a result, and nothing under ``app/`` called either one -- only
tests did. The consequence was not subtle: a deployment could hold dozens of
submissions and zero machine-review audit events, so the curator queue that
reads those events was empty by construction rather than because nothing
needed a curator. This module is the call that was missing.

Two properties are load-bearing.

**Advisory, and only that.** A run writes exactly one row: one
``submission_audit_event``. It does not touch ``submission.status``, the
approval/rejection columns, ``record_review`` (human trust), curator tasks, or
any scientific record. Machine review is a third axis alongside human review
and submission moderation: it endorses nothing, approves nothing, and a
curator reads the result and decides. The curator-task builder is a separate,
separately triggered step that reads the event this writes.

**Never a raise past this boundary.** A missing configuration, a transport
failure, a timeout, a truncated or refused answer, malformed JSON, a contract
violation, even a provider that returns the wrong type -- each becomes an
advisory ``machine_review_failed`` result *and* a recorded audit event saying
why. A caller never sees an exception from the reviewer, and a submission is
never worse off for having been reviewed. The single exception is a submission
that does not exist, which is the caller's error rather than the reviewer's:
that raises :class:`~app.api.errors.NotFoundError`, which the API layer
renders as a 404.

**What this module deliberately does not do.** It never sees raw model output,
so it adds no parse.
:func:`~app.services.machine_review.providers.interface.parse_machine_review_payload`,
called inside the provider, stays the only boundary untrusted text crosses.
Identity (``model``, ``provider``) is likewise the provider's to stamp from
configuration on the success path -- see
:mod:`app.services.machine_review.providers.cloud`; this module stamps it from
configuration only when it is *inventing* a failed result, because then there
is no provider payload to carry it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.config import settings as app_settings
from app.api.errors import NotFoundError
from app.db.models.common import MachineReviewJobFailure
from app.db.models.submission import Submission, SubmissionAuditEvent
from app.services.machine_review.agent.context_pack import build_review_context_pack
from app.services.machine_review.offer import offer_violation, offered_refs_from_context
from app.services.machine_review.providers.disabled import (
    DisabledMachineReviewProvider,
)
from app.services.machine_review.providers.factory import (
    build_machine_review_provider,
)
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    MachineReviewProvider,
    MachineReviewProviderAuthError,
    MachineReviewProviderConfigurationError,
    MachineReviewProviderTimeoutError,
    MachineReviewProviderUnavailableError,
    ReservedMachineReviewStatusError,
)
from app.services.machine_review.providers.openai_transport import ModelOutputTruncatedError, ModelRefusedError
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_FORMAT,
    SUMMARY_MAX_CHARS,
    MachineReviewProviderResult,
    MachineReviewStatus,
)
from app.services.submission import record_machine_review_audit_event

#: Failure text is built from an exception message, which has no length bound
#: at all -- a client library is free to put a whole response body in one. The
#: text is kept to this many characters for ``failure_reason`` and a job's
#: failure detail; the stored summary is clipped further, to the contract's cap.
#: Clipping is not cosmetic: an over-long summary would fail validation while
#: *constructing the failed result*, and the one thing this module must never do
#: is raise on the failure path. ``model`` is capped at 128.
_MAX_FAILURE_TEXT_CHARS = 2000
_MAX_MODEL_CHARS = 128

#: Marker appended to text that was cut to fit, so a reader can tell a short
#: message from a clipped one.
_TRUNCATION_MARKER = " [truncated]"

#: An API key shorter than this is not redacted from failure text. Redaction
#: is a substring replacement, and replacing a two-character "secret" would
#: shred every message that happened to contain those two characters while
#: protecting nothing a real key looks like.
_MIN_REDACTABLE_SECRET_CHARS = 8

#: What replaces a secret that did turn up in an exception message.
_REDACTED = "[redacted]"


@dataclass(frozen=True)
class MachineReviewRunOutcome:
    """What one run produced, in the terms a caller needs.

    Thin on purpose: the validated provider result is the payload, and the two
    extra fields are the things the result itself cannot say -- whether the
    audit event actually landed, and why the run failed when it did.

    ``failure_reason`` is non-``None`` exactly when :attr:`status` is
    ``machine_review_failed``. It carries the failure text, which can be longer
    than the stored :attr:`summary` (clipped to the contract's cap). The payload
    is ``extra="forbid"`` and has no error field, so in the *recorded event* the
    reason has nowhere to live but ``summary``. Naming it separately means a
    caller need not infer "this summary is an error" from the status.

    No row id appears here. Whether an event was
    written is a fact a caller can act on; which row it was is not.
    """

    result: MachineReviewProviderResult
    audit_event_recorded: bool
    failure_reason: str | None = None

    @property
    def status(self) -> MachineReviewStatus:
        """The advisory status of this run."""
        return self.result.status

    @property
    def findings_count(self) -> int:
        """How many advisory findings the reviewer returned."""
        return len(self.result.findings)

    @property
    def summary(self) -> str | None:
        """The reviewer's one-line summary, or the failure text."""
        return self.result.summary

    @property
    def model(self) -> str | None:
        """The model identity, stamped from configuration -- never claimed."""
        return self.result.model

    @property
    def provider(self) -> str | None:
        """The provider identity, stamped from configuration."""
        return self.result.provider


def _clip(text: str, limit: int) -> str:
    """Return ``text`` cut to ``limit`` characters, marked if it was cut."""
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _redact_secrets(text: str, settings_obj: Any) -> str:
    """Remove the configured API key from ``text`` if it appears verbatim.

    A belt to the transport's braces. Nothing in this codebase puts the key in
    an exception, but the exception text on the failure path comes from a
    third-party client library, and it is written into a durable audit event
    and an HTTP response. The cost of being wrong about what that library
    prints is a credential sitting in a database; the cost of the check is one
    string comparison on a path that only runs when something already failed.
    """
    key_env = getattr(settings_obj, "llm_precheck_api_key_env", None)
    if not key_env:
        return text
    secret = os.environ.get(key_env)
    if not secret or len(secret) < _MIN_REDACTABLE_SECRET_CHARS:
        return text
    return text.replace(secret, _REDACTED)


def _failure_text(prefix: str, exc: BaseException, settings_obj: Any) -> str:
    """Build the advisory failure text for ``exc``: named, redacted, clipped.

    The exception *class* is named alongside its message because the message
    alone rarely says which layer failed --
    :class:`~app.services.machine_review.providers.openai_transport.ModelOutputTruncatedError`
    and a JSON decode error read almost identically as prose, and the lever for
    each is different (raise ``LLM_PRECHECK_MAX_OUTPUT_TOKENS`` versus look at
    the model).
    """
    detail = str(exc).strip() or exc.__class__.__name__
    text = f"{prefix}: {exc.__class__.__name__}: {detail}"
    return _clip(_redact_secrets(text, settings_obj), _MAX_FAILURE_TEXT_CHARS)


def _configured_model(settings_obj: Any) -> str | None:
    """The model name configuration names, clipped to the contract's width."""
    model = getattr(settings_obj, "llm_precheck_model", None)
    if not model:
        return None
    return _clip(str(model), _MAX_MODEL_CHARS)


def _failed_result(
    *,
    reason: str,
    settings_obj: Any,
    provider_name: str | None,
    model: str | None = None,
) -> MachineReviewProviderResult:
    """Build the advisory failed result for a run that could not complete.

    ``model`` names the model the caller was committed to asking; it wins over
    the configured one.

    Identity is stamped from configuration here, not read from anywhere: on
    this path there is no provider payload to read it from, and a stored
    failure that cannot say which model was being asked is much harder to act
    on than one that can.
    """
    return MachineReviewProviderResult(
        format=MACHINE_REVIEW_FORMAT,
        status=MachineReviewStatus.machine_review_failed,
        curator_priority=None,
        summary=_clip(reason, SUMMARY_MAX_CHARS) or _clip("Machine review failed to review.", SUMMARY_MAX_CHARS),
        findings=(),
        model=_clip(model, _MAX_MODEL_CHARS) if model else _configured_model(settings_obj),
        provider=provider_name,
    )


def _record(
    session: Session,
    *,
    submission: Submission,
    result: MachineReviewProviderResult,
    provider_name: str | None,
) -> SubmissionAuditEvent | None:
    """Record the audit event; return it if it landed, ``None`` if not, never raise.

    A failure to *write down* a failure still must not reach the caller as an
    exception: an advisory reviewer that can take down an admin request by
    being unable to journal is worse than one that silently did not journal.
    The caller learns the difference from
    :attr:`MachineReviewRunOutcome.audit_event_recorded`, which is why that
    field is not cosmetic.
    """
    try:
        return record_machine_review_audit_event(
            session,
            submission=submission,
            result=result,
            provider=provider_name,
        )
    except Exception:
        # Roll back, or the caller inherits a poisoned session and the route
        # 500s in teardown -- which is the failure this whole function exists
        # to avoid, arriving one layer later and less legibly.
        #
        # Without this, a write failure leaves the session in
        # ``InFailedSqlTransaction``; ``get_write_db`` then commits during
        # teardown, that commit raises, and the admin gets a 500 instead of
        # the 200 carrying ``audit_event_recorded=false``. The API tests
        # cannot see it because they override ``get_write_db`` with the test
        # session, so the teardown commit never runs.
        #
        # Rolling back discards nothing a caller wanted kept: this function
        # is the only writer on the path, and the audit event is the only
        # thing in the transaction.
        try:
            session.rollback()
        except Exception:
            # A session too broken to roll back is still not worth raising
            # over. The outcome already says the event did not land.
            pass
        return None


class MachineReviewCancelled(Exception):
    """Raised by a provider that stopped because the job was cancelled."""


def resolve_provider(
    settings_obj: Any,
    provider: MachineReviewProvider | None = None,
) -> tuple[MachineReviewProvider | None, str | None]:
    """Return ``(provider, None)``, or ``(None, failure_reason)`` when it cannot be configured.

    An injected provider wins over the configured factory. Covers the
    configuration errors the factory raises for an incomplete cloud/local
    setup: a misconfigured deployment is the reviewer's failure to report.
    """
    if provider is not None:
        return provider, None
    try:
        return build_machine_review_provider(settings_obj), None
    except Exception as exc:
        return None, _failure_text("Machine review could not be configured", exc, settings_obj)


def build_review_context(
    session: Session, submission_id: int, settings_obj: Any = app_settings
) -> MachineReviewContext:
    """Build the review context of a submission, cut to ``LLM_PRECHECK_MAX_INPUT_TOKENS``."""
    return MachineReviewContext.from_llm_precheck_context(
        build_review_context_pack(
            session, submission_id, max_input_tokens=settings_obj.llm_precheck_max_input_tokens
        )
    )


def _failure_code_for(exc: BaseException) -> MachineReviewJobFailure:
    """The job failure code for an exception a provider raised."""
    if isinstance(exc, MachineReviewProviderUnavailableError):
        return MachineReviewJobFailure.provider_unavailable
    if isinstance(exc, MachineReviewProviderTimeoutError):
        return MachineReviewJobFailure.provider_timeout
    if isinstance(exc, MachineReviewProviderAuthError):
        return MachineReviewJobFailure.auth_failed
    if isinstance(exc, MachineReviewProviderConfigurationError):
        return MachineReviewJobFailure.config_error
    if isinstance(
        exc,
        (
            json.JSONDecodeError,
            ValidationError,
            ReservedMachineReviewStatusError,
            ModelOutputTruncatedError,
            ModelRefusedError,
            ValueError,
            TypeError,
        ),
    ):
        return MachineReviewJobFailure.invalid_output
    return MachineReviewJobFailure.internal_error


def invoke_provider(
    selected: MachineReviewProvider,
    context: MachineReviewContext,
    settings_obj: Any,
) -> tuple[MachineReviewProviderResult | None, str | None, MachineReviewJobFailure | None]:
    """Ask ``selected`` for a review; return ``(result, None, None)`` or ``(None, failure_reason, failure_code)``.

    Touches no database. Every exception becomes a failure reason and code, except
    :class:`MachineReviewCancelled`, which passes through untouched.
    """
    try:
        result = selected.review_submission(context)
    except MachineReviewCancelled:
        raise
    except Exception as exc:
        return None, _failure_text("Machine review failed to review", exc, settings_obj), _failure_code_for(exc)

    if not isinstance(result, MachineReviewProviderResult):
        # A type check, not a parse. Re-validating an object that is already
        # supposed to be validated would stand a second, more forgiving
        # boundary next to ``parse_machine_review_payload`` -- and a second
        # boundary that coerces is how a payload the strict one would have
        # refused gets in anyway. So this refuses; it does not repair.
        return (
            None,
            _clip(
                "Machine review provider returned "
                f"{type(result).__name__}, not a validated "
                "MachineReviewProviderResult.",
                _MAX_FAILURE_TEXT_CHARS,
            ),
            MachineReviewJobFailure.internal_error,
        )
    return result, None, None


def record_review(
    session: Session,
    *,
    submission: Submission,
    result: MachineReviewProviderResult | None,
    failure_reason: str | None,
    provider_name: str | None,
    settings_obj: Any,
    model: str | None = None,
) -> tuple[MachineReviewRunOutcome, SubmissionAuditEvent | None]:
    """Record a review, or an advisory failed result when ``failure_reason`` is given.

    ``model`` overrides the configured model name on a failed result.

    Returns the outcome and the audit event (``None`` if it did not land).
    """
    if result is None:
        result = _failed_result(
            reason=failure_reason or "Machine review failed to review.",
            settings_obj=settings_obj,
            provider_name=provider_name,
            model=model,
        )
        reason: str | None = failure_reason
    else:
        reason = (
            result.summary
            if result.status is MachineReviewStatus.machine_review_failed
            else None
        )
    event = _record(session, submission=submission, result=result, provider_name=provider_name)
    outcome = MachineReviewRunOutcome(
        result=result,
        audit_event_recorded=event is not None,
        failure_reason=reason,
    )
    return outcome, event


def run_machine_review_for_submission(
    session: Session,
    submission_id: int,
    *,
    provider: MachineReviewProvider | None = None,
    settings_obj: Any = app_settings,
) -> MachineReviewRunOutcome:
    """Run one advisory machine review for a submission and record the result.

    Resolves the provider (an injected one wins over the configured factory,
    which is how tests and the fake provider get in without any deployment
    setting ever selecting them), builds the review context, asks the provider
    for a review, and records exactly one audit event carrying the validated
    payload.

    Writes only ``submission_audit_event``. ``submission.status``, the
    moderation columns, ``record_review``, curator tasks and every scientific
    record are untouched -- machine review is advisory and authoritative for
    nothing.

    Off mode is the one outcome with no event. The disabled provider returns
    ``not_run`` without calling anything, and the absence of an event already
    means exactly that, so writing a
    row to say "nothing happened" would only add noise a curator reads past.

    Every other failure is converted, not raised: ``machine_review_failed``
    with a summary naming the cause, plus the event. Raises
    :class:`~app.api.errors.NotFoundError` -- and only that -- when
    ``submission_id`` names no submission; the API layer renders it as a 404.

    Commit control stays with the caller, as it does for every other service
    helper here: the event is flushed, not committed.
    """
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise NotFoundError("Submission not found.")

    selected, reason = resolve_provider(settings_obj, provider)
    if selected is None:
        outcome, _ = record_review(
            session,
            submission=submission,
            result=None,
            failure_reason=reason,
            provider_name=None,
            settings_obj=settings_obj,
        )
        return outcome

    provider_name = type(selected).__name__

    if isinstance(selected, DisabledMachineReviewProvider):
        return MachineReviewRunOutcome(
            result=selected.review_submission(
                MachineReviewContext(submission_id=submission_id)
            ),
            audit_event_recorded=False,
            failure_reason=None,
        )

    result: MachineReviewProviderResult | None = None
    try:
        context = build_review_context(session, submission_id, settings_obj)
    except Exception as exc:
        reason = _failure_text("Machine review failed to review", exc, settings_obj)
    else:
        try:
            result, reason, _ = invoke_provider(selected, context, settings_obj)
        except MachineReviewCancelled:
            result, reason = None, "Machine review was cancelled."
        else:
            if result is not None:
                violation = offer_violation(
                    result, offered_refs=offered_refs_from_context(context), returned_sources=frozenset()
                )
                if violation is not None:
                    reason = _clip(f"Machine review output was invalid: {violation}", _MAX_FAILURE_TEXT_CHARS)
                    result = None

    outcome, _ = record_review(
        session,
        submission=submission,
        result=result,
        failure_reason=reason,
        provider_name=provider_name,
        settings_obj=settings_obj,
    )
    return outcome


__all__ = [
    "MachineReviewCancelled",
    "MachineReviewRunOutcome",
    "build_review_context",
    "invoke_provider",
    "record_review",
    "resolve_provider",
    "run_machine_review_for_submission",
]
