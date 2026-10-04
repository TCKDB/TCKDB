"""Print how many stored kinetics records can answer the question they state, and why the rest cannot (JSON). Read-only.

Writes nothing and backfills nothing: the transaction is opened READ ONLY, so a database refuses any write this
script (or a later edit of it) tries to make. It never defaults to the configured database: name one. Run from
``backend`` with ``PYTHONPATH=.``, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/kinetics_selection_coverage_inventory.py \\
        --database-url postgresql+psycopg://user:password@host:5432/dbname

or, on purpose, against the database this installation is configured for (the live one inside a deployed
container)::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/kinetics_selection_coverage_inventory.py \\
        --use-configured-database

Use it to see how much of the corpus method-aware kinetics selection can act on, and which missing declarations
(determination, applicability, temperature window, pressure, collider, protocol) hold the rest back.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.kinetics_selection.inventory import kinetics_coverage_inventory
from app.services.read_only_report import choose_database_url, print_read_only_report


def main(argv: Sequence[str] | None = None) -> None:
    url = choose_database_url(
        argv,
        prog="kinetics_selection_coverage_inventory.py",
        description="Count the stored kinetics records that can answer the question they state, read-only.",
    )
    print_read_only_report(url, kinetics_coverage_inventory)


if __name__ == "__main__":
    main()
