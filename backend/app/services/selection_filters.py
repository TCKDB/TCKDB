"""Step 1 of a select call: the caller's filters, applied with the browse's own matching.

A select request narrows the candidates by facts before anything is judged. The filters use the vocabulary and the
matching code of the matching browse or search endpoint:

* method and basis compare by identity key (:mod:`app.services.scientific_read.lot_identity_filters`);
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

from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.services.scientific_read.common import ids_withheld_as_evidence, software_names_by_calculation
from app.services.scientific_read.handles import NO_MATCH, reconcile_level_of_theory_pair
from app.services.scientific_read.lot_identity_filters import basis_matches, method_matches


def shown_calculations(session: Session, calculation_ids: Collection[int]) -> set[int]:
    """Which of these calculations may take part in a match: those the evidence rule does not withhold (all of them
    without a review floor)."""
    ids = set(calculation_ids)
    return ids - ids_withheld_as_evidence(session, record_type=SubmissionRecordType.calculation, record_ids=ids)


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
) -> set[int]:
    """The calculations among *calculation_ids* matching EVERY supplied constraint (one calculation, all fields)."""
    ids = set(calculation_ids)
    if not ids:
        return set()
    stmt = select(Calculation.id).where(Calculation.id.in_(ids))
    if method is not None or basis is not None:
        stmt = stmt.join(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        if method is not None:
            stmt = stmt.where(method_matches(method))
        if basis is not None:
            stmt = stmt.where(basis_matches(basis))
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
) -> set[int]:
    """The candidate ids kept by method/basis/software/workflow filters (all kept when none is supplied).

    *calculations_of* maps each candidate id to the ids of the source calculations it rests on.
    """
    constraints = {
        "method": method,
        "basis": basis,
        "software": software,
        "software_version": software_version,
        "workflow_tool": workflow_tool,
        "workflow_tool_version": workflow_tool_version,
    }
    if all(v is None for v in constraints.values()):
        return set(calculations_of)
    every = {c for calcs in calculations_of.values() for c in calcs}
    shown = shown_calculations(session, every)
    matching = calculations_matching(session, shown, **constraints)
    return candidates_with_a_match(calculations_of, matching, shown)


def filter_by_level_and_software(
    session: Session,
    calculations_of: Mapping[int, Collection[int]],
    *,
    level_of_theory_ref: str | None = None,
    software: str | None = None,
) -> set[int]:
    """The per-entry thermo/kinetics browse's two independent provenance filters.

    A candidate is kept when ANY shown source calculation ran at the level of theory and ANY shown source calculation ran
    in a software of that name; the two need not be one calculation. An unknown level-of-theory ref keeps nothing.
    """
    keep = set(calculations_of)
    if level_of_theory_ref is None and software is None:
        return keep
    every = {c for calcs in calculations_of.values() for c in calcs}
    shown = shown_calculations(session, every)
    if level_of_theory_ref is not None:
        lot_id = reconcile_level_of_theory_pair(session, id_value=None, ref_value=level_of_theory_ref)
        if lot_id is NO_MATCH or lot_id is None:
            return set()
        at_level = set(session.scalars(select(Calculation.id).where(Calculation.id.in_(shown), Calculation.lot_id == lot_id)))
        keep &= candidates_with_a_match(calculations_of, at_level, shown)
    if software is not None:
        names = software_names_by_calculation(session, shown)
        in_software = {c for c, name in names.items() if name == software}
        keep &= candidates_with_a_match(calculations_of, in_software, shown)
    return keep
