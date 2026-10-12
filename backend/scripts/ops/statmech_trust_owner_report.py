"""Print what the owner-scoped statmech rubric does to a database's statmech records (JSON). Read-only.

Run this on a RESTORED BACKUP with the PR's code, before deploying. It writes nothing and has no apply mode:
the transaction is opened READ ONLY. It never defaults to the configured database: name one. Run from
``backend`` with ``PYTHONPATH=.``, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/statmech_trust_owner_report.py \\
        --database-url postgresql+psycopg://user:password@host:5432/scratch_restore

or, on purpose, against the database this installation is configured for (the live one inside a deployed
container; do not do this before reading the report on a restore)::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/statmech_trust_owner_report.py \\
        --use-configured-database

Read ``species_entry_unchanged`` first: ``false`` is a defect in the change, not a finding about the data.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.read_only_report import choose_database_url, print_read_only_report
from app.services.trust.statmech_owner_report import statmech_trust_owner_report


def main(argv: Sequence[str] | None = None) -> None:
    url = choose_database_url(
        argv,
        prog="statmech_trust_owner_report.py",
        description=(
            "Report, per owner kind, how many statmech records change badge under the owner-scoped rubric "
            "and which stored machine reviews become stale, read-only."
        ),
    )
    print_read_only_report(url, statmech_trust_owner_report)


if __name__ == "__main__":
    main()
