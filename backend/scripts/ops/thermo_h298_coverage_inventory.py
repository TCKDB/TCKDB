"""Print how many stored thermo records can answer H298 and why the rest cannot (JSON). Read-only.

Writes nothing and backfills nothing: the transaction is opened READ ONLY, so a database
refuses any write this script (or a later edit of it) tries to make. It never defaults to the configured database:
name one. Run from ``backend`` with ``PYTHONPATH=.``, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/thermo_h298_coverage_inventory.py \\
        --database-url postgresql+psycopg://user:password@host:5432/dbname

or, on purpose, against the database this installation is configured for (the live one inside a deployed
container)::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/thermo_h298_coverage_inventory.py \\
        --use-configured-database

Use it to see how much of the corpus method-aware H298 selection can act on, and which missing
declarations (enthalpy reference, thermodynamic target, protocol) hold the rest back.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.read_only_report import choose_database_url, print_read_only_report
from app.services.thermo_selection.inventory import h298_coverage_inventory


def main(argv: Sequence[str] | None = None) -> None:
    url = choose_database_url(
        argv,
        prog="thermo_h298_coverage_inventory.py",
        description="Count the stored thermo records that can answer H298, read-only.",
    )
    print_read_only_report(url, h298_coverage_inventory)


if __name__ == "__main__":
    main()
