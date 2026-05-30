"""Contract tests for the provisional machine-review layer.

These prove the *contract* properties required before any provider,
persistence, or public exposure exists:

* status derivation is deterministic,
* malformed status/category/severity values are rejected,
* machine-review schemas are a separate axis from human review
  (``RecordReviewStatus``) and submission precheck (``SubmissionPrecheckLabel``),
* the result/finding models carry no mutation-payload field,
* ``evidence_keys`` are accepted as pointers only,
* ``used_rag`` is constrained ``False`` for the MVP.

No database, provider, RAG, or public read-API is involved.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.common import RecordReviewStatus, SubmissionPrecheckLabel
from app.services.machine_review import (
    CuratorPriority,
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewOutcome,
    MachineReviewResult,
    MachineReviewSeverity,
    MachineReviewStatus,
    derive_machine_review_status,
)


def _finding(severity: MachineReviewSeverity) -> MachineReviewFinding:
    """Build a minimal valid finding of a given severity."""
    return MachineReviewFinding(
        severity=severity,
        category=MachineReviewCategory.consistency,
        message="placeholder finding",
    )


# --------------------------------------------------------------------------- #
# Deterministic status derivation
# --------------------------------------------------------------------------- #


def test_derivation_failed_outcome_overrides_findings():
    """A reviewer failure yields machine_review_failed regardless of findings."""
    findings = (_finding(MachineReviewSeverity.critical),)
    assert (
        derive_machine_review_status(findings, MachineReviewOutcome.failed)
        is MachineReviewStatus.machine_review_failed
    )


def test_derivation_not_performed_outcome_overrides_findings():
    """A disabled/skipped reviewer yields not_run regardless of findings."""
    findings = (_finding(MachineReviewSeverity.warning),)
    assert (
        derive_machine_review_status(findings, MachineReviewOutcome.not_performed)
        is MachineReviewStatus.not_run
    )


def test_derivation_critical_finding_needs_attention():
    """Any critical finding maps to machine_screened_needs_attention."""
    findings = (
        _finding(MachineReviewSeverity.info),
        _finding(MachineReviewSeverity.warning),
        _finding(MachineReviewSeverity.critical),
    )
    assert (
        derive_machine_review_status(findings)
        is MachineReviewStatus.machine_screened_needs_attention
    )


def test_derivation_warning_finding_warning_status():
    """A warning (and no critical) maps to machine_screened_warning."""
    findings = (
        _finding(MachineReviewSeverity.info),
        _finding(MachineReviewSeverity.warning),
    )
    assert (
        derive_machine_review_status(findings)
        is MachineReviewStatus.machine_screened_warning
    )


def test_derivation_info_only_passes():
    """info-only findings still count as machine_screened_pass."""
    findings = (_finding(MachineReviewSeverity.info),)
    assert (
        derive_machine_review_status(findings)
        is MachineReviewStatus.machine_screened_pass
    )


def test_derivation_no_findings_passes():
    """A completed review with no findings is machine_screened_pass."""
    assert (
        derive_machine_review_status(())
        is MachineReviewStatus.machine_screened_pass
    )


def test_derivation_is_deterministic_and_pure():
    """Identical inputs always yield an identical status across repeated calls."""
    findings = (
        _finding(MachineReviewSeverity.warning),
        _finding(MachineReviewSeverity.critical),
    )
    results = {derive_machine_review_status(findings) for _ in range(25)}
    assert results == {MachineReviewStatus.machine_screened_needs_attention}


def test_derivation_does_not_emit_reserved_blocking_concern():
    """The reserved blocking_concern value is never produced and never defined."""
    assert "machine_screened_blocking_concern" not in {
        member.value for member in MachineReviewStatus
    }
    for outcome in MachineReviewOutcome:
        status = derive_machine_review_status(
            (_finding(MachineReviewSeverity.critical),), outcome
        )
        assert status in set(MachineReviewStatus)


# --------------------------------------------------------------------------- #
# Malformed enum values are rejected
# --------------------------------------------------------------------------- #


def test_malformed_status_value_rejected():
    """A non-vocabulary status fails validation."""
    with pytest.raises(ValidationError):
        MachineReviewResult(status="approved")  # human-review value, not ours


def test_malformed_severity_value_rejected():
    """A non-vocabulary severity fails validation."""
    with pytest.raises(ValidationError):
        MachineReviewFinding(
            severity="blocking",
            category=MachineReviewCategory.consistency,
            message="x",
        )


def test_malformed_category_value_rejected():
    """A non-vocabulary category fails validation."""
    with pytest.raises(ValidationError):
        MachineReviewFinding(
            severity=MachineReviewSeverity.info,
            category="made_up_category",
            message="x",
        )


# --------------------------------------------------------------------------- #
# Separate axis from human review and submission precheck
# --------------------------------------------------------------------------- #


def test_machine_status_is_distinct_type_from_record_review_status():
    """MachineReviewStatus is its own enum, not RecordReviewStatus."""
    assert MachineReviewStatus is not RecordReviewStatus
    assert not issubclass(MachineReviewStatus, RecordReviewStatus)


def test_machine_status_values_disjoint_from_record_review_status():
    """No machine-review status token collides with a human-review token."""
    machine_values = {m.value for m in MachineReviewStatus}
    human_values = {m.value for m in RecordReviewStatus}
    assert machine_values.isdisjoint(human_values)


def test_human_verdicts_are_not_valid_machine_status():
    """Human verdicts (approved/rejected/deprecated) are not machine statuses."""
    for human in ("approved", "rejected", "deprecated", "under_review"):
        with pytest.raises(ValidationError):
            MachineReviewResult(status=human)


def test_machine_status_values_disjoint_from_submission_precheck_label():
    """Machine-review status is separate from the submission precheck label."""
    machine_values = {m.value for m in MachineReviewStatus}
    precheck_values = {m.value for m in SubmissionPrecheckLabel}
    assert MachineReviewStatus is not SubmissionPrecheckLabel
    assert machine_values.isdisjoint(precheck_values)


def test_precheck_labels_are_not_valid_machine_status():
    """A SubmissionPrecheckLabel value cannot stand in for a machine status."""
    for label in SubmissionPrecheckLabel:
        with pytest.raises(ValidationError):
            MachineReviewResult(status=label.value)


# --------------------------------------------------------------------------- #
# No mutation payload; pointers only
# --------------------------------------------------------------------------- #


_MUTATION_HINTS = ("set", "mutat", "override", "write", "patch", "update", "apply")


def test_result_carries_no_mutation_payload_field():
    """No field on the result/finding can carry a 'set field X' instruction."""
    for model in (MachineReviewResult, MachineReviewFinding):
        for name in model.model_fields:
            assert not any(hint in name.lower() for hint in _MUTATION_HINTS), (
                f"{model.__name__}.{name} looks like a mutation payload"
            )


def test_result_rejects_injected_mutation_payload():
    """extra='forbid' blocks an injected mutation field on the result."""
    with pytest.raises(ValidationError):
        MachineReviewResult(
            status=MachineReviewStatus.machine_screened_pass,
            set_field={"trust_status": "well_supported"},
        )


def test_finding_rejects_injected_mutation_payload():
    """extra='forbid' blocks an injected mutation field on a finding."""
    with pytest.raises(ValidationError):
        MachineReviewFinding(
            severity=MachineReviewSeverity.info,
            category=MachineReviewCategory.consistency,
            message="x",
            mutation={"is_certified": True},
        )


def test_evidence_keys_accepted_as_pointer_strings():
    """evidence_keys accept citation pointers and round-trip unchanged."""
    finding = MachineReviewFinding(
        severity=MachineReviewSeverity.warning,
        category=MachineReviewCategory.transition_state_validation,
        record_type="transition_state_entry",
        record_ref="tse_abc123",
        message="Missing IRC/path-search evidence.",
        evidence_keys=(
            "evidence.missing_checks.irc_evidence_present",
            "evidence.passed_checks.single_imaginary_frequency_for_ts",
        ),
    )
    assert finding.evidence_keys == (
        "evidence.missing_checks.irc_evidence_present",
        "evidence.passed_checks.single_imaginary_frequency_for_ts",
    )
    # Pointers cite; the model exposes no value-bearing/structured counterpart.
    assert "evidence_values" not in MachineReviewFinding.model_fields


# --------------------------------------------------------------------------- #
# used_rag constrained False for the MVP
# --------------------------------------------------------------------------- #


def test_used_rag_defaults_false():
    """used_rag defaults to False."""
    result = MachineReviewResult(status=MachineReviewStatus.machine_screened_pass)
    assert result.used_rag is False


def test_used_rag_true_is_rejected():
    """A provider claiming RAG usage fails validation in the MVP."""
    with pytest.raises(ValidationError):
        MachineReviewResult(
            status=MachineReviewStatus.machine_screened_pass,
            used_rag=True,
        )


# --------------------------------------------------------------------------- #
# curator_priority is advisory metadata, optional
# --------------------------------------------------------------------------- #


def test_curator_priority_is_optional_and_enum_constrained():
    """curator_priority is optional and only accepts the low/medium/high vocab."""
    assert (
        MachineReviewResult(
            status=MachineReviewStatus.machine_screened_warning
        ).curator_priority
        is None
    )
    assert (
        MachineReviewResult(
            status=MachineReviewStatus.machine_screened_warning,
            curator_priority=CuratorPriority.high,
        ).curator_priority
        is CuratorPriority.high
    )
    with pytest.raises(ValidationError):
        MachineReviewResult(
            status=MachineReviewStatus.machine_screened_warning,
            curator_priority="urgent",
        )
