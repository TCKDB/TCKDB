#!/usr/bin/env python
"""Re-check literature titles against the DOI/ISBN registry and record where each came from.

Admin tool. A ``literature`` row whose ``title_source`` is NULL (written before the
column existed: origin unknown) or ``depositor_supplied`` (the depositor typed it)
may now have a registry title available -- the lookup failed at deposit time, or
the row predates the column. For each such row that has a DOI or ISBN, this
re-fetches the registry metadata and, when a title comes back, sets ``title`` to
the registry's and ``title_source`` to ``resolved_metadata``.

A title is not identity: the DOI/ISBN is. This changes only ``title`` and
``title_source``. It never touches ``doi``, ``isbn``, ``public_ref`` or any other
column, so every record citing the row keeps citing the same row. The resolver
(``resolve_or_create_literature``) deliberately never does this on a later
deposit; this script is the one explicit, operator-run path.

Lookups go through the resolver's own fetch functions
(``app.services.literature_metadata``): the same normalisation, in-process cache,
10 s timeout and request headers. Only the DOI or ISBN is sent to the registry.
The lookups are made one at a time, ``--delay`` seconds apart (default 0.5) so a
large run stays polite to Crossref.

A lookup that errors, or returns no title, leaves the row unchanged and is counted.
A row already ``resolved_metadata`` is never selected.

Usage::

    # Dry run -- the default. Looks up, reports, writes nothing.
    python backend/scripts/ops/reresolve_literature_titles.py

    # Write the upgrades.
    python backend/scripts/ops/reresolve_literature_titles.py --apply

    # --apply on a database not named tckdb_test* also needs:
    ... --apply --i-know-this-is-deployed

    # Narrow the run.
    ... --ref lit_...            one row
    ... --limit 50               at most 50 rows (oldest first)
    ... --since 2026-10-01       rows created on or after this date (UTC)

Database connection comes from the same ``DB_*`` environment variables as the API.
Prints public refs and title sources only, never titles or primary keys.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from datetime import time as dtime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.models.common import LiteratureTitleSource  # noqa: E402
from app.db.models.literature import Literature  # noqa: E402
from app.schemas.utils import normalize_required_text  # noqa: E402
from app.services.literature_metadata import (  # noqa: E402
    fetch_doi_metadata,
    fetch_isbn_metadata,
    normalize_doi,
    normalize_isbn,
)
from app.services.literature_resolution import _metadata_to_fields  # noqa: E402

_TEST_DB_NAME = re.compile(r"^tckdb_test(?:_[A-Za-z0-9_]+)?$")


def _scope(session: Session, *, ref: str | None, since: date | None, limit: int | None) -> list[Literature]:
    stmt = select(Literature).where(
        or_(Literature.doi.is_not(None), Literature.isbn.is_not(None)),
        or_(
            Literature.title_source.is_(None),
            Literature.title_source == LiteratureTitleSource.depositor_supplied,
        ),
    )
    if ref is not None:
        stmt = stmt.where(Literature.public_ref == ref)
    if since is not None:
        stmt = stmt.where(Literature.created_at >= datetime.combine(since, dtime.min, tzinfo=timezone.utc))
    stmt = stmt.order_by(Literature.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def _registry_title(row: Literature) -> tuple[str | None, str]:
    """The registry's title for *row*, or ``None``, plus a short outcome word.

    Same preference as the resolver: the DOI first, else the ISBN.
    """
    try:
        doi = normalize_doi(row.doi) if row.doi else None
        if doi is not None:
            fields = _metadata_to_fields(fetch_doi_metadata(doi) or {}, source="doi")
        else:
            isbn = normalize_isbn(row.isbn) if row.isbn else None
            if isbn is None:
                return None, "unusable_identifier"
            fields = _metadata_to_fields(fetch_isbn_metadata(isbn) or {}, source="isbn")
    except Exception:
        return None, "lookup_error"
    title = fields.get("title")
    if isinstance(title, str) and title.strip():
        return normalize_required_text(title), "title_found"
    return None, "no_title"


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {value!r}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the upgrades. Without this, the default is a dry run.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing (the default)")
    parser.add_argument(
        "--i-know-this-is-deployed",
        action="store_true",
        help="required alongside --apply on a database whose name does not match tckdb_test*",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the number of rows processed")
    parser.add_argument("--since", type=_parse_date, default=None, help="only rows created on or after YYYY-MM-DD (UTC)")
    parser.add_argument("--ref", default=None, help="a single literature public ref (lit_...)")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds to wait between registry lookups (default 0.5)")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("--apply and --dry-run are mutually exclusive.", file=sys.stderr)
        return 2

    from app.api.config import settings
    from app.api.deps import SessionLocal

    if args.apply and not _TEST_DB_NAME.match(settings.db_name) and not args.i_know_this_is_deployed:
        print(
            "Refusing --apply: the configured database does not match "
            f"{_TEST_DB_NAME.pattern!r} (a test database). If this is intentionally a deployed "
            "database, pass --i-know-this-is-deployed as well.",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as session:
        rows = _scope(session, ref=args.ref, since=args.since, limit=args.limit)
        if not rows:
            print("No literature rows with a DOI/ISBN and an unverified title in scope. Nothing to do.")
            return 0

        tally: Counter[str] = Counter()
        for index, row in enumerate(rows):
            if index and args.delay > 0:
                time.sleep(args.delay)
            title, outcome = _registry_title(row)
            old = row.title_source.value if row.title_source is not None else "not_recorded"
            if title is None:
                tally[outcome] += 1
                print(f"  {row.public_ref}  {old} -> unchanged ({outcome})")
                continue
            tally["upgraded" if args.apply else "would_upgrade"] += 1
            print(f"  {row.public_ref}  {old} -> resolved_metadata")
            if args.apply:
                row.title = title
                row.title_source = LiteratureTitleSource.resolved_metadata

        if args.apply:
            session.commit()

        print(f"{len(rows)} row(s) examined: " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        if not args.apply:
            print("Dry run -- nothing was written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
