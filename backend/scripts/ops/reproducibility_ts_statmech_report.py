"""Print what the owner-scoped reproducibility rubric does to a database's statmech records (JSON). Read-only.

Run this on a RESTORED BACKUP with the PR's code, before deploying. It writes nothing, re-assesses nothing and has no
apply mode: the transaction is opened READ ONLY. It never defaults to the configured database: name one. Run from
``backend`` with ``PYTHONPATH=.``, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/reproducibility_ts_statmech_report.py \\
        --database-url postgresql+psycopg://user:password@host:5432/scratch_restore

Add ``--list-stale`` to also print, for each transition-state-owned statmech whose latest assessment reads stale, its
record id and assessment ref (operator-only: it carries identifiers). A curator can re-assess a chosen record through
``POST /api/v1/curation/reproducibility-assessments/statmech/{record_id}/evaluate``; nothing here does.

Reading it:

* ``species_entry_unchanged`` checks ROUTING only. ``false`` is a defect in the change; ``true`` does not show the
  shared checks are untouched.
* ``species_currency_changed`` must be 0: no species row changes currency. A species row already stale because its
  evidence changed after it was assessed reads stale under both the old and the new code and is not counted.
* Stored transition-state-owned assessments read ``stale`` with ``rubric_identity_mismatch`` until re-assessed.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from app.services.read_only_report import choose_database_url, print_read_only_report
from app.services.reproducibility_owner_report import reproducibility_owner_report


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    include_list = "--list-stale" in args
    args = [arg for arg in args if arg != "--list-stale"]
    url = choose_database_url(
        args,
        prog="reproducibility_ts_statmech_report.py",
        description=(
            "Report, per owner kind, how the owner-scoped reproducibility rubric changes statmech grades and which "
            "stored assessments read stale, read-only. Add --list-stale to list the stale transition-state records."
        ),
    )
    print_read_only_report(
        url,
        lambda session: (
            reproducibility_owner_report(session, include_stale_list=True)
            if include_list
            else reproducibility_owner_report(session)
        ),
    )


if __name__ == "__main__":
    main()
