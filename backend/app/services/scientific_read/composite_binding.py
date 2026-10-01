"""Which composite scheme, if any, a level of theory is bound to (ADR 0021).

Every ``LevelOfTheorySummary`` the read layer builds carries
``composite_scheme``. The summaries are built in many places, from a row, a
joined tuple or a cached metadata dict, so the lookup is one shared function:
one query for any number of levels, and an unbound level is simply absent from
the result (the caller reads that as ``None``, never as "unknown").
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
from app.schemas.reads.scientific_common import CompositeSchemeSummary


def composite_scheme_summaries(
    session: Session, lot_ids: Iterable[int | None]
) -> dict[int, CompositeSchemeSummary]:
    """``{level_of_theory id: its scheme}`` for the bound levels among ``lot_ids``.

    :param session: Active session.
    :param lot_ids: Level-of-theory ids; ``None`` and duplicates are ignored.
    :returns: Only the bound levels. An id with no binding is not a key.
    """
    wanted = {i for i in lot_ids if i is not None}
    if not wanted:
        return {}
    rows = session.execute(
        select(
            LevelOfTheoryComposite.level_of_theory_id,
            CompositeScheme.public_ref,
            CompositeScheme.kind,
            CompositeScheme.name,
        )
        .join(CompositeScheme, CompositeScheme.id == LevelOfTheoryComposite.scheme_id)
        .where(LevelOfTheoryComposite.level_of_theory_id.in_(wanted))
    ).all()
    return {
        lot_id: CompositeSchemeSummary(composite_scheme_ref=ref, kind=kind, name=name)
        for lot_id, ref, kind, name in rows
    }


def composite_scheme_summary(session: Session, lot_id: int | None) -> CompositeSchemeSummary | None:
    """The scheme one level of theory is bound to, or ``None`` when it is unbound."""
    return composite_scheme_summaries(session, [lot_id]).get(lot_id) if lot_id is not None else None
