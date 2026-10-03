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
from sqlalchemy.orm import Session, aliased

from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
from app.db.models.level_of_theory import LevelOfTheory
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
    geometry_level = aliased(LevelOfTheory)
    rows = session.execute(
        select(
            LevelOfTheoryComposite.level_of_theory_id,
            CompositeScheme.public_ref,
            CompositeScheme.kind,
            CompositeScheme.name,
            geometry_level.public_ref,
        )
        .join(CompositeScheme, CompositeScheme.id == LevelOfTheoryComposite.scheme_id)
        .outerjoin(geometry_level, geometry_level.id == CompositeScheme.geometry_level_of_theory_id)
        .where(LevelOfTheoryComposite.level_of_theory_id.in_(wanted))
    ).all()
    return {
        lot_id: CompositeSchemeSummary(
            composite_scheme_ref=ref, kind=kind, name=name, geometry_level_of_theory_ref=geometry_ref
        )
        for lot_id, ref, kind, name, geometry_ref in rows
    }


#: ``session.info`` key of the per-session memo behind :func:`composite_scheme_summary`.
MEMO_KEY = "composite_scheme_summary_by_lot"


def composite_scheme_summary(session: Session, lot_id: int | None) -> CompositeSchemeSummary | None:
    """The scheme one level of theory is bound to, or ``None`` when it is unbound.

    For the builders that summarise one level at a time, inside a loop over
    records: the answer is remembered on the session (an unbound level too), so
    a page of records on a few levels costs a few statements, not one per
    record. A request's session is short-lived; the writer of a binding
    (:func:`app.services.composite_scheme_resolution.ensure_named_method_binding`)
    clears the memo, so a session that binds a level reads it back correctly.
    """
    if lot_id is None:
        return None
    memo: dict[int, CompositeSchemeSummary | None] = session.info.setdefault(MEMO_KEY, {})
    if lot_id not in memo:
        memo[lot_id] = composite_scheme_summaries(session, [lot_id]).get(lot_id)
    return memo[lot_id]
