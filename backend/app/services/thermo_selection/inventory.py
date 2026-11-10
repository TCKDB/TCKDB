"""Read-only coverage inventory: how many stored thermo records can answer H298, and why not.

Runs the same assessor the selection service uses over every stored
thermo record, each against the target *it* declares (an undeclared target is reported as
undeclared, not as a mismatch), and counts. It writes nothing, backfills nothing, and states
no claim for a record that did not state it: an unresolved record stays unresolved and the
reason is counted.

The numbers describe what the stored data supports today, which is the point: they show how
much of the corpus a selection could act on before depositors declare anything more.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import RecordReviewStatus, ScientificOriginKind, SubmissionRecordType, ThermoTargetKind
from app.db.models.thermo import Thermo
from app.services.scientific_read.common import fetch_review_badges
from app.services.scientific_read.thermo import THERMO_TRUST_EAGER_LOADS
from app.services.selection_kernel.evidence_view import evidence_subject
from app.services.structure_selection.source_findings import assess_source_findings, thermo_uses
from app.services.thermo_selection.assessment import assess_candidate
from app.services.thermo_selection.loader import unavailable_evidence
from app.services.thermo_selection.models import MAX_CANDIDATES, Applicability, H298Request
from app.services.trust import evaluate_loaded_thermo


def _batches(session: Session, size: int) -> Iterator[list[int]]:
    last = 0
    while True:
        ids = list(session.scalars(select(Thermo.id).where(Thermo.id > last).order_by(Thermo.id).limit(size)))
        if not ids:
            return
        yield ids
        last = ids[-1]


def _code(reason_code: str) -> str:
    """The reason's stable head (``phase_not_gas:liquid`` counts as ``phase_not_gas``)."""
    return reason_code.split(":", 1)[0]


def h298_coverage_inventory(session: Session, *, batch_size: int = 200) -> dict[str, Any]:
    """Count stored thermo records by H298 applicability and by why they are not applicable. Read-only."""
    applicability: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    blocking: Counter[str] = Counter()
    origin: Counter[str] = Counter()
    answered_by: Counter[str] = Counter()
    review: Counter[str] = Counter()
    total = 0

    for ids in _batches(session, batch_size):
        rows = list(
            session.scalars(select(Thermo).where(Thermo.id.in_(ids)).options(*THERMO_TRUST_EAGER_LOADS).order_by(Thermo.id))
        )
        badges = fetch_review_badges(session, record_type=SubmissionRecordType.thermo, record_ids=ids)
        statuses: dict[int, RecordReviewStatus] = {i: badges[i].status for i in ids}
        unavailable = unavailable_evidence(session, rows)
        source_findings = assess_source_findings(session, {t.id: thermo_uses(t, unavailable) for t in rows})
        for thermo in rows:
            total += 1
            kind = thermo.thermodynamic_target_kind or ThermoTargetKind.equilibrium_ensemble
            group = thermo.target_conformer_group_id if kind is ThermoTargetKind.single_conformer else None
            request = H298Request(target_kind=kind, conformer_group_id=group, max_candidates=MAX_CANDIDATES)
            evidence = evaluate_loaded_thermo(evidence_subject(thermo, unavailable)) if thermo.scientific_origin is ScientificOriginKind.computed else None
            assessment = assess_candidate(
                thermo, request=request, evidence=evidence, source_findings=source_findings[thermo.id]
            )
            applicability[assessment.applicability.value] += 1
            for reason in assessment.reasons:
                reasons[_code(reason.code)] += 1
            for item in assessment.blocking:
                blocking[_code(item)] += 1
            origin[thermo.scientific_origin.value] += 1
            review[statuses[thermo.id].value] += 1
            if assessment.answer_representation is not None:
                answered_by[assessment.answer_representation] += 1

    entries_over_cap = session.scalar(
        select(func.count()).select_from(
            select(Thermo.species_entry_id).group_by(Thermo.species_entry_id).having(func.count() > MAX_CANDIDATES).subquery()
        )
    )
    return {
        "thermo_records": total,
        "by_origin": dict(sorted(origin.items())),
        "by_review_status": dict(sorted(review.items())),
        "h298_applicability": {a.value: applicability.get(a.value, 0) for a in Applicability},
        "h298_not_applicable_reasons": dict(sorted(reasons.items())),
        "h298_blocking_validation": dict(sorted(blocking.items())),
        "h298_answered_by_representation": dict(sorted(answered_by.items())),
        "species_entries_over_candidate_cap": int(entries_over_cap or 0),
        "candidate_cap": MAX_CANDIDATES,
        "notes": [
            "Each record is assessed against the target it declares; an undeclared target is counted as thermodynamic_target_not_declared.",
        ],
    }
