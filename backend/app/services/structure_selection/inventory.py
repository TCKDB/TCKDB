"""Read-only coverage inventory for structure selection: what the stored corpus can and cannot support today.

Structure selection (calculation, conformer and transition-state evidence ordering) needs, per candidate, an
established actual recipe, a geometry, a grouping, a validation basis and a comparable energy. Calculations deposited
before the declarations existed carry none of these as stated facts. This inventory counts, in aggregate SQL, which
of those unknowns remain open. It writes nothing, backfills nothing and states no claim for any record: an absent
declaration is counted as absent, never assumed.

Categories (every count is of stored rows, none is inferred):

* ``calculations``: totals by type; energy-bearing calculations (a stored electronic energy) and, among them, how many
  carry an actual-protocol declaration (the recipe) and a geometry link, and how many are not linked to a
  conformer observation (grouping); rejected quality; the automated geometry-validation verdicts.
* ``species_entries``: entries holding two or more energy-bearing calculations (the only entries for which an
  ordering question exists) and how many of those have every such calculation declared, so a justified cohort can
  be formed, versus at least one undeclared.
* ``determinations`` and ``findings``: rows of the new claim tables, by kind (so the corpus's adoption is visible).
* ``transition_states``: entries, those with a frequency result, with a recorded ``n_imag``, with several imaginary
  modes and no designated reaction coordinate (the case the saddle claim cannot judge), and with an IRC calculation.
* ``conformers``: groups and observations.

The numbers describe what the stored data supports today; they are not a quality score and change nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import CalculationQuality, CalculationType
from app.db.models.species import ConformerGroup, ConformerObservation
from app.db.models.structure_determination import StructureDetermination, StructureEvidenceFinding
from app.db.models.transition_state import TransitionStateEntry


def _count(session: Session, stmt) -> int:
    return int(session.execute(stmt).scalar_one())


def _energy_bearing():
    """A calculation with a stored electronic energy: an sp result or an opt final energy."""
    has_sp = select(CalculationSPResult.calculation_id).where(
        CalculationSPResult.calculation_id == Calculation.id,
        CalculationSPResult.electronic_energy_hartree.is_not(None),
    )
    has_opt = select(CalculationOptResult.calculation_id).where(
        CalculationOptResult.calculation_id == Calculation.id,
        CalculationOptResult.final_energy_hartree.is_not(None),
    )
    return or_(has_sp.exists(), has_opt.exists())


def _has_geometry():
    """The calculation names a geometry it ran on or produced (an input or an output link)."""
    produced = select(CalculationOutputGeometry.calculation_id).where(
        CalculationOutputGeometry.calculation_id == Calculation.id
    )
    consumed = select(CalculationInputGeometry.calculation_id).where(
        CalculationInputGeometry.calculation_id == Calculation.id
    )
    return or_(produced.exists(), consumed.exists())


def _declared():
    return Calculation.actual_protocol_declaration.is_not(None)


def _calculations(session: Session) -> dict[str, Any]:
    by_type: dict[CalculationType, int] = {
        kind: int(n) for kind, n in session.execute(select(Calculation.type, func.count()).group_by(Calculation.type)).all()
    }
    energy = _energy_bearing()
    energy_total = _count(session, select(func.count()).select_from(Calculation).where(energy))

    def among_energy(*conditions) -> int:
        return _count(session, select(func.count()).select_from(Calculation).where(energy, *conditions))

    verdicts = {
        status.value if status is not None else "not_validated": n
        for status, n in session.execute(
            select(CalculationGeometryValidation.validation_status, func.count())
            .select_from(Calculation)
            .join(
                CalculationGeometryValidation,
                CalculationGeometryValidation.calculation_id == Calculation.id,
                isouter=True,
            )
            .group_by(CalculationGeometryValidation.validation_status)
        ).all()
    }
    return {
        "total": sum(by_type.values()),
        "by_type": {t.value: n for t, n in sorted(by_type.items(), key=lambda kv: kv[0].value)},
        "energy_bearing": energy_total,
        "energy_bearing_with_actual_protocol": among_energy(_declared()),
        "energy_bearing_without_actual_protocol": among_energy(~_declared()),
        "energy_bearing_with_geometry": among_energy(_has_geometry()),
        "energy_bearing_without_geometry": among_energy(~_has_geometry()),
        "energy_bearing_species_calculations_without_conformer_observation": among_energy(
            Calculation.species_entry_id.is_not(None), Calculation.conformer_observation_id.is_(None)
        ),
        "rejected_quality": _count(
            session, select(func.count()).select_from(Calculation).where(Calculation.quality == CalculationQuality.rejected)
        ),
        "automated_geometry_validation": dict(sorted(verdicts.items())),
    }


def _species_entries(session: Session) -> dict[str, Any]:
    per_entry = (
        select(
            Calculation.species_entry_id.label("entry_id"),
            func.count().label("n"),
            func.count().filter(_declared()).label("declared"),
        )
        .where(Calculation.species_entry_id.is_not(None), _energy_bearing())
        .group_by(Calculation.species_entry_id)
        .subquery()
    )
    multi = per_entry.c.n >= 2
    return {
        "with_energy_bearing_calculations": _count(session, select(func.count()).select_from(per_entry)),
        "with_two_or_more_energy_bearing_calculations": _count(
            session, select(func.count()).select_from(per_entry).where(multi)
        ),
        "of_which_every_calculation_has_an_actual_protocol": _count(
            session, select(func.count()).select_from(per_entry).where(multi, per_entry.c.declared == per_entry.c.n)
        ),
        "of_which_none_has_an_actual_protocol": _count(
            session, select(func.count()).select_from(per_entry).where(multi, per_entry.c.declared == 0)
        ),
        "of_which_some_but_not_all_have_an_actual_protocol": _count(
            session,
            select(func.count()).select_from(per_entry).where(multi, per_entry.c.declared > 0, per_entry.c.declared < per_entry.c.n),
        ),
    }


def _determinations(session: Session) -> dict[str, Any]:
    by_target = {
        kind.value: n
        for kind, n in session.execute(
            select(StructureDetermination.target_kind, func.count()).group_by(StructureDetermination.target_kind)
        ).all()
    }
    findings = [
        {"kind": kind.value, "verdict": verdict.value, "authority": authority.value, "count": n}
        for kind, verdict, authority, n in session.execute(
            select(
                StructureEvidenceFinding.kind,
                StructureEvidenceFinding.verdict,
                StructureEvidenceFinding.authority,
                func.count(),
            ).group_by(
                StructureEvidenceFinding.kind, StructureEvidenceFinding.verdict, StructureEvidenceFinding.authority
            )
        ).all()
    ]
    findings.sort(key=lambda row: (row["kind"], row["verdict"], row["authority"]))
    return {
        "total": sum(by_target.values()),
        "by_target_kind": dict(sorted(by_target.items())),
        "findings": findings,
        "findings_total": sum(row["count"] for row in findings),
    }


def _transition_states(session: Session) -> dict[str, Any]:
    freq_of_entry = (
        select(
            Calculation.transition_state_entry_id.label("entry_id"),
            func.count().label("n_freq"),
            func.count().filter(CalculationFreqResult.n_imag.is_not(None)).label("n_recorded"),
            func.count()
            .filter(
                and_(
                    CalculationFreqResult.n_imag > 1,
                    CalculationFreqResult.reaction_coordinate_mode_index.is_(None),
                )
            )
            .label("n_undesignated_multi"),
        )
        .join(CalculationFreqResult, CalculationFreqResult.calculation_id == Calculation.id)
        .where(Calculation.transition_state_entry_id.is_not(None), Calculation.type == CalculationType.freq)
        .group_by(Calculation.transition_state_entry_id)
        .subquery()
    )
    irc_entries = (
        select(Calculation.transition_state_entry_id)
        .where(Calculation.transition_state_entry_id.is_not(None), Calculation.type == CalculationType.irc)
        .distinct()
        .subquery()
    )
    return {
        "entries": _count(session, select(func.count()).select_from(TransitionStateEntry)),
        "with_a_frequency_result": _count(session, select(func.count()).select_from(freq_of_entry)),
        "with_a_recorded_imaginary_mode_count": _count(
            session, select(func.count()).select_from(freq_of_entry).where(freq_of_entry.c.n_recorded > 0)
        ),
        "with_several_imaginary_modes_and_no_designated_reaction_coordinate": _count(
            session, select(func.count()).select_from(freq_of_entry).where(freq_of_entry.c.n_undesignated_multi > 0)
        ),
        "with_an_irc_calculation": _count(session, select(func.count()).select_from(irc_entries)),
        "with_a_structure_determination": _count(
            session,
            select(func.count(func.distinct(StructureDetermination.transition_state_entry_id))).where(
                StructureDetermination.transition_state_entry_id.is_not(None)
            ),
        ),
    }


def _conformers(session: Session) -> dict[str, Any]:
    return {
        "groups": _count(session, select(func.count()).select_from(ConformerGroup)),
        "observations": _count(session, select(func.count()).select_from(ConformerObservation)),
    }


def structure_coverage_inventory(session: Session) -> dict[str, Any]:
    """Counts of what the stored corpus supports for structure selection. Pure reads; see the module docstring."""
    return {
        "calculations": _calculations(session),
        "species_entries": _species_entries(session),
        "determinations": _determinations(session),
        "transition_states": _transition_states(session),
        "conformers": _conformers(session),
        "note": (
            "Counts of stored rows only. An absent declaration is counted as absent and is never assumed; nothing "
            "here is a quality score and nothing was written or backfilled."
        ),
    }


__all__ = ["structure_coverage_inventory"]
