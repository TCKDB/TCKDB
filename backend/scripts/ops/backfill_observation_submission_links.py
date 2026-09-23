#!/usr/bin/env python
"""CLI: backfill a submission link for legacy unresolved observation rows.

Closes issue #514. ``app.services.observation_identity_attach`` refuses to
attach a species-entry identity to a ``molecular_property_observation`` row
with no ``submission_record_link``
(``observation_identity_attach_requires_submission``) -- there is nowhere
honest to record the curation fact. The ThermoML importer has always linked
every row it writes; the CCCBDB importer only started doing so at Phase
C-E5 review round 3, so rows it wrote before that change carry no link and
cannot be curated.

This script finds every such row on the database it is pointed at, groups
it by the external source that produced it (``external_source_name``, or
the literal label ``unknown`` for a row with none recorded), opens one
``Submission(source_kind=migration)`` per source attributed to a named
operator with a ``historical_review`` rights attestation, and links the
rows to it -- see ``app/services/observation_submission_link_backfill.py``
for the full design and the reasoning behind that attestation basis.

Default behavior is a dry-run preview: the full pipeline runs inside one
transaction, which is rolled back at the end regardless of outcome. Pass
``--commit`` to persist. The script never touches the object store either
way -- nothing it calls does.

Idempotent: a row this script links no longer matches "unlinked" the next
time it runs, so a second run (over the same database, ``--commit`` or not)
finds nothing left to do and opens no new submission.

Safety: refuses to run (``--commit`` or dry-run alike) against a database
whose name does not look like a test database, unless the operator also
names that exact database with ``--target-db`` -- so a wrong or leftover
``DB_NAME`` cannot point this at something by accident.

Exit codes:

    0 -- dry-run or commit finished. Inspect the printed summary.
    2 -- argument / configuration error, or the database-target guard
        refused the run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.observation_submission_link_backfill import (  # noqa: E402
    backfill_observation_submission_links,
)

_logger = logging.getLogger(__name__)

#: Same shape ``backend/scripts/ops/backfill_assumed_tau.py`` and
#: ``backend/scripts/dev/reclaim_leaked_test_databases.py`` use for the same
#: judgement: the fixture default is ``tckdb_test``, ``DB_TEST_NAME``
#: overrides the whole name, and CI/dev tooling appends a suffix
#: (``tckdb_test_ci``, ``tckdb_test_backfill``). Anchored both ends so
#: ``tckdb_test-prod`` or ``tckdb_testing_real`` do not pass as a test
#: database by accident.
_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backfill_observation_submission_links",
        description=(
            "Backfill a Submission(source_kind=migration) wrapper, with a "
            "historical_review rights attestation, for every "
            "molecular_property_observation row that carries no "
            "submission_record_link -- one submission per external source. "
            "Default is a dry-run preview; pass --commit to persist."
        ),
    )
    p.add_argument(
        "--license",
        dest="license_id",
        required=True,
        help=(
            "SPDX identifier to attest for each opened submission "
            "(basis=historical_review). Must equal the release's own "
            "data_license exactly for a citable release to include these "
            "rows (app.services.rights.licenses_match)."
        ),
    )
    p.add_argument(
        "--actor",
        required=True,
        help=(
            "Username of the AppUser recorded as the creator of every "
            "opened submission and as the historical_review rights "
            "attestor. Created (role=curator) if it does not exist yet. "
            "Named explicitly, with no default: unlike an importer run, "
            "this is a specific operator remediating specific legacy "
            "rows, and the audit trail should say who."
        ),
    )
    p.add_argument(
        "--commit", action="store_true",
        help="Actually persist the submissions and links. Default is dry-run.",
    )
    p.add_argument(
        "--target-db",
        default=None,
        help=(
            "Required to run against a database whose name does not "
            "match tckdb_test* -- must equal the configured DB_NAME "
            "exactly, so this cannot be pointed at a deployed database "
            "by an unnoticed default."
        ),
    )
    p.add_argument(
        "--summary-path", type=Path, default=None,
        help=(
            "Optional path to write the full result JSON. Always printed "
            "to stdout in summary form regardless."
        ),
    )
    return p


def _database_url() -> str:
    """Compose the SQLAlchemy URL from the same env vars Alembic uses."""

    user = os.environ.get("DB_USER", "tckdb")
    password = os.environ.get("DB_PASSWORD", "tckdb")
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "tckdb_dev")
    return (
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"
        "?client_encoding=utf8"
    )


def _db_name() -> str:
    return os.environ.get("DB_NAME", "tckdb_dev")


def _refuse_target(db_name: str, target_db: str | None) -> str | None:
    """``None`` when the run may proceed; else the refusal message."""
    if _TEST_DB_NAME.match(db_name):
        return None
    if target_db is not None and target_db == db_name:
        return None
    return (
        f"Refusing to run: database {db_name!r} does not match "
        f"{_TEST_DB_NAME.pattern!r} (a test database), and no --target-db "
        "matching it was given. If this is a deliberate target, pass "
        f"--target-db {db_name!r} to confirm."
    )


def _ensure_actor(session: Session, username: str):
    from app.db.models.app_user import AppUser, AppUserRole

    user = session.query(AppUser).filter_by(username=username).first()
    if user is None:
        user = AppUser(
            username=username,
            full_name=f"Observation submission-link backfill operator ({username})",
            role=AppUserRole.curator,
        )
        session.add(user)
        session.flush()
    return user


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    db_name = _db_name()
    refusal = _refuse_target(db_name, args.target_db)
    if refusal is not None:
        # print(file=sys.stderr) rather than _logger.error: whatever hosts
        # this script may already have configured the root logger (adding
        # handlers `logging.basicConfig` above then silently no-ops
        # against), so a log call is not a reliable way to guarantee an
        # exit-code-2 refusal is actually visible. Same choice
        # backend/scripts/ops/backfill_assumed_tau.py makes for its own
        # database-target guard.
        print(refusal, file=sys.stderr)
        return 2

    engine = create_engine(_database_url(), future=True)
    with Session(engine) as session:
        actor = _ensure_actor(session, args.actor)
        result = backfill_observation_submission_links(
            session,
            actor=actor,
            license_id=args.license_id,
            commit=args.commit,
        )

    summary = {"commit": args.commit, "db_name": db_name, **result.to_json()}
    summary_text = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary_path is not None:
        args.summary_path.write_text(summary_text + "\n", encoding="utf-8")
    print(summary_text)

    if result.total_unlinked_found == 0:
        print("No unlinked molecular_property_observation rows found. Nothing to do.")
    elif not args.commit:
        print(
            f"Dry run -- would link {result.total_linked} row(s) across "
            f"{len(result.outcomes)} submission(s). Nothing was written. "
            "Re-run with --commit to write."
        )
    else:
        print(
            f"Linked {result.total_linked} row(s) across "
            f"{len(result.outcomes)} submission(s)."
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
