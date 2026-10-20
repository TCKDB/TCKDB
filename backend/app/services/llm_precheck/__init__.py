"""Submission context for machine review.

Holds only :class:`LLMPrecheckContext` and the builder that assembles it. It performs no online API calls, local model
calls, RAG lookups, upload workflow wiring, or public read integration; the runner lives in
:mod:`app.services.machine_review`.
"""

from app.services.llm_precheck.schemas import LLMPrecheckContext

__all__ = ["LLMPrecheckContext"]
