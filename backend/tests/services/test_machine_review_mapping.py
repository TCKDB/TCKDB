"""Unit tests for the submission -> record machine-review mapping policy.

These prove the safety policy in
``backend/docs/specs/provisional_machine_review.md`` §6/§13: a
submission-scoped advisory result must never automatically become a
record-level result, and a record-addressed finding maps **only** to the
exact linked record it names — never fanning out to every record in the
submission.

The mapper under test is pure: no database, no provider, no persistence, no
public exposure. The tests build :class:`MachineReviewFinding` objects and
lightweight :class:`SubmissionRecordLinkRef` link descriptions directly.
"""

from __future__ import annotations

from app.services.machine_review import (
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewOutcome,
    MachineReviewSeverity,
    MachineReviewStatus,
    SubmissionRecordLinkRef,
    UnmappedReason,
    map_findings_to_submission_records,
)


def _finding(
    severity: MachineReviewSeverity,
    *,
    record_type: str | None = None,
    record_ref: str | None = None,
    message: str = "advisory finding",
    category: MachineReviewCategory = MachineReviewCategory.consistency,
) -> MachineReviewFinding:
    """Build a minimal valid finding, optionally addressing a record."""
    return MachineReviewFinding(
        severity=severity,
        category=category,
        record_type=record_type,
        record_ref=record_ref,
        message=message,
    )


def _link(record_type: str, record_ref: str, record_id: int) -> SubmissionRecordLinkRef:
    """Build a resolved submission->record link."""
    return SubmissionRecordLinkRef(
        record_type=record_type, record_ref=record_ref, record_id=record_id
    )


# --------------------------------------------------------------------------- #
# Submission-scoped findings never become record-level results
# --------------------------------------------------------------------------- #


def test_submission_summary_only_does_not_map_to_any_record():
    """A finding with no record_type stays submission-scoped (rule 1)."""
    links = [_link("calculation", "calc_aaa", 1)]
    finding = _finding(MachineReviewSeverity.warning)  # no record_type

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert mapping.mapped_by_record == {}
    assert len(mapping.unmapped_findings) == 1
    assert mapping.unmapped_findings[0].reason is UnmappedReason.submission_scoped
    assert mapping.unmapped_findings[0].finding is finding
    # Submission-scoped is expected, not a defect -> no warning.
    assert mapping.mapping_warnings == ()


# --------------------------------------------------------------------------- #
# Exact-match mapping, and the absence of fan-out
# --------------------------------------------------------------------------- #


def test_finding_maps_only_to_exact_linked_record():
    """A record-addressed finding maps to exactly the record it names (rule 3)."""
    links = [
        _link("calculation", "calc_aaa", 1),
        _link("calculation", "calc_bbb", 2),
    ]
    finding = _finding(
        MachineReviewSeverity.warning,
        record_type="calculation",
        record_ref="calc_bbb",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert set(mapping.mapped_by_record) == {("calculation", "calc_bbb")}
    record = mapping.mapped_by_record[("calculation", "calc_bbb")]
    assert record.record_id == 2
    assert record.findings == (finding,)
    assert mapping.unmapped_findings == ()


def test_finding_does_not_fan_out_to_all_submission_records():
    """One finding for one record must not attach to sibling records (rule 4)."""
    links = [
        _link("calculation", "calc_aaa", 1),
        _link("calculation", "calc_bbb", 2),
        _link("species", "spc_ccc", 3),
    ]
    finding = _finding(
        MachineReviewSeverity.critical,
        record_type="calculation",
        record_ref="calc_aaa",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    # Only the named record is present; the other two linked records are absent.
    assert set(mapping.mapped_by_record) == {("calculation", "calc_aaa")}
    assert ("calculation", "calc_bbb") not in mapping.mapped_by_record
    assert ("species", "spc_ccc") not in mapping.mapped_by_record


# --------------------------------------------------------------------------- #
# Unmappable findings are routed safely with a warning
# --------------------------------------------------------------------------- #


def test_finding_for_unlinked_record_is_unmapped():
    """A finding naming a record not linked to the submission does not map (rule 7)."""
    links = [_link("calculation", "calc_aaa", 1)]
    finding = _finding(
        MachineReviewSeverity.warning,
        record_type="calculation",
        record_ref="calc_not_in_submission",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert mapping.mapped_by_record == {}
    assert len(mapping.unmapped_findings) == 1
    assert mapping.unmapped_findings[0].reason is UnmappedReason.unlinked_record
    assert len(mapping.mapping_warnings) == 1
    assert "not linked" in mapping.mapping_warnings[0]


def test_typed_finding_with_no_ref_and_no_linked_record_of_type_is_unmapped():
    """A typed finding with no ref and no linked record of that type is unmapped.

    Precedence step 2 (single-unambiguous-type) has no candidate to fall back
    to, so the finding stays unmapped with ``missing_record_ref``.
    """
    links = [_link("calculation", "calc_aaa", 1)]
    finding = _finding(
        MachineReviewSeverity.warning,
        record_type="thermo",  # no thermo record is linked
        record_ref=None,
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert mapping.mapped_by_record == {}
    assert mapping.unmapped_findings[0].reason is UnmappedReason.missing_record_ref
    assert len(mapping.mapping_warnings) == 1
    assert "no record_ref" in mapping.mapping_warnings[0]


def test_typed_finding_with_no_ref_maps_to_single_linked_record_of_type():
    """A typed finding with no ref maps to the one linked record of that type.

    Precedence step 2: exactly one linked ``thermo`` record exists, so the
    mapping is unambiguous and is allowed (no guessing required).
    """
    links = [
        _link("calculation", "calc_aaa", 1),
        _link("thermo", "thm_zzz", 4),  # the only linked thermo record
    ]
    finding = _finding(
        MachineReviewSeverity.warning,
        record_type="thermo",
        record_ref=None,
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert set(mapping.mapped_by_record) == {("thermo", "thm_zzz")}
    record = mapping.mapped_by_record[("thermo", "thm_zzz")]
    assert record.record_id == 4
    assert record.findings == (finding,)
    # The unrelated calculation record is never touched (no fan-out).
    assert ("calculation", "calc_aaa") not in mapping.mapped_by_record
    assert mapping.unmapped_findings == ()
    assert mapping.mapping_warnings == ()


def test_typed_finding_with_no_ref_does_not_guess_among_multiple_of_type():
    """A typed finding with no ref stays unmapped when the type is ambiguous.

    Two linked ``calculation`` records means the single-unambiguous-type
    fallback refuses to guess (anti-fan-out for type-only findings).
    """
    links = [
        _link("calculation", "calc_aaa", 1),
        _link("calculation", "calc_bbb", 2),
    ]
    finding = _finding(
        MachineReviewSeverity.critical,
        record_type="calculation",
        record_ref=None,
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert mapping.mapped_by_record == {}
    assert (
        mapping.unmapped_findings[0].reason is UnmappedReason.ambiguous_record_type
    )
    assert len(mapping.mapping_warnings) == 1
    assert "multiple linked records" in mapping.mapping_warnings[0]


def test_ref_addressed_finding_is_not_redirected_to_single_record_of_type():
    """An explicit non-matching ref is unlinked, never redirected via fallback.

    Precedence step 1 (exact ref) fails and the type-only fallback must **not**
    rescue a finding that explicitly named a different (absent) record — that
    would be guessing.
    """
    links = [_link("calculation", "calc_aaa", 1)]  # exactly one calculation
    finding = _finding(
        MachineReviewSeverity.warning,
        record_type="calculation",
        record_ref="calc_does_not_exist",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert mapping.mapped_by_record == {}
    assert mapping.unmapped_findings[0].reason is UnmappedReason.unlinked_record
    assert "not linked" in mapping.mapping_warnings[0]


def test_finding_with_unknown_record_type_is_unmapped():
    """A finding citing a record_type outside the vocabulary is unmapped (rule 5)."""
    links = [_link("calculation", "calc_aaa", 1)]
    finding = _finding(
        MachineReviewSeverity.critical,
        record_type="not_a_real_table",
        record_ref="x_123",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    assert mapping.mapped_by_record == {}
    assert mapping.unmapped_findings[0].reason is UnmappedReason.unknown_record_type
    assert len(mapping.mapping_warnings) == 1
    assert "unknown record_type" in mapping.mapping_warnings[0]


# --------------------------------------------------------------------------- #
# Per-record status derivation
# --------------------------------------------------------------------------- #


def test_multiple_findings_for_same_record_derive_single_status():
    """Several findings on one record collapse to one record + one status (rule 8)."""
    links = [_link("thermo", "thm_aaa", 7)]
    findings = [
        _finding(
            MachineReviewSeverity.info,
            record_type="thermo",
            record_ref="thm_aaa",
            message="info note",
        ),
        _finding(
            MachineReviewSeverity.warning,
            record_type="thermo",
            record_ref="thm_aaa",
            message="warning note",
        ),
        _finding(
            MachineReviewSeverity.critical,
            record_type="thermo",
            record_ref="thm_aaa",
            message="critical note",
        ),
    ]

    mapping = map_findings_to_submission_records(
        findings=findings, submission_record_links=links
    )

    assert set(mapping.mapped_by_record) == {("thermo", "thm_aaa")}
    record = mapping.mapped_by_record[("thermo", "thm_aaa")]
    assert len(record.findings) == 3
    # Worst severity (critical) drives the single derived status (rule 10).
    assert record.derived_status is MachineReviewStatus.machine_screened_needs_attention


def test_warning_only_record_maps_to_machine_screened_warning():
    """A record whose worst finding is a warning derives machine_screened_warning."""
    links = [_link("kinetics", "kin_aaa", 9)]
    findings = [
        _finding(
            MachineReviewSeverity.info,
            record_type="kinetics",
            record_ref="kin_aaa",
        ),
        _finding(
            MachineReviewSeverity.warning,
            record_type="kinetics",
            record_ref="kin_aaa",
        ),
    ]

    mapping = map_findings_to_submission_records(
        findings=findings, submission_record_links=links
    )

    record = mapping.mapped_by_record[("kinetics", "kin_aaa")]
    assert record.derived_status is MachineReviewStatus.machine_screened_warning


def test_info_only_record_maps_to_machine_screened_pass():
    """A record with only info-level findings derives machine_screened_pass."""
    links = [_link("statmech", "stm_aaa", 11)]
    finding = _finding(
        MachineReviewSeverity.info,
        record_type="statmech",
        record_ref="stm_aaa",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    record = mapping.mapped_by_record[("statmech", "stm_aaa")]
    assert record.derived_status is MachineReviewStatus.machine_screened_pass


# --------------------------------------------------------------------------- #
# Scope isolation across records and between submission/record scopes
# --------------------------------------------------------------------------- #


def test_mixed_submission_and_record_findings_keep_scopes_separate():
    """Submission-scoped and record-scoped findings coexist without leaking (rule 9)."""
    links = [_link("calculation", "calc_aaa", 1)]
    submission_finding = _finding(MachineReviewSeverity.critical)  # no record_type
    record_finding = _finding(
        MachineReviewSeverity.warning,
        record_type="calculation",
        record_ref="calc_aaa",
    )

    mapping = map_findings_to_submission_records(
        findings=[submission_finding, record_finding],
        submission_record_links=links,
    )

    # The record only sees its own (warning) finding; the submission-scoped
    # critical never leaks into the record's status.
    record = mapping.mapped_by_record[("calculation", "calc_aaa")]
    assert record.findings == (record_finding,)
    assert record.derived_status is MachineReviewStatus.machine_screened_warning
    # The submission-scoped finding stays unmapped.
    assert len(mapping.unmapped_findings) == 1
    assert mapping.unmapped_findings[0].finding is submission_finding
    assert mapping.unmapped_findings[0].reason is UnmappedReason.submission_scoped


def test_critical_finding_on_one_record_does_not_affect_other_records():
    """A critical on record A leaves record B's derived status untouched (rule 10)."""
    links = [
        _link("calculation", "calc_aaa", 1),
        _link("calculation", "calc_bbb", 2),
    ]
    findings = [
        _finding(
            MachineReviewSeverity.critical,
            record_type="calculation",
            record_ref="calc_aaa",
        ),
        _finding(
            MachineReviewSeverity.info,
            record_type="calculation",
            record_ref="calc_bbb",
        ),
    ]

    mapping = map_findings_to_submission_records(
        findings=findings, submission_record_links=links
    )

    record_a = mapping.mapped_by_record[("calculation", "calc_aaa")]
    record_b = mapping.mapped_by_record[("calculation", "calc_bbb")]
    assert record_a.derived_status is MachineReviewStatus.machine_screened_needs_attention
    # Record B is unaffected by record A's critical finding.
    assert record_b.derived_status is MachineReviewStatus.machine_screened_pass
    assert record_b.findings[0].severity is MachineReviewSeverity.info


# --------------------------------------------------------------------------- #
# Reviewer-outcome reconciliation (the one event-level signal that dominates)
# --------------------------------------------------------------------------- #


def test_failed_outcome_overrides_record_finding_severity():
    """A failed reviewer outcome dominates per-record status (provider failure).

    A reviewer failure describes the *pass*, not the record, so every record the
    pass mapped becomes machine_review_failed regardless of finding severity —
    this is event-level, not a concern fanned out across records.
    """
    links = [_link("kinetics", "kin_aaa", 9)]
    finding = _finding(
        MachineReviewSeverity.info,  # would otherwise derive a pass
        record_type="kinetics",
        record_ref="kin_aaa",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding],
        submission_record_links=links,
        outcome=MachineReviewOutcome.failed,
    )

    record = mapping.mapped_by_record[("kinetics", "kin_aaa")]
    assert record.derived_status is MachineReviewStatus.machine_review_failed


def test_not_performed_outcome_yields_not_run_status():
    """A not_performed reviewer outcome yields not_run per mapped record."""
    links = [_link("kinetics", "kin_aaa", 9)]
    finding = _finding(
        MachineReviewSeverity.warning,
        record_type="kinetics",
        record_ref="kin_aaa",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding],
        submission_record_links=links,
        outcome=MachineReviewOutcome.not_performed,
    )

    record = mapping.mapped_by_record[("kinetics", "kin_aaa")]
    assert record.derived_status is MachineReviewStatus.not_run


def test_completed_outcome_is_the_default_and_derives_from_findings():
    """The default outcome derives status from each record's own findings.

    Confirms the failed/not_performed tests above differ only by ``outcome``;
    omitting it is equivalent to ``completed`` and preserves the per-record,
    finding-driven derivation (no event-level concern leakage).
    """
    links = [_link("kinetics", "kin_aaa", 9)]
    finding = _finding(
        MachineReviewSeverity.warning,
        record_type="kinetics",
        record_ref="kin_aaa",
    )

    mapping = map_findings_to_submission_records(
        findings=[finding], submission_record_links=links
    )

    record = mapping.mapped_by_record[("kinetics", "kin_aaa")]
    assert record.derived_status is MachineReviewStatus.machine_screened_warning
