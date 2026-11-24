"""The compact submission context a machine-review provider reviews over."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LLMRecordRef(BaseModel):
    """Compact reference to a submission-linked record."""

    model_config = ConfigDict(frozen=True)

    record_type: str
    record_id: int
    role: str | None = None


class LLMFindingSummary(BaseModel):
    """One deterministic check finding, as TCKDB's own check wrote it."""

    model_config = ConfigDict(frozen=True)

    severity: str | None = None
    category: str | None = None
    message: str
    evidence_keys: tuple[str, ...] = ()


class LLMWarningSummary(BaseModel):
    """One deposit warning: its code and the sentence TCKDB renders for it."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str


class LLMRecordSummary(BaseModel):
    """What the reviewer is shown about one linked record.

    ``values`` are the record's own stored numbers, booleans and vocabulary values (column names carry the
    units); ``levels_of_theory`` and ``software`` are labels keyed by the column that references them.
    """

    model_config = ConfigDict(frozen=True)

    record_type: str
    record_ref: str
    values: dict[str, float | int | bool | str] = Field(default_factory=dict)
    levels_of_theory: dict[str, str] = Field(default_factory=dict)
    software: dict[str, str] = Field(default_factory=dict)
    deterministic_findings: tuple[LLMFindingSummary, ...] = ()
    deposit_warnings: tuple[LLMWarningSummary, ...] = ()


class LLMOmission(BaseModel):
    """What the token cap cut from the pack."""

    model_config = ConfigDict(frozen=True)

    summaries_omitted: int = 0
    refs_omitted: int = 0
    note: str


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
    record_summaries: tuple[LLMRecordSummary, ...] = Field(default_factory=tuple)
    submission_warnings: tuple[LLMWarningSummary, ...] = Field(default_factory=tuple)
    omitted: LLMOmission | None = None
    included_artifact_text: bool = False
    included_coordinates: bool = False
    included_private_notes: bool = False
