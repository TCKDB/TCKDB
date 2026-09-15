"""Run one machine review for one submission, and write down what happened.

Until this module existed the machine-review stack had every part but an entry
point. :func:`~app.services.machine_review.providers.factory.build_machine_review_provider`
built a real provider, :func:`~app.services.submission.record_machine_review_v2_audit_event`
recorded a v2 result, and nothing under ``app/`` called either one -- only
tests did. The consequence was not subtle: a deployment could hold dozens of
submissions and zero machine-review audit events, so the curator queue that
reads those events was empty by construction rather than because nothing
needed a curator. This module is the call that was missing.

It is the v2 counterpart of
:func:`~app.services.llm_precheck.service.run_llm_precheck_for_submission` and
keeps that function's two load-bearing properties.

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
:func:`~app.services.machine_review.providers.interface.parse_machine_review_v2_payload`,
called inside the provider, stays the only boundary untrusted text crosses.
Identity (``model``, ``provider``) is likewise the provider's to stamp from
configuration on the success path -- see
:mod:`app.services.machine_review.providers.cloud`; this module stamps it from
configuration only when it is *inventing* a failed result, because then there
is no provider payload to carry it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.api.config import settings as app_settings
from app.api.errors import NotFoundError
from app.db.models.submission import Submission
from app.services.llm_precheck.context_builder import build_llm_precheck_context
from app.services.machine_review.providers.disabled import (
    DisabledMachineReviewProvider,
)
from app.services.machine_review.providers.factory import (
    build_machine_review_provider,
)
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    MachineReviewProvider,
)
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewProviderResultV2,
    MachineReviewStatus,
)
from app.services.submission import record_machine_review_v2_audit_event

#: ``MachineReviewProviderResultV2.summary`` is capped at 2000 characters and
#: ``model`` at 128. A failure summary is built from an exception message,
#: which has no length bound at all -- a client library is free to put a whole
#: response body in one. Clipping here is not cosmetic: an over-long summary
#: would fail validation while *constructing the failed result*, and the one
#: thing this module must never do is raise on the failure path.
_MAX_SUMMARY_CHARS = 2000
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
    ``machine_review_failed``, and on that path it carries the same text as
    :attr:`summary`. That is deliberate rather than duplication left in by
    accident: the v2 payload is ``extra="forbid"`` and has no error field, so
    in the *recorded event* the reason has nowhere to live but ``summary``.
    Naming it separately means a caller need not know that, and need not infer
    "this summary is an error" from the status.

    No row id appears here (DR-0028 Requirement 2). Whether an event was
    written is a fact a caller can act on; which row it was is not.
    """

    result: MachineReviewProviderResultV2
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
    return _clip(_redact_secrets(text, settings_obj), _MAX_SUMMARY_CHARS)


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
) -> MachineReviewProviderResultV2:
    """Build the advisory failed result for a run that could not complete.

    Identity is stamped from configuration here, not read from anywhere: on
    this path there is no provider payload to read it from, and a stored
    failure that cannot say which model was being asked is much harder to act
    on than one that can.
    """
    return MachineReviewProviderResultV2(
        schema_version=MACHINE_REVIEW_V2_SCHEMA_VERSION,
        status=MachineReviewStatus.machine_review_failed,
        curator_priority=None,
        summary=reason,
        findings=(),
        model=_configured_model(settings_obj),
        provider=provider_name,
        used_rag=False,
    )


def _record(
    session: Session,
    *,
    submission: Submission,
    result: MachineReviewProviderResultV2,
    provider_name: str | None,
) -> bool:
    """Record the audit event; report whether it landed, never raise.

    A failure to *write down* a failure still must not reach the caller as an
    exception: an advisory reviewer that can take down an admin request by
    being unable to journal is worse than one that silently did not journal.
    The caller learns the difference from
    :attr:`MachineReviewRunOutcome.audit_event_recorded`, which is why that
    field is not cosmetic.
    """
    try:
        record_machine_review_v2_audit_event(
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
        # MEASURED: without this, a write failure leaves the session in
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
        return False
    return True


def _failed_outcome(
    session: Session,
    *,
    submission: Submission,
    reason: str,
    settings_obj: Any,
    provider_name: str | None,
) -> MachineReviewRunOutcome:
    """Build, record and return the advisory failed outcome."""
    result = _failed_result(
        reason=reason,
        settings_obj=settings_obj,
        provider_name=provider_name,
    )
    recorded = _record(
        session,
        submission=submission,
        result=result,
        provider_name=provider_name,
    )
    return MachineReviewRunOutcome(
        result=result,
        audit_event_recorded=recorded,
        failure_reason=reason,
    )


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
    v2 payload.

    Writes only ``submission_audit_event``. ``submission.status``, the
    moderation columns, ``record_review``, curator tasks and every scientific
    record are untouched -- machine review is advisory and authoritative for
    nothing.

    Off mode is the one outcome with no event. The disabled provider returns
    ``not_run`` without calling anything, and the absence of an event already
    means exactly that (``optional_llm_precheck.md`` section 13), so writing a
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

    try:
        selected = (
            provider
            if provider is not None
            else build_machine_review_provider(settings_obj)
        )
    except Exception as exc:
        # Covers the configuration errors the factory raises for an incomplete
        # cloud/local setup, and the NotImplementedError local mode still
        # raises. A misconfigured deployment is the reviewer's failure to
        # report, not the admin request's failure to suffer.
        return _failed_outcome(
            session,
            submission=submission,
            reason=_failure_text(
                "Machine review could not be configured", exc, settings_obj
            ),
            settings_obj=settings_obj,
            provider_name=None,
        )

    provider_name = type(selected).__name__

    if isinstance(selected, DisabledMachineReviewProvider):
        return MachineReviewRunOutcome(
            result=selected.review_submission(
                MachineReviewContext(submission_id=submission_id)
            ),
            audit_event_recorded=False,
            failure_reason=None,
        )

    try:
        context = MachineReviewContext.from_llm_precheck_context(
            build_llm_precheck_context(session, submission_id)
        )
        result = selected.review_submission(context)
    except Exception as exc:
        return _failed_outcome(
            session,
            submission=submission,
            reason=_failure_text(
                "Machine review failed to review", exc, settings_obj
            ),
            settings_obj=settings_obj,
            provider_name=provider_name,
        )

    if not isinstance(result, MachineReviewProviderResultV2):
        # A type check, not a parse. Re-validating an object that is already
        # supposed to be validated would stand a second, more forgiving
        # boundary next to ``parse_machine_review_v2_payload`` -- and a second
        # boundary that coerces is how a payload the strict one would have
        # refused gets in anyway. So this refuses; it does not repair.
        return _failed_outcome(
            session,
            submission=submission,
            reason=_clip(
                "Machine review provider returned "
                f"{type(result).__name__}, not a validated "
                "MachineReviewProviderResultV2.",
                _MAX_SUMMARY_CHARS,
            ),
            settings_obj=settings_obj,
            provider_name=provider_name,
        )

    recorded = _record(
        session,
        submission=submission,
        result=result,
        provider_name=provider_name,
    )
    return MachineReviewRunOutcome(
        result=result,
        audit_event_recorded=recorded,
        failure_reason=(
            result.summary
            if result.status is MachineReviewStatus.machine_review_failed
            else None
        ),
    )


__all__ = [
    "MachineReviewRunOutcome",
    "run_machine_review_for_submission",
]
