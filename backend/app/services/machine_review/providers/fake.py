"""Test-only fake machine-review provider emitting native results.

Developer/test-only. The deployer-facing factory in :mod:`factory` builds the
scripted agent (:class:`ScriptedFakeMachineReviewAgent`) for
``AI_REVIEW_ASSISTANT_MODE=test`` only under ``DEPLOYMENT_MODE=local`` and
refuses it in every hosted mode; the plain :class:`FakeMachineReviewProvider`
comes only from :func:`build_fake_machine_review_provider` or direct
instantiation in a test.

It makes no network call and returns deterministic
:class:`~app.services.machine_review.schemas.MachineReviewProviderResult`
payloads. The module-level builders cover the four shapes a test needs —
pass / warning / critical / failed — and exercise the categories only the machine-review vocabulary has
(``transition_state_validation``, ``schema_gap``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    machine_review_result_to_details_json,
    parse_machine_review_payload,
)
from app.services.machine_review.schemas import (
    MACHINE_REVIEW_FORMAT,
    CuratorPriority,
    MachineReviewCategory,
    MachineReviewProviderFinding,
    MachineReviewProviderResult,
    MachineReviewSeverity,
    MachineReviewStatus,
)

_FAKE_MODEL = "fake_test/machine-review"
_FAKE_PROVIDER_NAME = "FakeMachineReviewProvider"


def make_pass_result(
    *,
    summary: str = "Fake machine review found no advisory concerns.",
) -> MachineReviewProviderResult:
    """Build a clean pass result with no findings."""
    return MachineReviewProviderResult(
        format=MACHINE_REVIEW_FORMAT,
        status=MachineReviewStatus.machine_screened_pass,
        curator_priority=None,
        summary=summary,
        findings=(),
        model=_FAKE_MODEL,
        provider=_FAKE_PROVIDER_NAME,
        used_rag=False,
    )


def make_warning_result(
    *,
    record_type: str | None = "calculation",
    record_ref: str | None = "1",
) -> MachineReviewProviderResult:
    """Build a warning result using a native ``schema_gap`` finding.

    ``schema_gap`` is a category native to the machine-review vocabulary.
    """
    return MachineReviewProviderResult(
        format=MACHINE_REVIEW_FORMAT,
        status=MachineReviewStatus.machine_screened_warning,
        curator_priority=CuratorPriority.medium,
        summary="Fake machine review found one advisory warning.",
        findings=(
            MachineReviewProviderFinding(
                severity=MachineReviewSeverity.warning,
                category=MachineReviewCategory.schema_gap,
                record_type=record_type,
                record_ref=record_ref,
                message=(
                    "A reported quantity has no schema field to hold it; the "
                    "value is currently only in a free-text note."
                ),
                evidence_keys=("schema_gap.unmapped_quantity",),
                recommended_action=(
                    "Decide whether to model this quantity explicitly or accept "
                    "the note; flag to the uploader if it should be structured."
                ),
            ),
        ),
        model=_FAKE_MODEL,
        provider=_FAKE_PROVIDER_NAME,
        used_rag=False,
    )


def make_critical_result(
    *,
    record_type: str | None = "transition_state_entry",
    record_ref: str | None = "9002",
) -> MachineReviewProviderResult:
    """Build a needs-attention result using a native ``transition_state_validation`` finding.

    ``transition_state_validation`` is the other category native to the machine-review vocabulary.
    """
    return MachineReviewProviderResult(
        format=MACHINE_REVIEW_FORMAT,
        status=MachineReviewStatus.machine_screened_needs_attention,
        curator_priority=CuratorPriority.high,
        summary="Fake machine review found a critical transition-state contradiction.",
        findings=(
            MachineReviewProviderFinding(
                severity=MachineReviewSeverity.critical,
                category=MachineReviewCategory.transition_state_validation,
                record_type=record_type,
                record_ref=record_ref,
                message=(
                    "Marked validated, but the frequency set shows no single "
                    "imaginary mode expected for a first-order saddle point."
                ),
                evidence_keys=("ts.imaginary_frequency_count", "ts.validated"),
                recommended_action=(
                    "Re-run the TS frequency analysis and confirm exactly one "
                    "imaginary mode before validating."
                ),
            ),
        ),
        model=_FAKE_MODEL,
        provider=_FAKE_PROVIDER_NAME,
        used_rag=False,
    )


def make_failed_result(
    *,
    summary: str = "Fake machine review failed to review.",
) -> MachineReviewProviderResult:
    """Build a failed result (the reviewer-failure axis, not a record failure)."""
    return MachineReviewProviderResult(
        format=MACHINE_REVIEW_FORMAT,
        status=MachineReviewStatus.machine_review_failed,
        curator_priority=None,
        summary=summary,
        findings=(),
        model=_FAKE_MODEL,
        provider=_FAKE_PROVIDER_NAME,
        used_rag=False,
    )


class FakeMachineReviewProvider:
    """Deterministic test/dev provider that never calls the network.

    With a ``fixed_result`` it returns exactly that payload; otherwise it
    derives a simple result from the context (a pass when records are linked, a
    warning when none are). Test-only — see the module docstring.
    """

    def __init__(
        self,
        fixed_result: MachineReviewProviderResult | None = None,
    ) -> None:
        """Create a fake provider with an optional fixed result."""
        self._fixed_result = fixed_result

    def review_submission(
        self,
        context: MachineReviewContext,
    ) -> MachineReviewProviderResult:
        """Return either the configured result or a simple context-derived one."""
        if self._fixed_result is not None:
            return self._fixed_result

        precheck = context.precheck_context
        if precheck is None or not precheck.record_refs:
            return make_warning_result(record_type="submission", record_ref=None)

        return make_pass_result(
            summary=(
                "Fake machine review inspected "
                f"{len(precheck.record_refs)} linked record(s)."
            ),
        )


class ScriptedFakeMachineReviewAgent:
    """A fixed-length scripted reviewer that reports each step and honours cancellation.

    Makes no network call. :meth:`run` returns the review as JSON text, the way
    a real agent does, so the worker and the synchronous route both cross the
    same parse boundary.
    """

    def __init__(
        self,
        *,
        steps: int = 3,
        step_delay_seconds: float = 0.0,
        result: MachineReviewProviderResult | None = None,
    ) -> None:
        """Create an agent that takes ``steps`` steps of ``step_delay_seconds`` each."""
        self.steps = steps
        self.step_delay_seconds = step_delay_seconds
        self._result = result if result is not None else make_pass_result()

    def run(
        self,
        context: MachineReviewContext,
        *,
        on_step: Callable[[int], None],
        cancelled: Callable[[], bool],
    ) -> str:
        """Take the scripted steps, then return the review as JSON text.

        Raises :class:`~app.services.machine_review.run.MachineReviewCancelled`
        when ``cancelled()`` is true before a step.
        """
        from app.services.machine_review.run import MachineReviewCancelled

        for index in range(1, self.steps + 1):
            if cancelled():
                raise MachineReviewCancelled
            if self.step_delay_seconds > 0:
                time.sleep(self.step_delay_seconds)
            on_step(index)
        return json.dumps(machine_review_result_to_details_json(self._result))

    def review_submission(self, context: MachineReviewContext) -> MachineReviewProviderResult:
        """Run without progress reporting or cancellation and return the parsed review."""
        text = self.run(context, on_step=lambda _step: None, cancelled=lambda: False)
        return parse_machine_review_payload(text)


def build_fake_machine_review_provider(
    fixed_result: MachineReviewProviderResult | None = None,
) -> FakeMachineReviewProvider:
    """Test helper: build the fake provider.

    Deliberately a separate, clearly test-named entry point. The deployer-facing
    :func:`~app.services.machine_review.providers.factory.build_machine_review_provider`
    returns the scripted agent, not this provider, and only under
    ``DEPLOYMENT_MODE=local``.
    """
    return FakeMachineReviewProvider(fixed_result=fixed_result)
