"""Build the context pack a machine reviewer reviews over, and cut it to a token budget.

For each record linked to a submission the pack carries its own stored numbers (column names carry the units), the
level of theory and software it cites, TCKDB's own deterministic check findings, and the deposit warnings as TCKDB
words them. It never carries a string, text or JSON column, so a depositor's notes and labels cannot reach the model
except through the title and summary, which the prompt marks untrusted. Primary and foreign keys never appear; a record
is named only by its offered ``record_ref``.

The pack is deterministic for a given database state. When it would exceed ``max_input_tokens`` (estimated as characters
divided by four, over the system prompt plus the rendered user message), the lowest-priority summaries are dropped first,
then offered refs from the tail, and the pack says how many.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from sqlalchemy import Boolean, Float, Integer, Numeric, select
from sqlalchemy import Enum as SAEnum
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import RelationshipDirection, Session

from app.api.errors import not_found
from app.db.models.common import SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.software import SoftwareRelease
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.deposit_warning_messages import render_message
from app.services.deposit_warnings import current_warnings, deposit_warnings_for
from app.services.llm_precheck.schemas import (
    LLMFindingSummary,
    LLMOmission,
    LLMPrecheckContext,
    LLMRecordRef,
    LLMRecordSummary,
    LLMWarningSummary,
)
from app.services.machine_review.providers.interface import MachineReviewContext
from app.services.machine_review.providers.prompt import MACHINE_REVIEW_SYSTEM_PROMPT, build_user_message
from app.services.machine_review.query import SCIENTIFIC_CHECK_PROVIDER, _latest_first_order_by
from app.services.record_models import RECORD_MODELS

_MAX_LABEL_CHARS = 64
_MAX_FINDING_CHARS = 240
_MAX_WARNING_CHARS = 400
_MAX_FINDINGS_PER_RECORD = 5
_MAX_WARNINGS_PER_RECORD = 10
_MAX_EVIDENCE_KEYS = 8
_MAX_VALUES_PER_RECORD = 80
_CHARS_PER_TOKEN = 4

_VALUE_TYPES = (Float, Integer, Numeric, Boolean, SAEnum)

#: Which kinds of record the reviewer should see first; anything not listed comes last, in link order.
_TYPE_ORDER = (
    "thermo",
    "kinetics",
    "statmech",
    "transport",
    "network_solve",
    "transition_state_entry",
    "transition_state",
    "network",
    "calculation",
    "applied_energy_correction",
    "species_entry",
    "reaction_entry",
    "conformer_group",
    "conformer_observation",
    "species",
    "reaction",
)
_TYPE_RANK = {name: rank for rank, name in enumerate(_TYPE_ORDER)}

_OMISSION_NOTE = (
    "Records or details were left out to fit the size limit. Their absence here says nothing about them."
)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _own_values(row: Any, *, prefix: str = "") -> dict[str, float | int | bool | str]:
    """The row's non-key numeric, boolean and vocabulary columns that hold a value."""
    values: dict[str, float | int | bool | str] = {}
    mapper = sa_inspect(type(row))
    for column in mapper.columns:
        if column.primary_key or column.foreign_keys or not isinstance(column.type, _VALUE_TYPES):
            continue
        value = getattr(row, mapper.get_property_by_column(column).key)
        if value is None:
            continue
        if isinstance(value, bool):
            pass
        elif hasattr(value, "value") and not isinstance(value, (int, float)):
            value = value.value
        elif isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                continue
        else:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
        values[f"{prefix}{column.key}"] = value
    return values


def _record_values(row: Any) -> dict[str, float | int | bool | str]:
    values = _own_values(row)
    mapper = sa_inspect(type(row))
    for relationship in sorted(mapper.relationships, key=lambda r: r.key):
        if relationship.direction is not RelationshipDirection.ONETOMANY or relationship.uselist:
            continue
        child = getattr(row, relationship.key)
        if child is not None:
            values.update(_own_values(child, prefix=f"{relationship.key}."))
    return dict(list(values.items())[:_MAX_VALUES_PER_RECORD])


def _referenced_labels(session: Session, row: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Level-of-theory and software labels for the row's foreign keys to those tables."""
    levels: dict[str, str] = {}
    software: dict[str, str] = {}
    mapper = sa_inspect(type(row))
    for column in mapper.columns:
        for foreign_key in column.foreign_keys:
            target = foreign_key.column.table.name
            if target not in ("level_of_theory", "software_release"):
                continue
            ident = getattr(row, mapper.get_property_by_column(column).key)
            if ident is None:
                continue
            if target == "level_of_theory":
                lot = session.get(LevelOfTheory, ident)
                if lot is not None:
                    label = f"{lot.method}/{lot.basis}" if lot.basis else lot.method
                    levels[column.key] = _clip(label, _MAX_LABEL_CHARS)
            else:
                release = session.get(SoftwareRelease, ident)
                if release is not None:
                    name = release.software.name
                    software[column.key] = _clip(f"{name} {release.version}" if release.version else name, _MAX_LABEL_CHARS)
    return levels, software


def _findings_by_record(
    session: Session, record_type: SubmissionRecordType, ids: list[int]
) -> dict[int, tuple[LLMFindingSummary, ...]]:
    """The latest deterministic check findings per record (the newest check row wins)."""
    rows = session.scalars(
        select(RecordMachineReviewRow)
        .where(
            RecordMachineReviewRow.record_type == record_type,
            RecordMachineReviewRow.record_id.in_(ids),
            RecordMachineReviewRow.provider == SCIENTIFIC_CHECK_PROVIDER,
        )
        .order_by(*_latest_first_order_by())
    )
    out: dict[int, tuple[LLMFindingSummary, ...]] = {}
    for row in rows:
        if row.record_id in out:
            continue
        found: list[LLMFindingSummary] = []
        for item in row.findings_json or []:
            if not isinstance(item, dict) or not isinstance(item.get("message"), str):
                continue
            keys = item.get("evidence_keys")
            found.append(
                LLMFindingSummary(
                    severity=item.get("severity") if isinstance(item.get("severity"), str) else None,
                    category=item.get("category") if isinstance(item.get("category"), str) else None,
                    message=_clip(item["message"], _MAX_FINDING_CHARS),
                    evidence_keys=tuple(
                        _clip(k, _MAX_LABEL_CHARS) for k in (keys if isinstance(keys, list) else [])[:_MAX_EVIDENCE_KEYS] if isinstance(k, str)
                    ),
                )
            )
        out[row.record_id] = tuple(found[:_MAX_FINDINGS_PER_RECORD])
    return out


def _submission_warnings(session: Session, submission_id: int) -> tuple[LLMWarningSummary, ...]:
    rows = session.scalars(
        current_warnings(
            select(DepositWarning)
            .where(
                DepositWarning.submission_id == submission_id,
                DepositWarning.record_id.is_(None),
                DepositWarning.code.is_not(None),
            )
            .order_by(DepositWarning.id)
        )
    )
    return tuple(
        LLMWarningSummary(code=row.code, message=_clip(render_message(row.code, row.context), _MAX_WARNING_CHARS))
        for row in rows
    )[:_MAX_WARNINGS_PER_RECORD]


def _summaries(session: Session, links: list[SubmissionRecordLink]) -> list[tuple[bool, int, int, LLMRecordSummary]]:
    """``(quiet, type_rank, link_id, summary)`` for every linked record that still exists."""
    by_type: dict[SubmissionRecordType, list[SubmissionRecordLink]] = defaultdict(list)
    for link in links:
        by_type[link.record_type].append(link)
    out: list[tuple[bool, int, int, LLMRecordSummary]] = []
    for record_type, group in by_type.items():
        model = RECORD_MODELS.get(record_type)
        if model is None:
            continue
        ids = sorted({link.record_id for link in group})
        rows = {row.id: row for row in session.scalars(select(model).where(model.id.in_(ids)))}
        findings = _findings_by_record(session, record_type, ids)
        warnings = deposit_warnings_for(session, record_type, ids)
        for link in group:
            row = rows.get(link.record_id)
            if row is None:
                continue
            levels, software = _referenced_labels(session, row)
            record_findings = findings.get(link.record_id, ())
            record_warnings = tuple(
                LLMWarningSummary(code=item.code, message=_clip(item.message, _MAX_WARNING_CHARS))
                for item in warnings[link.record_id]["items"]
            )[:_MAX_WARNINGS_PER_RECORD]
            summary = LLMRecordSummary(
                record_type=record_type.value,
                record_ref=str(link.record_id),
                values=_record_values(row),
                levels_of_theory=levels,
                software=software,
                deterministic_findings=record_findings,
                deposit_warnings=record_warnings,
            )
            out.append(
                (not (record_findings or record_warnings), _TYPE_RANK.get(record_type.value, len(_TYPE_RANK)), link.id, summary)
            )
    out.sort(key=lambda entry: entry[:3])
    return out


def estimate_input_tokens(context: LLMPrecheckContext) -> int:
    """Estimated prompt tokens for reviewing ``context``: system prompt plus user message, characters / 4."""
    message = build_user_message(MachineReviewContext.from_llm_precheck_context(context))
    return math.ceil((len(MACHINE_REVIEW_SYSTEM_PROMPT) + len(message)) / _CHARS_PER_TOKEN)


def build_review_context_pack(session: Session, submission_id: int, *, max_input_tokens: int) -> LLMPrecheckContext:
    """Build the review context of a submission, cut to ``max_input_tokens`` estimated tokens."""
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise not_found("Submission", row_id=submission_id)
    links = list(
        session.scalars(
            select(SubmissionRecordLink)
            .where(SubmissionRecordLink.submission_id == submission_id)
            .order_by(SubmissionRecordLink.id.asc())
        )
    )
    refs = tuple(
        LLMRecordRef(record_type=link.record_type.value, record_id=link.record_id, role=link.role) for link in links
    )
    summaries = tuple(entry[3] for entry in _summaries(session, links))
    base = LLMPrecheckContext(
        submission_id=submission.id,
        submission_status=submission.status.value,
        submission_kind=submission.submission_kind.value,
        source_kind=submission.source_kind.value,
        title=submission.title,
        summary=submission.summary,
        submission_warnings=_submission_warnings(session, submission_id),
    )

    def pack(kept_summaries: int, kept_refs: int) -> LLMPrecheckContext:
        dropped_summaries = len(summaries) - kept_summaries
        dropped_refs = len(refs) - kept_refs
        omitted = (
            LLMOmission(summaries_omitted=dropped_summaries, refs_omitted=dropped_refs, note=_OMISSION_NOTE)
            if dropped_summaries or dropped_refs
            else None
        )
        return base.model_copy(
            update={"record_refs": refs[:kept_refs], "record_summaries": summaries[:kept_summaries], "omitted": omitted}
        )

    candidate = pack(len(summaries), len(refs))
    for drop in range(len(summaries) + len(refs) + 1):
        if drop <= len(summaries):
            candidate = pack(len(summaries) - drop, len(refs))
        else:
            candidate = pack(0, len(refs) - (drop - len(summaries)))
        if estimate_input_tokens(candidate) <= max_input_tokens:
            return candidate
    return candidate


__all__ = ["build_review_context_pack", "estimate_input_tokens"]
