"""``python -m tckdb_schemas.contract``: find or print the producer contract."""

from __future__ import annotations

import argparse
import sys

from tckdb_schemas import __version__
from tckdb_schemas.contract import changes_since, markdown, path, schema_names, schema_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tckdb_schemas.contract",
        description=(
            "Locate or print the TCKDB producer contract shipped with this "
            "tckdb-schemas. With no option, print the path of PRODUCER_CONTRACT.md."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--print", action="store_true", help="print the whole contract")
    group.add_argument(
        "--since",
        metavar="VERSION",
        help="print only the changelog entries newer than VERSION (the tckdb-schemas version you target)",
    )
    group.add_argument("--schemas", action="store_true", help="list the JSON Schema files, one per upload payload")
    args = parser.parse_args(argv)

    if args.print:
        sys.stdout.write(markdown())
    elif args.since:
        changes = changes_since(args.since)
        if changes:
            sys.stdout.write(changes)
        else:
            print(f"No changes recorded after {args.since} (installed tckdb-schemas {__version__}).")
    elif args.schemas:
        for name in schema_names():
            print(f"{name}\t{schema_path(name)}")
    else:
        print(path())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
