"""CLI: fetch-verify -> select -> validate -> parse -> map -> persist one
ThermoML archive article's heat-capacity rows.

Default behavior is a dry-run preview -- the full pipeline runs inside one
transaction, which is rolled back at the end regardless of outcome. Pass
``--commit`` to persist.

Exit codes:

    0 -- dry-run or commit finished. Inspect the printed summary +
        ``dispositions`` for per-row outcomes.
    2 -- argument / configuration error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.importers.thermoml import ARCHIVE_URL
from app.importers.thermoml.archive import fetch_archive, select_article
from app.services.thermoml_cp_import import import_thermoml_cp_article

_logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="thermoml_cp_import",
        description=(
            "Persist one NIST TRC ThermoML Archive article's heat-capacity "
            "Cp(T) observations: fetch/verify the pinned archive, select "
            "and schema-validate the article, parse and map it, then write "
            "the observation rows, a source-custody row, and a submission "
            "carrying a source_terms rights attestation."
        ),
    )
    p.add_argument(
        "--archive",
        type=Path,
        required=True,
        help=(
            "Path to a local ThermoML.v2020-09-30.tgz. If it does not "
            f"exist yet, it is fetched from the single pinned URL "
            f"({ARCHIVE_URL})."
        ),
    )
    p.add_argument(
        "--doi",
        required=True,
        help="The article's own DOI, e.g. 10.1016/j.jct.2013.08.022",
    )
    p.add_argument(
        "--license",
        dest="license_id",
        required=True,
        help=(
            "SPDX identifier to license this deposit's rows under. Must "
            "equal the release's own data_license exactly for a citable "
            "release to include them (app.services.rights.licenses_match)."
        ),
    )
    p.add_argument(
        "--actor",
        default="thermoml_importer",
        help=(
            "Username of the AppUser recorded as this deposit's creator "
            "and rights attestor. Created (role=curator) if it does not "
            "exist yet. Default: thermoml_importer."
        ),
    )
    p.add_argument(
        "--commit", action="store_true",
        help="Actually persist rows. Default is dry-run.",
    )
    p.add_argument(
        "--summary-path", type=Path, default=None,
        help=(
            "Optional path to write the full result + dispositions JSON. "
            "Always printed to stdout in summary form regardless."
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


def _ensure_actor(session: Session, username: str):
    from app.db.models.app_user import AppUser, AppUserRole

    user = session.query(AppUser).filter_by(username=username).first()
    if user is None:
        user = AppUser(
            username=username,
            full_name="ThermoML Cp Importer",
            role=AppUserRole.curator,
        )
        session.add(user)
        session.flush()
    return user


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.archive.exists():
        try:
            fetch_archive(ARCHIVE_URL, args.archive)
        except Exception as exc:
            _logger.error("failed to fetch archive: %s", exc)
            return 2

    try:
        article = select_article(args.archive, args.doi)
    except Exception as exc:
        _logger.error("failed to select article for DOI %s: %s", args.doi, exc)
        return 2

    engine = create_engine(_database_url(), future=True)
    with Session(engine) as session:
        actor = _ensure_actor(session, args.actor)
        result = import_thermoml_cp_article(
            session,
            article=article,
            doi=args.doi,
            actor=actor,
            license_id=args.license_id,
            commit=args.commit,
        )

    summary = {"commit": args.commit, **result.to_json()}
    summary_text = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary_path is not None:
        args.summary_path.write_text(summary_text + "\n", encoding="utf-8")
    print(summary_text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
