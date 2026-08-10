"""Read the custody record: what TCKDB has observed about its own storage.

The write side of ``artifact_integrity_event`` and its trust consequence
both landed with ADR 0014; the rows themselves stayed SQL-only, so the
question the table exists to answer -- "has this happened, and to what?"
-- was answerable only by someone with a psql prompt on the deployment.

Aggregating here rather than paging raw observations is deliberate, and
so is *not* aggregating at rest: see the module docstring of
:mod:`app.schemas.reads.scientific_artifact_integrity`.
"""

from __future__ import annotations

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    ArtifactIntegrityEvent,
    Calculation,
    CalculationArtifact,
)
from app.db.models.common import ArtifactIntegrityFinding
from app.schemas.reads.scientific_artifact_integrity import (
    ArtifactIntegrityDigestSummary,
    ArtifactIntegrityObservation,
    IntegrityRequestEcho,
    ScientificArtifactIntegrityHistoryResponse,
    ScientificArtifactIntegrityResponse,
)
from app.schemas.reads.scientific_common import Pagination
from app.services.scientific_read.common import validate_pagination

#: The findings that assert a break, taken from the enum rather than
#: restated, so a new finding cannot quietly count as clean here while
#: the trust evaluator treats it as a break.
_BREAK_FINDINGS = tuple(
    finding for finding in ArtifactIntegrityFinding if finding.is_break
)

#: v0 sort vocabulary. Most recently observed first, because the question
#: this surface answers is almost always "what has gone wrong lately".
_SORT = "last_observed_at:desc"


def _observation(row: ArtifactIntegrityEvent) -> ArtifactIntegrityObservation:
    return ArtifactIntegrityObservation(
        finding=row.finding,
        detected_during=row.detected_during,
        detected_at=row.created_at,
        is_break=row.finding.is_break,
        expected_sha256=row.sha256,
        observed_sha256=row.observed_sha256,
        expected_bytes=row.expected_bytes,
        observed_bytes=row.observed_bytes,
        object_last_modified_at=row.object_last_modified_at,
        object_etag=row.object_etag,
        object_content_length=row.object_content_length,
        artifact_recorded_at=row.artifact_recorded_at,
        detail=row.detail,
    )


def _digest_scope(
    *, sha256: str | None, calculation_ref: str | None
) -> Select | None:
    """A subquery restricting which digests are in scope, or ``None``."""
    if sha256 is None and calculation_ref is None:
        return None
    statement = select(CalculationArtifact.sha256)
    if sha256 is not None:
        statement = statement.where(CalculationArtifact.sha256 == sha256)
    if calculation_ref is not None:
        statement = statement.join(
            Calculation, Calculation.id == CalculationArtifact.calculation_id
        ).where(Calculation.public_ref == calculation_ref)
    return statement


def _summaries(
    session: Session,
    *,
    sha256: str | None,
    calculation_ref: str | None,
    only_currently_broken: bool,
    offset: int,
    limit: int,
) -> tuple[list[ArtifactIntegrityDigestSummary], int]:
    stats = (
        select(
            ArtifactIntegrityEvent.sha256.label("sha256"),
            func.count().label("observation_count"),
            func.min(ArtifactIntegrityEvent.created_at).label("first_observed_at"),
            func.max(ArtifactIntegrityEvent.created_at).label("last_observed_at"),
            func.max(ArtifactIntegrityEvent.id).label("latest_id"),
            func.count(
                case((ArtifactIntegrityEvent.finding.in_(_BREAK_FINDINGS), 1))
            ).label("break_count"),
        )
        .group_by(ArtifactIntegrityEvent.sha256)
        .subquery()
    )
    # ``max(id)`` is the latest observation. The ids are monotonic and the
    # trust evaluator already reads "latest" the same way, so a second
    # definition of recency here could only introduce a disagreement.
    latest = ArtifactIntegrityEvent
    statement = select(stats, latest).join(latest, latest.id == stats.c.latest_id)

    scope = _digest_scope(sha256=sha256, calculation_ref=calculation_ref)
    if scope is not None:
        statement = statement.where(stats.c.sha256.in_(scope))
    if sha256 is not None:
        # A digest with recorded observations but no surviving artifact row
        # is still a real answer -- the ``store_dedup_verification`` context
        # records exactly that -- so an explicit digest is matched directly
        # rather than only through the artifact table.
        statement = statement.where(stats.c.sha256 == sha256)
    if only_currently_broken:
        statement = statement.where(latest.finding.in_(_BREAK_FINDINGS))

    total = session.scalar(
        select(func.count()).select_from(statement.subquery())
    )
    rows = session.execute(
        statement.order_by(stats.c.last_observed_at.desc(), stats.c.sha256)
        .offset(offset)
        .limit(limit)
    ).all()

    digests = [row.sha256 for row in rows]
    contexts = _contexts_by_digest(session, digests)
    references = _references_by_digest(session, digests)
    records = [
        ArtifactIntegrityDigestSummary(
            sha256=row.sha256,
            currently_broken=row[-1].finding.is_break,
            latest=_observation(row[-1]),
            first_observed_at=row.first_observed_at,
            last_observed_at=row.last_observed_at,
            observation_count=row.observation_count,
            break_count=row.break_count,
            detected_during=contexts.get(row.sha256, []),
            calculation_refs=references.get(row.sha256, ([], []))[0],
            filenames=references.get(row.sha256, ([], []))[1],
        )
        for row in rows
    ]
    return records, int(total or 0)


def _contexts_by_digest(
    session: Session, digests: list[str]
) -> dict[str, list]:
    if not digests:
        return {}
    rows = session.execute(
        select(ArtifactIntegrityEvent.sha256, ArtifactIntegrityEvent.detected_during)
        .where(ArtifactIntegrityEvent.sha256.in_(digests))
        .distinct()
        .order_by(ArtifactIntegrityEvent.sha256, ArtifactIntegrityEvent.detected_during)
    ).all()
    out: dict[str, list] = {}
    for digest, context in rows:
        out.setdefault(digest, []).append(context)
    return out


def _references_by_digest(
    session: Session, digests: list[str]
) -> dict[str, tuple[list[str], list[str]]]:
    """Which calculations and filenames point at each digest.

    Plural on purpose. One object may be shared by many
    ``calculation_artifact`` rows across unrelated calculations, and a
    surface that named only one of them would reproduce the very failure
    the digest key exists to avoid.
    """
    if not digests:
        return {}
    rows = session.execute(
        select(
            CalculationArtifact.sha256,
            Calculation.public_ref,
            CalculationArtifact.filename,
        )
        .join(Calculation, Calculation.id == CalculationArtifact.calculation_id)
        .where(CalculationArtifact.sha256.in_(digests))
        .order_by(CalculationArtifact.sha256, Calculation.public_ref)
    ).all()
    out: dict[str, tuple[list[str], list[str]]] = {}
    for digest, calculation_ref, filename in rows:
        refs, names = out.setdefault(digest, ([], []))
        if calculation_ref not in refs:
            refs.append(calculation_ref)
        if filename not in names:
            names.append(filename)
    return out


def search_artifact_integrity(
    session: Session,
    *,
    sha256: str | None = None,
    calculation_ref: str | None = None,
    only_currently_broken: bool = False,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> ScientificArtifactIntegrityResponse:
    """One record per digest that has ever been observed.

    :raises ValueError: pagination out of bounds, or a client-supplied
        sort (the vocabulary is v0-frozen, as on every other scientific
        read).
    """
    if sort is not None:
        raise ValueError("client_sort_not_supported")
    offset, limit = validate_pagination(offset, limit)
    records, total = _summaries(
        session,
        sha256=sha256,
        calculation_ref=calculation_ref,
        only_currently_broken=only_currently_broken,
        offset=offset,
        limit=limit,
    )
    return ScientificArtifactIntegrityResponse(
        request=IntegrityRequestEcho(
            filter={
                "sha256": sha256,
                "calculation_ref": calculation_ref,
                "only_currently_broken": only_currently_broken,
            },
            sort=_SORT,
        ),
        records=records,
        pagination=Pagination(
            offset=offset,
            limit=limit,
            returned=len(records),
            total=total,
            post_collapse_total=total,
        ),
    )


def artifact_integrity_history(
    session: Session,
    *,
    sha256: str,
    offset: int = 0,
    limit: int = 50,
) -> ScientificArtifactIntegrityHistoryResponse:
    """Every observation recorded for one digest, oldest first.

    Oldest first because the sequence is the finding: "broken, then
    verified, then broken again" is a claim about the storage layer that
    reverse-chronological order makes harder to read and that a
    current-state summary cannot express at all.
    """
    offset, limit = validate_pagination(offset, limit)
    summaries, _total = _summaries(
        session,
        sha256=sha256,
        calculation_ref=None,
        only_currently_broken=False,
        offset=0,
        limit=1,
    )
    total = int(
        session.scalar(
            select(func.count())
            .select_from(ArtifactIntegrityEvent)
            .where(ArtifactIntegrityEvent.sha256 == sha256)
        )
        or 0
    )
    rows = session.scalars(
        select(ArtifactIntegrityEvent)
        .where(ArtifactIntegrityEvent.sha256 == sha256)
        .order_by(ArtifactIntegrityEvent.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return ScientificArtifactIntegrityHistoryResponse(
        request=IntegrityRequestEcho(filter={"sha256": sha256}, sort="detected_at:asc"),
        sha256=sha256,
        summary=summaries[0] if summaries else None,
        observations=[_observation(row) for row in rows],
        pagination=Pagination(
            offset=offset,
            limit=limit,
            returned=len(rows),
            total=total,
            post_collapse_total=total,
        ),
    )


__all__ = [
    "artifact_integrity_history",
    "search_artifact_integrity",
]
