"""Disabled/off machine-review provider.

Selected when ``AI_REVIEW_ASSISTANT_MODE=off``. Makes no model call, needs no
API key, base URL, or extra service, and returns a valid native v2 result with
``status=not_run``. The service layer
(:func:`~app.services.machine_review.run.run_machine_review_for_submission`)
writes nothing when it gets this provider — absence of an audit event already
means ``not_run`` (``optional_llm_precheck.md`` §13), so a row saying "nothing
happened" would only be noise a curator reads past. This provider exists so the
v2 type contract holds in every mode and so off mode has a deterministic,
dependency-free result.
"""

from __future__ import annotations

from app.services.machine_review.providers.interface import MachineReviewContext
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_V2_SCHEMA_VERSION,
    MachineReviewProviderResultV2,
    MachineReviewStatus,
)

#: Stable off-mode summary; mirrors the v1 disabled provider's intent.
DISABLED_SUMMARY = "AI Review Assistant is disabled / not run"


class DisabledMachineReviewProvider:
    """Provider used when machine review is off; returns a v2 not-run result."""

    def review_submission(
        self,
        context: MachineReviewContext,
    ) -> MachineReviewProviderResultV2:
        """Return a deterministic not-run v2 result without external dependencies."""
        return MachineReviewProviderResultV2(
            schema_version=MACHINE_REVIEW_V2_SCHEMA_VERSION,
            status=MachineReviewStatus.not_run,
            curator_priority=None,
            summary=DISABLED_SUMMARY,
            findings=(),
            model=None,
            provider=type(self).__name__,
            used_rag=False,
        )
