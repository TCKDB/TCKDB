"""Vocabulary / discovery reads for the scientific API (DR none; Phase 7).

Exact-string filters (method, basis, reaction family, software) are only
usable if a client can discover which values actually exist in the
database. These helpers return the distinct stored values with usage
counts so a modeler can see, e.g., which levels of theory or reaction
families are available before issuing a filtered search.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction, ReactionFamily
from app.db.models.software import Software


def _counted_distinct(session: Session, column) -> list[dict]:
    """Return ``[{"value": v, "count": n}, ...]`` for non-null values of a
    column, ordered by descending count then value."""
    rows = session.execute(
        select(column, func.count())
        .where(column.is_not(None))
        .group_by(column)
        .order_by(func.count().desc(), column.asc())
    ).all()
    return [{"value": value, "count": count} for value, count in rows]


def list_methods(session: Session) -> list[dict]:
    """Distinct level-of-theory ``method`` values with usage counts."""
    return _counted_distinct(session, LevelOfTheory.method)


def list_basis_sets(session: Session) -> list[dict]:
    """Distinct level-of-theory ``basis`` values with usage counts."""
    return _counted_distinct(session, LevelOfTheory.basis)


def list_software(session: Session) -> list[dict]:
    """Distinct software ``name`` values with usage counts."""
    return _counted_distinct(session, Software.name)


def list_reaction_families(session: Session) -> list[dict]:
    """Canonical reaction families with usage counts (0 if unused).

    Lists the seeded ``reaction_family`` vocabulary and how many reactions
    reference each — the discoverable set of valid ``family=`` filter
    values for reaction search.
    """
    rows = session.execute(
        select(ReactionFamily.name, func.count(ChemReaction.id))
        .outerjoin(
            ChemReaction, ChemReaction.reaction_family_id == ReactionFamily.id
        )
        .group_by(ReactionFamily.name)
        .order_by(func.count(ChemReaction.id).desc(), ReactionFamily.name.asc())
    ).all()
    return [{"value": name, "count": count} for name, count in rows]
