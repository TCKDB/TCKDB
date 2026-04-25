from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, KineticsCalculationRole
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.services.calculation_resolution import resolve_level_of_theory_ref
from app.services.kinetics_resolution import persist_kinetics, resolve_kinetics_upload
from app.services.species_resolution import resolve_species_entry
from app.workflows.reaction import persist_reaction_upload


def _find_sp_for_species(
    session: Session,
    *,
    species_entry_id: int,
    lot_id: int,
) -> Calculation:
    """Find exactly one SP calculation for a species entry at a given LOT.

    :raises ValueError: If zero or multiple matches are found.
    """
    results = session.scalars(
        select(Calculation)
        .where(
            Calculation.species_entry_id == species_entry_id,
            Calculation.type == CalculationType.sp,
            Calculation.lot_id == lot_id,
        )
        .order_by(Calculation.id)
        .limit(2)
    ).all()

    if len(results) == 0:
        raise ValueError(
            f"No SP calculation found for species_entry {species_entry_id} "
            f"at the declared energy level of theory. "
            f"Upload the conformer with the SP as an additional calculation first."
        )
    if len(results) > 1:
        raise ValueError(
            f"Multiple SP calculations found for species_entry {species_entry_id} "
            f"at the declared energy level of theory. "
            f"Cannot auto-resolve — multi-conformer disambiguation not yet supported."
        )
    return results[0]


def persist_kinetics_upload(
    session: Session,
    request: KineticsUploadRequest,
    *,
    created_by: int | None = None,
) -> Kinetics:
    """Persist a complete kinetics upload workflow.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing kinetics upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Kinetics`` row attached to a backend-resolved reaction entry.
    """

    # 1. Resolve reaction
    reaction_entry = persist_reaction_upload(
        session,
        ReactionUploadRequest(
            reversible=request.reaction.reversible,
            reaction_family=request.reaction.reaction_family,
            reaction_family_source_note=request.reaction.reaction_family_source_note,
            reactants=[
                {
                    "species_entry": participant.species_entry,
                    "note": participant.note,
                }
                for participant in request.reaction.reactants
            ],
            products=[
                {
                    "species_entry": participant.species_entry,
                    "note": participant.note,
                }
                for participant in request.reaction.products
            ],
        ),
        created_by=created_by,
    )

    # 2. Create kinetics record
    kinetics_create = resolve_kinetics_upload(
        session,
        request,
        reaction_entry_id=reaction_entry.id,
    )
    kinetics = persist_kinetics(session, kinetics_create, created_by=created_by)

    # 3. Auto-resolve source calculations from energy_level_of_theory
    #    For each reaction participant, find the SP at that LOT and link it.
    if request.energy_level_of_theory is not None:
        lot = resolve_level_of_theory_ref(session, request.energy_level_of_theory)

        # Reactant SPs
        for participant in request.reaction.reactants:
            species_entry = resolve_species_entry(
                session, participant.species_entry, created_by=created_by
            )
            calc = _find_sp_for_species(
                session, species_entry_id=species_entry.id, lot_id=lot.id
            )
            session.add(
                KineticsSourceCalculation(
                    kinetics_id=kinetics.id,
                    calculation_id=calc.id,
                    role=KineticsCalculationRole.reactant_energy,
                )
            )

        # Product SPs
        for participant in request.reaction.products:
            species_entry = resolve_species_entry(
                session, participant.species_entry, created_by=created_by
            )
            calc = _find_sp_for_species(
                session, species_entry_id=species_entry.id, lot_id=lot.id
            )
            session.add(
                KineticsSourceCalculation(
                    kinetics_id=kinetics.id,
                    calculation_id=calc.id,
                    role=KineticsCalculationRole.product_energy,
                )
            )

        session.flush()

    return kinetics
