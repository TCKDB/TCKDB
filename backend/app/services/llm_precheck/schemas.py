"""Structured schemas for optional AI Review Assistant prechecks."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMPrecheckLabel(str, Enum):
    """Advisory result labels returned by an optional LLM precheck."""

    not_run = "not_run"
    pass_ = "pass"
    warning = "warning"
    needs_attention = "needs_attention"
    failed_to_review = "failed_to_review"


class LLMFindingSeverity(str, Enum):
    """Severity vocabulary for advisory LLM findings."""

    info = "info"
    warning = "warning"
    critical = "critical"


class LLMFindingCategory(str, Enum):
    """Finding categories an LLM precheck may use for curator-facing notes."""

    provenance = "provenance"
    units = "units"
    geometry = "geometry"
    kinetics = "kinetics"
    thermo = "thermo"
    statmech = "statmech"
    calculation_parameters = "calculation_parameters"
    consistency = "consistency"


class LLMRecordRef(BaseModel):
    """Compact reference to a submission-linked record."""

    model_config = ConfigDict(frozen=True)

    record_type: str
    record_id: int
    role: str | None = None


class LLMFinding(BaseModel):
    """One advisory finding emitted by the AI Review Assistant."""

    model_config = ConfigDict(frozen=True)

    severity: LLMFindingSeverity
    category: LLMFindingCategory
    record_type: str | None = None
    record_id: int | None = None
    message: str = Field(min_length=1, max_length=1000)
    evidence_keys: tuple[str, ...] = Field(default_factory=tuple, max_length=20)


class LLMPrecheckContext(BaseModel):
    """Compact structured context sent to an optional LLM precheck provider."""

    model_config = ConfigDict(frozen=True)

    submission_id: int
    submission_status: str | None = None
    submission_kind: str | None = None
    source_kind: str | None = None
    title: str | None = None
    summary: str | None = None
    record_refs: tuple[LLMRecordRef, ...] = Field(default_factory=tuple)
    trust_summaries: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    included_artifact_text: bool = False
    included_coordinates: bool = False
    included_private_notes: bool = False


class LLMPrecheckResult(BaseModel):
    """Validated structured result from an optional LLM precheck provider."""

    model_config = ConfigDict(frozen=True)

    label: LLMPrecheckLabel
    summary: str | None = Field(default=None, max_length=2000)
    findings: tuple[LLMFinding, ...] = Field(default_factory=tuple, max_length=50)
    model: str | None = Field(default=None, max_length=128)
    used_rag: bool = False


def llm_precheck_result_to_details_json(
    result: LLMPrecheckResult,
) -> dict[str, Any]:
    """Serialize a validated result into an audit-event-friendly dict."""
    return result.model_dump(mode="json")
