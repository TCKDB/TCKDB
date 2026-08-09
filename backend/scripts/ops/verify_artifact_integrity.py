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

    # See what would be read without reading it.
    python backend/scripts/ops/verify_artifact_integrity.py --all --dry-run

Exit status is ``1`` when any break was found, so a release runbook can
gate on it.
"""

from __future__ import annotations

import argparse
import random
import sys
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
from app.services.artifact_integrity import record_integrity_failure  # noqa: E402
from app.services.artifact_storage import (  # noqa: E402
    S3_BUCKET,
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    _get_s3_client,
    content_addressed_key,
    load_artifact_bytes,
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
        statement = statement.where(
            CalculationArtifact.calculation_id.in_(
                _release_calculation_ids(session, release_ref)
            )
        )

    seen: dict[str, CalculationArtifact] = {}
    for row in session.scalars(statement):
        seen.setdefault(row.sha256, row)
        if limit is not None and len(seen) >= limit:
            break
    return list(seen.values())


def _release_calculation_ids(session: Session, release_ref: str):
    """Calculation ids a dataset release selects directly.

    **Scope limit, stated rather than hidden.** A release selects
    *products* — thermo, kinetics, statmech, transport — and only
    sometimes a calculation directly. The calculations those products
    cite as sources are reached through per-product source tables and are
    NOT yet included here; verifying a release therefore covers its
    directly-selected calculations only. Sweeping the full transitive
    evidence of a release is the right eventual behaviour and is left as
    a separate change rather than approximated silently.
    """
    from app.db.models.common import SubmissionRecordType
    from app.db.models.dataset_release import DatasetRelease, ReleaseSelection

    release_id = session.scalar(
        select(DatasetRelease.id).where(DatasetRelease.public_ref == release_ref)
    )
    if release_id is None:
        raise SystemExit(f"no dataset release with public_ref={release_ref!r}")
    return (
        select(ReleaseSelection.record_id)
        .where(ReleaseSelection.dataset_release_id == release_id)
        .where(ReleaseSelection.record_type == SubmissionRecordType.calculation)
        .scalar_subquery()
    )


def _verify_one(
    session_factory,
    artifact: CalculationArtifact,
    *,
    client,
    bucket: str,
) -> str | None:
    """Read one object and record any break. Returns the finding, or None."""
    try:
        load_artifact_bytes(
            artifact.sha256,
            expected_bytes=artifact.bytes,
            client=client,
            bucket=bucket,
        )
        return None
    except ArtifactIntegrityError as exc:
        record_integrity_failure(
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
        record_integrity_failure(
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


def _report_orphans(session: Session, *, client, bucket: str) -> list[str]:
    """List content-addressed objects no row references. Never deletes.

    Nearly free here because the bucket is already being enumerated. It
    stays a report: ``artifact_persistence`` retains objects after a
    failed upload precisely because a digest may be shared with a
    committed row or a concurrent transaction, and a collector that
    cannot see concurrent writers is a data-loss mechanism.
    """
    referenced = set(session.scalars(select(CalculationArtifact.sha256)).all())
    orphans: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            digest = str(obj["Key"]).rsplit("/", 1)[-1]
            if len(digest) == 64 and digest not in referenced:
                orphans.append(digest)
    return orphans


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
        orphans = (
            _report_orphans(session, client=client, bucket=bucket)
            if args.orphans and not args.dry_run
            else []
        )

    print(f"bucket={bucket} digests_in_scope={len(artifacts)}")
    if args.dry_run:
        for artifact in artifacts:
            print(f"  would read {content_addressed_key(artifact.sha256)}")
        return 0

    findings: dict[str, int] = {}
    for artifact in artifacts:
        finding = _verify_one(
            SessionLocal, artifact, client=client, bucket=bucket
        )
        if finding is not None:
            findings[finding] = findings.get(finding, 0) + 1
            print(f"  BREAK {finding}: sha={artifact.sha256} file={artifact.filename}")

    verified = len(artifacts) - sum(findings.values())
    print(f"verified={verified} breaks={sum(findings.values())}")
    for name, count in sorted(findings.items()):
        print(f"  {name}: {count}")
    if orphans:
        print(f"unreferenced objects (reported, NOT deleted): {len(orphans)}")
        for digest in orphans[:50]:
            print(f"  orphan {digest}")

    recorded = {k: v for k, v in findings.items() if k != "unavailable"}
    return 1 if recorded else 0


if __name__ == "__main__":
    raise SystemExit(main())
