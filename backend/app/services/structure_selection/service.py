"""Structure assessment: scan, load, assess.

Service layer only. No HTTP, no persistence: the function reads and returns a result. Browse order and every
existing read are untouched. Ordering and outcomes (cohorts, repeats, fronts) are the stage built on this result;
here the question is only which units answer the request and what each finding says.

Flow: open one read-only REPEATABLE READ snapshot, scan the visible population (review floor first, rejected and
deprecated out), refuse above a bound with a coded 422 before anything is loaded, load and normalise it, assess
every unit.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.structure_determination import StructureDetermination
from app.services.read_snapshot import begin_read_snapshot
from app.services.selection_kernel import Applicability
from app.services.structure_selection.assessment import assess_unit
from app.services.structure_selection.loader import (
    MAX_EXCLUDED_LISTED,
    PopulationScan,
    load_population,
    scan_population,
)
from app.services.structure_selection.models import (
    Grain,
    StructureAssessment,
    StructureAssessmentResult,
    StructureRequest,
)


def _excluded_refs(session: Session, scan: PopulationScan) -> tuple[dict[str, str], ...]:
    """The first ``MAX_EXCLUDED_LISTED`` excluded units by id, named by public ref; the total is reported separately."""
    listed = scan.excluded[:MAX_EXCLUDED_LISTED]
    if not listed:
        return ()
    model = Calculation if scan.grain is Grain.calculation else StructureDetermination
    refs: dict[int, str] = dict(
        session.execute(select(model.id, model.public_ref).where(model.id.in_([i for i, _, _ in listed]))).tuples().all()
    )
    return tuple(
        {"unit_ref": refs[i], "review_status": status.value if status is not None else "", "reason": reason}
        for i, status, reason in listed
    )


def assess_entry_structures(
    session: Session, *, entry_id: int, request: StructureRequest, require_snapshot: bool = True
) -> StructureAssessmentResult:
    """Assess every visible unit of an entry for the request.

    :param entry_id: The species entry (calculation and conformer grains) or transition state entry.
    :param require_snapshot: Insist that the read runs in one read-only REPEATABLE READ snapshot, so the population
        and everything loaded for it are one consistent state (``SnapshotNotConsistentError`` otherwise). A route
        opens a session for the purpose; only a caller that cannot (a test inside one outer transaction) passes
        ``False``, and the isolation level actually in force is reported on the result either way.
    :raises NotFoundError: unknown or hidden entry, or an explicit member naming nothing authorized.
    :raises CodedValueError: a bound exceeded; nothing is assessed.
    """
    isolation = begin_read_snapshot(session, require=require_snapshot)
    scan = scan_population(session, entry_id=entry_id, request=request)
    excluded = _excluded_refs(session, scan)
    loaded = load_population(session, scan, request)

    calculations_by_ref = loaded.calculations_by_ref
    units = (
        [loaded.calculations[i] for i in sorted(loaded.calculations)]
        if request.grain is Grain.calculation
        else [loaded.determinations[i] for i in sorted(loaded.determinations)]
    )
    assessments: list[StructureAssessment] = [
        assess_unit(u, request=request, subject=loaded.subject, calculations=calculations_by_ref) for u in units
    ]
    unresolved = tuple(a.unit_ref for a in assessments if a.applicability is Applicability.unresolved)
    unsupported = tuple(a.unit_ref for a in assessments if a.applicability is Applicability.unsupported)
    notes: list[str] = []
    if unresolved or unsupported:
        # Uniform across every outcome: a result is scoped to what is eligible now, and these units might compete if
        # their missing facts were recorded or their form evaluated.
        notes.append(
            f"{len(unresolved)} unit(s) are unresolved and {len(unsupported)} unsupported; they do not compete in this "
            "result and may be applicable if their missing facts were recorded or their form evaluated"
        )
    return StructureAssessmentResult(
        request=request,
        subject=loaded.subject,
        effective_statuses=tuple(sorted(scan.effective_statuses, key=lambda s: s.value)),
        total_units=scan.total_units,
        visible_units=len(scan.unit_ids),
        excluded_by_review=excluded,
        excluded_count=len(scan.excluded),
        nested_rows=scan.nested_rows,
        snapshot_isolation=isolation,
        calculations=tuple(loaded.calculations[i] for i in sorted(loaded.calculations)),
        determinations=tuple(loaded.determinations[i] for i in sorted(loaded.determinations)),
        assessments=tuple(assessments),
        unresolved_refs=unresolved,
        unsupported_refs=unsupported,
        notes=tuple(notes),
    )
