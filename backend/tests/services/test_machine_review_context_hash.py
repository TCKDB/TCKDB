"""Unit tests for the pure machine-review ``context_hash`` builder.

These prove the policy in
``backend/docs/specs/record_machine_review_policy.md`` §3: the digest is stable
and deterministic, order-insensitive for set-like inputs, sensitive to every
material evidence change, and structurally incapable of including the excluded
provenance/volatile inputs (they are rejected at the typed-context boundary, not
silently ignored).

The builder under test is pure: no DB, no provider, no persistence. Tests build
:class:`MachineReviewEvidenceContext` objects directly and compare digests.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.machine_review import (
    MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION,
    GeometryValidationContext,
    MachineReviewContextDigest,
    MachineReviewEvidenceContext,
    SourceCalculationContext,
    build_machine_review_context_hash,
)


def _context(**overrides) -> MachineReviewEvidenceContext:
    """Build a representative, fully-populated evidence context.

    Defaults exercise every input family (checks, source calcs, geometry
    validations, artifact kinds); individual tests override one field to assert
    its effect on the digest.
    """
    base = dict(
        record_type="kinetics",
        record_ref="kin_aaa",
        rubric_name="computed_kinetics_v1",
        rubric_version=1,
        passed_checks=("a_present", "ea_present"),
        missing_checks=("uncertainty_present",),
        warning_checks=("thin_provenance",),
        not_applicable_checks=("irc_evidence_present",),
        hard_fail_reason=None,
        source_calculations=(
            SourceCalculationContext(ref="calc_sp", role="sp"),
            SourceCalculationContext(ref="calc_opt", role="opt"),
        ),
        geometry_validations=(
            GeometryValidationContext(ref="geo_1", status="valid"),
        ),
        artifact_kinds=("input", "output"),
    )
    base.update(overrides)
    return MachineReviewEvidenceContext(**base)


def _hash(ctx: MachineReviewEvidenceContext, **kwargs) -> str:
    return build_machine_review_context_hash(ctx, **kwargs).context_hash


# --------------------------------------------------------------------------- #
# Stability / determinism
# --------------------------------------------------------------------------- #


def test_same_context_produces_same_hash():
    """Two equal contexts produce byte-identical digests (determinism)."""
    assert _hash(_context()) == _hash(_context())


def test_digest_returns_context_schema_version():
    """The digest carries the schema version it was produced under (policy §3.4)."""
    digest = build_machine_review_context_hash(_context())
    assert isinstance(digest, MachineReviewContextDigest)
    assert digest.context_schema_version == MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION
    assert len(digest.context_hash) == 64  # SHA-256 hex


# --------------------------------------------------------------------------- #
# Order-insensitivity for set-like inputs
# --------------------------------------------------------------------------- #


def test_check_list_order_does_not_affect_hash():
    """Reordering check lists must not change the digest (set-like inputs)."""
    a = _context(passed_checks=("a_present", "ea_present"))
    b = _context(passed_checks=("ea_present", "a_present"))
    assert _hash(a) == _hash(b)


def test_source_calculation_order_does_not_affect_hash():
    """Reordering source calculations must not change the digest."""
    a = _context(
        source_calculations=(
            SourceCalculationContext(ref="calc_sp", role="sp"),
            SourceCalculationContext(ref="calc_opt", role="opt"),
        )
    )
    b = _context(
        source_calculations=(
            SourceCalculationContext(ref="calc_opt", role="opt"),
            SourceCalculationContext(ref="calc_sp", role="sp"),
        )
    )
    assert _hash(a) == _hash(b)


def test_artifact_kind_order_does_not_affect_hash():
    """Reordering artifact kinds must not change the digest."""
    a = _context(artifact_kinds=("input", "output"))
    b = _context(artifact_kinds=("output", "input"))
    assert _hash(a) == _hash(b)


def test_geometry_validation_order_does_not_affect_hash():
    """Reordering geometry validations must not change the digest."""
    a = _context(
        geometry_validations=(
            GeometryValidationContext(ref="geo_1", status="valid"),
            GeometryValidationContext(ref="geo_2", status="warning"),
        )
    )
    b = _context(
        geometry_validations=(
            GeometryValidationContext(ref="geo_2", status="warning"),
            GeometryValidationContext(ref="geo_1", status="valid"),
        )
    )
    assert _hash(a) == _hash(b)


def test_none_roles_and_statuses_sort_without_error():
    """``None`` roles/statuses are sortable and stable (no TypeError)."""
    a = _context(
        source_calculations=(
            SourceCalculationContext(ref="calc_b", role=None),
            SourceCalculationContext(ref="calc_a", role="opt"),
        ),
        geometry_validations=(
            GeometryValidationContext(ref="geo_2", status=None),
            GeometryValidationContext(ref="geo_1", status="valid"),
        ),
    )
    b = _context(
        source_calculations=(
            SourceCalculationContext(ref="calc_a", role="opt"),
            SourceCalculationContext(ref="calc_b", role=None),
        ),
        geometry_validations=(
            GeometryValidationContext(ref="geo_1", status="valid"),
            GeometryValidationContext(ref="geo_2", status=None),
        ),
    )
    assert _hash(a) == _hash(b)
    # A None role is not conflated with an empty-string role.
    none_role = _context(
        source_calculations=(SourceCalculationContext(ref="calc_a", role=None),)
    )
    empty_role = _context(
        source_calculations=(SourceCalculationContext(ref="calc_a", role=""),)
    )
    assert _hash(none_role) != _hash(empty_role)


# --------------------------------------------------------------------------- #
# Sensitivity: every material evidence change changes the hash
# --------------------------------------------------------------------------- #


def test_changing_passed_checks_changes_hash():
    """Adding a passed check changes the digest."""
    assert _hash(_context()) != _hash(
        _context(passed_checks=("a_present", "ea_present", "geometry_present"))
    )


def test_changing_missing_checks_changes_hash():
    """Changing the missing-check set changes the digest."""
    assert _hash(_context()) != _hash(_context(missing_checks=()))


def test_changing_warning_checks_changes_hash():
    """Changing the warning-check set changes the digest."""
    assert _hash(_context()) != _hash(
        _context(warning_checks=("thin_provenance", "implausible_value"))
    )


def test_changing_not_applicable_checks_changes_hash():
    """Changing the not-applicable-check set changes the digest."""
    assert _hash(_context()) != _hash(_context(not_applicable_checks=()))


def test_changing_hard_fail_reason_changes_hash():
    """Setting/clearing hard_fail_reason changes the digest."""
    assert _hash(_context()) != _hash(
        _context(hard_fail_reason="single_imaginary_frequency_missing")
    )


def test_adding_source_calculation_changes_hash():
    """Linking a new source calculation changes the digest (required trigger)."""
    extended = _context(
        source_calculations=(
            SourceCalculationContext(ref="calc_sp", role="sp"),
            SourceCalculationContext(ref="calc_opt", role="opt"),
            SourceCalculationContext(ref="calc_freq", role="freq"),
        )
    )
    assert _hash(_context()) != _hash(extended)


def test_changing_source_calculation_role_changes_hash():
    """The same ref under a different role is a different context."""
    a = _context(
        source_calculations=(SourceCalculationContext(ref="calc_sp", role="sp"),)
    )
    b = _context(
        source_calculations=(SourceCalculationContext(ref="calc_sp", role="opt"),)
    )
    assert _hash(a) != _hash(b)


def test_changing_geometry_validation_status_changes_hash():
    """Changing a geometry validation status changes the digest."""
    a = _context(
        geometry_validations=(
            GeometryValidationContext(ref="geo_1", status="valid"),
        )
    )
    b = _context(
        geometry_validations=(
            GeometryValidationContext(ref="geo_1", status="invalid"),
        )
    )
    assert _hash(a) != _hash(b)


def test_changing_artifact_kind_summary_changes_hash():
    """Adding an artifact kind changes the digest (required trigger)."""
    assert _hash(_context()) != _hash(
        _context(artifact_kinds=("input", "output", "checkpoint"))
    )


def test_changing_rubric_version_changes_hash():
    """A rubric version bump changes the digest even with identical checks."""
    assert _hash(_context(rubric_version=1)) != _hash(_context(rubric_version=2))


def test_record_identity_is_part_of_the_hash():
    """Two different records with identical evidence do not collide."""
    assert _hash(_context(record_ref="kin_aaa")) != _hash(
        _context(record_ref="kin_bbb")
    )
    assert _hash(_context(record_type="kinetics")) != _hash(
        _context(record_type="thermo")
    )


# --------------------------------------------------------------------------- #
# Optional-inclusion fields (review_status / notes)
# --------------------------------------------------------------------------- #


def test_review_status_inclusion_changes_hash_only_when_set():
    """review_status affects the hash only when explicitly included (policy §6)."""
    # Default (not included) is stable.
    assert _hash(_context()) == _hash(_context(review_status=None))
    # Including it is a distinct context.
    assert _hash(_context()) != _hash(_context(review_status="approved"))


def test_notes_inclusion_changes_hash_and_is_order_insensitive():
    """Selected notes affect the hash when present, order-insensitively."""
    assert _hash(_context()) != _hash(_context(notes=("mentions tunneling",)))
    a = _context(notes=("note one", "note two"))
    b = _context(notes=("note two", "note one"))
    assert _hash(a) == _hash(b)


# --------------------------------------------------------------------------- #
# context_schema_version is folded into the hash
# --------------------------------------------------------------------------- #


def test_different_context_schema_version_changes_hash_and_metadata():
    """A different schema version changes both the hash and the digest metadata.

    Folding the version into the payload means even a hash-only comparison is
    recipe-safe: a v1 digest never equals a v2 digest of the same inputs.
    """
    v1 = build_machine_review_context_hash(_context(), context_schema_version="v1")
    v2 = build_machine_review_context_hash(_context(), context_schema_version="v2")

    assert v1.context_hash != v2.context_hash
    assert v1.context_schema_version == "v1"
    assert v2.context_schema_version == "v2"


# --------------------------------------------------------------------------- #
# Excluded provenance/volatile inputs are REJECTED, not silently ignored
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "forbidden",
    [
        {"provider": "FakeLLMPrecheckProvider"},
        {"model": "fake_test/simple-v1"},
        {"reviewed_at": "2026-05-31T12:00:00"},
        {"created_at": "2026-05-31T12:00:00"},
        {"raw_log": "...gigabytes of Gaussian output..."},
        {"coordinates": "0.0 0.0 0.0"},
        {"api_key": "sk-secret"},
        {"request_id": "req-123"},
    ],
)
def test_forbidden_fields_are_rejected_at_construction(forbidden):
    """Provenance/volatile/raw inputs cannot enter the context (extra=forbid).

    The builder therefore cannot hash one — they are rejected, not ignored
    (policy §3.3). This is the documented behavior choice.
    """
    with pytest.raises(ValidationError):
        _context(**forbidden)


def test_minimal_context_only_requires_record_identity():
    """A context with only record identity is valid and hashes deterministically."""
    minimal = MachineReviewEvidenceContext(
        record_type="calculation", record_ref="calc_xyz"
    )
    assert _hash(minimal) == _hash(minimal)
    # Distinct from a populated context for the same record.
    assert _hash(minimal) != _hash(_context(record_type="calculation",
                                             record_ref="calc_xyz"))
