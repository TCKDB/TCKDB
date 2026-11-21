"""Load the method and basis-set catalogue seed (``backend/app/data/catalogue/``) into the database.

Operator tool. Compares the catalogue tables with the seed and prints one line per difference: entries to create,
update or delete, identity keys to add or remove, reference lists to rewrite, literature rows to create. Literature
rows that already exist (matched by normalised DOI) are used as they stand; none is rewritten or deleted.

Usage::

    # Dry run -- the default. Prints the differences, writes nothing.
    python backend/scripts/ops/load_method_catalogue.py

    # Exit 1 when the database differs from the seed, 0 when it matches. Writes nothing.
    python backend/scripts/ops/load_method_catalogue.py --check

    # Write the differences. Running it again prints nothing.
    python backend/scripts/ops/load_method_catalogue.py --apply

    # --apply on a database not named tckdb_test* also needs:
    ... --apply --i-know-this-is-deployed

Database connection comes from the same ``DB_*`` environment variables as the API.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.engine import Connection  # noqa: E402

from app.services.method_catalogue_seed import CatalogueSeed, load_seed  # noqa: E402
from app.services.method_catalogue_sync import sync_catalogue  # noqa: E402

_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")


def run(conn: Connection, *, apply: bool, check: bool, seed: CatalogueSeed | None = None) -> int:
    """Print the differences between the database and the seed; apply them when asked.

    :returns: The exit status: 1 under ``check`` when anything differs, else 0.
    """
    changes = sync_catalogue(conn, seed if seed is not None else load_seed(), apply=apply)
    for line in changes:
        print(line)
    if not changes:
        print("The catalogue matches the seed.")
    elif apply:
        print(f"Applied {len(changes)} change(s).")
    else:
        print(f"{len(changes)} difference(s) from the seed.")
    return 1 if check and changes else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the differences")
    mode.add_argument("--check", action="store_true", help="exit 1 when the database differs from the seed")
    parser.add_argument(
        "--i-know-this-is-deployed",
        action="store_true",
        help="required alongside --apply on a database whose name does not match tckdb_test*",
    )
    args = parser.parse_args(argv)

    from app.api.config import settings
    from app.api.deps import engine

    if args.apply and not _TEST_DB_NAME.match(settings.db_name) and not args.i_know_this_is_deployed:
        print(
            "Refusing --apply: the configured database does not match "
            f"{_TEST_DB_NAME.pattern!r} (a test database). If this is intentionally a deployed "
            "database, pass --i-know-this-is-deployed as well.",
            file=sys.stderr,
        )
        return 2

    with engine.connect() as conn:
        status = run(conn, apply=args.apply, check=args.check)
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    return status


if __name__ == "__main__":
    sys.exit(main())
