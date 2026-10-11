"""Print how much of the stored statmech corpus each supported claim can act on (JSON). Read-only.

Writes nothing and backfills nothing: the transaction is opened READ ONLY. It never defaults to the configured
database: name one. Run from ``backend`` with ``PYTHONPATH=.``, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/statmech_selection_coverage_inventory.py \\
        --database-url postgresql+psycopg://user:password@host:5432/dbname

or, on purpose, against the database this installation is configured for (the live one inside a deployed
container)::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/statmech_selection_coverage_inventory.py \\
        --use-configured-database
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.read_only_report import choose_database_url, print_read_only_report
from app.services.statmech_selection.inventory import statmech_coverage_inventory


def main(argv: Sequence[str] | None = None) -> None:
    url = choose_database_url(
        argv,
        prog="statmech_selection_coverage_inventory.py",
        description="Count what the stored statmech corpus supports for each claim, read-only.",
    )
    print_read_only_report(url, statmech_coverage_inventory)


if __name__ == "__main__":
    main()
