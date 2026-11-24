"""Build the context pack a machine reviewer reviews over, and cut it to a token budget.

For each record linked to a submission the pack carries its own stored numbers (column names carry the units, and
the few unit-bearing columns whose name does not are renamed or paired with their unit), the level of theory,
software and workflow tool it cites, TCKDB's own deterministic check findings, the deposit warnings as TCKDB words
them, and the chemical subject TCKDB canonicalised (species SMILES and InChIKey, reaction equation). It carries no
other string, text or JSON column, so a depositor's notes and labels cannot reach the model except through the title
and summary, which the prompt marks untrusted. Primary and foreign keys never appear; a record is named only by its
offered ``record_ref``, and a link to another offered record is a ``(record_type, record_ref)`` pair. Curation state
is never read.

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
from sqlalchemy.orm import Session, selectinload

from app.api.errors import not_found
from app.chemistry.level_label import render_level_label
from app.db.models.common import SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.software import SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.workflow import WorkflowToolRelease
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
_MAX_LEVEL_LABEL_CHARS = 160
_MAX_FINDING_CHARS = 240
_MAX_WARNING_CHARS = 400
_MAX_FINDINGS_PER_RECORD = 5
_MAX_WARNINGS_PER_RECORD = 10
_MAX_EVIDENCE_KEYS = 8
_MAX_VALUES_PER_RECORD = 80
_CHARS_PER_TOKEN = 4

_VALUE_TYPES = (Float, Integer, Numeric, Boolean, SAEnum)

#: Result-child relationships whose columns are shown beside the record's own. Curation overlays are not listed.
_CHILD_RELATIONSHIPS = frozenset(
    {
        "sp_result",
        "composite_result",
        "opt_result",
        "freq_result",
        "scan_result",
        "irc_result",
        "path_search_result",
        "scf_stability",
        "hessian",
        "wavefunction_diagnostic",
        "spin_diagnostic",
        "geometry_validation",
        "model_declaration",
        "nasa",
        "wilhoit",
        "applied_group_additivity",
        "falloff",
        "chebyshev",
    }
)

#: Text columns that qualify a number and are constrained by TCKDB, so they are shown with it.
_UNIT_TEXT_COLUMNS = frozenset({("molecular_property_observation", "scalar_unit")})

#: Columns whose name carries no unit, and the key the pack shows them under.
_RENAMED_COLUMNS = {
    ("calc_geometry_validation", "rmsd"): "rmsd_angstrom",
    ("calc_geometry_validation", "rmsd_warning_threshold"): "rmsd_warning_threshold_angstrom",
}

_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

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
    table = mapper.local_table.name
    for column in mapper.columns:
        if column.primary_key or column.foreign_keys:
            continue
        is_unit_text = (table, column.key) in _UNIT_TEXT_COLUMNS
        if not (isinstance(column.type, _VALUE_TYPES) or is_unit_text):
            continue
        value = getattr(row, mapper.get_property_by_column(column).key)
        if value is None:
            continue
        if is_unit_text or isinstance(value, bool):
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
        values[f"{prefix}{_RENAMED_COLUMNS.get((table, column.key), column.key)}"] = value
    return values


def _child_names(model: type) -> list[str]:
    return sorted(
        relationship.key for relationship in sa_inspect(model).relationships if relationship.key in _CHILD_RELATIONSHIPS
    )


def _record_values(row: Any) -> dict[str, float | int | bool | str]:
    values = _own_values(row)
    for name in _child_names(type(row)):
        child = getattr(row, name)
        if child is not None:
            values.update(_own_values(child, prefix=f"{name}."))
    return dict(list(values.items())[:_MAX_VALUES_PER_RECORD])


class _Provenance:
    """Level-of-theory, software and workflow-tool rows for a set of records, fetched once per type."""

    def __init__(self, session: Session, rows: list[Any]) -> None:
        wanted: dict[str, set[int]] = {"level_of_theory": set(), "software_release": set(), "workflow_tool_release": set()}
        for row in rows:
            for _, target, ident in _foreign_keys(row):
                if target in wanted:
                    wanted[target].add(ident)
        self.levels = {
            r.id: r
            for r in session.scalars(select(LevelOfTheory).where(LevelOfTheory.id.in_(wanted["level_of_theory"])))
        }
        self.software = {
            r.id: r
            for r in session.scalars(
                select(SoftwareRelease)
                .where(SoftwareRelease.id.in_(wanted["software_release"]))
                .options(selectinload(SoftwareRelease.software))
            )
        }
        self.tools = {
            r.id: r
            for r in session.scalars(
                select(WorkflowToolRelease)
                .where(WorkflowToolRelease.id.in_(wanted["workflow_tool_release"]))
                .options(selectinload(WorkflowToolRelease.workflow_tool))
            )
        }


def _foreign_keys(row: Any):
    """``(column key, target table, value)`` for each non-null foreign key of the row."""
    mapper = sa_inspect(type(row))
    for column in mapper.columns:
        ident = getattr(row, mapper.get_property_by_column(column).key)
        if ident is None:
            continue
        for foreign_key in column.foreign_keys:
            yield column.key, foreign_key.column.table.name, ident


def _release_label(name: str, version: str | None) -> str:
    return _clip(f"{name} {version}" if version else name, _MAX_LABEL_CHARS)


def _level_label(lot: LevelOfTheory) -> str:
    return _clip(
        render_level_label(
            method=lot.method,
            basis=lot.basis,
            aux_basis=lot.aux_basis,
            cabs_basis=lot.cabs_basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            solvent_model=lot.solvent_model,
            spin_treatment=lot.spin_treatment,
            core_treatment=lot.core_treatment,
            relativistic_hamiltonian=lot.relativistic_hamiltonian,
            pno_truncation=lot.pno_truncation,
            ri_approximation=lot.ri_approximation,
        ),
        _MAX_LEVEL_LABEL_CHARS,
    )


def _referenced_labels(
    provenance: _Provenance, row: Any
) -> tuple[dict[str, str], dict[str, str], bool]:
    """Level-of-theory labels, software and workflow-tool labels, and whether a literature source is cited."""
    levels: dict[str, str] = {}
    software: dict[str, str] = {}
    has_literature = False
    for key, target, ident in _foreign_keys(row):
        if target == "level_of_theory" and ident in provenance.levels:
            levels[key] = _level_label(provenance.levels[ident])
        elif target == "software_release" and ident in provenance.software:
            release = provenance.software[ident]
            software[key] = _release_label(release.software.name, release.version)
        elif target == "workflow_tool_release" and ident in provenance.tools:
            release = provenance.tools[ident]
            software[key] = _release_label(release.workflow_tool.name, release.version)
        elif target == "literature":
            has_literature = True
    return levels, software, has_literature


class _Subjects:
    """The canonical species and reaction a record is about, cached across the pack."""

    def __init__(self, session: Session, offered: set[tuple[str, int]]) -> None:
        self._session = session
        self._offered = offered
        self._tables = {model.__tablename__: record_type.value for record_type, model in RECORD_MODELS.items()}
        self._reactions: dict[int, str | None] = {}

    def _species(self, row: Any) -> Species | None:
        if isinstance(row, Species):
            return row
        species_id = getattr(row, "species_id", None)
        entry_id = getattr(row, "species_entry_id", None)
        if species_id is None and entry_id is not None:
            entry = self._session.get(SpeciesEntry, entry_id)
            species_id = entry.species_id if entry is not None else None
        return self._session.get(Species, species_id) if species_id is not None else None

    def _reaction_equation(self, reaction_id: int) -> str | None:
        if reaction_id not in self._reactions:
            reaction = self._session.get(ChemReaction, reaction_id)
            text = None
            if reaction is not None:
                sides: dict[str, list[str]] = {"reactant": [], "product": []}
                for participant in sorted(reaction.participants, key=lambda p: (p.role.value, p.species_id)):
                    smiles = participant.species.smiles
                    sides[participant.role.value].append(
                        smiles if participant.stoichiometry == 1 else f"{participant.stoichiometry} {smiles}"
                    )
                arrow = " <=> " if reaction.reversible else " => "
                text = _clip(f"{' + '.join(sides['reactant'])}{arrow}{' + '.join(sides['product'])}", 400)
            self._reactions[reaction_id] = text
        return self._reactions[reaction_id]

    def subject(self, row: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        species = self._species(row)
        if species is not None:
            out["smiles"] = species.smiles
            out["inchi_key"] = species.inchi_key
        reaction_id = row.id if isinstance(row, ChemReaction) else getattr(row, "reaction_id", None)
        entry_id = getattr(row, "reaction_entry_id", None)
        if reaction_id is None and entry_id is not None:
            entry = self._session.get(ReactionEntry, entry_id)
            reaction_id = entry.reaction_id if entry is not None else None
        if reaction_id is not None:
            equation = self._reaction_equation(reaction_id)
            if equation:
                out["reaction"] = equation
        return out

    def parents(self, row: Any) -> dict[str, str]:
        """Links to other records of the pack, as ``record_type:record_ref`` under the linking column."""
        out: dict[str, str] = {}
        for key, target, ident in _foreign_keys(row):
            record_type = self._tables.get(target)
            if record_type is not None and (record_type, ident) in self._offered:
                out[key] = f"{record_type}:{ident}"
        return out


def _findings_by_record(
    session: Session, record_type: SubmissionRecordType, ids: list[int]
) -> dict[int, tuple[LLMFindingSummary, ...]]:
    """Findings of the latest row of each check runner per record, most severe first."""
    rows = session.scalars(
        select(RecordMachineReviewRow)
        .where(
            RecordMachineReviewRow.record_type == record_type,
            RecordMachineReviewRow.record_id.in_(ids),
            RecordMachineReviewRow.provider == SCIENTIFIC_CHECK_PROVIDER,
        )
        .order_by(*_latest_first_order_by())
    )
    seen: set[tuple[int, str | None]] = set()
    found: dict[int, list[LLMFindingSummary]] = defaultdict(list)
    for row in rows:
        if (row.record_id, row.model) in seen:
            continue
        seen.add((row.record_id, row.model))
        for item in row.findings_json or []:
            if not isinstance(item, dict) or not isinstance(item.get("message"), str):
                continue
            keys = item.get("evidence_keys")
            found[row.record_id].append(
                LLMFindingSummary(
                    severity=item.get("severity") if isinstance(item.get("severity"), str) else None,
                    category=item.get("category") if isinstance(item.get("category"), str) else None,
                    message=_clip(item["message"], _MAX_FINDING_CHARS),
                    evidence_keys=tuple(
                        _clip(k, _MAX_LABEL_CHARS) for k in (keys if isinstance(keys, list) else [])[:_MAX_EVIDENCE_KEYS] if isinstance(k, str)
                    ),
                )
            )
    return {
        record_id: tuple(
            sorted(items, key=lambda f: _SEVERITY_RANK.get(f.severity or "", len(_SEVERITY_RANK)))[
                :_MAX_FINDINGS_PER_RECORD
            ]
        )
        for record_id, items in found.items()
    }


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
    subjects = _Subjects(session, {(link.record_type.value, link.record_id) for link in links})
    by_type: dict[SubmissionRecordType, list[SubmissionRecordLink]] = defaultdict(list)
    for link in links:
        by_type[link.record_type].append(link)
    out: list[tuple[bool, int, int, LLMRecordSummary]] = []
    for record_type, group in by_type.items():
        model = RECORD_MODELS.get(record_type)
        if model is None:
            continue
        ids = sorted({link.record_id for link in group})
        query = select(model).where(model.id.in_(ids))
        for name in _child_names(model):
            query = query.options(selectinload(getattr(model, name)))
        rows = {row.id: row for row in session.scalars(query)}
        provenance = _Provenance(session, list(rows.values()))
        findings = _findings_by_record(session, record_type, ids)
        warnings = deposit_warnings_for(session, record_type, ids)
        for link in group:
            row = rows.get(link.record_id)
            if row is None:
                continue
            levels, software, has_literature = _referenced_labels(provenance, row)
            values = _record_values(row)
            if has_literature:
                values["has_literature"] = True
            record_findings = findings.get(link.record_id, ())
            record_warnings = tuple(
                LLMWarningSummary(code=item.code, message=_clip(item.message, _MAX_WARNING_CHARS))
                for item in warnings[link.record_id]["items"]
            )[:_MAX_WARNINGS_PER_RECORD]
            summary = LLMRecordSummary(
                record_type=record_type.value,
                record_ref=str(link.record_id),
                subject=subjects.subject(row),
                parents=subjects.parents(row),
                values=values,
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

    def fits(kept_summaries: int, kept_refs: int) -> bool:
        return estimate_input_tokens(pack(kept_summaries, kept_refs)) <= max_input_tokens

    # The estimate only shrinks as more is dropped, so the largest kept count that fits is found by bisection.
    if fits(len(summaries), len(refs)):
        return pack(len(summaries), len(refs))
    if fits(0, len(refs)):
        return pack(_largest_fitting(len(summaries) - 1, lambda kept: fits(kept, len(refs))), len(refs))
    return pack(0, _largest_fitting(len(refs) - 1, lambda kept: fits(0, kept)))


def _largest_fitting(high: int, fits: Any) -> int:
    """The largest ``kept`` in ``[0, high]`` with ``fits(kept)``, given that ``fits(0)`` holds or that 0 is the floor."""
    low = 0
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


__all__ = ["build_review_context_pack", "estimate_input_tokens"]
