"""Submission context for machine review.

Holds the context types a machine-review provider reviews over; the builder is
:func:`app.services.machine_review.agent.context_pack.build_review_context_pack`. It performs no online API calls, local model
calls, RAG lookups, upload workflow wiring, or public read integration; the runner lives in
:mod:`app.services.machine_review`.
"""

from app.services.llm_precheck.schemas import LLMPrecheckContext

__all__ = ["LLMPrecheckContext"]
