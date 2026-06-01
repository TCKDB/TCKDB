"""Private admin-only fake machine-review trigger (resolution + recipe + run).

This module is the *service* half of the admin-only "explicitly run fake machine
review for one record" endpoint (policy
``backend/docs/specs/record_machine_review_policy.md`` §5.3 — *admin / manual
re-review triggers*). For a single record it ties three already-tested pieces
together::

    record_type + record_id
      -> resolve the live deterministic TrustFragment   (trust evaluator, read-only)
      -> the active reviewer recipe                       (private constants here)
      -> run_record_machine_review_with_producer(...)     (orchestration, fake producer)
      -> appended row (run_not_reviewed / run_stale) OR skipped (current)

It is allowed to append exactly one ``record_machine_review`` row, and **only**
through the orchestration/executor write path. It mutates nothing else — not
``review_status`` / ``benchmark_reference`` / ``is_certified``, deterministic
evidence/trust, scientific records, ``submission.status`` / approval / rejection
fields, nor the public ``TrustFragment`` (which it only reads).

This is a maintainer/debug surface, not public scientific trust exposure. It
calls **no** real provider (only :class:`FakeMachineReviewProducer`), no RAG, no
background worker; nothing here is wired into uploads or any public read, and no
``trust.machine_review`` is emitted.

Active recipe
-------------

The active prompt version is a private constant; the active rubric versions are
**derived from the existing trust rubric constants** (so a rubric bump changes
the currency key without a second source of truth) rather than hand-maintained.
Only the record types that already have both a computed trust evaluator and a
persisted-machine-review home are supported; any other ``record_type`` is a
:class:`DomainError` (400), and a supported type with no live row is a
:class:`NotFoundError` (404).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.api.errors import DomainError, NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.transport import Transport
from app.services.machine_review.orchestration import (
    MachineReviewOrchestrationResult,
    run_record_machine_review_with_producer,
)
from app.services.machine_review.producer import (
    FakeMachineReviewProducer,
    MachineReviewProducer,
)
from app.services.machine_review.recipe import (
    ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
    ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS,
    public_rubric_name,
)
from app.services.record_review import get_record_review
from app.services.trust.evaluator import (
    evaluate_computed_calculation,
    evaluate_computed_kinetics,
    evaluate_computed_statmech,
    evaluate_computed_thermo,
    evaluate_computed_transition_state_entry,
    evaluate_computed_transport,
)
from app.services.trust.fragment import build_trust_fragment
from app.services.trust.models import (
    EvidenceEvaluation,
    EvidenceRubric,
    TrustFragment,
)
from app.services.trust.rubrics import (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    COMPUTED_STATMECH_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_TRANSITION_STATE_V1,
    COMPUTED_TRANSPORT_V1,
)


@dataclass(frozen=True)
class _RecordTypeResolver:
    """Everything the trigger needs to resolve one supported record type.

    ``model`` is used only for the existence check (404); ``evaluate`` is the
    existing computed trust evaluator that owns rubric logic (never duplicated
    here); ``submission_record_type`` keys the human-review lookup that feeds
    the read-only ``review_status`` context input; ``rubric`` is the code-defined
    rubric whose versioned public name forms this record type's currency key.
    """

    model: type
    evaluate: Callable[[Session, int], EvidenceEvaluation]
    submission_record_type: SubmissionRecordType
    rubric: EvidenceRubric


# The supported record types: exactly those that have BOTH a computed trust
# evaluator and a persisted-machine-review home (a ``SubmissionRecordType``).
_RESOLVERS: dict[str, _RecordTypeResolver] = {
    "calculation": _RecordTypeResolver(
        Calculation,
        evaluate_computed_calculation,
        SubmissionRecordType.calculation,
        COMPUTED_CALCULATION_V1,
    ),
    "kinetics": _RecordTypeResolver(
        Kinetics,
        evaluate_computed_kinetics,
        SubmissionRecordType.kinetics,
        COMPUTED_KINETICS_V1,
    ),
    "thermo": _RecordTypeResolver(
        Thermo,
        evaluate_computed_thermo,
        SubmissionRecordType.thermo,
        COMPUTED_THERMO_V1,
    ),
    "statmech": _RecordTypeResolver(
        Statmech,
        evaluate_computed_statmech,
        SubmissionRecordType.statmech,
        COMPUTED_STATMECH_V1,
    ),
    "transport": _RecordTypeResolver(
        Transport,
        evaluate_computed_transport,
        SubmissionRecordType.transport,
        COMPUTED_TRANSPORT_V1,
    ),
    "transition_state_entry": _RecordTypeResolver(
        TransitionStateEntry,
        evaluate_computed_transition_state_entry,
        SubmissionRecordType.transition_state_entry,
        COMPUTED_TRANSITION_STATE_V1,
    ),
}

#: The record types this admin trigger can run (stable, sorted for messages).
SUPPORTED_RECORD_TYPES: tuple[str, ...] = tuple(_RESOLVERS)


def _require_resolver(record_type: str) -> _RecordTypeResolver:
    """Return the resolver for ``record_type`` or raise :class:`DomainError` (400)."""
    resolver = _RESOLVERS.get(record_type)
    if resolver is None:
        raise DomainError(
            f"Unsupported machine-review record_type: {record_type!r}. "
            f"Supported: {', '.join(SUPPORTED_RECORD_TYPES)}."
        )
    return resolver


def active_rubric_versions_for_record_type(record_type: str) -> dict[str, str]:
    """Return the active rubric-version recipe for one record type.

    Only the rubric relevant to ``record_type`` is included, so bumping (say)
    the thermo rubric never restales calculation reviews. Raises
    :class:`DomainError` for an unsupported type.
    """
    resolver = _require_resolver(record_type)
    name = public_rubric_name(resolver.rubric)
    return {name: ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS[name]}


def resolve_record_trust_fragment(
    session: Session,
    *,
    record_type: str,
    record_id: int,
) -> TrustFragment:
    """Build the live deterministic :class:`TrustFragment` for one record.

    Validates ``record_type`` (:class:`DomainError`/400 if unsupported), checks
    the record exists (:class:`NotFoundError`/404 if missing — the evaluators
    otherwise return an *empty* evaluation for a missing row), then evaluates the
    existing computed rubric and folds in the record's current human
    ``review_status`` as a read-only context input. This is the same
    ``TrustFragment`` shape the context adapter consumes; no rubric logic is
    duplicated. Strictly read-only — nothing is mutated.
    """
    resolver = _require_resolver(record_type)
    if session.get(resolver.model, record_id) is None:
        raise NotFoundError(f"{record_type} record not found.")

    evaluation = resolver.evaluate(session, record_id)
    review = get_record_review(
        session,
        record_type=resolver.submission_record_type,
        record_id=record_id,
    )
    review_status = review.status if review is not None else None
    return build_trust_fragment(evaluation, review_status=review_status)


def run_admin_fake_machine_review(
    session: Session,
    *,
    record_type: str,
    record_id: int,
    reviewed_at: datetime,
    producer: MachineReviewProducer | None = None,
) -> MachineReviewOrchestrationResult:
    """Run the private fake machine-review loop for one record (admin trigger).

    Resolves the live trust fragment (validating the type and the record's
    existence), looks up the active recipe, and delegates to
    :func:`run_record_machine_review_with_producer` with a
    :class:`FakeMachineReviewProducer` (a benign ``machine_screened_pass`` by
    default). A row is appended only for ``run_not_reviewed`` / ``run_stale``;
    an already-current record is skipped. ``producer`` is injectable for tests,
    but is always a fake — no real provider is ever called. Appends only through
    the executor; mutates nothing else.
    """
    fragment = resolve_record_trust_fragment(
        session, record_type=record_type, record_id=record_id
    )
    return run_record_machine_review_with_producer(
        session,
        record_type=record_type,
        record_id=record_id,
        record_ref=str(record_id),
        trust_fragment=fragment,
        active_prompt_version=ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
        active_rubric_versions=active_rubric_versions_for_record_type(record_type),
        producer=producer if producer is not None else FakeMachineReviewProducer(),
        reviewed_at=reviewed_at,
    )
