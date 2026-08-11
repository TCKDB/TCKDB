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

    # Undo a reclaim: put one held object back at its content-addressed key.
    python backend/scripts/ops/verify_artifact_integrity.py --restore abc123...

    # See what would be read without reading it.
    python backend/scripts/ops/verify_artifact_integrity.py --all --dry-run

    # Accept a scope that is legitimately empty (a fresh deployment).
    python backend/scripts/ops/verify_artifact_integrity.py --all --allow-empty

Exit status separates the two ways this stops being a clean gate: ``1``
when a break was recorded, ``2`` when something in scope was **not
checked at all** -- an empty population, or a store that would not
answer. A runbook gates on "not zero"; an operator triaging needs to
know which, because "the evidence is corrupt" and "we could not look"
call for opposite responses. ``--allow-empty`` is the explicit opt-out
for a scope that is legitimately empty.

**A pass has to mean something was checked.** The defect this script
carried was not a wrong answer but a confident one over an empty set:
``--release`` filtered on a ``record_type`` the enum excludes, matched
no digests, printed a clean summary and exited 0, and the release
runbook cited that as a gate. Everything below that distinguishes
"nothing was wrong" from "nothing was checked" is here because of it,
and because the same silence had three further forms -- any scope
resolving to zero digests, a storage outage counted as neither a break
nor a verification, and a digest recorded on the ``store_artifact``
dedup path, which by construction has no ``calculation_artifact`` row
to be found through and so could not be swept by digest at all.

Reclaiming orphans is deliberately two operators' decisions in sequence,
not one. Objects accumulate because ``store_artifact`` writes bytes
before the row that references them commits, so an unreferenced digest
may be an upload in flight rather than garbage; nothing in a single pass
can tell those apart with certainty, because there is no lock spanning
the database and the object store. So the first step only *moves* the
object, out of the content-addressed namespace and into a hold where it
can be put back, and the second step -- the one that destroys bytes --
re-reads the references and refuses any digest a row points at.

**The move can lose a race, and what that costs is a key, not bytes.**
``store_artifact`` deduplicates: an upload of bytes already at the
content-addressed key verifies and returns without writing, transaction
still open. So a genuine month-old orphan can be deduplicated against
mid-sweep and moved anyway, leaving a committed row pointing at a held
digest. The window is seconds and the recovery is ``--restore <sha256>``;
until someone runs it, the next read of that artifact records
``object_missing`` and hard-fails a legitimate calculation. Both age
gates have enforced floors for this reason -- at zero they stop being
guards -- and an object the store will not date is left alone rather than
assumed ancient.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.calculation import (  # noqa: E402
    ArtifactIntegrityEvent,
    Calculation,
    CalculationArtifact,
)
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
    head_artifact_object,
    hold_artifact_object,
    load_artifact_bytes,
    purge_held_object,
    restore_held_object,
)


@dataclass(frozen=True)
class _VerificationTarget:
    """One object to re-read, and what the database says it should be.

    Not a ``CalculationArtifact``, and the difference is the whole point
    of ``--sha256``. The unit of verification is the *object*, and an
    object can be in scope with no artifact row behind it: the
    ``store_dedup_verification`` context records a break against a digest
    whose referencing row is being refused, so it never commits. Making
    the sweep's scope a list of artifact rows meant the one digest most
    in need of a re-read by hand -- the one an operator was just told
    about, that no row points at -- resolved to an empty scope and a
    clean exit.

    ``expected_bytes`` may be ``None`` for such a target; the digest
    check still runs, and it is the digest that the key *is*.
    """

    sha256: str
    expected_bytes: int | None
    artifact_id: int | None
    artifact_recorded_at: datetime | None
    filename: str

    @classmethod
    def from_artifact(cls, row: CalculationArtifact) -> "_VerificationTarget":
        return cls(
            sha256=row.sha256,
            expected_bytes=row.bytes,
            artifact_id=row.id,
            artifact_recorded_at=row.created_at,
            filename=row.filename,
        )


def _scope_targets(
    session: Session,
    *,
    sha256: str | None,
    calculation_ref: str | None,
    release_ref: str | None,
    limit: int | None,
) -> list[_VerificationTarget]:
    """One target per distinct digest in scope.

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
            raise _not_verified(
                f"dataset release {release_ref!r} cites no calculations; "
                "nothing to verify"
            )
        statement = statement.where(CalculationArtifact.calculation_id.in_(ids))

    seen: dict[str, _VerificationTarget] = {}
    for row in session.scalars(statement):
        seen.setdefault(row.sha256, _VerificationTarget.from_artifact(row))
        if limit is not None and len(seen) >= limit:
            break
    if sha256 and not seen:
        recorded = _recorded_only_target(session, sha256)
        if recorded is not None:
            seen[sha256] = recorded
    return list(seen.values())


def _recorded_only_target(
    session: Session, sha256: str
) -> _VerificationTarget | None:
    """A digest the custody record knows and ``calculation_artifact`` does not.

    ``store_artifact`` deduplicates: an upload of bytes already at the
    content-addressed key verifies before writing a row, so a break
    detected there is recorded against a digest whose only referencing
    row is in a transaction that is being rolled back. That digest is
    exactly the one an operator has been handed and asked to re-check,
    and routing ``--sha256`` solely through ``calculation_artifact``
    turned it into an empty scope and an exit 0 -- the same shape as the
    ``--release`` filter this script was fixed for.

    The read surface already refuses to resolve an explicit digest
    through the artifact table for this reason; see
    ``app.services.scientific_read.artifact_integrity_reads``.
    """
    event = session.scalars(
        select(ArtifactIntegrityEvent)
        .where(ArtifactIntegrityEvent.sha256 == sha256)
        .order_by(ArtifactIntegrityEvent.id.desc())
        .limit(1)
    ).first()
    if event is None:
        return None
    return _VerificationTarget(
        sha256=sha256,
        expected_bytes=event.expected_bytes,
        artifact_id=event.artifact_id,
        artifact_recorded_at=event.artifact_recorded_at,
        filename=f"(no artifact row; recorded during {event.detected_during.value})",
    )


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
        raise _not_verified(f"no dataset release with public_ref={release_ref!r}")

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


#: The outcome of a read that told us nothing about the object. Counted
#: apart from both breaks and verifications, because it is neither: the
#: store declined to answer, so the digest is exactly as unchecked as it
#: was before the sweep ran. Naming it here rather than as a bare string
#: is what lets :func:`main` refuse to call such a run a pass.
UNAVAILABLE = "unavailable"
REPAIRED = "repaired"


def _verify_one(
    session_factory,
    target: _VerificationTarget,
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
            target.sha256,
            expected_bytes=target.expected_bytes,
            client=client,
            bucket=bucket,
        )
        if target.sha256 in previously_broken:
            record_integrity_verified(
                sha256=target.sha256,
                detected_during=(
                    ArtifactIntegrityDetectionContext.verification_sweep
                ),
                observed_bytes=len(content),
                artifact_id=target.artifact_id,
                artifact_recorded_at=target.artifact_recorded_at,
                detail="re-read cleanly; supersedes the recorded break",
                session_factory=session_factory,
                storage_client=client,
                bucket=bucket,
            )
            return REPAIRED
        return None
    except ArtifactIntegrityError as exc:
        record_integrity_observation(
            sha256=exc.sha256,
            finding=exc.finding,
            detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
            observed_sha256=exc.observed_sha256,
            expected_bytes=exc.expected_bytes,
            observed_bytes=exc.observed_bytes,
            artifact_id=target.artifact_id,
            artifact_recorded_at=target.artifact_recorded_at,
            detail=str(exc),
            session_factory=session_factory,
            storage_client=client,
            bucket=bucket,
        )
        return exc.finding.value
    except ArtifactStorageUnavailable as exc:
        if not getattr(exc, "missing", False):
            # The store did not answer. That says nothing about the
            # object, so recording a custody break would be a lie -- and
            # it says nothing about the object's soundness either, so
            # calling the run a pass would be the opposite lie. It is
            # returned as its own outcome and counted as unchecked.
            print(f"  ! storage unavailable for {target.sha256}: {exc}")
            return UNAVAILABLE
        record_integrity_observation(
            sha256=target.sha256,
            finding=ArtifactIntegrityFinding.object_missing,
            detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
            expected_bytes=target.expected_bytes,
            artifact_id=target.artifact_id,
            artifact_recorded_at=target.artifact_recorded_at,
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
            if not _older_than(obj.get("LastModified"), cutoff):
                continue
            orphans.append(digest)
    return orphans


def _older_than(modified, cutoff) -> bool:
    """Is the store willing to say this object predates ``cutoff``?

    ``None`` is **not** old enough. A store that will not date an object
    has told us its age is unknown, and the age floor is the only thing
    standing between a reclaim and an upload still in flight -- so
    treating an undated object as ancient would quietly delete the guard
    for exactly the objects we know least about. Unknown age means leave
    it alone; an operator who wants it gone can say so by digest.
    """
    return modified is not None and modified <= cutoff


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
    reachable only for objects that have been out of the
    content-addressed namespace for ``min_age_days``. Being held is most
    of the argument: an object that is not at its content-addressed key
    cannot be deduplicated against, so an upload of the same bytes
    repopulates the key rather than pointing at this copy.

    The reference re-check is the rest of it, and it is not decoration.
    A row *can* end up referencing a held digest -- an upload that
    deduplicated against the object moments before it was moved commits
    afterwards -- and this is the last thing standing between that row
    and the permanent loss of its bytes. If a held digest turns out to be
    referenced, the reasoning above has failed somewhere, and the right
    response to a failure of reasoning about an irreversible operation is
    to keep the bytes and let a human look.
    """
    referenced = set(session.scalars(select(CalculationArtifact.sha256)).all())
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    purged: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=RECLAIM_HOLD_PREFIX):
        for obj in page.get("Contents", []):
            digest = str(obj["Key"]).rsplit("/", 1)[-1]
            if len(digest) != 64:
                continue
            if digest in referenced:
                print(
                    f"  ! NOT purging {digest}: a row references it. Restore it "
                    f"with --restore {digest}"
                )
                continue
            if not _older_than(obj.get("LastModified"), cutoff):
                continue
            purge_held_object(digest, client=client, bucket=bucket)
            purged.append(digest)
    return purged


def _restore_held(session: Session, digest: str, *, client, bucket: str) -> bool:
    """Put a held object back at its content-addressed key.

    The documented recovery for the one race the reclaim cannot close.
    Refuses when the key is already occupied: that is the ordinary
    outcome of somebody re-uploading the same bytes, and the copy sitting
    there is the one every row is reading. Overwriting it would either be
    a no-op or would destroy the evidence of a genuine corruption, and
    ADR 0014 does not let this script do the second one.
    """
    if head_artifact_object(digest, client=client, bucket=bucket):
        print(
            f"  ! {digest} is already at its content-addressed key; leaving both "
            "copies alone"
        )
        return False
    if not restore_held_object(digest, client=client, bucket=bucket):
        print(f"  ! nothing held under {digest}")
        return False
    referenced = session.scalar(
        select(func.count())
        .select_from(CalculationArtifact)
        .where(CalculationArtifact.sha256 == digest)
    )
    print(f"  restored {digest} ({referenced or 0} row(s) reference it)")
    return True


#: Floors on the two age gates, enforced rather than merely defaulted.
#:
#: The gates are the whole safety argument, and both degenerate at zero:
#: ``--orphan-age-days 0`` reclaims objects an upload wrote a second ago
#: and has not yet committed a row for, and ``--purge-hold-days 0``
#: deletes a held object before an operator could plausibly notice it was
#: held. A default nobody is required to keep is not a guard, so these
#: are rejected rather than warned about. A week is longer than any
#: upload transaction this system can hold open and short enough that an
#: operator clearing a full bucket is not blocked for a month.
MIN_ORPHAN_AGE_DAYS = 7
MIN_HOLD_DAYS = 7


#: Exit codes. ``1`` keeps its documented meaning -- a break was recorded
#: -- so an existing runbook that gates on it is unaffected. ``2`` is the
#: answer to the question this script could not previously express: the
#: run is not a pass, and not because anything was found wrong, but
#: because part of the scope was never read. A gate on "not zero" catches
#: both; triage needs them apart, because a corrupt object and a store
#: that would not answer call for opposite first moves.
EXIT_OK = 0
EXIT_BREAK = 1
EXIT_NOT_VERIFIED = 2


def _not_verified(message: str) -> SystemExit:
    """Abort with the "nothing was checked" code, saying why.

    ``raise SystemExit("text")`` exits ``1``, which under the codes above
    would claim a break was recorded. Every precondition failure in this
    script is the other thing -- the run did not verify what it was asked
    to -- and that includes argparse's own usage errors, which already
    exit ``2``. So ``2`` reads consistently as "fix the invocation, the
    scope or the store, then run it again", whichever layer refused.
    """
    print(f"RESULT: NOT VERIFIED. {message}", file=sys.stderr)
    return SystemExit(EXIT_NOT_VERIFIED)


def _validate_age_floors(args) -> None:
    if args.orphan_age_days < MIN_ORPHAN_AGE_DAYS:
        raise _not_verified(
            f"--orphan-age-days must be at least {MIN_ORPHAN_AGE_DAYS}: an "
            "object younger than that may belong to an upload that has not "
            "committed its row yet"
        )
    if args.purge_hold_days is not None and args.purge_hold_days < MIN_HOLD_DAYS:
        raise _not_verified(
            f"--purge-hold-days must be at least {MIN_HOLD_DAYS}: the hold "
            "exists so a mistaken reclaim can be noticed and undone, and it "
            "cannot do that if it is emptied immediately"
        )


def _scope_description(args) -> str:
    """How the operator named this scope, for a refusal that can be acted on."""
    if args.sha256:
        return f"--sha256 {args.sha256}"
    if args.calculation_ref:
        return f"--calculation-ref {args.calculation_ref}"
    if args.release:
        return f"--release {args.release}"
    return "--all"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="every stored digest")
    scope.add_argument("--sha256", help="one content-addressed digest")
    scope.add_argument("--calculation-ref", help="artifacts of one calculation")
    scope.add_argument("--release", help="artifacts cited by one dataset release")
    scope.add_argument(
        "--restore",
        metavar="SHA256",
        help="copy one held object back to its content-addressed key and stop",
    )
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
            "old, so an upload still in flight is never mistaken for garbage "
            f"(minimum {MIN_ORPHAN_AGE_DAYS})"
        ),
    )
    parser.add_argument(
        "--purge-hold-days",
        type=int,
        default=None,
        help=(
            "DELETE held objects that have been in the reclaim hold for at "
            f"least this many days (minimum {MIN_HOLD_DAYS}). Irreversible."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be read without reading or recording",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "accept a scope that resolves to zero digests. Without this an "
            "empty scope exits "
            f"{EXIT_NOT_VERIFIED}, because a sweep that checked nothing is "
            "not a sweep that found nothing"
        ),
    )
    args = parser.parse_args()
    _validate_age_floors(args)

    from app.api.deps import SessionLocal

    client = _get_s3_client()
    bucket = S3_BUCKET

    if args.restore:
        with SessionLocal() as session:
            return EXIT_OK if _restore_held(
                session, args.restore, client=client, bucket=bucket
            ) else EXIT_BREAK

    with SessionLocal() as session:
        targets = _scope_targets(
            session,
            sha256=args.sha256,
            calculation_ref=args.calculation_ref,
            release_ref=args.release,
            limit=args.limit,
        )
        if args.sample is not None:
            rng = random.Random(args.seed)
            keep = max(1, round(len(targets) * args.sample)) if targets else 0
            targets = rng.sample(targets, keep)
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
            session, [target.sha256 for target in targets]
        )

    print(f"bucket={bucket} digests_in_scope={len(targets)}")
    if args.dry_run:
        for target in targets:
            print(f"  would read {content_addressed_key(target.sha256)}")
        # A dry run is a plan, and an empty plan for a named scope is the
        # cheapest place to find out the name was wrong.
        return EXIT_OK if targets else _refuse_empty_scope(args)

    findings: dict[str, int] = {}
    for target in targets:
        finding = _verify_one(
            SessionLocal,
            target,
            client=client,
            bucket=bucket,
            previously_broken=previously_broken,
        )
        if finding is None:
            continue
        findings[finding] = findings.get(finding, 0) + 1
        label = {REPAIRED: "REPAIRED", UNAVAILABLE: "UNCHECKED"}.get(finding, "BREAK")
        print(f"  {label} {finding}: sha={target.sha256} file={target.filename}")

    breaks = {
        name: count
        for name, count in findings.items()
        if name not in (REPAIRED, UNAVAILABLE)
    }
    break_count = sum(breaks.values())
    unchecked = findings.get(UNAVAILABLE, 0)
    repaired = findings.get(REPAIRED, 0)
    # ``verified`` counts objects whose bytes were actually read back and
    # hashed correctly on this run: the silently-clean ones plus the ones
    # whose clean read cleared a recorded break. It is printed first and
    # unconditionally, because it is the number that says whether this
    # run is evidence of anything at all.
    verified = len(targets) - sum(findings.values()) + repaired
    print(
        f"verified={verified} breaks={break_count} repaired={repaired} "
        f"unchecked={unchecked} in_scope={len(targets)}"
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
            "- bytes retained; undo any one of them with --restore <sha256>"
        )
    if purged:
        print(f"purged from the reclaim hold (DELETED): {len(purged)}")

    # After the orphan and purge reports, not before: those are useful
    # findings in their own right and an empty verification scope is no
    # reason to withhold them.
    if not targets:
        return _refuse_empty_scope(args)
    if break_count:
        print(
            f"RESULT: {break_count} break(s) recorded. The owning "
            "calculations now hard-fail; see /scientific/artifacts/integrity."
        )
        return EXIT_BREAK
    if unchecked:
        print(
            f"RESULT: NOT VERIFIED. {unchecked} of {len(targets)} digest(s) "
            "could not be read -- the store did not answer. Those objects are "
            "exactly as unchecked as before this ran, and this is not a pass. "
            "Re-run once the store is reachable."
        )
        return EXIT_NOT_VERIFIED
    print(f"RESULT: {verified} digest(s) verified, no breaks.")
    return EXIT_OK


def _refuse_empty_scope(args) -> int:
    """Nothing in scope is a scope error, never a clean bill of health.

    The three ways to land here are a mistyped ref, a release whose cited
    calculations retained no artifacts at all, and a corpus that really is
    empty. The first two are the failure ADR 0014 names the sweep as the
    guard against, and the third is indistinguishable from them without
    the operator saying so -- which is what ``--allow-empty`` is for, on
    the same reasoning as the age floors: a guard any invocation may
    silently satisfy is not a guard.
    """
    scope = _scope_description(args)
    if args.allow_empty:
        print(
            f"RESULT: {scope} matched no digests, accepted under "
            "--allow-empty. Nothing was verified."
        )
        return EXIT_OK
    print(
        f"RESULT: NOT VERIFIED. {scope} matched no digests, so nothing was "
        "checked. That is not the same as nothing being wrong. Confirm the "
        "scope is right -- a release whose cited calculations retained no "
        "artifacts has no evidence to verify, which is itself worth knowing "
        "-- then pass --allow-empty to accept it."
    )
    return EXIT_NOT_VERIFIED


if __name__ == "__main__":
    raise SystemExit(main())
