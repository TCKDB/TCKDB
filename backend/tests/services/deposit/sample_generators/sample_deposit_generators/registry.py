"""A small generator registry for the deposit tests, importable from its own root."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationFreqMode, CalculationHessian
from app.db.models.common import SubmissionRecordType
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import SubmissionRecordLink
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from sample_deposit_generators.helpers.counting import count_rows


def row_counts(session: Session) -> dict[str, Any]:
    return {
        "species": count_rows(session, Species),
        "species_entries": count_rows(session, SpeciesEntry),
    }


def thermo_values(session: Session) -> dict[str, Any]:
    rows = session.execute(select(Thermo.h298_kj_mol).order_by(Thermo.h298_kj_mol)).all()
    return {"h298_kj_mol": [row[0] for row in rows]}


def transition_state_evidence(session: Session) -> dict[str, Any]:
    """TS entries, those with exactly one imaginary mode, and those with a stored Hessian."""
    one_imaginary = 0
    with_hessian = 0
    entries = list(session.scalars(select(TransitionStateEntry)))
    for entry in entries:
        calculations = list(
            session.scalars(select(Calculation).where(Calculation.transition_state_entry_id == entry.id))
        )
        imaginary = [
            session.scalar(
                select(func.count())
                .select_from(CalculationFreqMode)
                .where(CalculationFreqMode.calculation_id == calc.id, CalculationFreqMode.is_imaginary.is_(True))
            )
            for calc in calculations
        ]
        one_imaginary += 1 if 1 in imaginary else 0
        stored = session.scalar(
            select(func.count())
            .select_from(CalculationHessian)
            .where(CalculationHessian.calculation_id.in_([calc.id for calc in calculations]))
        )
        with_hessian += 1 if stored else 0
    return {
        "transition_state_entries": len(entries),
        "with_exactly_one_imaginary_mode": one_imaginary,
        "with_stored_hessian": with_hessian,
    }


def candidate_lineage(session: Session) -> dict[str, Any]:
    """Per species entry with more than one thermo candidate: candidates and distinct submissions."""
    rows: list[dict[str, Any]] = []
    for entry_id, count in session.execute(
        select(Thermo.species_entry_id, func.count()).group_by(Thermo.species_entry_id)
    ):
        if count < 2:
            continue
        thermo_ids = list(session.scalars(select(Thermo.id).where(Thermo.species_entry_id == entry_id)))
        submissions = session.scalar(
            select(func.count(func.distinct(SubmissionRecordLink.submission_id))).where(
                SubmissionRecordLink.record_type == SubmissionRecordType.thermo,
                SubmissionRecordLink.record_id.in_(thermo_ids),
            )
        )
        rows.append(
            {
                "species_entry_ref": session.get(SpeciesEntry, entry_id).public_ref,
                "candidate_count": count,
                "distinct_submissions": submissions,
            }
        )
    rows.sort(key=lambda row: row["species_entry_ref"])
    return {"lineage": rows}


GENERATORS = {
    "row_counts": row_counts,
    "thermo_values": thermo_values,
    "transition_state_evidence": transition_state_evidence,
    "candidate_lineage": candidate_lineage,
}
