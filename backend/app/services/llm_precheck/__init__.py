"""Optional AI Review Assistant service package.

This package contains only local plumbing for disabled and fake/test LLM
precheck providers. It intentionally performs no online API calls, local model
calls, RAG lookups, upload workflow wiring, or public read integration.
"""

from app.services.llm_precheck.schemas import (
    LLMFinding,
    LLMFindingCategory,
    LLMFindingSeverity,
    LLMPrecheckContext,
    LLMPrecheckLabel,
    LLMPrecheckResult,
)
from app.services.llm_precheck.service import run_llm_precheck_for_submission

__all__ = [
    "LLMFinding",
    "LLMFindingCategory",
    "LLMFindingSeverity",
    "LLMPrecheckContext",
    "LLMPrecheckLabel",
    "LLMPrecheckResult",
    "run_llm_precheck_for_submission",
]
