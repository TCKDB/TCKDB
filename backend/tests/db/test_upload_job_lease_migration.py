"""Data upgrade coverage for the upload-job lease migration."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_HEAD = "a8b9c0d1e2f3"
CURRENT_HEAD = "b1c2d3e4f5a6"


def test_lease_upgrade_recovers_processing_rows_and_creates_index(db_engine, monkeypatch):
    """Prior-head rows become queueable or terminal at the lease migration."""
    db_name = db_engine.url.database
    db_engine.dispose()
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")
    config = Config(str(REPO_ROOT / "alembic.ini"))
    command.downgrade(config, PREVIOUS_HEAD)

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    inserted_ids = []
    try:
        with engine.begin() as connection:
            inserted_ids = list(
                connection.scalars(
                    text("""
                INSERT INTO upload_job (status, kind, payload, attempts, max_attempts)
                VALUES ('processing', 'thermo', '{}'::jsonb, 1, 3),
                       ('processing', 'thermo', '{}'::jsonb, 3, 3)
                RETURNING id
            """)
                )
            )
        command.upgrade(config, CURRENT_HEAD)
        with engine.connect() as connection:
            rows = connection.execute(
                text("""
                SELECT status, attempts, lease_expires_at, heartbeat_at
                FROM upload_job WHERE attempts >= 1 ORDER BY attempts
            """)
            ).all()
            assert rows[0] == ("queued", 1, None, None)
            assert rows[1][0] == "failed"
            assert rows[1][1] == 3
            assert rows[1][2] is None and rows[1][3] is None
            assert (
                connection.scalar(
                    text("""
                SELECT count(*) FROM pg_indexes
                WHERE tablename = 'upload_job'
                  AND indexname = 'ix_upload_job_status_lease_expires_at'
            """)
                )
                == 1
            )
    finally:
        if inserted_ids:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM upload_job WHERE id = ANY(:ids)"),
                    {"ids": inserted_ids},
                )
        engine.dispose()
        # Put the schema back where this test found it. It downgrades the
        # *per-run database every other test in the process is using*, and
        # ``CURRENT_HEAD`` above is a historical revision — stopping there
        # leaves every later migration un-applied, which is how this one file
        # used to take the rest of the suite down with it
        # (``species_entry.isotope_key does not exist``, hundreds of tests
        # later, in files with nothing to do with upload jobs).
        command.upgrade(config, "head")
