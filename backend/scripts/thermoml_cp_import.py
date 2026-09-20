"""CLI: fetch-verify -> select -> validate -> parse -> map -> persist one
ThermoML article's heat-capacity rows -- either selected from the pinned
NIST bulk archive (``--archive``/``--doi``) or a standalone ThermoML file
anyone can hand it directly (``--file``, Phase C-E6: "let anyone ingest a
ThermoML file, not only the NIST archive").

Default behavior is a dry-run preview -- the full pipeline runs inside one
transaction, which is rolled back at the end regardless of outcome. Pass
``--commit`` to persist.

``--archive`` and ``--file`` are mutually exclusive (argparse enforces
this before any code here runs). ``--doi`` is required with ``--archive``
(it selects which article inside the bulk tarball) and optional with
``--file``: it overrides the file's own ``sDOI`` citation field only when
the file carries none; a ``--doi`` that disagrees with the file's own
``sDOI`` is refused (:class:`~app.services.thermoml_cp_import.
ThermoMLDoiConflictError`) before anything is written.

Custody differs between the two modes -- see
``app/services/thermoml_cp_import.py``'s module docstring for the full
design: ``--archive`` custody names NIST/TRC and stands on a
``source_terms`` attestation quoting NIST's published terms; ``--file``
custody names the depositor-upload channel and stands on the operator's
own ``--license`` agreement (recorded as an ordinary
``depositor_agreement`` attestation, never NIST's terms).

Exit codes:

    0 -- dry-run or commit finished. Inspect the printed summary +
        ``dispositions`` for per-row outcomes.
    2 -- argument / configuration error, or a doi conflict
        (``thermoml_doi_conflict``).
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
from tckdb_schemas.rights import DepositRights

from app.importers.thermoml import ARCHIVE_URL
from app.importers.thermoml.archive import (
    ArticleBytes,
    build_standalone_article,
    fetch_archive,
    select_article,
)
from app.services.thermoml_cp_import import (
    ThermoMLDoiConflictError,
    ThermoMLNoSupportedContentError,
    import_thermoml_cp_article,
    import_thermoml_cp_upload,
)

_logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="thermoml_cp_import",
        description=(
            "Persist one ThermoML article's heat-capacity Cp(T) "
            "observations: validate, parse and map it, then write the "
            "observation rows, a source-custody row, and a submission "
            "carrying a rights attestation. Source is either the pinned "
            "NIST TRC ThermoML Archive (--archive/--doi) or a standalone "
            "file (--file)."
        ),
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--archive",
        type=Path,
        default=None,
        help=(
            "Path to a local ThermoML.v2020-09-30.tgz. If it does not "
            f"exist yet, it is fetched from the single pinned URL "
            f"({ARCHIVE_URL}). Requires --doi. Mutually exclusive with "
            "--file."
        ),
    )
    mode.add_argument(
        "--file",
        type=Path,
        default=None,
        help=(
            "Path to a standalone ThermoML XML document to persist "
            "directly -- not selected from the NIST archive (Phase "
            "C-E6). Custody names the depositor-upload channel, not "
            "NIST. Mutually exclusive with --archive."
        ),
    )
    p.add_argument(
        "--doi",
        default=None,
        help=(
            "The article's own DOI, e.g. 10.1016/j.jct.2013.08.022. "
            "Required with --archive. Optional with --file: overrides "
            "the file's own sDOI only when the file has none; a value "
            "that disagrees with the file's own sDOI is refused."
        ),
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


def _article_from_file(path: Path) -> ArticleBytes:
    """Wrap a standalone ThermoML XML file as :class:`ArticleBytes`.

    Thin wrapper over :func:`~app.importers.thermoml.archive.
    build_standalone_article`, the helper shared with the
    ``POST /uploads/thermoml`` route (Phase C-E6 review round 2, F8), so
    the two never independently drift on how a from-scratch
    :class:`ArticleBytes` gets built.
    """
    return build_standalone_article(path.read_bytes(), label=str(path))


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.archive is not None and args.doi is None:
        parser.error("--doi is required with --archive")

    if args.archive is not None:
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
    else:
        assert args.file is not None  # argparse mutually-exclusive-group(required=True)
        if not args.file.exists():
            _logger.error("file not found: %s", args.file)
            return 2

        article = _article_from_file(args.file)
        rights = DepositRights(
            license=args.license_id,
            depositor_attests_right_to_license=True,
        )

        engine = create_engine(_database_url(), future=True)
        with Session(engine) as session:
            actor = _ensure_actor(session, args.actor)
            try:
                result = import_thermoml_cp_upload(
                    session,
                    article=article,
                    doi=args.doi,
                    actor=actor,
                    rights=rights,
                    commit=args.commit,
                )
            except (
                ThermoMLDoiConflictError,
                ThermoMLNoSupportedContentError,
            ) as exc:
                _logger.error("%s", exc)
                return 2

    summary = {"commit": args.commit, **result.to_json()}
    summary_text = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary_path is not None:
        args.summary_path.write_text(summary_text + "\n", encoding="utf-8")
    print(summary_text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
