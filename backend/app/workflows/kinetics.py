from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.units import convert_ea_to_kj_mol
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    SubmissionRecordType,
)
from app.db.models.kinetics import (
    Kinetics,
    KineticsArrheniusEntry,
    KineticsChebyshev,
    KineticsFalloff,
    KineticsPlog,
    KineticsSourceCalculation,
    KineticsThirdBodyEfficiency,
)
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.services.calculation_resolution import resolve_level_of_theory_ref
from app.services.kinetics_resolution import persist_kinetics, resolve_kinetics_upload
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species, resolve_species_entry
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
    review_policy: ReviewPolicy | None = ReviewPolicy(),
) -> Kinetics:
    """Persist a complete kinetics upload workflow.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing kinetics upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Kinetics`` row attached to a backend-resolved reaction entry.
    """

    # 1. Resolve reaction
    #    Pass the same review_policy so the reaction_entry created en route is
    #    captured in the same review state as the kinetics row this workflow
    #    is producing.
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
        review_policy=review_policy,
    )

    # 2. Create kinetics record
    kinetics_create = resolve_kinetics_upload(
        session,
        request,
        reaction_entry_id=reaction_entry.id,
    )
    kinetics = persist_kinetics(session, kinetics_create, created_by=created_by)

    # 2b. Pressure-dependent falloff + third-body efficiencies (DR-0032 B).
    if request.falloff is not None:
        f = request.falloff
        session.add(
            KineticsFalloff(
                kinetics_id=kinetics.id,
                low_a=f.low_a,
                low_a_units=f.low_a_units,
                low_n=f.low_n,
                low_ea_kj_mol=f.low_ea_kj_mol,
                troe_alpha=f.troe_alpha,
                troe_t3=f.troe_t3,
                troe_t1=f.troe_t1,
                troe_t2=f.troe_t2,
                sri_a=f.sri_a,
                sri_b=f.sri_b,
                sri_c=f.sri_c,
                sri_d=f.sri_d,
                sri_e=f.sri_e,
                note=f.note,
            )
        )
    for tb in request.third_body_efficiencies:
        collider = resolve_species(session, tb.collider)
        session.add(
            KineticsThirdBodyEfficiency(
                kinetics_id=kinetics.id,
                collider_species_id=collider.id,
                efficiency=tb.efficiency,
            )
        )
    # 2c. Standalone PLOG / Chebyshev fits (DR-0032 Part C).
    for entry in request.plog_entries:
        session.add(
            KineticsPlog(
                kinetics_id=kinetics.id,
                entry_index=entry.entry_index,
                pressure_bar=entry.pressure_bar,
                a=entry.a,
                a_units=entry.a_units,
                n=entry.n,
                ea_kj_mol=entry.ea_kj_mol,
            )
        )
    # 2d. Sum-of-Arrhenius (Chemkin DUPLICATE) terms (DR-0036).
    for term in request.arrhenius_entries:
        session.add(
            KineticsArrheniusEntry(
                kinetics_id=kinetics.id,
                entry_index=term.entry_index,
                a=term.a,
                a_units=term.a_units,
                n=term.n,
                ea_kj_mol=(
                    convert_ea_to_kj_mol(term.reported_ea, term.reported_ea_units)
                    if term.reported_ea is not None
                    else None
                ),
            )
        )
    if request.chebyshev is not None:
        c = request.chebyshev
        session.add(
            KineticsChebyshev(
                kinetics_id=kinetics.id,
                n_temperature=c.n_temperature,
                n_pressure=c.n_pressure,
                tmin_k=c.tmin_k,
                tmax_k=c.tmax_k,
                pmin_bar=c.pmin_bar,
                pmax_bar=c.pmax_bar,
                coefficients=c.coefficients,
            )
        )
    if (
        request.falloff is not None
        or request.third_body_efficiencies
        or request.plog_entries
        or request.arrhenius_entries
        or request.chebyshev is not None
    ):
        session.flush()

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

    apply_review_policy(
        session,
        targets=[
            RecordRef(SubmissionRecordType.kinetics, kinetics.id),
            RecordRef(SubmissionRecordType.reaction_entry, reaction_entry.id),
        ],
        policy=review_policy,
        created_by=created_by,
    )

    return kinetics
