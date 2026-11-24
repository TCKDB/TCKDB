#!/usr/bin/env python
"""Queue new machine reviews for submissions whose latest review is in a previous format.

A review stored in a format this release no longer reads shows as unparsed.
This lists the submissions whose newest review is in the previous format and,
with ``--commit``, queues one machine-review job for each through the job
service (the same switches and open-job guard as a request from the UI).
A submission that already has a queued or running job is skipped.

Usage::

    # Plan only -- the default. Lists the submissions; writes nothing.
    python scripts/ops/rerun_machine_reviews.py --requested-by ADMIN_USERNAME

    # Queue the jobs. A database not named tckdb_test* also needs
    # --i-know-this-is-deployed. --process runs the queued jobs in this
    # process until none is left; omit it when the app's own worker is running.
    python scripts/ops/rerun_machine_reviews.py --requested-by ADMIN_USERNAME \
        --commit --i-know-this-is-deployed --process

The reviewer mode must be configured (``AI_REVIEW_ASSISTANT_MODE`` other than
``off``). Submissions are named by ``public_ref``, never by primary key.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")

_PROVIDER_FOR_MODE = {"test": "fake", "cloud": "cloud", "local": "local"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--requested-by", required=True, metavar="USERNAME", help="an admin user recorded as the requester"
    )
    parser.add_argument(
        "--provider",
        choices=("local", "cloud", "fake"),
        help="provider key of the jobs; default: the one matching AI_REVIEW_ASSISTANT_MODE",
    )
    parser.add_argument("--limit", type=int, metavar="N", help="queue at most N submissions")
    parser.add_argument("--commit", action="store_true", help="queue the jobs; without this the default is a plan")
    parser.add_argument(
        "--i-know-this-is-deployed",
        action="store_true",
        help="required alongside --commit on a database not named tckdb_test*",
    )
    parser.add_argument(
        "--process",
        action="store_true",
        help="after queueing, run queued jobs in this process until none is left",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    from sqlalchemy import select

    from app.api.config import settings
    from app.api.deps import SessionLocal
    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole, MachineReviewProviderKey
    from app.services.machine_review.rerun import (
        find_submissions_with_previous_format_review,
        queue_reruns,
    )

    if args.commit and not _TEST_DB_NAME.match(settings.db_name) and not args.i_know_this_is_deployed:
        print(
            "Refusing --commit: the configured database does not match "
            f"{_TEST_DB_NAME.pattern!r}. If this is intentionally a deployed "
            "database, pass --i-know-this-is-deployed as well.",
            file=sys.stderr,
        )
        return 2

    provider_name = args.provider or _PROVIDER_FOR_MODE.get(settings.ai_review_assistant_mode)
    if provider_name is None and args.commit:
        print("No provider: AI_REVIEW_ASSISTANT_MODE is off. Set it, or pass --provider.", file=sys.stderr)
        return 2

    with SessionLocal() as session:
        requester = session.scalar(select(AppUser).where(AppUser.username == args.requested_by))
        if requester is None or requester.role is not AppUserRole.admin:
            print("--requested-by must name an existing admin user.", file=sys.stderr)
            return 2

        submissions = find_submissions_with_previous_format_review(session)
        if args.limit is not None:
            submissions = submissions[: args.limit]
        print(f"{len(submissions)} submission(s) whose latest machine review is in the previous format:")
        for submission in submissions:
            print(f"  {submission.public_ref}")

        if not args.commit:
            print("\nPlan only -- nothing was queued. Re-run with --commit to queue the jobs.")
            if provider_name is None:
                print("--commit will need AI_REVIEW_ASSISTANT_MODE set or --provider.")
            return 0

        report = queue_reruns(
            session,
            submissions,
            requester=requester,
            provider_key=MachineReviewProviderKey(provider_name),
            settings_obj=settings,
        )
        session.commit()

    print(f"\nQueued {len(report.queued)} job(s).")
    for ref in report.already_open:
        print(f"  skipped (a job is already queued or running): {ref}")
    for ref, reason in report.refused.items():
        print(f"  refused ({reason}): {ref}")

    if args.process and report.queued:
        from app.workers.machine_review_worker import process_one_cycle

        ran = 0
        while process_one_cycle():
            ran += 1
        print(f"Ran {ran} job(s) in this process.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
