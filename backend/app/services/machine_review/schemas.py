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


# ---------------------------------------------------------------------------
# v2 provider output contract (machine_review_provider_contract_v2.md)
# ---------------------------------------------------------------------------
#
# The v1 source contract (``app/services/llm_precheck/schemas.py``:
# ``LLMPrecheckResult`` / ``LLMFinding``) is narrower than this machine-review
# vocabulary: it has only a precheck ``label`` (translated to a status), no
# ``curator_priority``, no ``recommended_action``, and a smaller category set.
# The v2 provider payload speaks this vocabulary natively, so the adapter can
# validate it directly with no label->status translation. v2 is detected by a
# single root marker (``schema_version``); its absence means a legacy v1
# payload. See ``machine_review_provider_contract_v2.md`` §3/§4/§7.

#: Root marker value identifying a v2 machine-review provider payload.
MACHINE_REVIEW_V2_SCHEMA_VERSION = "machine_review_v2"


class MachineReviewProviderFindingV2(BaseModel):
    """One finding in a v2 provider payload, using native machine-review terms.

    Mirrors :class:`MachineReviewFinding` field-for-field and adds explicit
    record addressing: a provider may cite ``record_ref`` (the mapping key) or
    ``record_id`` (an internal-id alias; the adapter derives
    ``record_ref = str(record_id)`` when only ``record_id`` is given). Like the
    rest of the contract it is ``extra="forbid"`` so no mutation field can be
    smuggled through, and ``recommended_action`` is advisory text for a human
    curator — never executed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: MachineReviewSeverity
    category: MachineReviewCategory
    record_type: str | None = Field(default=None, max_length=128)
    record_ref: str | None = Field(default=None, max_length=256)
    record_id: int | None = None
    message: str = Field(min_length=1, max_length=1000)
    evidence_keys: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    recommended_action: str | None = Field(default=None, max_length=1000)


class MachineReviewProviderResultV2(BaseModel):
    """Versioned provider payload for native machine-review output (v2).

    Carries the machine-review ``status`` directly (no precheck ``label`` to
    translate), an optional ``curator_priority``, a first-class ``provider``
    field (in v1 the provider is a sibling key on the audit event), and the
    full :class:`MachineReviewCategory` vocabulary on findings. Schema-validated
    before any use; ``extra="forbid"`` rejects mutation payloads and
    ``used_rag`` is constrained to ``False`` (RAG is a non-goal). Backward
    compatible: v1 payloads have no ``schema_version`` and take the legacy path.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["machine_review_v2"]
    status: MachineReviewStatus
    curator_priority: CuratorPriority | None = None
    summary: str | None = Field(default=None, max_length=2000)
    findings: tuple[MachineReviewProviderFindingV2, ...] = Field(
        default_factory=tuple, max_length=50
    )
    model: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=128)
    used_rag: Literal[False] = False
