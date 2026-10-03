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
    rows = session.execute(
        select(
            LevelOfTheoryComposite.level_of_theory_id,
            CompositeScheme.public_ref,
            CompositeScheme.kind,
            CompositeScheme.name,
            CompositeScheme.geometry_level_of_theory_id,
        )
        .join(CompositeScheme, CompositeScheme.id == LevelOfTheoryComposite.scheme_id)
        .where(LevelOfTheoryComposite.level_of_theory_id.in_(wanted))
    ).all()
    # The recipe's own geometry level, by ref. A separate statement, and only when a bound level states one:
    # an outer join to ``level_of_theory`` here would be counted (and priced) as a per-page level lookup by every
    # read that builds these summaries (``test_record_builder_statement_cost``), and an ordinary page has no bound level.
    geometry_ids = {row[4] for row in rows if row[4] is not None}
    geometry_refs: dict[int, str] = {}
    if geometry_ids:
        for geometry_id, geometry_ref in session.execute(
            select(LevelOfTheory.id, LevelOfTheory.public_ref).where(LevelOfTheory.id.in_(geometry_ids))
        ).all():
            geometry_refs[geometry_id] = geometry_ref
    return {
        lot_id: CompositeSchemeSummary(
            composite_scheme_ref=ref,
            kind=kind,
            name=name,
            geometry_level_of_theory_ref=geometry_refs.get(geometry_id) if geometry_id is not None else None,
        )
        for lot_id, ref, kind, name, geometry_id in rows
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
