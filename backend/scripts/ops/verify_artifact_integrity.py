#!/usr/bin/env python
"""Verify that stored artifact bytes still match their digests, and record what they don't.

Corruption is otherwise found only when a reader stumbles on it, so an
artifact nobody downloads is checked by nothing. This pass closes that
gap for a *bounded* set of objects and writes an
``artifact_integrity_event`` for every break it finds, which the trust
evaluator then reads at read time (ADR 0014).

**This is not a cron job.** It re-reads whole objects, so its cost scales
with stored volume while its value scales with what someone will
actually cite. Run it when TCKDB is about to make a citable claim about
a set of records — that is, when cutting a dataset release (see
``backend/docs/deployment/cutting_a_dataset_release.md``) — or on a
sample, continuously, if you want a detection-time distribution rather
than a guarantee.

Usage::

    # Everything a release cites — the intended trigger.
    python backend/scripts/ops/verify_artifact_integrity.py --release rel_...

    # One calculation, or one digest.
    python backend/scripts/ops/verify_artifact_integrity.py --calculation-ref calc_...
    python backend/scripts/ops/verify_artifact_integrity.py --sha256 abc123...

    # A 2% sample of the whole corpus, for continuous background coverage.
    python backend/scripts/ops/verify_artifact_integrity.py --all --sample 0.02

    # Report unreferenced objects alongside (never deletes).
    python backend/scripts/ops/verify_artifact_integrity.py --all --orphans

    # Move month-old unreferenced objects out of the way. Reversible:
    # they are copied to ``reclaimed/<digest>``, not deleted.
    python backend/scripts/ops/verify_artifact_integrity.py --all --reclaim-orphans

    # Finally delete what has sat in the hold for a quarter. Irreversible.
    python backend/scripts/ops/verify_artifact_integrity.py --all --purge-hold-days 90

    # See what would be read without reading it.
    python backend/scripts/ops/verify_artifact_integrity.py --all --dry-run

Exit status is ``1`` when any break was found, so a release runbook can
gate on it.

Reclaiming orphans is deliberately two operators' decisions in sequence,
not one. Objects accumulate because ``store_artifact`` writes bytes
before the row that references them commits, so an unreferenced digest
may be an upload in flight rather than garbage; nothing in a single pass
can tell those apart with certainty, because there is no lock spanning
the database and the object store. So the first step only *moves* the
object, out of the content-addressed namespace and into a hold where it
can be put back, and the second step -- which is the one that destroys
bytes -- can only reach objects that have been unreachable for as long as
the operator says.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.calculation import Calculation, CalculationArtifact  # noqa: E402
from app.db.models.common import (  # noqa: E402
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
)
from app.services.artifact_integrity import (  # noqa: E402
    digests_with_recorded_breaks,
    record_integrity_observation,
    record_integrity_verified,
)
from app.services.artifact_storage import (  # noqa: E402
    RECLAIM_HOLD_PREFIX,
    S3_BUCKET,
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    _get_s3_client,
    content_addressed_key,
    hold_artifact_object,
    load_artifact_bytes,
    purge_held_object,
)


def _distinct_artifacts(
    session: Session,
    *,
    sha256: str | None,
    calculation_ref: str | None,
    release_ref: str | None,
    limit: int | None,
) -> list[CalculationArtifact]:
    """One representative artifact row per distinct digest in scope.

    Per *digest*, not per row: the store is content-addressed, so reading
    the same object once for each of the twelve rows that share it would
    multiply the sweep's cost by twelve and learn nothing new.
    """
    statement = select(CalculationArtifact).order_by(CalculationArtifact.id)
    if sha256:
        statement = statement.where(CalculationArtifact.sha256 == sha256)
    if calculation_ref:
        statement = statement.join(
            Calculation, Calculation.id == CalculationArtifact.calculation_id
        ).where(Calculation.public_ref == calculation_ref)
    if release_ref:
        ids = _release_calculation_ids(session, release_ref)
        if not ids:
            raise SystemExit(
                f"dataset release {release_ref!r} cites no calculations; "
                "nothing to verify"
            )
        statement = statement.where(CalculationArtifact.calculation_id.in_(ids))

    seen: dict[str, CalculationArtifact] = {}
    for row in session.scalars(statement):
        seen.setdefault(row.sha256, row)
        if limit is not None and len(seen) >= limit:
            break
    return list(seen.values())


def _release_calculation_ids(session: Session, release_ref: str) -> set[int]:
    """Every calculation a dataset release rests on, transitively.

    A release selects *products* — thermo, kinetics, statmech, transport,
    network solves — and saddle-point entries. It cannot select a
    calculation: ``SELECTABLE_RECORD_TYPES`` excludes it and a check
    constraint enforces that, so the earlier filter on
    ``record_type == calculation`` matched nothing and ``--release``
    swept an empty set while exiting 0. A release gate that verifies
    nothing and reports success is worse than no gate, because the
    runbook then cites it.

    Three hops, all of them things a reader can actually reach from the
    release:

    1. the selections that still stand (a withdrawn or superseded
       selection is not part of the release and its evidence is not what
       the release rests on);
    2. the calculations each selected record cites through its
       ``*_source_calculation`` table — the same helper the release
       artifact uses to print cited provenance, so the sweep and the
       published citation list cannot drift apart — plus, for a selected
       ``transition_state_entry``, the calculations attached to it;
    3. the transitive upstream closure over ``calculation_dependency``.
       A freq cited by a statmech was run on a geometry from an opt whose
       log is equally part of the evidence, and a reader following the
       provenance will land there.
    """
    from app.db.models.common import SubmissionRecordType
    from app.db.models.dataset_release import DatasetRelease
    from app.services.release.artifacts import load_selection_state
    from app.services.release.records import cited_calculation_ids

    release = session.scalars(
        select(DatasetRelease).where(DatasetRelease.public_ref == release_ref)
    ).first()
    if release is None:
        raise SystemExit(f"no dataset release with public_ref={release_ref!r}")

    by_type: dict[SubmissionRecordType, list[int]] = {}
    for selection in load_selection_state(session, release).active:
        by_type.setdefault(selection.record_type, []).append(selection.record_id)

    roots: set[int] = set()
    for record_type, record_ids in by_type.items():
        if record_type is SubmissionRecordType.transition_state_entry:
            roots.update(
                session.scalars(
                    select(Calculation.id).where(
                        Calculation.transition_state_entry_id.in_(sorted(set(record_ids)))
                    )
                ).all()
            )
            continue
        cited = cited_calculation_ids(
            session, record_type=record_type, record_ids=sorted(set(record_ids))
        )
        for ids in cited.values():
            roots.update(ids)
    return _dependency_closure(session, roots)


def _dependency_closure(session: Session, roots: set[int]) -> set[int]:
    """Add every calculation the roots depend on, however far up.

    One recursive CTE rather than a Python walk: the depth is unbounded
    and the sweep should not issue a query per level. Cycles cannot loop
    forever here because the CTE's ``UNION`` (not ``UNION ALL``) drops
    rows it has already produced.
    """
    if not roots:
        return set()
    from app.db.models.calculation import CalculationDependency

    seed = (
        select(Calculation.id)
        .where(Calculation.id.in_(sorted(roots)))
        .cte("evidence", recursive=True)
    )
    closure = seed.union(
        select(CalculationDependency.parent_calculation_id).join(
            seed, seed.c.id == CalculationDependency.child_calculation_id
        )
    )
    return set(session.scalars(select(closure.c.id)).all())


def _verify_one(
    session_factory,
    artifact: CalculationArtifact,
    *,
    client,
    bucket: str,
    previously_broken: frozenset[str] = frozenset(),
) -> str | None:
    """Read one object and record what it shows. Returns the finding, or None.

    A clean read is recorded **only** when the digest already carries a
    break, because that read is what clears the hard fail. Writing a row
    for every clean read would turn an incident log into a read log.
    """
    try:
        content = load_artifact_bytes(
            artifact.sha256,
            expected_bytes=artifact.bytes,
            client=client,
            bucket=bucket,
        )
        if artifact.sha256 in previously_broken:
            record_integrity_verified(
                sha256=artifact.sha256,
                detected_during=(
                    ArtifactIntegrityDetectionContext.verification_sweep
                ),
                observed_bytes=len(content),
                artifact_id=artifact.id,
                artifact_recorded_at=artifact.created_at,
                detail="re-read cleanly; supersedes the recorded break",
                session_factory=session_factory,
                storage_client=client,
                bucket=bucket,
            )
            return "repaired"
        return None
    except ArtifactIntegrityError as exc:
        record_integrity_observation(
            sha256=exc.sha256,
            finding=exc.finding,
            detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
            observed_sha256=exc.observed_sha256,
            expected_bytes=exc.expected_bytes,
            observed_bytes=exc.observed_bytes,
            artifact_id=artifact.id,
            artifact_recorded_at=artifact.created_at,
            detail=str(exc),
            session_factory=session_factory,
            storage_client=client,
            bucket=bucket,
        )
        return exc.finding.value
    except ArtifactStorageUnavailable as exc:
        if not getattr(exc, "missing", False):
            # The store did not answer. That says nothing about the
            # object, so recording a custody break would be a lie.
            print(f"  ! storage unavailable for {artifact.sha256}: {exc}")
            return "unavailable"
        record_integrity_observation(
            sha256=artifact.sha256,
            finding=ArtifactIntegrityFinding.object_missing,
            detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
            expected_bytes=artifact.bytes,
            artifact_id=artifact.id,
            artifact_recorded_at=artifact.created_at,
            detail=str(exc),
            session_factory=session_factory,
            storage_client=client,
            bucket=bucket,
        )
        return ArtifactIntegrityFinding.object_missing.value


def _find_orphans(
    session: Session, *, client, bucket: str, min_age_days: int
) -> list[str]:
    """Content-addressed objects no row references and nobody wrote recently.

    Nearly free here because the bucket is already being enumerated.

    The age floor is what makes the answer mean anything. A digest may be
    unreferenced simply because the upload that will reference it has not
    committed yet -- ``store_artifact`` writes the object before the row,
    which is the correct order -- so "unreferenced right now" and
    "garbage" are different claims for as long as any transaction could
    still be in flight. An object nobody has referenced for a month is
    the second thing; an object written four seconds ago is not.
    """
    referenced = set(session.scalars(select(CalculationArtifact.sha256)).all())
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    orphans: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            key = str(obj["Key"])
            if key.startswith(RECLAIM_HOLD_PREFIX):
                continue
            digest = key.rsplit("/", 1)[-1]
            if len(digest) != 64 or digest in referenced:
                continue
            modified = obj.get("LastModified")
            if modified is not None and modified > cutoff:
                continue
            orphans.append(digest)
    return orphans


def _reclaim_orphans(
    session_factory, orphans: list[str], *, client, bucket: str
) -> list[str]:
    """Move orphans to the reclaim hold, re-reading the references first.

    Two checks, not one. The first was against the reference set captured
    before the bucket was enumerated, which is stale by the length of the
    enumeration; this one is taken immediately before the move, in a fresh
    session, so a row committed during the sweep is seen. Neither check
    can be perfect -- there is no lock spanning the database and the
    object store -- which is exactly why the move is a move: the failure
    mode of being wrong is an object under a different key, not an object
    that no longer exists.
    """
    if not orphans:
        return []
    with session_factory() as session:
        referenced = set(
            session.scalars(
                select(CalculationArtifact.sha256).where(
                    CalculationArtifact.sha256.in_(sorted(orphans))
                )
            ).all()
        )
    held: list[str] = []
    for digest in orphans:
        if digest in referenced:
            print(f"  ! skipping {digest}: referenced since the scan began")
            continue
        if hold_artifact_object(digest, client=client, bucket=bucket):
            held.append(digest)
    return held


def _purge_hold(
    session: Session, *, client, bucket: str, min_age_days: int
) -> list[str]:
    """Delete held objects that have sat in the hold long enough.

    This is the only irreversible operation in this script, and it is
    reachable only for objects that have already been out of the
    content-addressed namespace for ``min_age_days``. That is what makes
    it safe: nothing can have deduplicated against a held object, so
    nothing can have started referencing it since it was held. A digest
    that somehow *is* referenced again is skipped anyway, because a
    surprise here would mean the reasoning above is wrong and the right
    response to that is to keep the bytes.
    """
    referenced = set(session.scalars(select(CalculationArtifact.sha256)).all())
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    purged: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=RECLAIM_HOLD_PREFIX):
        for obj in page.get("Contents", []):
            digest = str(obj["Key"]).rsplit("/", 1)[-1]
            if len(digest) != 64 or digest in referenced:
                continue
            modified = obj.get("LastModified")
            if modified is not None and modified > cutoff:
                continue
            purge_held_object(digest, client=client, bucket=bucket)
            purged.append(digest)
    return purged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="every stored digest")
    scope.add_argument("--sha256", help="one content-addressed digest")
    scope.add_argument("--calculation-ref", help="artifacts of one calculation")
    scope.add_argument("--release", help="artifacts cited by one dataset release")
    parser.add_argument("--limit", type=int, default=None, help="cap digests read")
    parser.add_argument(
        "--sample",
        type=float,
        default=None,
        help="verify this fraction (0-1) of the in-scope digests, chosen at random",
    )
    parser.add_argument("--seed", type=int, default=None, help="sampling seed")
    parser.add_argument(
        "--orphans",
        action="store_true",
        help="also report objects in the bucket that no row references",
    )
    parser.add_argument(
        "--reclaim-orphans",
        action="store_true",
        help=(
            "move reported orphans to the reclaim hold (implies --orphans; "
            "reversible - nothing is deleted)"
        ),
    )
    parser.add_argument(
        "--orphan-age-days",
        type=int,
        default=30,
        help=(
            "only treat an unreferenced object as an orphan once it is this "
            "old, so an upload still in flight is never mistaken for garbage"
        ),
    )
    parser.add_argument(
        "--purge-hold-days",
        type=int,
        default=None,
        help=(
            "DELETE held objects that have been in the reclaim hold for at "
            "least this many days. Irreversible."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be read without reading or recording",
    )
    args = parser.parse_args()

    from app.api.deps import SessionLocal

    client = _get_s3_client()
    bucket = S3_BUCKET

    with SessionLocal() as session:
        artifacts = _distinct_artifacts(
            session,
            sha256=args.sha256,
            calculation_ref=args.calculation_ref,
            release_ref=args.release,
            limit=args.limit,
        )
        if args.sample is not None:
            rng = random.Random(args.seed)
            keep = max(1, round(len(artifacts) * args.sample)) if artifacts else 0
            artifacts = rng.sample(artifacts, keep)
        want_orphans = (args.orphans or args.reclaim_orphans) and not args.dry_run
        orphans = (
            _find_orphans(
                session,
                client=client,
                bucket=bucket,
                min_age_days=args.orphan_age_days,
            )
            if want_orphans
            else []
        )
        purged = (
            _purge_hold(
                session,
                client=client,
                bucket=bucket,
                min_age_days=args.purge_hold_days,
            )
            if args.purge_hold_days is not None and not args.dry_run
            else []
        )
        # Which of these already carry a break, so a clean read of one
        # can be recorded as the observation that clears it.
        previously_broken = digests_with_recorded_breaks(
            session, [artifact.sha256 for artifact in artifacts]
        )

    print(f"bucket={bucket} digests_in_scope={len(artifacts)}")
    if args.dry_run:
        for artifact in artifacts:
            print(f"  would read {content_addressed_key(artifact.sha256)}")
        return 0

    findings: dict[str, int] = {}
    for artifact in artifacts:
        finding = _verify_one(
            SessionLocal,
            artifact,
            client=client,
            bucket=bucket,
            previously_broken=previously_broken,
        )
        if finding is None:
            continue
        findings[finding] = findings.get(finding, 0) + 1
        label = "REPAIRED" if finding == "repaired" else "BREAK"
        print(f"  {label} {finding}: sha={artifact.sha256} file={artifact.filename}")

    breaks = {k: v for k, v in findings.items() if k not in ("repaired", "unavailable")}
    clean = len(artifacts) - sum(findings.values())
    print(
        f"verified={clean + findings.get('repaired', 0)} "
        f"breaks={sum(breaks.values())} repaired={findings.get('repaired', 0)}"
    )
    for name, count in sorted(findings.items()):
        print(f"  {name}: {count}")
    if orphans:
        print(
            f"unreferenced objects older than {args.orphan_age_days}d: {len(orphans)}"
        )
        for digest in orphans[:50]:
            print(f"  orphan {digest}")
    if args.reclaim_orphans:
        held = _reclaim_orphans(SessionLocal, orphans, client=client, bucket=bucket)
        print(
            f"moved to the reclaim hold ({RECLAIM_HOLD_PREFIX}): {len(held)} "
            "- bytes retained, restore by copying the key back"
        )
    if purged:
        print(f"purged from the reclaim hold (DELETED): {len(purged)}")

    return 1 if breaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
