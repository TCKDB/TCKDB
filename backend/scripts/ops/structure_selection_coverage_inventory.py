"""Print how much of the stored corpus structure selection can act on, and which unknowns remain open (JSON). Read-only.

Writes nothing and backfills nothing: the transaction is opened READ ONLY, so a database refuses any write this
script (or a later edit of it) tries to make. It never defaults to the configured database: name one. Run from
``backend`` with ``PYTHONPATH=.``, for example::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/structure_selection_coverage_inventory.py \\
        --database-url postgresql+psycopg://user:password@host:5432/dbname

or, on purpose, against the database this installation is configured for (the live one inside a deployed
container)::

    PYTHONPATH=. conda run -n tckdb_env python scripts/ops/structure_selection_coverage_inventory.py \\
        --use-configured-database

Use it to see which recipe, geometry, grouping, validation and coverage facts calculation, conformer and
transition-state ordering would still find unstated, before depositors declare anything more.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.read_only_report import choose_database_url, print_read_only_report
from app.services.structure_selection.inventory import structure_coverage_inventory


def main(argv: Sequence[str] | None = None) -> None:
    url = choose_database_url(
        argv,
        prog="structure_selection_coverage_inventory.py",
        description="Count what the stored corpus supports for structure selection, read-only.",
    )
    print_read_only_report(url, structure_coverage_inventory)


if __name__ == "__main__":
    main()
