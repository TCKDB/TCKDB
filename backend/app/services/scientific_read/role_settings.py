"""The settings behind a thermo or statmech record's geometry, frequency and energy levels (read-time view; nothing is stored).

One question per level: which calculation does this record link for it, and what grid and SCF convergence did that
calculation use? The calculation is picked from the record's own links, by the source the record's ``levels`` already
name (``geometry_source``, ``frequency_source``, ``energy_source``), so the settings shown always belong to the
calculation the level was derived from. The settings come from :func:`build_effective_settings`, the builder the
calculation read uses; there is no second computation.

A level with no linked calculation is listed as absent. It is never filled from a sibling record, an unlinked
statmech, or any calculation the record does not link.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType
from app.schemas.reads.scientific_calculation import RoleCalculationSettings
from app.schemas.reads.scientific_common import ScientificLevelsSummary
from app.services.scientific_read.effective_settings import build_effective_settings


def pick_level_calculations(
    levels: ScientificLevelsSummary,
    role_calc_ids: Mapping[str, Sequence[int]],
    calculations: Mapping[int, Calculation],
    withheld: Collection[int] = frozenset(),
) -> dict[str, tuple[int | None, str | None]]:
    """Level -> (calculation id, absent reason). Exactly one of the two is ``None``.

    ``role_calc_ids`` maps a link role (``opt``, ``freq``, ``sp``, ``composite``, ``imported``) to the record's linked
    calculation ids, lowest first. The first one the review profile shows is used, as everywhere else.
    """

    def first(role: str, *, composite_typed: bool | None = None) -> int | None:
        for cid in role_calc_ids.get(role, ()):
            calc = calculations.get(cid)
            if calc is None or cid in withheld:
                continue
            if composite_typed is not None and (calc.type is CalculationType.composite) != composite_typed:
                continue
            return cid
        return None

    def absent(source: str | None, role_set: tuple[str, ...]) -> str:
        if source == "composite_recipe":
            return "composite_recipe"
        if source is None and any(cid in withheld for role in role_set for cid in role_calc_ids.get(role, ())):
            return "withheld"
        return "not_linked"

    out: dict[str, tuple[int | None, str | None]] = {}

    geometry = first("opt") if levels.geometry_source == "opt" else None
    out["geometry"] = (geometry, None) if geometry is not None else (None, absent(levels.geometry_source, ("opt", "composite")))

    if levels.frequency_source == "freq":
        frequency = first("freq")
    elif levels.frequency_source == "opt":
        frequency = first("opt")
    else:
        frequency = None
    out["frequency"] = (
        (frequency, None) if frequency is not None else (None, absent(levels.frequency_source, ("freq", "opt", "composite")))
    )

    source = levels.energy_source
    if source == "composite":
        energy = first("composite", composite_typed=True)
        if energy is None:
            energy = first("composite", composite_typed=False)
    elif source in ("sp", "opt", "imported"):
        energy = first(source)
    else:
        energy = None
    if energy is not None:
        out["energy"] = (energy, None)
    elif source == "ambiguous":
        out["energy"] = (None, "ambiguous")
    else:
        out["energy"] = (None, absent(source, ("sp", "opt", "composite", "imported")))
    return out


def build_calculation_settings(
    session: Session,
    *,
    levels: ScientificLevelsSummary,
    role_calc_ids: Mapping[str, Sequence[int]],
    withheld: Collection[int] = frozenset(),
) -> list[RoleCalculationSettings]:
    """The three levels, in order geometry / frequency / energy, each with its calculation's effective settings."""
    wanted = {cid for ids in role_calc_ids.values() for cid in ids}
    calculations: dict[int, Calculation] = {}
    if wanted:
        calculations = {
            c.id: c
            for c in session.scalars(
                select(Calculation)
                .where(Calculation.id.in_(wanted))
                .options(
                    selectinload(Calculation.lot),
                    selectinload(Calculation.software_release),
                )
            )
        }
    picked = pick_level_calculations(levels, role_calc_ids, calculations, withheld)
    out: list[RoleCalculationSettings] = []
    for role in ("geometry", "frequency", "energy"):
        calc_id, reason = picked[role]
        if calc_id is None:
            out.append(RoleCalculationSettings(role=role, absent_reason=reason))
            continue
        calc = calculations[calc_id]
        out.append(
            RoleCalculationSettings(
                role=role,
                calculation_ref=calc.public_ref,
                calculation_type=calc.type,
                settings=build_effective_settings(session, calc),
            )
        )
    return out
