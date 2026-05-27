"""Provider interface for optional AI Review Assistant prechecks."""

from __future__ import annotations

from typing import Protocol

from app.services.llm_precheck.schemas import LLMPrecheckContext, LLMPrecheckResult


class LLMPrecheckProvider(Protocol):
    """Provider interface for optional LLM-based submission prechecks."""

    def review_submission(
        self,
        context: LLMPrecheckContext,
    ) -> LLMPrecheckResult:
        """Return a structured advisory precheck result for a submission context."""
