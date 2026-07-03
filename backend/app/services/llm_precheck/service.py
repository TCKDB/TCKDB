"""Service orchestration for optional AI Review Assistant prechecks."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.config import settings as app_settings
from app.db.models.submission import Submission
from app.services.llm_precheck.context_builder import build_llm_precheck_context
from app.services.llm_precheck.interface import LLMPrecheckProvider
from app.services.llm_precheck.providers import (
    DisabledLLMPrecheckProvider,
    LLMPrecheckConfigurationError,
    build_llm_precheck_provider,
)
from app.services.llm_precheck.schemas import (
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.submission import record_llm_precheck_audit_event


def _failed_to_review_result(summary: str) -> LLMPrecheckResult:
    """Build a standardized advisory failure result."""
    return LLMPrecheckResult(
        label=LLMPrecheckLabel.failed_to_review,
        summary=summary,
        findings=(),
        model=None,
        used_rag=False,
    )


def _record_precheck_attempt(
    session: Session,
    *,
    submission_id: int,
    result: LLMPrecheckResult,
    provider_name: str | None = None,
    mode: str | None = None,
    error_kind: str | None = None,
) -> None:
    submission = session.get(Submission, submission_id)
    if submission is None:
        return

    record_llm_precheck_audit_event(
        session,
        submission=submission,
        result=result,
        provider=provider_name,
        mode=mode,
        error_kind=error_kind,
    )


def run_llm_precheck_for_submission(
    session: Session,
    submission_id: int,
    *,
    provider: LLMPrecheckProvider | None = None,
    settings_obj=app_settings,
) -> LLMPrecheckResult:
    """Run optional AI Review Assistant precheck for a submission.

    Provider failures are converted into advisory ``failed_to_review``
    results. This function does not mutate submission status, scientific
    records, deterministic trust outputs, or public read behavior.
    """
    provider_supplied = provider is not None
    mode = getattr(settings_obj, "ai_review_assistant_mode", None)

    try:
        selected_provider = provider or build_llm_precheck_provider(settings_obj)
    except LLMPrecheckConfigurationError as exc:
        result = _failed_to_review_result(str(exc))
        _record_precheck_attempt(
            session,
            submission_id=submission_id,
            result=result,
            mode=mode,
            error_kind="configuration_error",
        )
        return result

    if isinstance(selected_provider, DisabledLLMPrecheckProvider):
        return selected_provider.review_submission(
            build_llm_precheck_context(session, submission_id)
        )

    provider_name = selected_provider.__class__.__name__
    event_mode = None if provider_supplied else mode

    try:
        context = build_llm_precheck_context(session, submission_id)
        result = selected_provider.review_submission(context)
        validated = LLMPrecheckResult.model_validate(result)
        _record_precheck_attempt(
            session,
            submission_id=submission_id,
            result=validated,
            provider_name=provider_name,
            mode=event_mode,
        )
        return validated
    except ValidationError:
        result = _failed_to_review_result("AI Review Assistant returned malformed output.")
        _record_precheck_attempt(
            session,
            submission_id=submission_id,
            result=result,
            provider_name=provider_name,
            mode=event_mode,
            error_kind="malformed_output",
        )
        return result
    except Exception as exc:
        result = _failed_to_review_result(f"AI Review Assistant failed to review: {exc}")
        _record_precheck_attempt(
            session,
            submission_id=submission_id,
            result=result,
            provider_name=provider_name,
            mode=event_mode,
            error_kind=exc.__class__.__name__,
        )
        return result
