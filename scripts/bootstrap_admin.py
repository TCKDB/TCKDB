"""Create or promote the first TCKDB admin user.

Idempotent operational entry point for seeding an admin without going
through public registration. Looks up by username (then email) and:

- creates a new admin row if none matches, or
- promotes the matched user to ``admin`` (and reactivates them).

Usage::

    conda run -n tckdb_env python scripts/bootstrap_admin.py \\
        --username alice \\
        --email alice@example.com \\
        --password 'correct horse battery staple' \\
        --full-name 'Alice Example'

Password may also be supplied via the ``TCKDB_BOOTSTRAP_PASSWORD``
environment variable, which is preferable in shared shells. When
promoting an existing user the password flag is optional.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.config import settings
from app.services.auth import BootstrapResult, bootstrap_admin


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", default=None)
    parser.add_argument(
        "--password",
        default=None,
        help="Password for new admin. Falls back to $TCKDB_BOOTSTRAP_PASSWORD.",
    )
    parser.add_argument("--full-name", default=None)
    parser.add_argument("--affiliation", default=None)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    password = args.password or os.environ.get("TCKDB_BOOTSTRAP_PASSWORD")

    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            with session.begin():
                user, outcome = bootstrap_admin(
                    session,
                    username=args.username,
                    password=password,
                    email=args.email,
                    full_name=args.full_name,
                    affiliation=args.affiliation,
                )
            print(
                f"{outcome}: id={user.id} username={user.username} role={user.role.value}"
            )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
