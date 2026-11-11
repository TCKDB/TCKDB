"""Reaction energy profiles: reactants, transition state and products at one level of theory and one program.

The energy lookups here are shared with the ML export's electronic barrier (:mod:`.ml_dataset`), so both read the same
definition of "the energy of a molecule at a level". A profile never mixes programs: its points share one level of theory
and one software name, and a transition-state entry without such a complete set is listed under ``unavailable`` with a code.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.units import HARTREE_TO_KJ_MOL
from app.db.models.calculation import Calculation, CalculationOptResult, CalculationSPResult
from app.db.models.common import KineticsCalculationRole, ReactionRole, RecordReviewStatus, SubmissionRecordType
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import SpeciesEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.reads.scientific_common import REVIEW_RANK, LevelOfTheorySummary
from app.schemas.reads.scientific_reaction_energy_profile import (
    CitedCalculation,
    EnergyProfileMolecule,
    EnergyProfileRequestEcho,
    EnergyProfileSoftware,
    EnergyProfileTransitionState,
    EnergyProfileUnavailable,
    EnergyProfileUnavailableCode,
    EnergyProfileValues,
    KineticsCitedEnergies,
    ReactionEnergyProfile,
    ReactionEnergyProfileResponse,
)
from app.services.scientific_read.calculations import _build_lot_summary
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_floor_predicate,
    visible_statuses,
    where_visible,
)
from app.services.scientific_read.hidden_by_default import (
    hidden_by_default_from_statuses,
    record_hidden_by_default,
)
from app.services.scientific_read.profile import ResolvedReadProfile


def ml_energy_row_floor(profile: ResolvedReadProfile) -> Any:
    """The review floor on the ML export's ENERGY ROWS: a named exception to the evidence rule (owner decision Q7, ADR 0023).

    The ML export's rows are calculations (a geometry with the energies, frequencies and Hessian computed at it), so a
    calculation is a RECORD of the dataset, not linked evidence of another record, and it keeps the ``approved`` floor under
    ``curated``: a row is left out whatever its review state below it, and the barrier and reactant sums read only those rows.
    Everything else nested in an ML record follows the evidence rule (conformer observations and groups:
    :func:`_conformer_refs`; the listed thermo, kinetics and transition-state records keep the record floor as in every list).
    Dataset releases are unchanged. ``None`` without a review floor (exploratory), so nothing is added to the statement.
    """
    return review_floor_predicate(Calculation.id, SubmissionRecordType.calculation, profile=profile)


def species_entry_energy_at_lot(
    session: Session, species_entry_id: int, lot_id: int, profile: ResolvedReadProfile
) -> tuple[float, str, str] | None:
    """Lowest electronic energy for a species entry at ONE level of theory, with where it came from.

    Returns ``(energy_hartree, energy_type, calculation_ref)`` or ``None``. Single-point energies are used before
    optimisation final energies, and the lowest value wins (the lowest-conformer convention, inside one level). Used to
    assemble a same-level reactant energy sum for the electronic barrier; the barrier record carries the calculation
    each reactant energy came from.
    """
    shown = ml_energy_row_floor(profile)
    sp_rows = session.execute(
        where_visible(
            select(CalculationSPResult.electronic_energy_hartree, Calculation.public_ref)
            .join(Calculation, Calculation.id == CalculationSPResult.calculation_id)
            .where(
                Calculation.species_entry_id == species_entry_id,
                Calculation.lot_id == lot_id,
                CalculationSPResult.electronic_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    if sp_rows:
        energy, ref = min(sp_rows, key=lambda r: (r[0], r[1]))
        return energy, "single_point", ref
    opt_rows = session.execute(
        where_visible(
            select(CalculationOptResult.final_energy_hartree, Calculation.public_ref)
            .join(Calculation, Calculation.id == CalculationOptResult.calculation_id)
            .where(
                Calculation.species_entry_id == species_entry_id,
                Calculation.lot_id == lot_id,
                CalculationOptResult.final_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    if opt_rows:
        energy, ref = min(opt_rows, key=lambda r: (r[0], r[1]))
        return energy, "optimization", ref
    return None


def ts_calculation_energies(
    session: Session, ts_entry_id: int, profile: ResolvedReadProfile
) -> list[tuple[float, str, Calculation]]:
    """EVERY electronic energy of EVERY shown calculation of one TS entry: ``(energy_hartree, energy_type, calculation)``.

    Nothing is dropped and nothing is chosen: two single points at one level are two entries, and an optimisation final
    energy is an entry beside them (as the species-side ``energies`` list is). Callers group by level of theory; the
    lowest-within-a-level convention is applied only to compute a barrier, and each barrier names the calculation it
    used.
    """
    # Hidden calculations are not candidates: their energies would otherwise set the barrier.
    shown = ml_energy_row_floor(profile)
    sp_rows = session.execute(
        where_visible(
            select(CalculationSPResult.electronic_energy_hartree, Calculation)
            .join(Calculation, Calculation.id == CalculationSPResult.calculation_id)
            .where(
                Calculation.transition_state_entry_id == ts_entry_id,
                CalculationSPResult.electronic_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    opt_rows = session.execute(
        where_visible(
            select(CalculationOptResult.final_energy_hartree, Calculation)
            .join(Calculation, Calculation.id == CalculationOptResult.calculation_id)
            .where(
                Calculation.transition_state_entry_id == ts_entry_id,
                CalculationOptResult.final_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    return [(e, "single_point", c) for e, c in sp_rows] + [(e, "optimization", c) for e, c in opt_rows]


def ts_entries(
    session: Session, reaction_entry_id: int, profile: ResolvedReadProfile
) -> list[TransitionStateEntry]:
    """EVERY transition-state entry of a reaction entry that the profile shows, in DISPLAY order.

    Review rank, then deposition time newest first, then id descending (``docs/specs/browse_order.md``): a listing
    order, never a pick; all entries are returned. A hidden entry is not in the population, so it is neither listed nor
    named.
    """
    entries = list(
        session.scalars(
            where_visible(
                select(TransitionStateEntry)
                .join(
                    TransitionState,
                    TransitionState.id == TransitionStateEntry.transition_state_id,
                )
                .where(TransitionState.reaction_entry_id == reaction_entry_id),
                review_floor_predicate(
                    TransitionStateEntry.id, SubmissionRecordType.transition_state_entry, profile=profile
                ),
            )
        ).all()
    )
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_ids=[e.id for e in entries],
    )
    entries.sort(key=lambda e: (REVIEW_RANK[badges[e.id].status], -e.created_at.timestamp(), -e.id))
    return entries


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Point:
    energy: float
    energy_type: str
    calculation_ref: str
    lot_id: int
    software: EnergyProfileSoftware


def _lowest(points: list[_Point]) -> _Point:
    """Single points before optimisation energies; the lowest energy wins, ties by calculation ref."""
    single_points = [p for p in points if p.energy_type == "single_point"]
    return min(single_points or points, key=lambda p: (p.energy, p.calculation_ref))


def _owner_points(
    session: Session, *, species_entry_ids: list[int], ts_entry_ids: list[int], profile: ResolvedReadProfile
) -> tuple[dict[int, list[_Point]], dict[int, list[_Point]]]:
    """Every electronic energy with a level and a program, for all owners at once (one statement per energy table).

    A calculation with no recorded software release is left out: its program is unknown, so it cannot share one.
    """
    shown = ml_energy_row_floor(profile)
    by_species: dict[int, list[_Point]] = defaultdict(list)
    by_ts: dict[int, list[_Point]] = defaultdict(list)
    owner = Calculation.species_entry_id.in_(species_entry_ids) | Calculation.transition_state_entry_id.in_(ts_entry_ids)
    for energy_type, result_model, energy_column in (
        ("single_point", CalculationSPResult, CalculationSPResult.electronic_energy_hartree),
        ("optimization", CalculationOptResult, CalculationOptResult.final_energy_hartree),
    ):
        stmt = where_visible(
            select(
                Calculation.species_entry_id,
                Calculation.transition_state_entry_id,
                energy_column,
                Calculation.public_ref,
                Calculation.lot_id,
                Software.name,
                SoftwareRelease.version,
                SoftwareRelease.revision,
                SoftwareRelease.build,
            )
            .join(Calculation, Calculation.id == result_model.calculation_id)
            .join(SoftwareRelease, SoftwareRelease.id == Calculation.software_release_id)
            .join(Software, Software.id == SoftwareRelease.software_id)
            .where(owner, Calculation.lot_id.is_not(None), energy_column.is_not(None)),
            shown,
        )
        for se_id, ts_id, energy, ref, lot_id, name, version, revision, build in session.execute(stmt).all():
            point = _Point(
                energy,
                energy_type,
                ref,
                lot_id,
                EnergyProfileSoftware(program=name, version=version, revision=revision, build=build),
            )
            if se_id is not None:
                by_species[se_id].append(point)
            if ts_id is not None:
                by_ts[ts_id].append(point)
    return by_species, by_ts


def _participants(session: Session, reaction_entry_id: int) -> dict[ReactionRole, list[tuple[int, str, int]]]:
    """``(species_entry_id, ref, count)`` per side, in slot order; a species listed twice is one row with count 2."""
    rows = session.execute(
        select(ReactionEntryStructureParticipant.role, SpeciesEntry.id, SpeciesEntry.public_ref)
        .join(SpeciesEntry, SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id)
        .where(ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id)
        .order_by(ReactionEntryStructureParticipant.role, ReactionEntryStructureParticipant.participant_index)
    ).all()
    sides: dict[ReactionRole, dict[int, list]] = {ReactionRole.reactant: {}, ReactionRole.product: {}}
    for role, se_id, ref in rows:
        if role in sides:
            sides[role].setdefault(se_id, [ref, 0])[1] += 1
    return {role: [(se_id, ref, n) for se_id, (ref, n) in side.items()] for role, side in sides.items()}


def _lacks_level(side: list[tuple[int, str, int]], by_species: dict[int, list[_Point]], lot_id: int) -> bool:
    """Does any participant of the side have no energy at the level from any program?"""
    return any(not any(p.lot_id == lot_id for p in by_species.get(se_id, [])) for se_id, _ref, _n in side)


def _label(lot: LevelOfTheorySummary | None) -> tuple[str, str, str]:
    return (lot.method, lot.basis or "", lot.level_of_theory_ref) if lot is not None else ("", "", "")


def build_energy_profiles(
    session: Session, reaction_entry_id: int, profile: ResolvedReadProfile
) -> tuple[list[ReactionEnergyProfile], list[EnergyProfileUnavailable]]:
    """Profiles and unavailable entries for one reaction entry's own participants and transition-state entries."""
    sides = _participants(session, reaction_entry_id)
    reactants, products = sides[ReactionRole.reactant], sides[ReactionRole.product]
    entries = ts_entries(session, reaction_entry_id, profile)
    by_species, by_ts = _owner_points(
        session,
        species_entry_ids=[se_id for se_id, _r, _n in reactants + products],
        ts_entry_ids=[e.id for e in entries],
        profile=profile,
    )
    lot_cache: dict[int, LevelOfTheorySummary] = {}

    def lot_summary(lot_id: int) -> LevelOfTheorySummary:
        if lot_id not in lot_cache:
            summary = _build_lot_summary(session, lot_id)
            assert summary is not None  # the foreign key guarantees the row
            lot_cache[lot_id] = summary
        return lot_cache[lot_id]

    def molecule(se_id: int, ref: str, count: int, lot_id: int, program: str) -> EnergyProfileMolecule | None:
        at_key = [p for p in by_species.get(se_id, []) if p.lot_id == lot_id and p.software.program == program]
        if not at_key:
            return None
        best = _lowest(at_key)
        return EnergyProfileMolecule(
            species_entry_ref=ref,
            count=count,
            calculation_ref=best.calculation_ref,
            energy_type=best.energy_type,
            electronic_energy_hartree=best.energy,
            software=best.software,
        )

    profiles: list[ReactionEnergyProfile] = []
    unavailable: list[EnergyProfileUnavailable] = []
    for entry in entries:
        ts_points = by_ts.get(entry.id, [])
        if not ts_points:
            unavailable.append(
                EnergyProfileUnavailable(
                    transition_state_entry_ref=entry.public_ref, code=EnergyProfileUnavailableCode.no_ts_energy
                )
            )
            continue
        entry_profiles: list[ReactionEnergyProfile] = []
        entry_unavailable: list[EnergyProfileUnavailable] = []
        for lot_id, program in sorted({(p.lot_id, p.software.program) for p in ts_points}):
            ts_best = _lowest([p for p in ts_points if p.lot_id == lot_id and p.software.program == program])
            reactant_parts = [molecule(s, r, n, lot_id, program) for s, r, n in reactants]
            product_parts = [molecule(s, r, n, lot_id, program) for s, r, n in products]
            if reactants and products and None not in reactant_parts and None not in product_parts:
                found_reactants = [m for m in reactant_parts if m is not None]
                found_products = [m for m in product_parts if m is not None]
                e_react = sum(m.electronic_energy_hartree * m.count for m in found_reactants)
                e_prod = sum(m.electronic_energy_hartree * m.count for m in found_products)
                entry_profiles.append(
                    ReactionEnergyProfile(
                        transition_state_entry_ref=entry.public_ref,
                        level_of_theory=lot_summary(lot_id),
                        program=program,
                        reactants=found_reactants,
                        transition_state=EnergyProfileTransitionState(
                            transition_state_entry_ref=entry.public_ref,
                            calculation_ref=ts_best.calculation_ref,
                            energy_type=ts_best.energy_type,
                            electronic_energy_hartree=ts_best.energy,
                            software=ts_best.software,
                        ),
                        products=found_products,
                        energy_hartree=EnergyProfileValues(
                            reactants=e_react, transition_state=ts_best.energy, products=e_prod
                        ),
                        relative_kj_mol=EnergyProfileValues(
                            reactants=0.0,
                            transition_state=(ts_best.energy - e_react) * HARTREE_TO_KJ_MOL,
                            products=(e_prod - e_react) * HARTREE_TO_KJ_MOL,
                        ),
                    )
                )
                continue
            if not reactants or _lacks_level(reactants, by_species, lot_id):
                code = EnergyProfileUnavailableCode.no_reactant_energy_at_level
            elif not products or _lacks_level(products, by_species, lot_id):
                code = EnergyProfileUnavailableCode.no_product_energy_at_level
            else:
                code = EnergyProfileUnavailableCode.points_from_different_programs
            entry_unavailable.append(
                EnergyProfileUnavailable(
                    transition_state_entry_ref=entry.public_ref,
                    code=code,
                    level_of_theory=lot_summary(lot_id),
                    program=program,
                )
            )
        entry_profiles.sort(key=lambda p: (_label(p.level_of_theory), p.program))
        entry_unavailable.sort(key=lambda u: (_label(u.level_of_theory), u.program or ""))
        profiles.extend(entry_profiles)
        unavailable.extend(entry_unavailable)
    return profiles, unavailable


# ---------------------------------------------------------------------------
# Energies cited by kinetics
# ---------------------------------------------------------------------------

_CITED_ROLES = (
    KineticsCalculationRole.reactant_energy,
    KineticsCalculationRole.product_energy,
    KineticsCalculationRole.ts_energy,
)


def build_cited_energies(
    session: Session,
    reaction_entry_id: int,
    *,
    include_rejected: bool,
    include_deprecated: bool,
    min_review_status: RecordReviewStatus | None,
) -> list[KineticsCitedEnergies]:
    """The energy calculations each visible kinetics record of the entry cites, with their energies (bulk statements)."""
    visible = visible_statuses(
        min_review_status=min_review_status, include_rejected=include_rejected, include_deprecated=include_deprecated
    )
    kinetics = session.execute(
        select(Kinetics.id, Kinetics.public_ref, RecordReview.status)
        .outerjoin(
            RecordReview,
            (RecordReview.record_type == SubmissionRecordType.kinetics) & (RecordReview.record_id == Kinetics.id),
        )
        .where(Kinetics.reaction_entry_id == reaction_entry_id)
    ).all()
    statuses = {kid: (status or RecordReviewStatus.not_reviewed) for kid, _ref, status in kinetics}
    record_hidden_by_default(
        hidden_by_default_from_statuses(
            statuses.values(),
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            min_review_status=min_review_status,
        ),
        scope=("energy_profile", reaction_entry_id),
    )
    shown = {kid: ref for kid, ref, _s in kinetics if statuses[kid] in visible}
    if not shown:
        return []
    cites = session.execute(
        select(
            KineticsSourceCalculation.kinetics_id,
            KineticsSourceCalculation.role,
            Calculation.id,
            Calculation.public_ref,
            LevelOfTheory.public_ref,
            SpeciesEntry.public_ref,
            TransitionStateEntry.public_ref,
            Software.name,
            SoftwareRelease.version,
            SoftwareRelease.revision,
            SoftwareRelease.build,
        )
        .join(Calculation, Calculation.id == KineticsSourceCalculation.calculation_id)
        .outerjoin(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        .outerjoin(SpeciesEntry, SpeciesEntry.id == Calculation.species_entry_id)
        .outerjoin(TransitionStateEntry, TransitionStateEntry.id == Calculation.transition_state_entry_id)
        .outerjoin(SoftwareRelease, SoftwareRelease.id == Calculation.software_release_id)
        .outerjoin(Software, Software.id == SoftwareRelease.software_id)
        .where(KineticsSourceCalculation.kinetics_id.in_(list(shown)), KineticsSourceCalculation.role.in_(_CITED_ROLES))
    ).all()
    calc_ids = {row[2] for row in cites}
    energies: dict[int, tuple[str, float]] = {}
    if calc_ids:
        # Optimisation energies first, so a calculation that has a single point reads as that.
        for energy_type, result_model, column in (
            ("optimization", CalculationOptResult, CalculationOptResult.final_energy_hartree),
            ("single_point", CalculationSPResult, CalculationSPResult.electronic_energy_hartree),
        ):
            for cid, energy in session.execute(
                select(result_model.calculation_id, column).where(
                    result_model.calculation_id.in_(calc_ids), column.is_not(None)
                )
            ).all():
                energies[cid] = (energy_type, energy)
    out: dict[int, list[CitedCalculation]] = defaultdict(list)
    for kid, role, cid, ref, lot_ref, se_ref, ts_ref, name, version, revision, build in cites:
        energy_type, energy = energies.get(cid, (None, None))
        out[kid].append(
            CitedCalculation(
                role=role.value,
                calculation_ref=ref,
                species_entry_ref=se_ref,
                transition_state_entry_ref=ts_ref,
                level_of_theory_ref=lot_ref,
                software=(
                    EnergyProfileSoftware(program=name, version=version, revision=revision, build=build)
                    if name is not None
                    else None
                ),
                energy_type=energy_type,
                electronic_energy_hartree=energy,
            )
        )
    return [
        KineticsCitedEnergies(
            kinetics_ref=shown[kid], calculations=sorted(out[kid], key=lambda c: (c.role, c.calculation_ref))
        )
        for kid in sorted(out, key=lambda k: shown[k])
    ]


def get_reaction_energy_profile(
    session: Session,
    *,
    reaction_entry_id: int,
    reaction_entry_ref: str,
    profile: ResolvedReadProfile,
    include_rejected: bool = False,
    include_deprecated: bool = False,
    min_review_status: RecordReviewStatus | None = None,
) -> ReactionEnergyProfileResponse:
    profiles, unavailable = build_energy_profiles(session, reaction_entry_id, profile)
    return ReactionEnergyProfileResponse(
        request=EnergyProfileRequestEcho(
            reaction_entry_ref=reaction_entry_ref,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            min_review_status=min_review_status,
        ),
        reaction_entry_ref=reaction_entry_ref,
        profiles=profiles,
        unavailable=unavailable,
        cited_by_kinetics=build_cited_energies(
            session,
            reaction_entry_id,
            include_rejected=include_rejected,
            include_deprecated=include_deprecated,
            min_review_status=min_review_status,
        ),
    )
