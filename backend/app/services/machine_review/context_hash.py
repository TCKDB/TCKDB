"""Pure, deterministic ``context_hash`` builder for machine-review currency.

This module implements the ``context_hash`` policy from
``backend/docs/specs/record_machine_review_policy.md`` §3: a stable digest of
the **compact deterministic evidence context** a machine review saw, used to
decide whether a stored review is still *current* for the record's present
evidence (current/stale/historical, that spec §2/§3.5).

It is **pure**: it performs no database access, no persistence, no provider
call, and no public exposure. It mutates nothing — not scientific records, the
deterministic evidence/trust layer, nor the human-review layer. It builds a
hash from a typed, already-compact context the caller assembles; it does not
read raw artifacts or logs itself.

Design choices (policy §3, mirroring :func:`compute_finding_fingerprint`):

* **Rejection over silent-ignore.** :class:`MachineReviewEvidenceContext` is
  ``extra="forbid"``, so a forbidden input (``provider``, ``model``,
  ``reviewed_at``, ``created_at``, a raw log, a secret, …) raises at
  construction rather than being quietly dropped. The builder therefore cannot
  receive an excluded field at all. This is documented in the policy spec §3.3.
* **Order-insensitivity for set-like inputs.** Check lists, artifact kinds,
  notes, source calculations, and geometry validations are canonicalised
  (sorted) before hashing, so input order never changes the digest.
* **Schema version folded in.** ``context_schema_version`` is part of the
  hashed payload *and* returned on the digest, so a recipe bump changes the
  hash even for identical inputs — a hash-only comparison can never treat a
  prior-recipe review as current (policy §3.4).
* **Provenance excluded.** ``provider``/``model`` and all timestamps are
  excluded: the hash is the currency of the *evidence*, not the reviewer
  (policy §3.3/§3.5). Reviewer-recipe currency (``prompt_version``,
  ``rubric_versions``) lives in the currency key, not here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Version of the context-hash recipe (input set + canonicalisation). Bump this
#: whenever the hashed inputs or their canonical form change; every prior digest
#: then differs and prior reviews become stale until re-reviewed (policy §3.4).
MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION = "v1"


class SourceCalculationContext(BaseModel):
    """One source calculation in a machine-review evidence context.

    Identity is the ``(ref, role)`` pair: the same calculation cited under two
    roles is two distinct evidence inputs. ``ref`` is a stable reference (public
    ref or the private mapping key); raw calculation contents never appear here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str = Field(min_length=1, max_length=256)
    role: str | None = Field(default=None, max_length=128)


class GeometryValidationContext(BaseModel):
    """One geometry validation status in a machine-review evidence context.

    ``ref`` identifies the validated geometry (per source geometry, policy
    §3.2); ``status`` is its compact validation status, never the coordinates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str = Field(min_length=1, max_length=256)
    status: str | None = Field(default=None, max_length=128)


class MachineReviewEvidenceContext(BaseModel):
    """The compact, deterministic evidence context a machine review saw.

    This is the **included-inputs** contract of policy §3.2, and *only* those
    inputs. It is ``extra="forbid"`` so the excluded inputs of §3.3 (raw
    artifacts, full logs, coordinates, secrets, ``provider``/``model``, and any
    timestamp such as ``reviewed_at``/``created_at``) are **rejected** at
    construction — the builder can never hash one. Assembling this object from
    the deterministic evidence layer (and only the explicitly-included
    free-text/review-status fields) is the caller's job; this module only
    hashes what it is given.

    ``review_status`` and ``notes`` default to "not part of the context"
    (``None`` / empty). Per policy §6 the recommended default is to leave
    ``review_status`` out (machine and human review are separate axes); include
    it only when a reviewer prompt genuinely reasons over it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Record identity — pins the context to the exact record (policy §3.2), so
    # two different records with identical checks do not collide.
    record_type: str = Field(min_length=1, max_length=128)
    record_ref: str = Field(min_length=1, max_length=256)

    # Deterministic evidence rubric: name + version are in the hash on purpose —
    # a rubric version bump changes the meaning of the check set (policy §3.2).
    rubric_name: str | None = Field(default=None, max_length=128)
    rubric_version: int | None = None

    # Evidence check sets — set-like; canonicalised (sorted) before hashing.
    passed_checks: tuple[str, ...] = Field(default_factory=tuple)
    missing_checks: tuple[str, ...] = Field(default_factory=tuple)
    warning_checks: tuple[str, ...] = Field(default_factory=tuple)
    not_applicable_checks: tuple[str, ...] = Field(default_factory=tuple)

    hard_fail_reason: str | None = Field(default=None, max_length=512)

    # Source provenance, compact: refs + roles, and per-geometry validation
    # statuses. Set-like; canonicalised before hashing.
    source_calculations: tuple[SourceCalculationContext, ...] = Field(
        default_factory=tuple
    )
    geometry_validations: tuple[GeometryValidationContext, ...] = Field(
        default_factory=tuple
    )

    # Artifact *kinds* present, never contents (policy §3.2). Set-like.
    artifact_kinds: tuple[str, ...] = Field(default_factory=tuple)

    # Included only when explicitly part of the reviewed context (policy §3.2).
    # ``review_status`` / ``is_certified`` are **read-only context inputs** — the
    # human-review snapshot the reviewer saw — never machine-owned outputs.
    # Per policy §6 a deployment may prefer to leave ``review_status`` out to
    # keep the machine/human axes independent; the field is optional so either
    # posture is expressible.
    review_status: str | None = Field(default=None, max_length=64)
    is_certified: bool | None = None
    notes: tuple[str, ...] = Field(default_factory=tuple)


@dataclass(frozen=True)
class MachineReviewContextDigest:
    """Result of hashing a :class:`MachineReviewEvidenceContext`.

    Carries both halves the currency check needs (policy §3.4/§3.5): the
    ``context_hash`` and the ``context_schema_version`` it was produced under. A
    stored review is comparable to "now" only when both match the active recipe.
    """

    context_hash: str
    context_schema_version: str


def _sorted_pairs(pairs: list[tuple[Any, Any]]) -> list[list[Any]]:
    """Sort ``(a, b)`` pairs deterministically, tolerating ``None`` in either.

    ``None`` cannot be ordered against ``str`` directly, so each element is
    keyed as ``(is_none, value_or_empty)`` — ``None`` sorts before any value,
    and is never conflated with ``""`` because the ``is_none`` flag differs.
    Returns lists (JSON arrays) so the canonical serialisation is unambiguous.
    """

    def key(pair: tuple[Any, Any]) -> tuple[Any, ...]:
        a, b = pair
        return (a is None, a or "", b is None, b or "")

    return [[a, b] for a, b in sorted(pairs, key=key)]


def build_machine_review_context_hash(
    context: MachineReviewEvidenceContext,
    *,
    context_schema_version: str = MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION,
) -> MachineReviewContextDigest:
    """Build a stable digest for a machine-review evidence context.

    Pure and deterministic: the same context always yields the same digest, and
    set-like inputs (check lists, artifact kinds, notes, source calculations,
    geometry validations) are order-insensitive. The digest is a SHA-256 over a
    canonical, key-sorted, compact JSON serialisation of the included inputs
    plus ``context_schema_version`` — the same canonicalisation discipline as
    :func:`compute_finding_fingerprint`.

    The excluded inputs of policy §3.3 cannot reach here:
    :class:`MachineReviewEvidenceContext` is ``extra="forbid"`` and carries no
    field for raw artifacts/logs/coordinates, secrets, ``provider``/``model``,
    or any timestamp, so those are rejected at construction rather than ignored.

    ``context_schema_version`` is folded into the hashed payload, so two
    contexts that are identical except for their schema version hash
    differently and are never accidentally compared as current (policy §3.4).
    """
    payload = {
        "context_schema_version": context_schema_version,
        "record_type": context.record_type,
        "record_ref": context.record_ref,
        "rubric_name": context.rubric_name,
        "rubric_version": context.rubric_version,
        # Check sets: sorted so input order is irrelevant.
        "passed_checks": sorted(context.passed_checks),
        "missing_checks": sorted(context.missing_checks),
        "warning_checks": sorted(context.warning_checks),
        "not_applicable_checks": sorted(context.not_applicable_checks),
        "hard_fail_reason": context.hard_fail_reason,
        # Source provenance: sorted (ref, role) / (ref, status) pairs.
        "source_calculations": _sorted_pairs(
            [(sc.ref, sc.role) for sc in context.source_calculations]
        ),
        "geometry_validations": _sorted_pairs(
            [(gv.ref, gv.status) for gv in context.geometry_validations]
        ),
        "artifact_kinds": sorted(context.artifact_kinds),
        "review_status": context.review_status,
        "is_certified": context.is_certified,
        # Notes treated as an unordered set of included free-text values.
        "notes": sorted(context.notes),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    context_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return MachineReviewContextDigest(
        context_hash=context_hash,
        context_schema_version=context_schema_version,
    )
