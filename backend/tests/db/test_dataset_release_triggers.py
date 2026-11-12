"""Append-only triggers of the curated-release layer."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_append_only_tables_have_both_row_and_truncate_triggers(db_engine):
    """A BEFORE UPDATE OR DELETE trigger does not fire for TRUNCATE.

    Without a statement-level trigger, one ``TRUNCATE ... CASCADE`` silently
    erases an entire audited curation history that UPDATE and DELETE are
    forbidden from touching a row at a time.
    """
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            for table in ("release_selection", "release_manifest", "release_artifact"):
                events = set(
                    connection.scalars(
                        text(
                            "SELECT DISTINCT t.tgtype::int & 28 "
                            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                            "WHERE c.relname = :t AND NOT t.tgisinternal"
                        ),
                        {"t": table},
                    )
                )
                assert events, f"{table} has no append-only trigger"
                truncate = connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger t "
                        "JOIN pg_class c ON c.oid = t.tgrelid "
                        "WHERE c.relname = :t AND NOT t.tgisinternal "
                        "AND (t.tgtype::int & 32) <> 0"
                    ),
                    {"t": table},
                )
                assert truncate >= 1, f"{table} is truncatable"
    finally:
        engine.dispose()
