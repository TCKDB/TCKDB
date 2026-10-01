"""What a linked composite calculation says about geometry and frequency levels (ADR 0021, R1).

:func:`app.services.calculation_levels.derive_levels` takes a plain
:class:`~app.services.calculation_levels.RoleCalcInfo` per linked calculation.
For a calculation linked under the role ``composite`` it needs three facts the
read layer's per-calculation metadata does not hold: whether the calculation has
an output geometry of its own, and the internal geometry and frequency levels of
the composite scheme its level of theory is bound to (the levels the named
method runs inside itself). Every reader that calls ``derive_levels`` (thermo,
statmech, kinetics) gets them here, in two bulk statements, so the three cannot
answer "where does a composite record's geometry level come from" differently.
``backend/tests/invariants/test_composite_p3a_invariants.py`` fails a
caller of ``derive_levels`` that does not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationOutputGeometry
from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
from app.services.calculation_levels import RoleCalcInfo


class CompositeRoleFacts(NamedTuple):
    """The three composite-only facts of a ``RoleCalcInfo``."""

    has_output_geometry: bool
    recipe_geometry_lot_id: int | None
    recipe_frequency_lot_id: int | None


def composite_role_facts(
    session: Session, calc_lot_ids: Mapping[int, int | None]
) -> dict[int, CompositeRoleFacts]:
    """``{calculation id: facts}`` for composite-role calculations, in two statements.

    :param session: Active session.
    :param calc_lot_ids: Calculation id -> its level-of-theory id, for every
        calculation linked under the role ``composite`` (the caller selects the
        role; this function makes no assumption about it). ``None`` levels are
        allowed and have no recipe.
    :returns: One entry per calculation id given. A calculation whose level is
        bound to no scheme, or whose scheme does not state a level, has ``None``
        for that level; it is never given a default.
    """
    calc_ids = set(calc_lot_ids)
    if not calc_ids:
        return {}
    with_geometry = set(
        session.scalars(
            select(CalculationOutputGeometry.calculation_id)
            .where(CalculationOutputGeometry.calculation_id.in_(calc_ids))
            .distinct()
        ).all()
    )
    lot_ids = {lot for lot in calc_lot_ids.values() if lot is not None}
    recipes: dict[int, tuple[int | None, int | None]] = {}
    if lot_ids:
        recipes = {
            lot_id: (geometry_lot, frequency_lot)
            for lot_id, geometry_lot, frequency_lot in session.execute(
                select(
                    LevelOfTheoryComposite.level_of_theory_id,
                    CompositeScheme.geometry_level_of_theory_id,
                    CompositeScheme.frequency_level_of_theory_id,
                )
                .join(CompositeScheme, CompositeScheme.id == LevelOfTheoryComposite.scheme_id)
                .where(LevelOfTheoryComposite.level_of_theory_id.in_(lot_ids))
            ).all()
        }
    out: dict[int, CompositeRoleFacts] = {}
    for calc_id, lot_id in calc_lot_ids.items():
        geometry_lot, frequency_lot = recipes.get(lot_id, (None, None)) if lot_id is not None else (None, None)
        out[calc_id] = CompositeRoleFacts(calc_id in with_geometry, geometry_lot, frequency_lot)
    return out


def composite_role_info(
    lot_id: int | None, carries_frequencies: bool, facts: CompositeRoleFacts | None
) -> RoleCalcInfo:
    """The ``RoleCalcInfo`` of one composite-role calculation."""
    if facts is None:
        return RoleCalcInfo(lot_id=lot_id, carries_frequencies=carries_frequencies)
    return RoleCalcInfo(
        lot_id=lot_id,
        carries_frequencies=carries_frequencies,
        has_output_geometry=facts.has_output_geometry,
        recipe_geometry_lot_id=facts.recipe_geometry_lot_id,
        recipe_frequency_lot_id=facts.recipe_frequency_lot_id,
    )


def recipe_lot_ids(facts: Iterable[CompositeRoleFacts]) -> set[int]:
    """Every internal level id the given facts name, for summarising them."""
    ids: set[int] = set()
    for fact in facts:
        for lot_id in (fact.recipe_geometry_lot_id, fact.recipe_frequency_lot_id):
            if lot_id is not None:
                ids.add(lot_id)
    return ids
