"""Print how much of the stored pressure-dependent network corpus selection could act on, and why not (JSON). Read-only.

Counts, over every stored network solve, fit and determination, what each states and leaves unstated (target,
grouping, domain, protocol). Writes nothing and backfills nothing: the transaction is opened READ ONLY, so a database
refuses any write this script (or a later edit of it) tries to make. It never defaults to the configured database:
name one. Run from ``backend`` with ``PYTHONPATH=.``, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/network_selection_coverage_inventory.py \\
        --database-url postgresql+psycopg://user:password@host:5432/dbname

or, on purpose, against the database this installation is configured for (the live one inside a deployed
container)::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/network_selection_coverage_inventory.py \\
        --use-configured-database

Use it to see how much of the corpus network selection can act on before depositors declare anything more. Pointing
it at a deployed database is the operator's act: this repository's tooling and its tests never do.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.network_selection.inventory import network_coverage_inventory
from app.services.read_only_report import choose_database_url, print_read_only_report


def main(argv: Sequence[str] | None = None) -> None:
    url = choose_database_url(
        argv,
        prog="network_selection_coverage_inventory.py",
        description="Count what the stored network solves, fits and determinations state, read-only.",
    )
    print_read_only_report(url, network_coverage_inventory)


if __name__ == "__main__":
    main()
