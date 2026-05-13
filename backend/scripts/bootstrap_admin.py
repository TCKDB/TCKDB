"""Create or update a TCKDB user at a given role.

Idempotent operational entry point for seeding accounts without going
through public registration. Looks up by username (then email) and:

- creates a new user at ``--role`` if none matches, or
- updates the matched user's role (only with ``--force-role-change``)
  and reactivates them if disabled.

Defaults to ``--role admin`` for the original bootstrap-first-admin
use case. Pass ``--role curator`` (or ``user``) to seed other roles.

Usage::

    python scripts/bootstrap_admin.py \\
        --username alice \\
        --email alice@example.com \\
        --password 'correct horse battery staple' \\
        --full-name 'Alice Example' \\
        --role admin

Password may also be supplied via the ``TCKDB_BOOTSTRAP_PASSWORD``
environment variable, which is preferable in shared shells. When
updating an existing user the password flag is optional.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

# Ensure repo root is on sys.path so `app` is importable without
# requiring the caller to export PYTHONPATH.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.config import settings
from app.db.models.common import AppUserRole
from app.services.auth import RoleChangeRefused, bootstrap_user


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", default=None)
    parser.add_argument(
        "--password",
        default=None,
        help="Password for new user. Falls back to $TCKDB_BOOTSTRAP_PASSWORD.",
    )
    parser.add_argument("--full-name", default=None)
    parser.add_argument("--affiliation", default=None)
    parser.add_argument(
        "--role",
        default=AppUserRole.admin.value,
        choices=[r.value for r in AppUserRole],
        help="Role to assign (default: admin).",
    )
    parser.add_argument(
        "--force-role-change",
        action="store_true",
        help="Allow changing an existing user's role. Without this, the script "
             "refuses to modify the role of a pre-existing account.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    password = args.password or os.environ.get("TCKDB_BOOTSTRAP_PASSWORD")
    role = AppUserRole(args.role)

    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as session:
            with session.begin():
                try:
                    user, outcome = bootstrap_user(
                        session,
                        username=args.username,
                        role=role,
                        password=password,
                        email=args.email,
                        full_name=args.full_name,
                        affiliation=args.affiliation,
                        force_role_change=args.force_role_change,
                    )
                except RoleChangeRefused as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    print(
                        "hint: re-run with --force-role-change to override.",
                        file=sys.stderr,
                    )
                    return 2
            print(
                f"{outcome}: id={user.id} username={user.username} role={user.role.value}"
            )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
