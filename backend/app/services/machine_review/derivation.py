"""Deterministic derivation of a record-level :class:`MachineReviewStatus`.

The record-level ``status`` summarizes the *worst* finding, but the
non-finding outcomes (the reviewer could not complete, or no review was
performed) are not expressible as findings. This module turns a completion
outcome plus the finding set into a single status, deterministically: the
same inputs always yield the same status (spec §3 "State vs. severity").
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from app.services.machine_review.schemas import (
    MachineReviewFinding,
    MachineReviewSeverity,
    MachineReviewStatus,
)


class MachineReviewOutcome(str, Enum):
    """How a machine-review attempt concluded, independent of findings.

    Kept separate from :class:`MachineReviewStatus` because these describe
    *whether the reviewer ran*, while the status describes *what it
    concluded*. ``completed`` defers entirely to the finding severities.
    """

    not_performed = "not_performed"
    """The reviewer was disabled or intentionally skipped (-> ``not_run``)."""

    failed = "failed"
    """The reviewer could not complete: timeout, provider error, malformed or
    oversized output (-> ``machine_review_failed``). A failure of the
    reviewer, never of the record."""

    completed = "completed"
    """The reviewer ran to completion; status derives from findings."""


def derive_machine_review_status(
    findings: Sequence[MachineReviewFinding],
    outcome: MachineReviewOutcome = MachineReviewOutcome.completed,
) -> MachineReviewStatus:
    """Derive the single record-level status from an outcome and findings.

    Rules (spec §3), in precedence order:

    * ``outcome is failed``         -> ``machine_review_failed``
    * ``outcome is not_performed``  -> ``not_run``
    * any finding ``critical``      -> ``machine_screened_needs_attention``
    * any finding ``warning``       -> ``machine_screened_warning``
    * otherwise (completed, no warning/critical) -> ``machine_screened_pass``

    Notes for the implementer:

    * The first two rules are about the *reviewer*, not the findings, and
      take precedence over any findings that may have been collected before
      the outcome was known. ``info``-only findings are still a ``pass``.
    * ``machine_screened_blocking_concern`` is reserved and not produced here
      (see :class:`MachineReviewStatus`).
    """
    if outcome is MachineReviewOutcome.failed:
        return MachineReviewStatus.machine_review_failed

    if outcome is MachineReviewOutcome.not_performed:
        return MachineReviewStatus.not_run

    severities = {finding.severity for finding in findings}

    if MachineReviewSeverity.critical in severities:
        return MachineReviewStatus.machine_screened_needs_attention

    if MachineReviewSeverity.warning in severities:
        return MachineReviewStatus.machine_screened_warning

    return MachineReviewStatus.machine_screened_pass
