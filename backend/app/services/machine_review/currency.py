"""Pure current/stale/historical classifier for record-level machine review.

This module implements the currency model from
``backend/docs/specs/record_machine_review_policy.md`` §2/§3.5/§4: given the
machine-review passes recorded for one record, decide which (if any) is the
**current** one for the record's present deterministic evidence context, which
is merely the latest but **stale**, and which are **historical**.

It is **pure**: no database access, no persistence, no provider call, and no
public exposure. It reads only the projections and active-recipe inputs handed
to it and returns a frozen classification. It mutates nothing — not scientific
records, the deterministic evidence/trust layer, nor the human-review layer.
Staleness is **derived here, at read time** (policy §2): nothing stores a
mutable ``is_current`` flag, so an append-only history never needs back-update.

Currency vs. provenance (policy §3.5). A latest review is *current* only when
**all four** currency dimensions match the active recipe:

* ``context_schema_version`` — the hash recipe version (policy §3.4);
* ``context_hash`` — the digest of the record's current evidence (policy §3);
* ``prompt`` — the active machine-review prompt, a name and a version;
* ``rubric`` — the active rubric for this record, a name and a version.

Every review stored before the 2026-10 versioning reset is stale, and it says so by name. The reset restarted every
rubric and the prompt at version 1, and the migration (``c7d4a9e2b6f1``) stored each pre-reset stamp so that it cannot
equal an active one: a rubric's latest old version became 1, every older version became **0**, and every old prompt
became version **0** (the prompt's wording changed in the same release, and a deterministic runner's old stamp carried
no version at all). Version 0 is never current -- no registry holds it -- so every pre-reset review reports
``prompt_version_mismatch`` and/or ``rubric_versions_mismatch``, for a true reason (it was made under a prompt or rubric
that is not the active one), not because a hash happened to differ. Stored refs are read back as
:class:`~app.versioning.StoredVersionRef`, the one type that may hold a 0.

``provider``/``model`` are deliberately **not** currency dimensions (swapping
models does not invalidate the evidence a prior review saw, policy §3.5).

Latest-selection ordering exactly matches policy §4::

    reviewed_at DESC, source_audit_event_id DESC NULLS LAST, id DESC NULLS LAST

``source_audit_event_id`` is the persisted/projection name for the same source
event the in-memory read model calls ``audit_event_id``
(:class:`~app.services.machine_review.read_model.RecordMachineReview`); the
ordering behavior is identical (higher id = strictly later event, nulls last).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from tckdb_schemas.fragments.version import VersionRef

from app.services.machine_review.context_hash import MachineReviewContextDigest
from app.services.machine_review.schemas import MachineReviewStatus
from app.versioning import StoredVersionRef


class MachineReviewCurrencyState(str, Enum):
    """Currency classification for a record's machine review (policy §2).

    The *overall* classification state is derived from the **latest** review:
    ``not_run`` (none exist), ``current`` (latest matches the active recipe), or
    ``stale`` (latest exists but a currency dimension differs). ``historical``
    is the per-review label of every non-latest review — they are retained for
    audit and are never the active candidate, regardless of their own hash.
    """

    not_run = "not_run"
    current = "current"
    stale = "stale"
    historical = "historical"


class MachineReviewStaleReason(str, Enum):
    """Why the latest machine review is stale — one per mismatched dimension.

    Emitted only when the overall state is ``stale``; a ``current`` or
    ``not_run`` classification carries none. Multiple reasons may apply at once
    (e.g. both the evidence and the prompt changed); they are reported in a
    fixed order (schema version, hash, prompt, rubric versions).

    A stored prompt (or rubric) version of **0** means "stamped before the 2026-10 version reset": the stamp was
    renumbered by that reset and is never current, so ``prompt_version_mismatch`` on a version-0 review (a
    deterministic runner's included) says the review predates the reset, not that the runner's check changed.
    """

    context_schema_version_mismatch = "context_schema_version_mismatch"
    context_hash_mismatch = "context_hash_mismatch"
    prompt_version_mismatch = "prompt_version_mismatch"
    rubric_versions_mismatch = "rubric_versions_mismatch"


@dataclass(frozen=True)
class MachineReviewCurrencyKey:
    """The four version inputs that decide whether a machine review is current.

    Assembled for both sides of the comparison — the stored review and the
    record's active recipe — so the classifier compares like for like.
    ``prompt`` and ``rubric`` are compared as refs: a differing name *or* a
    differing version is a mismatch, so a review made under another rubric reads
    stale exactly as one made under an older version of the same rubric.
    """

    context_schema_version: int
    context_hash: str
    prompt: VersionRef | StoredVersionRef
    rubric: VersionRef | StoredVersionRef


@dataclass(frozen=True)
class StoredMachineReviewProjection:
    """Minimal machine-review metadata needed for currency classification.

    Mirrors the columns a future append-only ``record_machine_review`` row
    (policy §8) would carry that are relevant to currency and latest-selection.
    Currency itself is about *which* review is live, not its verdict, so the
    classifier ignores ``status``; it is retained as passthrough provenance (the
    verdict the live review carried) and to mirror the future table's column.
    ``id`` and ``source_audit_event_id`` are the latest-selection tiebreaks
    (both may be ``None`` for in-memory projections not yet persisted, and sort
    last under DESC NULLS LAST).
    """

    record_type: str
    record_id: int | str
    reviewed_at: datetime
    context_schema_version: int
    context_hash: str
    prompt: VersionRef | StoredVersionRef
    rubric: VersionRef | StoredVersionRef
    status: MachineReviewStatus | None = None
    id: int | None = None
    source_audit_event_id: int | None = None

    @property
    def currency_key(self) -> MachineReviewCurrencyKey:
        """This review's currency key, for comparison against the active one."""
        return MachineReviewCurrencyKey(
            context_schema_version=self.context_schema_version,
            context_hash=self.context_hash,
            prompt=self.prompt,
            rubric=self.rubric,
        )


@dataclass(frozen=True)
class MachineReviewCurrencyClassification:
    """Result of classifying one record's machine-review passes.

    * ``state`` — the overall, latest-derived state (``not_run`` / ``current`` /
      ``stale``); never ``historical`` (the latest review is never historical).
    * ``active_review`` — the latest review by the policy §4 ordering, or
      ``None`` when there are no reviews.
    * ``historical_reviews`` — every non-latest review, newest-first; each is
      :attr:`MachineReviewCurrencyState.historical` by definition.
    * ``stale_reasons`` — the mismatched currency dimensions when ``state`` is
      ``stale``; empty otherwise.
    """

    state: MachineReviewCurrencyState
    active_review: StoredMachineReviewProjection | None = None
    historical_reviews: tuple[StoredMachineReviewProjection, ...] = ()
    stale_reasons: tuple[MachineReviewStaleReason, ...] = field(default_factory=tuple)


def _nulls_last_desc_rank(value: int | None) -> tuple[int, int]:
    """Rank an optional id for a single DESC sort with NULLS LAST.

    Returns ``(1, value)`` for a real id and ``(0, 0)`` for ``None``. Under one
    ``reverse=True`` (descending) sort a real id ``(1, n)`` always outranks
    ``None`` ``(0, 0)``, so nulls land last — the policy §4 ``DESC NULLS LAST``
    behavior — without comparing ``int`` against ``None``.
    """
    return (1, value) if value is not None else (0, 0)


def _latest_ordering_key(
    review: StoredMachineReviewProjection,
) -> tuple[datetime, tuple[int, int], tuple[int, int], tuple[int | str, ...]]:
    """Total, descending-sortable ordering key for latest-selection (policy §4).

    Primary: ``reviewed_at``. Then ``source_audit_event_id`` then ``id``, each
    via :func:`_nulls_last_desc_rank` so nulls sort last under ``reverse=True``.
    A final currency-content component is appended purely as a determinism
    backstop: it only ever breaks a tie the three policy keys leave (identical
    ``reviewed_at`` **and** ``source_audit_event_id`` **and** ``id``), which
    cannot occur for real persisted rows since ``id`` is a unique PK. It
    guarantees the classification is deterministic across input order even for
    pathological in-memory inputs, without altering the policy ordering for any
    real data.
    """
    return (
        review.reviewed_at,
        _nulls_last_desc_rank(review.source_audit_event_id),
        _nulls_last_desc_rank(review.id),
        (
            review.context_schema_version,
            review.context_hash,
            review.prompt.name,
            review.prompt.version,
        ),
    )


def _currency_mismatches(
    *,
    stored: MachineReviewCurrencyKey,
    active: MachineReviewCurrencyKey,
) -> tuple[MachineReviewStaleReason, ...]:
    """Return the currency dimensions where ``stored`` differs from ``active``.

    Reported in a fixed order (schema version, hash, prompt, rubric) so the result
    is deterministic. ``prompt`` and ``rubric`` are compared as whole refs, name
    and version together: a different rubric is a mismatch just as a different
    version of the same rubric is.
    """
    reasons: list[MachineReviewStaleReason] = []
    if stored.context_schema_version != active.context_schema_version:
        reasons.append(MachineReviewStaleReason.context_schema_version_mismatch)
    if stored.context_hash != active.context_hash:
        reasons.append(MachineReviewStaleReason.context_hash_mismatch)
    if stored.prompt != active.prompt:
        reasons.append(MachineReviewStaleReason.prompt_version_mismatch)
    if stored.rubric != active.rubric:
        reasons.append(MachineReviewStaleReason.rubric_versions_mismatch)
    return tuple(reasons)


def classify_machine_review_currency(
    reviews: Sequence[StoredMachineReviewProjection],
    *,
    current_context: MachineReviewContextDigest,
    active_prompt: VersionRef,
    active_rubric: VersionRef,
) -> MachineReviewCurrencyClassification:
    """Classify a record's machine-review passes as current / stale / historical.

    Pure and deterministic. ``reviews`` are assumed already scoped to one record
    (the caller filters); this function does not re-scope them. Steps:

    1. No reviews -> ``not_run`` (nothing to be stale).
    2. Select the **latest** review by the policy §4 ordering
       (``reviewed_at`` DESC, ``source_audit_event_id`` DESC NULLS LAST, ``id``
       DESC NULLS LAST). Every other review is ``historical``, newest-first.
    3. The latest review is **current** iff all four currency dimensions match
       the active recipe (``current_context`` supplies the active
       ``context_schema_version``/``context_hash``; ``active_prompt`` and
       ``active_rubric`` the rest). Otherwise it is **stale**, and
       ``stale_reasons`` names every mismatched dimension.

    A stale latest review makes the whole record ``stale`` even when an older,
    historical review's hash still matches — the historical review is never the
    active candidate (policy §4).
    """
    if not reviews:
        return MachineReviewCurrencyClassification(
            state=MachineReviewCurrencyState.not_run
        )

    ordered = sorted(reviews, key=_latest_ordering_key, reverse=True)
    latest = ordered[0]
    historical = tuple(ordered[1:])

    active_key = MachineReviewCurrencyKey(
        context_schema_version=current_context.context_schema_version,
        context_hash=current_context.context_hash,
        prompt=active_prompt,
        rubric=active_rubric,
    )
    stale_reasons = _currency_mismatches(
        stored=latest.currency_key, active=active_key
    )

    state = (
        MachineReviewCurrencyState.current
        if not stale_reasons
        else MachineReviewCurrencyState.stale
    )

    return MachineReviewCurrencyClassification(
        state=state,
        active_review=latest,
        historical_reviews=historical,
        stale_reasons=stale_reasons,
    )
