"""The settings behind a thermo or statmech record's geometry, frequency and energy levels (read-time view; nothing is stored).

One question per level: which calculation does this record link for it, and what grid and SCF convergence did that
calculation use? The calculation is one of the SUPPLIERS of the level, as :func:`app.services.calculation_levels.level_suppliers`
defines them (the same rule the evidence rule uses), so the settings shown belong to the calculation the level's level of
theory was derived from; the priority order is not re-implemented here. The settings come from
:func:`effective_settings_from`, the computation the calculation read uses.

A level with no linked calculation is listed as absent. It is never filled from a sibling record, an unlinked statmech,
or any calculation the record does not link. A level all of whose suppliers the review profile hides is left out.

Cost: the record builders note the suppliers of each level on the record (:class:`LevelPlan`, no query), and
:func:`attach_calculation_settings` resolves a whole page of records in a fixed number of statements.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.schemas.reads.scientific_calculation import RoleCalculationSettings
from app.services.scientific_read.effective_settings import effective_settings_from, load_settings_inputs

LEVELS = ("geometry", "frequency", "energy")


@dataclass(frozen=True)
class LevelPlan:
    """Per level, the source that supplied it and the supplying calculation ids (lowest first), from the record's FULL links.

    ``withheld`` is the subset of those the review profile hides (the evidence rule).
    """

    suppliers: Mapping[str, tuple[str | None, tuple[int, ...]]] = field(default_factory=dict)
    withheld: frozenset[int] = frozenset()


def make_level_plan(
    suppliers: Mapping[str, tuple[str | None, Sequence[int]]], withheld: Collection[int] = frozenset()
) -> LevelPlan:
    ids = {cid for _, level_ids in suppliers.values() for cid in level_ids}
    return LevelPlan(
        suppliers={level: (source, tuple(level_ids)) for level, (source, level_ids) in suppliers.items()},
        withheld=frozenset(ids & set(withheld)),
    )


def pick_level_calculations(plan: LevelPlan) -> dict[str, tuple[int | None, str | None]]:
    """Level -> (calculation id, absent reason).

    Both ``None`` means the level is omitted because every calculation that supplied it is withheld. The calculation is the
    lowest supplier the review profile shows.
    """
    out: dict[str, tuple[int | None, str | None]] = {}
    for level in LEVELS:
        source, ids = plan.suppliers.get(level, (None, ()))
        shown = [cid for cid in ids if cid not in plan.withheld]
        all_withheld = bool(ids) and not shown
        if source is None:
            out[level] = (None, "not_linked")
        elif all_withheld:
            out[level] = (None, None)
        elif source == "composite_recipe":
            out[level] = (None, "composite_recipe")
        elif source == "ambiguous":
            out[level] = (None, "ambiguous")
        elif shown:
            out[level] = (shown[0], None)
        else:
            out[level] = (None, "not_linked")
    return out


def attach_calculation_settings(session: Any, records: Iterable[Any]) -> None:
    """Set ``calculation_settings`` on each record of a page, from the plan the record builder noted on it.

    A fixed number of statements whatever the number of records: every calculation the page needs is loaded together.
    """
    records = list(records)
    picks = []
    for record in records:
        plan = getattr(record, "_level_plan", None)
        if plan is None:
            # A record that never went through its builder has no suppliers to resolve; rendering "No calculation
            # linked" for it would be a false statement.
            raise RuntimeError("calculation_settings requested for a record built without its level plan")
        picks.append((record, pick_level_calculations(plan)))
    wanted = {cid for _, pick in picks for cid, _ in pick.values() if cid is not None}
    inputs = load_settings_inputs(session, wanted)
    for record, pick in picks:
        block: list[RoleCalculationSettings] = []
        for level in LEVELS:
            calc_id, reason = pick[level]
            if calc_id is None and reason is None:
                continue
            if calc_id is None or calc_id not in inputs:
                block.append(RoleCalculationSettings(role=level, absent_reason=reason or "not_linked"))
                continue
            loaded = inputs[calc_id]
            block.append(
                RoleCalculationSettings(
                    role=level,
                    calculation_ref=loaded.calculation.public_ref,
                    calculation_type=loaded.calculation.type,
                    settings=effective_settings_from(loaded),
                )
            )
        record.calculation_settings = block
