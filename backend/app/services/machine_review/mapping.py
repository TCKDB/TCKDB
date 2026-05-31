"""Pure submission -> record mapping policy for machine-review findings.

This module encodes the **safety policy** from
``backend/docs/specs/provisional_machine_review.md`` §6/§13 that gates the
future record-level ``machine_review`` layer: a submission-scoped advisory
result must **never** automatically become a record-level result, and a
record-addressed finding must map only to the *exact* linked record it
names — never to every record that happens to share the submission.

The function is deliberately **pure**: it takes the already-validated
:class:`MachineReviewFinding` set plus a lightweight description of the
submission's record links, and returns a structured mapping. It performs no
database access, no persistence, and no public exposure — this slice only
proves that a safe mapping policy exists (spec §6 Option C "Future").

Addressing model
----------------

Findings are addressed by the public ``(record_type, record_ref)`` pair
that :class:`MachineReviewFinding` already carries — raw internal
``record_id`` is governed by the internal-id policy and is deliberately not
the matching key here. The internal-only variant (matching on
``(record_type, record_id)`` once that addressing is allowed) is the same
algorithm with the key field swapped; the structures carry ``record_id``
through as passthrough metadata so a future persistence layer does not have
to re-resolve it.

Matching precedence (most specific first; the full policy is documented in
``provisional_machine_review.md`` "Submission-to-record mapping policy"):

1. exact ``(record_type, record_ref)`` of a linked record (in the audit path
   ``record_ref`` is the stringified internal ``record_id``, so the spec's
   "exact ``record_type`` + ``record_id``" and "exact ``record_type`` +
   ``record_ref``" levels collapse here);
2. the **single unambiguous** linked record of the finding's ``record_type``,
   used only when the finding carries no ``record_ref``;
3. otherwise unmapped, with a ``mapping_warnings`` entry for the defect cases.

``evidence_keys`` are deterministic-evidence citations, never a matching key:
the mapper does not infer a record from them (that would be guessing).

Policy (spec §6 / §13, and the slice's required decisions):

1. A finding with no ``record_type`` is submission-scoped only -> unmapped.
2. A finding maps only to a record it unambiguously identifies (precedence
   above); it never maps to a record merely because it shares the submission.
3. Unknown ``record_type`` -> unmapped (warning); never raises.
4. A ``record_ref`` that names a record not linked to the submission ->
   unmapped (warning); the mapper never silently redirects it to a different
   record (no guessing).
5. A typed finding with no ``record_ref`` maps to the one linked record of that
   type when exactly one exists; with several it is ambiguous -> unmapped
   (warning); with none it is unmapped (warning).
6. Multiple findings may map to the same record.
7. One submission may produce mapped and unmapped findings simultaneously.
8. A mapped record's *concern* status is derived **only** from its own findings
   — never a submission-scoped finding, never a sibling record's. The single
   event-level signal that dominates is the reviewer ``outcome`` (failed /
   not_performed): it describes whether the reviewer ran, not the record's
   quality, so it applies to every record the pass touched without being
   fan-out of a concern severity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from app.db.models.common import SubmissionRecordType
from app.services.machine_review.derivation import (
    MachineReviewOutcome,
    derive_machine_review_status,
)
from app.services.machine_review.schemas import (
    MachineReviewFinding,
    MachineReviewStatus,
)

# The controlled record-type vocabulary a finding is allowed to address.
# A finding citing anything outside this set is treated as unknown (rule 5).
_KNOWN_RECORD_TYPES: frozenset[str] = frozenset(t.value for t in SubmissionRecordType)


@runtime_checkable
class SubmissionRecordLinkLike(Protocol):
    """Structural type for one submission -> record link.

    The ORM ``SubmissionRecordLink`` addresses records by ``record_id``; the
    public mapping path needs the resolved ``record_ref``. Callers therefore
    resolve refs *before* invoking the mapper (keeping this function pure and
    DB-free) and pass objects exposing both. ``record_id`` is optional
    passthrough so the internal-id variant and the future persistence layer
    do not have to re-resolve it.
    """

    @property
    def record_type(self) -> str: ...

    @property
    def record_ref(self) -> str | None: ...

    @property
    def record_id(self) -> int | None: ...


@dataclass(frozen=True)
class SubmissionRecordLinkRef:
    """Concrete, lightweight :class:`SubmissionRecordLinkLike` for callers/tests.

    A submission's linked record, addressed by its public
    ``(record_type, record_ref)`` with the internal ``record_id`` carried
    through as passthrough metadata.
    """

    record_type: str
    record_ref: str | None = None
    record_id: int | None = None


class UnmappedReason(str, Enum):
    """Why a finding was *not* mapped to a record.

    Every value is a safety outcome, not an error: the mapper never raises on
    a malformed/over-broad finding, it routes it here so the caller can decide
    what to surface to an admin (spec: advisory, non-blocking).
    """

    submission_scoped = "submission_scoped"
    """No ``record_type`` -> the finding is about the submission as a whole
    (rule 1). This is expected, not a defect; it carries no warning."""

    missing_record_ref = "missing_record_ref"
    """``record_type`` present, no ``record_ref``, and **no** linked record of
    that type to fall back to -> nothing to map. Warns. (When exactly one linked
    record of the type exists the finding maps instead; when several exist see
    :attr:`ambiguous_record_type`.)"""

    ambiguous_record_type = "ambiguous_record_type"
    """``record_type`` present, no ``record_ref``, and **multiple** linked
    records of that type -> the single-unambiguous-type fallback refuses to
    guess which one. Warns. This is the anti-fan-out guard for type-only
    findings."""

    unknown_record_type = "unknown_record_type"
    """``record_type`` is outside the controlled vocabulary. Warns."""

    unlinked_record = "unlinked_record"
    """A finding naming an exact ``record_ref`` that is not linked to this
    submission -> must not map, and is never redirected to a different record.
    Warns. This is the anti-fan-out guard for ref-addressed findings."""


@dataclass(frozen=True)
class UnmappedFinding:
    """A finding that did not map, with the policy reason it did not."""

    finding: MachineReviewFinding
    reason: UnmappedReason


@dataclass(frozen=True)
class MappedRecord:
    """All findings mapped to one linked record, plus its derived status.

    ``derived_status`` is computed by the shared
    :func:`derive_machine_review_status` over **only** this record's findings
    (rule 10) — never submission-scoped findings, never a sibling record's.
    """

    record_type: str
    record_ref: str
    record_id: Optional[int]
    findings: tuple[MachineReviewFinding, ...]
    derived_status: MachineReviewStatus


@dataclass(frozen=True)
class MachineReviewRecordMapping:
    """Result of mapping a submission's findings onto its linked records."""

    mapped_by_record: dict[tuple[str, str], MappedRecord] = field(default_factory=dict)
    unmapped_findings: tuple[UnmappedFinding, ...] = ()
    mapping_warnings: tuple[str, ...] = ()


def _classify_finding(
    finding: MachineReviewFinding,
    linked_by_ref: dict[tuple[str, str], SubmissionRecordLinkLike],
    linked_keys_by_type: dict[str, list[tuple[str, str]]],
) -> tuple[str, str] | UnmappedReason:
    """Decide a single finding's bucket.

    Returns the ``(record_type, record_ref)`` key it maps to, or an
    :class:`UnmappedReason`. Precedence is ordered most-fundamental first so
    the warning a finding earns names its *root* defect: a finding that is
    both an unknown type *and* unlinked is reported as the unknown type.
    """
    # Rule 1: no record_type -> the finding is about the submission as a whole.
    if finding.record_type is None:
        return UnmappedReason.submission_scoped

    # Unknown record_type: a type outside the controlled vocabulary cannot
    # address any real record. Reported first: the type is the more fundamental
    # problem than any ref/fallback consideration.
    if finding.record_type not in _KNOWN_RECORD_TYPES:
        return UnmappedReason.unknown_record_type

    # Precedence 1 — exact ref match. A ref-addressed finding maps only to the
    # exact linked record it names; a record not in this submission's link set
    # never matches and is never redirected (anti-fan-out core).
    if finding.record_ref is not None:
        key = (finding.record_type, finding.record_ref)
        if key not in linked_by_ref:
            return UnmappedReason.unlinked_record
        return key

    # Precedence 2 — single-unambiguous-type fallback. A typed finding with no
    # ref maps to the one linked record of that type iff exactly one exists;
    # otherwise the mapper refuses to guess.
    candidates = linked_keys_by_type.get(finding.record_type, [])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        return UnmappedReason.ambiguous_record_type
    return UnmappedReason.missing_record_ref


def map_findings_to_submission_records(
    *,
    findings: Sequence[MachineReviewFinding],
    submission_record_links: Sequence[SubmissionRecordLinkLike],
    outcome: MachineReviewOutcome = MachineReviewOutcome.completed,
) -> MachineReviewRecordMapping:
    """Map record-addressed machine-review findings to linked submission records.

    Pure and DB-free. A finding maps to a record by the precedence in the
    module docstring: exact ``(record_type, record_ref)`` of a linked record,
    else the single unambiguous linked record of the finding's type (only when
    the finding has no ``record_ref``), else unmapped. Everything unmapped is
    routed to ``unmapped_findings`` with a reason (and a human-readable
    ``mapping_warnings`` entry for the defect cases). A submission-scoped
    finding (no ``record_type``) is never promoted to any record.

    ``outcome`` is the reviewer-completion signal for the pass these findings
    came from (spec §3 "State vs. severity"). It is the *only* event-level input
    allowed to influence per-record status, and only the non-``completed``
    values do: ``failed`` -> every mapped record is ``machine_review_failed``,
    ``not_performed`` -> ``not_run``. For ``completed`` (the default) each
    record's status is derived from **only its own findings**, so a
    submission-level concern can never fan out into a record. This keeps the
    anti-fan-out invariant: a *concern* severity never crosses records; a
    *reviewer outcome* legitimately applies to every record the pass touched.

    The returned ``mapped_by_record`` is keyed by ``(record_type, record_ref)``.
    """
    # Index links by their public key. Links missing a resolved ref cannot be
    # matched by ref-addressed findings; a same-key link seen twice (e.g. two
    # roles) collapses to one record — role is irrelevant to record identity.
    linked_by_ref: dict[tuple[str, str], SubmissionRecordLinkLike] = {}
    # Index the distinct record keys per type, for the single-unambiguous-type
    # fallback. Insertion order is preserved and duplicates collapse, so a type
    # with one distinct linked record has exactly one candidate key.
    linked_keys_by_type: dict[str, list[tuple[str, str]]] = {}
    for link in submission_record_links:
        if link.record_ref is None:
            continue
        key = (link.record_type, link.record_ref)
        if key in linked_by_ref:
            continue
        linked_by_ref[key] = link
        linked_keys_by_type.setdefault(link.record_type, []).append(key)

    grouped: dict[tuple[str, str], list[MachineReviewFinding]] = {}
    unmapped: list[UnmappedFinding] = []
    warnings: list[str] = []

    for finding in findings:
        classification = _classify_finding(finding, linked_by_ref, linked_keys_by_type)

        if isinstance(classification, UnmappedReason):
            unmapped.append(UnmappedFinding(finding=finding, reason=classification))
            if classification is UnmappedReason.unknown_record_type:
                warnings.append(
                    f"Finding cites unknown record_type "
                    f"{finding.record_type!r}; not mapped."
                )
            elif classification is UnmappedReason.missing_record_ref:
                warnings.append(
                    f"Finding for record_type {finding.record_type!r} has no "
                    f"record_ref and no linked record of that type; not mapped."
                )
            elif classification is UnmappedReason.ambiguous_record_type:
                warnings.append(
                    f"Finding for record_type {finding.record_type!r} has no "
                    f"record_ref and multiple linked records share that type; "
                    f"not mapped (refusing to guess)."
                )
            elif classification is UnmappedReason.unlinked_record:
                warnings.append(
                    f"Finding addresses {finding.record_type!r}/"
                    f"{finding.record_ref!r}, which is not linked to this "
                    f"submission; not mapped."
                )
            # submission_scoped is expected, not a defect -> no warning.
            continue

        grouped.setdefault(classification, []).append(finding)

    mapped_by_record: dict[tuple[str, str], MappedRecord] = {}
    for key, record_findings in grouped.items():
        record_type, record_ref = key
        link = linked_by_ref[key]
        findings_tuple = tuple(record_findings)
        mapped_by_record[key] = MappedRecord(
            record_type=record_type,
            record_ref=record_ref,
            record_id=link.record_id,
            findings=findings_tuple,
            # Status from only this record's findings (anti-fan-out), reconciled
            # with the reviewer ``outcome``: failed/not_performed dominate;
            # completed defers entirely to this record's finding severities.
            derived_status=derive_machine_review_status(findings_tuple, outcome),
        )

    return MachineReviewRecordMapping(
        mapped_by_record=mapped_by_record,
        unmapped_findings=tuple(unmapped),
        mapping_warnings=tuple(warnings),
    )
