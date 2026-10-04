"""Shared plumbing for the read-only operator reports (the coverage inventories).

Two rules, kept in one place so no report can drop them:

* **Name the database.** A report never defaults to the configured database. Inside a deployed container the
  configured database is the live one, and a report that connected to it because nobody said otherwise would read
  production silently. The operator passes ``--database-url <url>`` or, deliberately, ``--use-configured-database``;
  with neither the report prints its usage and exits with status 2.
* **Read-only, one snapshot.** The transaction is REPEATABLE READ and READ ONLY, so a database refuses any write the
  report (or a later edit of it) tries to make, and the counts describe one consistent state.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def choose_database_url(argv: Sequence[str] | None, *, prog: str, description: str) -> str:
    """The database URL the operator named. Exits 2 with a usage message when none was named."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--database-url", metavar="URL", help="read this database")
    choice.add_argument(
        "--use-configured-database",
        action="store_true",
        help=(
            "read the database this installation is configured for (the DB_* environment). Inside a deployed "
            "container that is the LIVE database; say so on purpose."
        ),
    )
    args = parser.parse_args(argv)
    if args.database_url is not None:
        # An empty value is not a name: ``--database-url "$URL"`` with ``URL`` unset must not fall through to the
        # configured (inside a deployed container, the live) database.
        if not args.database_url.strip():
            parser.error("--database-url is empty; name the database to read, or pass --use-configured-database")
        return str(args.database_url)
    from app.api.config import settings

    return str(settings.database_url)


def print_read_only_report(url: str, report: Callable[[Session], dict[str, Any]]) -> None:
    """Run ``report`` in a read-only REPEATABLE READ transaction on ``url`` and print its JSON."""
    engine = create_engine(url, isolation_level="REPEATABLE READ")
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            print(json.dumps(report(session), indent=2, sort_keys=True, allow_nan=False))
    finally:
        engine.dispose()


__all__ = ["choose_database_url", "print_read_only_report"]
