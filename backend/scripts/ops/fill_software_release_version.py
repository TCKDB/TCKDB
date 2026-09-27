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

Evidence is the artifact only. An owner's attestation is not accepted here:
there is nowhere structured to record who said what and when, and adding one
is a schema decision (see the #305 PR).

Calculations that were ever approved are reported as ``accepted`` and left
alone -- ``trg_as_root_calculation`` refuses the UPDATE without an
``accepted_science_repair`` declaration, which only a migration makes.

Needs the artifact store configured (``S3_*``), exactly as the API does.

Usage::

    # Dry run -- the default. Reads artifacts, writes nothing.
    python backend/scripts/ops/fill_software_release_version.py --software ORCA

    # Re-point the calculations whose logs name a version.
    python backend/scripts/ops/fill_software_release_version.py --software ORCA --commit

    # --commit on a database not named tckdb_test* also needs:
    ... --commit --i-know-this-is-deployed

Prints public refs only, never primary keys.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--software", required=True, help="program name, e.g. ORCA")
    parser.add_argument("--commit", action="store_true", help="re-point; default is a dry run")
    parser.add_argument(
        "--i-know-this-is-deployed",
        action="store_true",
        help="required alongside --commit on a database not named tckdb_test*",
    )
    args = parser.parse_args(argv)

    from app.api.config import settings
    from app.api.deps import SessionLocal
    from app.services.software_release_version_fill import (
        apply_release_fill,
        plan_release_fill,
        version_less_releases,
    )

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
