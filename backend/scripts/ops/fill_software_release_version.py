#!/usr/bin/env python
"""Give calculations on a version-less ``software_release`` the version their logs state.

Curator tool for issue #305, item 3. For every ``software_release`` of the
named program whose ``version`` is NULL, reads each citing calculation's
stored ``output_log`` (then ``input``) artifact, parses the program's own
startup banner, and -- with ``--commit`` -- re-points the calculation at the
release that banner describes, recording the banner on the calculation
(``observed_software_banner``, ``software_reconciliation_status=enriched``;
DR-0008). The version-less release is never modified; a recorded version is
never overwritten. See ``app/services/software_release_version_fill.py`` for
why the row is not filled in place.

Attestation mode (``--attest-version``; issue #305, decision (a)): where no
stored artifact carries a banner, the person who ran the calculations can
state the version. The statement is recorded verbatim in
``software_version_attestation`` (who, when, which release, which version),
and each calculation it re-points gets a
``software_version_attestation_calculation`` row with its release before and
after, all in one transaction. Only calculations the attester deposited
(``created_by``) are eligible; one whose stored artifact names a version is
reported ``banner_available`` and left to the banner mode. The target is the
version-less release's own fields with ``version`` set exactly as attested
-- ``("6", NULL, NULL)`` for a bare ORCA row; nothing else is inferred. A
second run finds nothing left to move and writes nothing.

Calculations that were ever approved are reported as ``accepted`` and left
alone -- ``trg_as_root_calculation`` refuses the UPDATE without an
``accepted_science_repair`` declaration, which only a migration makes.

Calculations pinned to an execution-environment manifest are reported as
``environment_bound`` and left alone (the manifest fixes their release).
For each fillable calculation the dry run also lists the reproducibility
assessments that would go stale and any approved thermo/statmech record
citing it, and says when a Gaussian banner fills ``build`` too.

Needs the artifact store configured (``S3_*``), exactly as the API does.

Usage::

    # Dry run -- the default. Reads artifacts, writes nothing.
    python backend/scripts/ops/fill_software_release_version.py --software ORCA

    # Re-point the calculations whose logs name a version.
    python backend/scripts/ops/fill_software_release_version.py --software ORCA --commit

    # --commit on a database not named tckdb_test* also needs:
    ... --commit --i-know-this-is-deployed

    # Attestation mode -- dry run first, then the same with --commit.
    python backend/scripts/ops/fill_software_release_version.py --software ORCA \
        --attest-version 6 --attested-by <username> --attested-on 2026-09-12 \
        --statement "ORCA 6 was used for all ORCA runs deposited by <owner>"

Prints public refs only, never primary keys.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import date, datetime, time, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")


def _print_plan(plan) -> None:
    counts = Counter(o.status for o in plan.outcomes)
    print(
        f"{plan.software_name} {plan.release_ref} (version NULL): "
        f"{len(plan.outcomes)} calculation(s) -- "
        + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
    )
    for o in plan.outcomes:
        line = f"  {o.calculation_ref}  {o.status}"
        if o.observed_banner:
            line += f"  banner={o.observed_banner!r}"
        if o.target:
            line += f"  -> version={o.target[0]!r} revision={o.target[1]!r} build={o.target[2]!r}"
        if o.detail:
            line += f"  ({o.detail})"
        print(line)
        if o.status == "environment_bound":
            print(
                "      pinned to an execution-environment manifest naming this "
                "release; skipped (the manifest is immutable)"
            )
        if o.fills_build:
            print(
                "      also fills build from the banner (DR-0008 enriched): the "
                "target may sit beside a build-less release of the same version"
            )
        if o.stale_assessment_refs:
            print(
                f"      {len(o.stale_assessment_refs)} reproducibility assessment(s) "
                "go stale: " + ", ".join(o.stale_assessment_refs)
            )
        if o.approved_product_refs:
            print(
                f"      source for {len(o.approved_product_refs)} APPROVED "
                "thermo/statmech record(s): " + ", ".join(o.approved_product_refs)
            )
    fillable = plan.by_status("fillable")
    if fillable:
        stale = sum(len(o.stale_assessment_refs) for o in fillable)
        approved = sorted({r for o in fillable for r in o.approved_product_refs})
        print(
            f"  --commit would re-point {len(fillable)} calculation(s); "
            f"{stale} reproducibility assessment(s) go stale; "
            f"{len(approved)} approved thermo/statmech record(s) cite them."
        )


def _refuse_commit(args, settings) -> bool:
    if (
        args.commit
        and not _TEST_DB_NAME.match(settings.db_name)
        and not args.i_know_this_is_deployed
    ):
        print(
            "Refusing --commit: the configured database does not match "
            f"{_TEST_DB_NAME.pattern!r}. If this is intentionally a deployed "
            "database, pass --i-know-this-is-deployed as well.",
            file=sys.stderr,
        )
        return True
    return False


def _attest(args, settings, SessionLocal) -> int:
    """Attestation mode: record the statement and re-point, or dry-run it."""

    from sqlalchemy import select

    from app.db.models.app_user import AppUser
    from app.services.software_release_version_fill import (
        Attestation,
        VersionFillRefused,
        apply_attestation,
        plan_attestation,
        version_less_releases,
    )

    if _refuse_commit(args, settings):
        return 2
    with SessionLocal() as session:
        attester = session.scalar(select(AppUser).where(AppUser.username == args.attested_by))
        if attester is None:
            print(f"No app_user named {args.attested_by!r}.", file=sys.stderr)
            return 2
        attested_at = (
            datetime.combine(args.attested_on, time())
            if args.attested_on is not None
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )
        attestation = Attestation(
            attested_version=args.attest_version,
            statement=args.statement,
            attested_by=attester,
            attested_at=attested_at,
        )
        releases = version_less_releases(session, args.software)
        if not releases:
            print(f"No version-less software_release for {args.software!r}. Nothing to do.")
            return 0
        print(
            f"Attestation by {attester.username!r} on {attested_at.date().isoformat()}: "
            f"{args.software} version {args.attest_version!r}\n"
            f"  statement (verbatim): {args.statement!r}"
        )
        total = 0
        try:
            for release in releases:
                if args.commit:
                    plan, moved = apply_attestation(session, release, attestation)
                    _print_plan(plan)
                    print(f"  re-pointed {len(moved)} calculation(s) under the attestation:")
                    for calc_ref, release_ref in moved.items():
                        print(f"    {calc_ref} -> {release_ref}")
                    total += len(moved)
                else:
                    _print_plan(plan_attestation(session, release, attestation))
        except VersionFillRefused as exc:
            # Nothing is committed: leaving the session block discards the
            # whole run, so no release is half-attested.
            print(f"Refused: {exc}", file=sys.stderr)
            return 2
        if args.commit:
            session.commit()
            if total == 0:
                print("Nothing to re-point; no attestation was recorded.")
        else:
            print("Dry run -- nothing was written. Re-run with --commit to record and re-point.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--software", required=True, help="program name, e.g. ORCA")
    parser.add_argument("--commit", action="store_true", help="re-point; default is a dry run")
    parser.add_argument(
        "--i-know-this-is-deployed",
        action="store_true",
        help="required alongside --commit on a database not named tckdb_test*",
    )
    attest = parser.add_argument_group(
        "attestation mode",
        "record an owner's statement of the version instead of reading banners",
    )
    attest.add_argument("--attest-version", help='the version exactly as attested, e.g. "6"')
    attest.add_argument("--statement", help="the attesting person's statement, recorded verbatim")
    attest.add_argument("--attested-by", help="username of the attesting app_user")
    attest.add_argument(
        "--attested-on",
        type=date.fromisoformat,
        help="date the statement was made (YYYY-MM-DD); default: now",
    )
    args = parser.parse_args(argv)

    attest_args = (args.attest_version, args.statement, args.attested_by)
    if any(a is not None for a in (*attest_args, args.attested_on)) and not all(
        a is not None for a in attest_args
    ):
        parser.error("--attest-version, --statement and --attested-by go together")

    from app.api.config import settings
    from app.api.deps import SessionLocal
    from app.services.software_release_version_fill import (
        apply_release_fill,
        plan_release_fill,
        version_less_releases,
    )

    if args.attest_version is not None:
        return _attest(args, settings, SessionLocal)

    if _refuse_commit(args, settings):
        return 2

    with SessionLocal() as session:
        releases = version_less_releases(session, args.software)
        if not releases:
            print(f"No version-less software_release for {args.software!r}. Nothing to do.")
            return 0
        for release in releases:
            if args.commit:
                plan, moved = apply_release_fill(session, release)
                _print_plan(plan)
                print(f"  re-pointed {len(moved)} calculation(s):")
                for calc_ref, release_ref in moved.items():
                    print(f"    {calc_ref} -> {release_ref}")
            else:
                _print_plan(plan_release_fill(session, release))
        if args.commit:
            session.commit()
        else:
            print("Dry run -- nothing was written. Re-run with --commit to re-point.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
