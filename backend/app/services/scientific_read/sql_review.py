"""SQL-side review visibility and ranking for the scientific read services.

Why this module exists
----------------------
Every scientific search needs the same two things from ``record_review``:

1. **visibility** — drop candidates whose review status the caller cannot see
   (the default trust posture, ``min_review_status``, and Stage 3's curated
   profile floor);
2. **rank** — order survivors by ``review_rank`` (approved first).

Both were historically done in Python, which forced the service to load the
*entire* candidate set before it could filter or order it. On the deployed
55-species database that is invisible. At catalog scale it has two failure
modes, both measured on the Stage 4 benchmark corpus:

- **Latency.** Broad searches materialize thousands of rows and every badge,
  availability count and section id for them, to return a page of 50.
- **A hard outage.** ``fetch_review_badges`` renders one bind parameter per
  candidate id. PostgreSQL's wire protocol caps a statement at 65,535
  parameters, so a search matching more candidates than that does not merely
  slow down — it raises ``OperationalError`` and the API returns
  ``503 database_unavailable``. ``calculation_type=sp`` on a 50,000-species
  corpus matches 119,701 calculations and fails exactly this way.

Expressing visibility and rank as SQL lets a service filter, order and slice
in the database and materialize only the page, which fixes both.

The helpers here are deliberately small and composable rather than a framework:
each takes a statement plus the id column it should join review rows against,
and returns the statement with the join applied. Services keep control of
their own query.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import Select, and_, case, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import REVIEW_RANK, ReviewStatusSummary

#: Rank used for a record with no ``record_review`` row at all. It must agree
#: with how the Python path treats a missing row — as ``not_reviewed`` — or a
#: SQL-ordered search and a Python-ordered one would disagree about the same
#: corpus.
_NO_REVIEW_ROW_RANK = REVIEW_RANK[RecordReviewStatus.not_reviewed]


def review_alias():
    """A fresh ``record_review`` alias, so one statement may join it twice."""
    return aliased(RecordReview)


def join_review(
    stmt: Select,
    id_column: Any,
    record_type: SubmissionRecordType,
    *,
    alias=None,
):
    """LEFT JOIN ``record_review`` for ``(record_type, id_column)``.

    A LEFT join is required, not an inner one: a record with no review row is
    ``not_reviewed`` and is visible by default. An inner join would silently
    drop every never-reviewed record, which on this corpus is most of them.

    :returns: ``(statement, alias)`` — the alias is needed to build rank and
        visibility expressions over the joined rows.
    """
    review = alias if alias is not None else review_alias()
    stmt = stmt.outerjoin(
        review,
        and_(
            review.record_type == record_type,
            review.record_id == id_column,
        ),
    )
    return stmt, review


def review_rank_expr(review):
    """SQL ``review_rank`` (lower is better) over a joined review alias.

    Mirrors :data:`app.schemas.reads.scientific_common.REVIEW_RANK` exactly,
    including mapping "no review row" to the ``not_reviewed`` rank.
    """
    return case(
        *(
            (review.status == status, rank)
            for status, rank in REVIEW_RANK.items()
        ),
        else_=_NO_REVIEW_ROW_RANK,
    )


def review_status_expr(review):
    """SQL review status, with a missing row reported as ``not_reviewed``.

    The fallback is bound with the column's own enum type. Passing the bare
    string would make PostgreSQL see ``CASE`` arms of type
    ``record_review_status`` and ``varchar`` and reject the expression with
    ``DatatypeMismatch``.
    """
    not_reviewed = literal(RecordReviewStatus.not_reviewed, review.status.type)
    return func.coalesce(review.status, not_reviewed)


def visible_review_filter(review, visible: Iterable[RecordReviewStatus]):
    """Predicate selecting rows whose review status is in ``visible``.

    Records with no review row count as ``not_reviewed``; they are included
    only when ``not_reviewed`` is itself visible. Getting this wrong in either
    direction is a correctness bug, not a performance one — omitting the NULL
    branch hides every unreviewed record, and always including it leaks
    unreviewed records into a curated read.
    """
    visible_set = set(visible)
    if not visible_set:
        # No status is visible: match nothing, without emitting `IN ()`.
        return and_(review.status.is_(None), review.status.is_not(None))

    predicate = review.status.in_(sorted(visible_set, key=lambda s: s.value))
    if RecordReviewStatus.not_reviewed in visible_set:
        predicate = or_(predicate, review.status.is_(None))
    return predicate


def status_from_sql(value: Any) -> RecordReviewStatus:
    """Coerce a SQL-side review status back to the enum.

    ``review_status_expr`` may come back as the enum (psycopg resolves the
    PostgreSQL enum) or as its string value, depending on whether the
    ``COALESCE`` fell through to the literal. Callers want one type.
    """
    if isinstance(value, RecordReviewStatus):
        return value
    return RecordReviewStatus(str(value))


def summary_from_sql(session: Session, ranked) -> ReviewStatusSummary:
    """Count review statuses over the *whole* filtered set in one aggregate.

    ``ranked`` is a subquery carrying a ``review_status`` column — normally
    the one :func:`review_status_expr` produced.

    The summary describes everything the filters matched, not the page, so it
    cannot be derived from the page rows — and materializing the candidate set
    to count it would give back exactly the property this module exists to
    remove. ``total`` is the count of the filtered set, so a caller that needs
    both can take it from here rather than issuing a second aggregate.
    """
    rows = session.execute(
        select(ranked.c.review_status, func.count())
        .select_from(ranked)
        .group_by(ranked.c.review_status)
    ).all()
    summary = ReviewStatusSummary()
    for status, count in rows:
        key = status.value if hasattr(status, "value") else str(status)
        if hasattr(summary, key):
            setattr(summary, key, getattr(summary, key) + count)
        summary.total += count
    return summary


__all__ = [
    "join_review",
    "review_alias",
    "review_rank_expr",
    "review_status_expr",
    "status_from_sql",
    "summary_from_sql",
    "visible_review_filter",
]
