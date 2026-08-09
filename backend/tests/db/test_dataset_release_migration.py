"""Migration coverage for the Stage 3 curated-release layer (``e3f4a5b6c7d8``).

Two things are checked that ``alembic upgrade head`` alone does not:

* the migration is genuinely reversible — ``downgrade`` drops the tables *and*
  the enum types it owns, and a subsequent ``upgrade`` succeeds. A revision
  whose downgrade has never been executed is a revision that does not have one.
* the downgrade refuses to run when a **published** release exists, because
  dropping one would leave an outstanding citation pointing at nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command

REPO_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_HEAD = "d2e3f4a5b6c7"
CURRENT_HEAD = "e3f4a5b6c7d8"

_NEW_TABLES = (
    "curation_policy",
    "dataset_release",
    "release_selection",
    "release_manifest",
    "release_artifact",
)
_OWNED_ENUMS = (
    "dataset_release_status",
    "release_selection_action",
    "release_artifact_kind",
    "read_profile",
)


def _configure(db_engine, monkeypatch) -> Config:
    db_name = db_engine.url.database
    db_engine.dispose()
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")
    return Config(str(REPO_ROOT / "alembic.ini"))


def _restore_head(config: Config) -> None:
    """Put the shared test database back at ``head``, not at ``CURRENT_HEAD``.

    These tests downgrade the *per-run database every other test in the process
    is using*, so restoring it is not optional. Restoring to ``CURRENT_HEAD``
    is not enough either: that is a historical revision, and every migration
    after it stays un-applied, which is how one file in ``tests/db/`` used to
    take the rest of the suite down with it — ``species_entry.isotope_key does
    not exist`` and similar, hundreds of tests later, in files that had nothing
    to do with releases.
    """
    command.upgrade(config, "head")


def _table_names(connection) -> set[str]:
    return set(
        connection.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
            )
        )
    )


def _enum_names(connection) -> set[str]:
    return set(
        connection.scalars(text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
    )


def test_downgrade_then_upgrade_round_trips(db_engine, monkeypatch):
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert set(_NEW_TABLES) <= _table_names(connection)

        command.downgrade(config, PREVIOUS_HEAD)
        with engine.connect() as connection:
            assert not (set(_NEW_TABLES) & _table_names(connection))
            # The revision owns these enums and must clean them up, or a
            # re-upgrade would fail on "type already exists".
            assert not (set(_OWNED_ENUMS) & _enum_names(connection))
            # …but not the pre-existing vocabulary it merely reuses.
            assert "submission_record_type" in _enum_names(connection)

        command.upgrade(config, CURRENT_HEAD)
        with engine.connect() as connection:
            assert set(_NEW_TABLES) <= _table_names(connection)
            assert set(_OWNED_ENUMS) <= _enum_names(connection)
    finally:
        engine.dispose()
        _restore_head(config)


def test_downgrade_refuses_to_orphan_a_published_citation(db_engine, monkeypatch):
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            user_id = connection.scalar(
                text(
                    "INSERT INTO app_user (username, role, is_active) "
                    "VALUES ('release-migration-user', 'curator', true) RETURNING id"
                )
            )
            policy_id = connection.scalar(
                text(
                    "INSERT INTO curation_policy "
                    "(name, version, description, public_ref, created_by) "
                    "VALUES ('p', '1', 'd', 'cpol_migrationtestrefaaaaaaaaaa', :u) "
                    "RETURNING id"
                ),
                {"u": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_release "
                    "(tag, title, status, curation_policy_id, data_license, "
                    " code_license, citation_text, contact, published_at, public_ref) "
                    "VALUES ('migration-test', 't', 'published', :p, 'CC-BY-4.0', "
                    "'MIT', 'c', 'x@example.org', now(), "
                    "'rel_migrationtestrefaaaaaaaaa')"
                ),
                {"p": policy_id},
            )

        with pytest.raises(RuntimeError, match="released datasets"):
            command.downgrade(config, PREVIOUS_HEAD)

        # Still at head, still intact.
        with engine.connect() as connection:
            assert set(_NEW_TABLES) <= _table_names(connection)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM dataset_release WHERE tag = 'migration-test'")
            )
            connection.execute(
                text("DELETE FROM curation_policy WHERE name = 'p' AND version = '1'")
            )
            connection.execute(
                text("DELETE FROM app_user WHERE username = 'release-migration-user'")
            )
        engine.dispose()
        _restore_head(config)


def test_downgrade_also_refuses_when_the_release_was_withdrawn(
    db_engine, monkeypatch
):
    """Withdrawal is not an escape hatch for dropping the tables.

    A withdrawn release is exactly the case where an outstanding citation must
    still resolve — to "this was retracted, and here is why" rather than a 404.
    Guarding only ``published`` meant the runbook's own retraction step made the
    data silently droppable.
    """
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            user_id = connection.scalar(
                text(
                    "INSERT INTO app_user (username, role, is_active) "
                    "VALUES ('release-withdrawn-user', 'curator', true) RETURNING id"
                )
            )
            policy_id = connection.scalar(
                text(
                    "INSERT INTO curation_policy "
                    "(name, version, description, public_ref, created_by) "
                    "VALUES ('pw', '1', 'd', 'cpol_withdrawntestrefaaaaaaaaa', :u) "
                    "RETURNING id"
                ),
                {"u": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO dataset_release "
                    "(tag, title, status, curation_policy_id, data_license, "
                    " code_license, citation_text, contact, published_at, "
                    " withdrawn_at, withdrawn_reason, public_ref) "
                    "VALUES ('withdrawn-test', 't', 'withdrawn', :p, 'CC-BY-4.0', "
                    "'MIT', 'c', 'x@example.org', now(), now(), 'retracted', "
                    "'rel_withdrawntestrefaaaaaaaaa')"
                ),
                {"p": policy_id},
            )

        with pytest.raises(RuntimeError, match="released datasets"):
            command.downgrade(config, PREVIOUS_HEAD)

        with engine.connect() as connection:
            assert set(_NEW_TABLES) <= _table_names(connection)
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM dataset_release WHERE tag = 'withdrawn-test'")
            )
            connection.execute(
                text("DELETE FROM curation_policy WHERE name = 'pw' AND version = '1'")
            )
            connection.execute(
                text("DELETE FROM app_user WHERE username = 'release-withdrawn-user'")
            )
        engine.dispose()
        _restore_head(config)


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
