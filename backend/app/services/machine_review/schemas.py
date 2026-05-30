"""Internal contract schemas for the provisional machine-review layer.

These are **types/contracts only**. They define the vocabulary and output
shape of the future machine-review layer described in
``backend/docs/specs/provisional_machine_review.md`` so the direction is
type-safe and testable before any provider, persistence, or public exposure
exists.

Deliberate boundaries (enforced here so the type system, not convention,
keeps them apart):

* These enums are **not** ``RecordReviewStatus`` (human review,
  ``app/db/models/common.py``) and **not** ``SubmissionPrecheckLabel``
  (submission precheck). Machine review is a separate, third axis.
* No field here can carry a mutation instruction. The machine reviewer may
  *cite* deterministic evidence (``evidence_keys``) but can never return
  "set field X". There is intentionally no ``set_*`` / ``mutation`` /
  ``override`` field anywhere in this module.
* ``used_rag`` is constrained to ``False`` for the MVP — RAG is a non-goal.

This module performs no provider calls, no persistence, no read-API
integration, and no submission-workflow wiring.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MachineReviewStatus(str, Enum):
    """Provisional, advisory record-level machine-review state.

    A dedicated enum, intentionally distinct from
    :class:`~app.db.models.common.RecordReviewStatus` (human review) and
    :class:`~app.db.models.common.SubmissionPrecheckLabel` (submission
    precheck). Authoritative for *nothing* the deterministic-evidence or
    human-review layers own; see the spec §2/§4.

    ``machine_screened_blocking_concern`` from the spec is intentionally
    **not** defined here: it is reserved/deferred (spec §3, §15 Q1) and must
    only be introduced when a concrete operational distinction from
    ``machine_screened_needs_attention`` is actually wired.
    """

    not_run = "not_run"
    machine_screened_pass = "machine_screened_pass"
    machine_screened_warning = "machine_screened_warning"
    machine_screened_needs_attention = "machine_screened_needs_attention"
    machine_review_failed = "machine_review_failed"


class MachineReviewSeverity(str, Enum):
    """Severity of a single machine-review finding (spec §8)."""

    info = "info"
    warning = "warning"
    critical = "critical"


class MachineReviewCategory(str, Enum):
    """Category vocabulary for machine-review findings (spec §8)."""

    provenance = "provenance"
    units = "units"
    geometry = "geometry"
    kinetics = "kinetics"
    thermo = "thermo"
    statmech = "statmech"
    transport = "transport"
    transition_state_validation = "transition_state_validation"
    calculation_parameters = "calculation_parameters"
    consistency = "consistency"
    schema_gap = "schema_gap"


class CuratorPriority(str, Enum):
    """Advisory ordering hint for a future human-review queue (spec §3).

    Pure metadata — it is *not* a state and has no effect on visibility,
    trust, or evidence.
    """

    low = "low"
    medium = "medium"
    high = "high"


class MachineReviewFinding(BaseModel):
    """One advisory finding produced by a machine reviewer (spec §8).

    ``record_ref`` and ``evidence_keys`` are **pointers only**: a finding
    cites a record and the deterministic evidence keys it relates to, but
    carries no instruction to change anything. ``recommended_action`` is
    advisory free text for a human curator and is never executed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: MachineReviewSeverity
    category: MachineReviewCategory
    # Public-ref pointers only; raw internal ``record_id`` is governed by the
    # existing internal-id policy and is deliberately absent from this contract.
    record_type: str | None = Field(default=None, max_length=128)
    record_ref: str | None = Field(default=None, max_length=256)
    message: str = Field(min_length=1, max_length=1000)
    evidence_keys: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    recommended_action: str | None = Field(default=None, max_length=1000)


class MachineReviewResult(BaseModel):
    """Validated structured result of one machine-review pass (spec §8).

    Schema-validated before any persistence: malformed provider output is a
    validation error the caller converts to
    :attr:`MachineReviewStatus.machine_review_failed`, never an upload
    failure. There is no mutation-payload field on this model by design.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: MachineReviewStatus
    curator_priority: CuratorPriority | None = None
    summary: str | None = Field(default=None, max_length=2000)
    findings: tuple[MachineReviewFinding, ...] = Field(
        default_factory=tuple, max_length=50
    )
    model: str | None = Field(default=None, max_length=128)
    # RAG is a non-goal for the MVP; constrain to False at the type level so a
    # provider that claims it used RAG fails validation.
    used_rag: Literal[False] = False
