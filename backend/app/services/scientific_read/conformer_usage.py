"""Which thermo and kinetics records use each conformer group of a species entry (two bulk statements)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import RecordReviewStatus, SubmissionRecordType, ThermoTargetKind
from app.db.models.kinetics import Kinetics, KineticsInterpretationAssignment
from app.db.models.reaction import ReactionEntry
from app.db.models.record_review import RecordReview
from app.db.models.species import ConformerGroup, ConformerSelection
from app.db.models.thermo import Thermo
from app.schemas.reads.scientific_conformer_usage import (
    ConformerUsageGroup,
    ConformerUsageKinetics,
    ConformerUsageRequestEcho,
    ConformerUsageResponse,
    ConformerUsageThermo,
)
from app.services.scientific_read.common import visible_statuses


def _with_review(stmt, record_type: SubmissionRecordType, id_column):
    """Left-join the review row so the status comes back beside the record; a missing row reads as not reviewed."""
    return stmt.add_columns(RecordReview.status).outerjoin(
        RecordReview, (RecordReview.record_type == record_type) & (RecordReview.record_id == id_column)
    )


def get_conformer_usage(session: Session, *, species_entry_id: int, species_entry_ref: str) -> ConformerUsageResponse:
    """Declared thermo targets and kinetics conformer selections of one entry, under the lists' visibility rule."""
    visible = visible_statuses(min_review_status=None, include_rejected=False, include_deprecated=False)

    thermo_stmt = _with_review(
        select(
            Thermo.public_ref,
            Thermo.thermodynamic_target_kind,
            Thermo.thermodynamic_target_declared_by,
            ConformerGroup.public_ref,
        )
        .outerjoin(ConformerGroup, ConformerGroup.id == Thermo.target_conformer_group_id)
        .where(Thermo.species_entry_id == species_entry_id, Thermo.thermodynamic_target_kind.is_not(None)),
        SubmissionRecordType.thermo,
        Thermo.id,
    )
    kinetics_stmt = _with_review(
        select(
            Kinetics.public_ref,
            ReactionEntry.public_ref,
            KineticsInterpretationAssignment.role,
            KineticsInterpretationAssignment.subject_key,
            ConformerSelection.selection_kind,
            KineticsInterpretationAssignment.ensemble_policy,
            ConformerGroup.public_ref,
        )
        .select_from(KineticsInterpretationAssignment)
        .join(Kinetics, Kinetics.id == KineticsInterpretationAssignment.kinetics_id)
        .join(ReactionEntry, ReactionEntry.id == Kinetics.reaction_entry_id)
        .join(ConformerSelection, ConformerSelection.id == KineticsInterpretationAssignment.conformer_selection_id)
        .join(ConformerGroup, ConformerGroup.id == ConformerSelection.conformer_group_id)
        .where(ConformerGroup.species_entry_id == species_entry_id),
        SubmissionRecordType.kinetics,
        Kinetics.id,
    )

    def shown(status: RecordReviewStatus | None) -> bool:
        return (status or RecordReviewStatus.not_reviewed) in visible

    thermo_by_group: dict[str, list[ConformerUsageThermo]] = {}
    ensemble: list[str] = []
    for ref, kind, declared_by, group_ref, status in session.execute(thermo_stmt).all():
        if not shown(status):
            continue
        if kind is ThermoTargetKind.equilibrium_ensemble:
            ensemble.append(ref)
        elif group_ref is not None:
            thermo_by_group.setdefault(group_ref, []).append(ConformerUsageThermo(thermo_ref=ref, declared_by=declared_by))

    kinetics_by_group: dict[str, list[ConformerUsageKinetics]] = {}
    for ref, reaction_entry_ref, role, subject_key, selection_kind, policy, group_ref, status in session.execute(
        kinetics_stmt
    ).all():
        if shown(status):
            kinetics_by_group.setdefault(group_ref, []).append(
                ConformerUsageKinetics(
                    kinetics_ref=ref,
                    reaction_entry_ref=reaction_entry_ref,
                    role=role,
                    subject_key=subject_key,
                    conformer_selection_kind=selection_kind,
                    ensemble_policy=policy,
                )
            )

    groups = [
        ConformerUsageGroup(
            conformer_group_ref=ref,
            thermo=sorted(thermo_by_group.get(ref, []), key=lambda t: t.thermo_ref),
            kinetics=sorted(kinetics_by_group.get(ref, []), key=lambda k: (k.kinetics_ref, k.subject_key)),
        )
        for ref in sorted(thermo_by_group.keys() | kinetics_by_group.keys())
    ]
    return ConformerUsageResponse(
        request=ConformerUsageRequestEcho(species_entry_ref=species_entry_ref),
        species_entry_ref=species_entry_ref,
        groups=groups,
        ensemble_thermo_refs=sorted(ensemble),
    )
