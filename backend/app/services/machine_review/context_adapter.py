"""Pure adapter: deterministic trust output -> machine-review currency inputs.

This module closes the private machine-review currency loop without any
persistence or public exposure (policy
``backend/docs/specs/record_machine_review_policy.md``)::

    deterministic evidence / trust output
      -> MachineReviewEvidenceContext      (build_machine_review_evidence_context_from_trust)
      -> MachineReviewContextDigest         (existing build_machine_review_context_hash)
      -> StoredMachineReviewProjection      (stored_projection_from_record_machine_review)
      -> classify_machine_review_currency   (existing classifier)

It proves currency can be computed against **real deterministic evidence**
(the public :class:`~app.services.trust.models.TrustFragment`) before any
``record_machine_review`` table exists.

Strictly read-only / non-interfering. It reads the deterministic
trust/evidence fragment and returns new value objects; it mutates nothing —
not ``review_status``, ``trust_status``, ``is_certified``,
``evidence_completeness``, the check sets, ``hard_fail_reason``, scientific
records, nor ``submission.status``. ``review_status`` and ``is_certified`` flow
through only as **read-only context inputs**, never as machine-owned outputs:
the machine reviewer never produces or changes them (policy §6; the hash
context fields are documented as inputs in ``context_hash.py``).

No public exposure: nothing here is wired into a scientific read, no
``trust.machine_review`` is emitted, and the public ``TrustFragment`` is
untouched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.services.machine_review.context_hash import (
    GeometryValidationContext,
    MachineReviewContextDigest,
    MachineReviewEvidenceContext,
    SourceCalculationContext,
)
from app.services.machine_review.currency import StoredMachineReviewProjection
from app.services.machine_review.read_model import RecordMachineReview
from app.services.trust.models import TrustFragment


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """Coerce an evidence check list (JSON list / tuple / None) to a str tuple.

    The fragment's ``evidence`` dict is a JSON dump, so check sets arrive as
    lists; ``None`` or a missing key becomes an empty tuple. Order is preserved
    here — the hash builder canonicalises (sorts) set-like inputs, so order does
    not affect the digest.
    """
    if not value:
        return ()
    return tuple(str(item) for item in value)


def build_machine_review_evidence_context_from_trust(
    *,
    record_type: str,
    record_ref: str | None,
    trust_fragment: TrustFragment,
    source_calculations: Sequence[SourceCalculationContext] = (),
    geometry_validations: Sequence[GeometryValidationContext] = (),
    artifact_kinds: Sequence[str] = (),
) -> MachineReviewEvidenceContext:
    """Build the machine-review hash context from deterministic trust output.

    Reads the deterministic evidence the public
    :class:`~app.services.trust.models.TrustFragment` carries and projects it,
    **value-for-value**, onto the included-inputs contract
    :class:`MachineReviewEvidenceContext` (policy §3.2). It preserves the
    evidence exactly — the rubric name + version, the four check sets,
    ``hard_fail_reason`` — and folds in ``review_status`` and ``is_certified``
    as read-only context inputs (never machine-owned outputs). Optional
    ``source_calculations`` / ``geometry_validations`` / ``artifact_kinds`` the
    reviewer also saw are passed through.

    ``record_ref`` is the stable record key. When ``None`` it falls back to the
    evidence dict's ``record_id`` (stringified); if neither resolves a
    non-empty key, :class:`ValueError` is raised (the context requires a ref).

    The returned context is the input to the existing
    :func:`~app.services.machine_review.context_hash.build_machine_review_context_hash`;
    this function does not hash it (keeping the steps composable and each
    independently testable). It performs no mutation and no persistence.
    """
    evidence = trust_fragment.evidence

    resolved_ref = record_ref
    if resolved_ref is None:
        record_id = evidence.get("record_id")
        resolved_ref = str(record_id) if record_id is not None else None
    if not resolved_ref:
        raise ValueError(
            "record_ref could not be resolved: no record_ref supplied and the "
            "trust fragment carries no record_id."
        )

    return MachineReviewEvidenceContext(
        record_type=record_type,
        record_ref=resolved_ref,
        # Public rubric name (already carries the _vN suffix) + integer version,
        # both preserved so a rubric version bump changes the digest (policy §3.2).
        rubric_name=evidence.get("rubric"),
        rubric_version=evidence.get("rubric_version"),
        passed_checks=_as_str_tuple(evidence.get("passed_checks")),
        missing_checks=_as_str_tuple(evidence.get("missing_checks")),
        warning_checks=_as_str_tuple(evidence.get("warning_checks")),
        not_applicable_checks=_as_str_tuple(evidence.get("not_applicable_checks")),
        hard_fail_reason=evidence.get("hard_fail_reason"),
        # Read-only human-review context inputs (policy §6) — observed, not owned.
        review_status=trust_fragment.review_status,
        is_certified=trust_fragment.is_certified,
        source_calculations=tuple(source_calculations),
        geometry_validations=tuple(geometry_validations),
        artifact_kinds=tuple(artifact_kinds),
    )


def stored_projection_from_record_machine_review(
    review: RecordMachineReview,
    *,
    context_digest: MachineReviewContextDigest,
    prompt_version: str,
    rubric_versions: Mapping[str, str],
    id: int | None = None,
    source_audit_event_id: int | None = None,
) -> StoredMachineReviewProjection:
    """Convert an in-memory record review into the minimal stored-review projection.

    Bridges the in-memory :class:`RecordMachineReview` (a single review pass) to
    the currency :class:`StoredMachineReviewProjection` the classifier consumes,
    stamping the currency dimensions: the ``context_digest``'s ``context_hash`` /
    ``context_schema_version`` (the evidence currency) plus the supplied
    ``prompt_version`` / ``rubric_versions`` (the reviewer-recipe currency). The
    review's ``status`` is carried through as passthrough provenance (the
    classifier does not use it).

    Naming bridge: the read model's ``audit_event_id`` is the projection's
    ``source_audit_event_id``. An explicit ``source_audit_event_id`` argument
    wins; otherwise the review's ``audit_event_id`` is used. ``record_id`` is the
    review's internal id when present, else its ``record_ref`` (the projection's
    ``record_id`` is ``int | str``).

    ``reviewed_at`` is required by the currency ordering, so a review without one
    raises :class:`ValueError`. Pure: no persistence, no mutation.
    """
    if review.reviewed_at is None:
        raise ValueError(
            "RecordMachineReview.reviewed_at is required to build a currency "
            "projection (it is the primary latest-selection key)."
        )

    resolved_record_id: int | str = (
        review.record_id if review.record_id is not None else review.record_ref
    )
    resolved_source_event = (
        source_audit_event_id
        if source_audit_event_id is not None
        else review.audit_event_id
    )

    return StoredMachineReviewProjection(
        record_type=review.record_type,
        record_id=resolved_record_id,
        reviewed_at=review.reviewed_at,
        context_schema_version=context_digest.context_schema_version,
        context_hash=context_digest.context_hash,
        prompt_version=prompt_version,
        rubric_versions=dict(rubric_versions),
        status=review.status,
        id=id,
        source_audit_event_id=resolved_source_event,
    )
