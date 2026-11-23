"""Internal contract schemas for the provisional machine-review layer.

These are **types/contracts only**. They define the vocabulary and output
shape of the future machine-review layer so the direction is
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
* ``retrieval_sources`` lists the guidance keys a review cites; each must be a
  key the run actually retrieved (checked by :mod:`.offer`), so a review cannot
  cite a source it was never shown.

This module performs no provider calls, no persistence, no read-API
integration, and no submission-workflow wiring.
"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from tckdb_schemas.fragments.version import VersionRef


class MachineReviewStatus(str, Enum):
    """Provisional, advisory record-level machine-review state.

    A dedicated enum, intentionally distinct from
    :class:`~app.db.models.common.RecordReviewStatus` (human review) and
    :class:`~app.db.models.common.SubmissionPrecheckLabel` (submission
    precheck). Authoritative for *nothing* the deterministic-evidence or
    human-review layers own.

    ``machine_screened_blocking_concern`` is intentionally
    **not** defined here: it is reserved/deferred and must
    only be introduced when a concrete operational distinction from
    ``machine_screened_needs_attention`` is actually wired.
    """

    not_run = "not_run"
    machine_screened_pass = "machine_screened_pass"
    machine_screened_warning = "machine_screened_warning"
    machine_screened_needs_attention = "machine_screened_needs_attention"
    machine_review_failed = "machine_review_failed"


class MachineReviewSeverity(str, Enum):
    """Severity of a single machine-review finding."""

    info = "info"
    warning = "warning"
    critical = "critical"


class MachineReviewCategory(str, Enum):
    """Category vocabulary for machine-review findings."""

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
    """Advisory ordering hint for a future human-review queue.

    Pure metadata — it is *not* a state and has no effect on visibility,
    trust, or evidence.
    """

    low = "low"
    medium = "medium"
    high = "high"


class MachineReviewFinding(BaseModel):
    """One advisory finding produced by a machine reviewer.

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
    """Validated structured result of one machine-review pass.

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
    retrieval_sources: tuple[str, ...] = Field(default_factory=tuple, max_length=10)


# ---------------------------------------------------------------------------
# provider output contract
# ---------------------------------------------------------------------------
#
# The provider payload speaks the machine-review vocabulary natively: a
# ``status``, a ``curator_priority``, ``recommended_action`` and the full
# category set. It is identified by a single root marker (``format``: a name and a
# separate integer version), which the audit adapter requires.

#: Root marker value identifying a machine-review provider payload: the format's
#: name beside its integer version.
MACHINE_REVIEW_FORMAT: VersionRef = VersionRef(name="machine_review", version=2)

#: Length caps of the provider payload. The prompt states them as numbers and the
#: exported schema enforces them as ``maxLength``/``maxItems``.
SUMMARY_MAX_CHARS = 280
FINDINGS_MAX = 10
MESSAGE_MAX_CHARS = 240
EVIDENCE_KEYS_MAX = 8
KEY_MAX_CHARS = 64
RECOMMENDED_ACTION_MAX_CHARS = 200
RETRIEVAL_SOURCES_MAX = 10
RECORD_TYPE_MAX_CHARS = 128
RECORD_REF_MAX_CHARS = 256
IDENTITY_MAX_CHARS = 128


class MachineReviewProviderFinding(BaseModel):
    """One finding in a provider payload, using native machine-review terms.

    A finding addresses a record by the ``(record_type, record_ref)`` pair the
    context offered, or sets both to null for a concern about the submission as a
    whole. Like the rest of the contract it is ``extra="forbid"`` so no mutation
    field can be smuggled through, and ``recommended_action`` is advisory text
    for a human curator -- never executed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: MachineReviewSeverity = Field(description="How serious the concern is.")
    category: MachineReviewCategory = Field(description="What kind of concern it is.")
    record_type: str | None = Field(
        default=None,
        max_length=RECORD_TYPE_MAX_CHARS,
        description="The record_type of a pair from offered_record_refs, or null for both record fields.",
    )
    record_ref: str | None = Field(
        default=None,
        max_length=RECORD_REF_MAX_CHARS,
        description="The record_ref of the same pair from offered_record_refs, or null for both record fields.",
    )
    message: str = Field(
        min_length=1,
        max_length=MESSAGE_MAX_CHARS,
        description=f"The concern, one or two short sentences, at most {MESSAGE_MAX_CHARS} characters.",
    )
    evidence_keys: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=EVIDENCE_KEYS_MAX,
        description=f"At most {EVIDENCE_KEYS_MAX} names of checks relied on, each at most {KEY_MAX_CHARS} characters.",
    )
    recommended_action: str | None = Field(
        default=None,
        max_length=RECOMMENDED_ACTION_MAX_CHARS,
        description=f"Advice to a person, at most {RECOMMENDED_ACTION_MAX_CHARS} characters, or null.",
    )

    @field_validator("evidence_keys")
    @classmethod
    def _evidence_keys_are_short(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(key) > KEY_MAX_CHARS for key in value):
            raise ValueError(f"an evidence key is longer than {KEY_MAX_CHARS} characters")
        return value


class MachineReviewProviderResult(BaseModel):
    """Versioned provider payload for machine-review output.

    Carries the machine-review ``status`` directly, an optional
    ``curator_priority``, a first-class ``provider`` field, and the full
    :class:`MachineReviewCategory` vocabulary on findings. Schema-validated
    before any use; ``extra="forbid"`` rejects mutation payloads. The root
    ``format`` marker is required: a payload without it, or with another value,
    is refused.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: VersionRef = Field(description="Always machine_review, version 2.")
    status: MachineReviewStatus = Field(description="The verdict for the submission as a whole.")
    curator_priority: CuratorPriority | None = Field(default=None, description="Queue-ordering hint, or null.")
    summary: str = Field(
        min_length=1,
        max_length=SUMMARY_MAX_CHARS,
        description=f"One sentence, at most {SUMMARY_MAX_CHARS} characters.",
    )
    findings: tuple[MachineReviewProviderFinding, ...] = Field(
        default_factory=tuple,
        max_length=FINDINGS_MAX,
        description=f"At most {FINDINGS_MAX} findings; empty when nothing needs attention.",
    )
    retrieval_sources: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=RETRIEVAL_SOURCES_MAX,
        description=(
            f"At most {RETRIEVAL_SOURCES_MAX} source_key values returned by a guidance search, "
            f"each at most {KEY_MAX_CHARS} characters; empty when none was searched."
        ),
    )
    model: str | None = Field(default=None, max_length=IDENTITY_MAX_CHARS)
    provider: str | None = Field(default=None, max_length=IDENTITY_MAX_CHARS)

    @field_validator("format")
    @classmethod
    def _format_is_the_one_this_model_reads(cls, value: VersionRef) -> VersionRef:
        if value != MACHINE_REVIEW_FORMAT:
            raise ValueError(
                f"this model reads format {MACHINE_REVIEW_FORMAT.name!r} version {MACHINE_REVIEW_FORMAT.version}, "
                f"not {value.name!r} version {value.version}"
            )
        return value

    @field_validator("retrieval_sources")
    @classmethod
    def _sources_are_short(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(key) > KEY_MAX_CHARS for key in value):
            raise ValueError(f"a retrieval source is longer than {KEY_MAX_CHARS} characters")
        return value


#: Statuses a provider may return; the other two belong to the runner.
_PROVIDER_STATUSES = (
    MachineReviewStatus.machine_screened_pass,
    MachineReviewStatus.machine_screened_warning,
    MachineReviewStatus.machine_screened_needs_attention,
)

_DRAFT_07 = "http://json-schema.org/draft-07/schema#"


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            target = copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]])
            extras = {k: v for k, v in node.items() if k != "$ref"}
            return _inline_refs({**target, **extras}, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _tighten(node: Any) -> Any:
    """Make every object closed and fully required; cap every free string."""
    if isinstance(node, list):
        return [_tighten(v) for v in node]
    if not isinstance(node, dict):
        return node
    node = {k: _tighten(v) if k in {"properties", "items", "anyOf"} else v for k, v in node.items()}
    if "properties" in node:
        node["properties"] = {name: _tighten(sub) for name, sub in node["properties"].items()}
    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node["properties"])
    for key in ("title", "default"):
        node.pop(key, None)
    return node


def _cap_strings(node: Any, cap: int) -> None:
    if isinstance(node, dict):
        if node.get("type") == "string" and not {"enum", "const", "maxLength"} & set(node):
            node["maxLength"] = cap
        for value in node.values():
            _cap_strings(value, cap)
    elif isinstance(node, list):
        for value in node:
            _cap_strings(value, cap)


def provider_output_json_schema() -> dict[str, Any]:
    """The provider payload as a draft-07 JSON Schema for grammar-constrained decoding.

    No ``$ref``/``$defs``; every object is closed and lists all its properties as
    required; every free string has ``maxLength``. ``model`` and ``provider`` are
    left out (the runner stamps them) and ``status`` allows only the statuses a
    provider may return. Returns a fresh copy on every call.
    """
    raw = MachineReviewProviderResult.model_json_schema()
    defs = {name: {k: v for k, v in d.items() if k != "description"} for name, d in raw.get("$defs", {}).items()}
    raw.pop("description", None)
    schema = _inline_refs(raw, defs)
    schema = _tighten(schema)
    properties = schema["properties"]
    properties.pop("model", None)
    properties.pop("provider", None)
    properties["format"] = {
        "type": "object",
        "properties": {
            "name": {"const": MACHINE_REVIEW_FORMAT.name},
            "version": {"const": MACHINE_REVIEW_FORMAT.version},
        },
        "required": ["name", "version"],
        "additionalProperties": False,
        "description": properties["format"].get("description", ""),
    }
    properties["status"] = {
        "type": "string",
        "enum": [s.value for s in _PROVIDER_STATUSES],
        "description": properties["status"].get("description", ""),
    }
    schema["required"] = list(properties)
    _cap_strings(schema, KEY_MAX_CHARS)
    return {"$schema": _DRAFT_07, **schema}
