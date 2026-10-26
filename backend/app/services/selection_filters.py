"""Step 1 of a select call: the caller's filters, applied with the browse's own matching.

A select request narrows the candidates by facts before anything is judged. The filters use the vocabulary and the
matching code of the matching browse or search endpoint:

* method and basis compare by identity key (:mod:`app.services.scientific_read.lot_identity_filters`);
* a method family or DFT rung classifies the calculation's method (:mod:`app.chemistry.method_families`). It is a
  classification of what kind of method it is, not a measure of accuracy, and it is a filter fact only;
* software, workflow tool and their versions match through the calculation's release rows;
* a level of theory is a public ref, resolved to its canonical row (a merged row to the row it was merged into);
* a calculation the evidence rule withholds (rejected or deprecated under the curated profile, ADR 0023) contributes
  nothing to a match; a not-reviewed or under-review one matches, with its badge.

A filter excludes on a fact. It never ranks, and a candidate it keeps is not thereby better than one it removed.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.method_families import MethodClassFilter, MethodFamily
from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.services.scientific_read.common import ids_withheld_as_evidence
from app.services.scientific_read.handles import NO_MATCH, reconcile_level_of_theory_pair
from app.services.scientific_read.lot_identity_filters import basis_matches, method_family_matches, method_matches


def shown_calculations(session: Session, calculation_ids: Collection[int]) -> set[int]:
    """Which of these calculations may take part in a match: those the evidence rule does not withhold (all of them
    without a review floor)."""
    ids = set(calculation_ids)
    return ids - ids_withheld_as_evidence(session, record_type=SubmissionRecordType.calculation, record_ids=ids)


def _class_filter(
    method_family: Collection[MethodFamily] | None, dft_rung_min: int | None, dft_rung_max: int | None
) -> MethodClassFilter:
    return MethodClassFilter.of(method_family, dft_rung_min, dft_rung_max)


def calculations_matching(
    session: Session,
    calculation_ids: Collection[int],
    *,
    method: str | None = None,
    basis: str | None = None,
    software: str | None = None,
    software_version: str | None = None,
    workflow_tool: str | None = None,
    workflow_tool_version: str | None = None,
    level_of_theory_id: int | None = None,
    method_family: Collection[MethodFamily] | None = None,
    dft_rung_min: int | None = None,
    dft_rung_max: int | None = None,
) -> set[int]:
    """The calculations among *calculation_ids* matching EVERY supplied constraint (one calculation, all fields)."""
    ids = set(calculation_ids)
    if not ids:
        return set()
    stmt = select(Calculation.id).where(Calculation.id.in_(ids))
    classification = _class_filter(method_family, dft_rung_min, dft_rung_max)
    if level_of_theory_id is not None:
        stmt = stmt.where(Calculation.lot_id == level_of_theory_id)
    if method is not None or basis is not None or classification.active:
        stmt = stmt.join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        if method is not None:
            stmt = stmt.where(method_matches(method))
        if basis is not None:
            stmt = stmt.where(basis_matches(basis))
        if classification.active:
            stmt = stmt.where(method_family_matches(classification))
    if software is not None or software_version is not None:
        stmt = stmt.join(SoftwareRelease, SoftwareRelease.id == Calculation.software_release_id).join(
            Software, Software.id == SoftwareRelease.software_id
        )
        if software is not None:
            stmt = stmt.where(Software.name == software)
        if software_version is not None:
            stmt = stmt.where(SoftwareRelease.version == software_version)
    if workflow_tool is not None or workflow_tool_version is not None:
        stmt = stmt.join(WorkflowToolRelease, WorkflowToolRelease.id == Calculation.workflow_tool_release_id).join(
            WorkflowTool, WorkflowTool.id == WorkflowToolRelease.workflow_tool_id
        )
        if workflow_tool is not None:
            stmt = stmt.where(WorkflowTool.name == workflow_tool)
        if workflow_tool_version is not None:
            stmt = stmt.where(WorkflowToolRelease.version == workflow_tool_version)
    return set(session.scalars(stmt))


def candidates_with_a_match(
    calculations_of: Mapping[int, Collection[int]], matching: Collection[int], shown: Collection[int]
) -> set[int]:
    """Candidates with at least one SHOWN calculation among *matching* (OR across the candidate's calculations)."""
    keep = set(matching) & set(shown)
    return {cid for cid, calcs in calculations_of.items() if keep.intersection(calcs)}


def filter_by_provenance(
    session: Session,
    calculations_of: Mapping[int, Collection[int]],
    *,
    method: str | None = None,
    basis: str | None = None,
    software: str | None = None,
    software_version: str | None = None,
    workflow_tool: str | None = None,
    workflow_tool_version: str | None = None,
    level_of_theory_id: int | None = None,
    method_family: Collection[MethodFamily] | None = None,
    dft_rung_min: int | None = None,
    dft_rung_max: int | None = None,
) -> set[int]:
    """The candidate ids kept by the provenance filters (all kept when none is supplied).

    *calculations_of* maps each candidate id to the ids of the source calculations it rests on. ONE shown source
    calculation has to match every supplied constraint at once; a record never matches through two different
    calculations. *level_of_theory_id* is a resolved, canonical id (:func:`resolve_level_of_theory_ref`).
    """
    constraints = {
        "method": method,
        "basis": basis,
        "software": software,
        "software_version": software_version,
        "workflow_tool": workflow_tool,
        "workflow_tool_version": workflow_tool_version,
        "level_of_theory_id": level_of_theory_id,
        "method_family": method_family,
        "dft_rung_min": dft_rung_min,
        "dft_rung_max": dft_rung_max,
    }
    if all(v is None for v in constraints.values()):
        return set(calculations_of)
    every = {c for calcs in calculations_of.values() for c in calcs}
    shown = shown_calculations(session, every)
    matching = calculations_matching(session, shown, **constraints)
    return candidates_with_a_match(calculations_of, matching, shown)


def resolve_level_of_theory_ref(session: Session, ref: str) -> int | None:
    """The canonical level-of-theory id a public ref names (a merged row names the row it was merged into).

    ``None`` when the ref names no level: a filter on it keeps nothing.
    """
    resolved = reconcile_level_of_theory_pair(session, id_value=None, ref_value=ref)
    if resolved is NO_MATCH or resolved is None:
        return None
    return resolved


def source_provenance_kwargs(request: object, *, level_of_theory_id: int | None = None) -> dict[str, object]:
    """The provenance constraints a thermo or kinetics read request states, as :func:`filter_by_provenance` keywords.

    Only the supplied ones: an empty dict means the request filters nothing by provenance. *level_of_theory_id* is the
    request's level of theory already resolved to one canonical id.
    """
    fields = (
        "method",
        "basis",
        "software",
        "software_version",
        "workflow_tool",
        "workflow_tool_version",
        "method_family",
        "dft_rung_min",
        "dft_rung_max",
    )
    out: dict[str, object] = {name: getattr(request, name) for name in fields if getattr(request, name, None) is not None}
    if level_of_theory_id is not None:
        out["level_of_theory_id"] = level_of_theory_id
    return out
