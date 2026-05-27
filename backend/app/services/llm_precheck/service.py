"""Service orchestration for optional AI Review Assistant prechecks."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.config import settings as app_settings
from app.services.llm_precheck.context_builder import build_llm_precheck_context
from app.services.llm_precheck.interface import LLMPrecheckProvider
from app.services.llm_precheck.providers import (
    LLMPrecheckConfigurationError,
    build_llm_precheck_provider,
)
from app.services.llm_precheck.schemas import (
    LLMPrecheckLabel,
    LLMPrecheckResult,
)


def _failed_to_review_result(summary: str) -> LLMPrecheckResult:
    """Build a standardized advisory failure result."""
    return LLMPrecheckResult(
        label=LLMPrecheckLabel.failed_to_review,
        summary=summary,
        findings=(),
        model=None,
        used_rag=False,
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
    try:
        selected_provider = provider or build_llm_precheck_provider(settings_obj)
    except LLMPrecheckConfigurationError as exc:
        return _failed_to_review_result(str(exc))

    try:
        context = build_llm_precheck_context(session, submission_id)
        result = selected_provider.review_submission(context)
        return LLMPrecheckResult.model_validate(result)
    except ValidationError:
        return _failed_to_review_result("AI Review Assistant returned malformed output.")
    except Exception as exc:  # noqa: BLE001 - precheck must never fail caller workflows.
        return _failed_to_review_result(f"AI Review Assistant failed to review: {exc}")
