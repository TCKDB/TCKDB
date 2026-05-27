"""Disabled and fake provider implementations for LLM prechecks."""

from __future__ import annotations

from typing import Any

from app.api.config import settings as app_settings
from app.services.llm_precheck.interface import LLMPrecheckProvider
from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckContext,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)


class LLMPrecheckConfigurationError(RuntimeError):
    """Raised when a configured AI Review Assistant mode is not usable."""


class DisabledLLMPrecheckProvider:
    """Provider used when AI Review Assistant is off."""

    def review_submission(
        self,
        context: LLMPrecheckContext,
    ) -> LLMPrecheckResult:
        """Return a deterministic not-run result without external dependencies."""
        return LLMPrecheckResult(
            label=LLMPrecheckLabel.not_run,
            summary="AI Review Assistant is off",
            findings=(),
            model=None,
            used_rag=False,
        )


class FakeLLMPrecheckProvider:
    """Deterministic test/dev provider that never calls the network."""

    def __init__(self, fixed_result: LLMPrecheckResult | None = None) -> None:
        """Create a fake provider with an optional fixed result."""
        self._fixed_result = fixed_result

    def review_submission(
        self,
        context: LLMPrecheckContext,
    ) -> LLMPrecheckResult:
        """Return either the configured result or a simple context-derived result."""
        if self._fixed_result is not None:
            return self._fixed_result

        if not context.record_refs:
            return LLMPrecheckResult(
                label=LLMPrecheckLabel.warning,
                summary="Fake precheck found no linked records to inspect.",
                findings=(
                    LLMFinding(
                        severity=LLMFindingSeverity.warning,
                        category=LLMFindingCategory.provenance,
                        record_type="submission",
                        record_id=context.submission_id,
                        message="Submission has no linked scientific records.",
                        evidence_keys=("submission.record_links",),
                    ),
                ),
                model="fake_test/simple-v1",
                used_rag=False,
            )

        return LLMPrecheckResult(
            label=LLMPrecheckLabel.pass_,
            summary=(
                "Fake precheck inspected "
                f"{len(context.record_refs)} linked record(s)."
            ),
            findings=(),
            model="fake_test/simple-v1",
            used_rag=False,
        )


def resolve_llm_precheck_provider_name(settings_obj: Any = app_settings) -> str:
    """Resolve user-facing AI Review Assistant mode to an internal provider."""
    mode = settings_obj.ai_review_assistant_mode
    if mode == "off":
        return "disabled"
    if mode == "cloud":
        return "online_api"
    if mode == "local":
        return "local_http"
    if mode == "test":
        return "fake_test"
    return settings_obj.llm_precheck_provider


def build_llm_precheck_provider(
    settings_obj: Any = app_settings,
) -> LLMPrecheckProvider:
    """Build the configured provider without enabling real model calls."""
    provider_name = resolve_llm_precheck_provider_name(settings_obj)
    if provider_name == "disabled":
        return DisabledLLMPrecheckProvider()
    if provider_name == "fake_test":
        return FakeLLMPrecheckProvider()
    if provider_name == "online_api":
        raise LLMPrecheckConfigurationError(
            "Cloud mode is specified but no online provider is implemented yet."
        )
    if provider_name == "local_http":
        raise LLMPrecheckConfigurationError(
            "Local mode is specified but no local provider is implemented yet."
        )
    raise LLMPrecheckConfigurationError(
        f"Unsupported LLM precheck provider: {provider_name}"
    )
