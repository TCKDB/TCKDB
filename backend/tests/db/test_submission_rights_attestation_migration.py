"""Migration coverage for the rights-attestation revision (``9b1c7e2d4a68``).

Checks what ``alembic upgrade head`` alone does not: the revision is genuinely
reversible (``downgrade`` drops the table, its immutability trigger and
function, the manifest column and the enum it owns, and a re-``upgrade``
succeeds), the table it creates starts empty (no backfill -- consent is not
something a migration can write), and the guard it installs actually refuses
an UPDATE.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The subject of this file, and the revision below it -- read from the
#: chain rather than written down twice. See ``_migration_chain``.
_MIGRATION = revision_under_test("9b1c7e2d4a68")

_TABLE = "submission_rights_attestation"
_ENUM = "rights_basis_kind"
_TRIGGER = "trg_submission_rights_attestation_immutable"
_FUNCTION = "reject_submission_rights_attestation_mutation"


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
    """Put the shared database back at ``head``, not at the revision under test."""
    command.upgrade(config, "head")


def _table_names(connection) -> set[str]:
    return set(
        connection.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
        )
    )


def _enum_names(connection) -> set[str]:
    return set(
        connection.scalars(text("SELECT typname FROM pg_type WHERE typtype = 'e'"))
    )


def _trigger_names(connection, table: str) -> set[str]:
    return set(
        connection.scalars(
            text(
                "SELECT t.tgname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE c.relname = :table AND NOT t.tgisinternal"
            ),
            {"table": table},
        )
    )


def _function_exists(connection, name: str) -> bool:
    return bool(
        connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = :name)"),
            {"name": name},
        )
    )


def _column_exists(connection, table: str, column: str) -> bool:
    return bool(
        connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column)"
            ),
            {"table": table, "column": column},
        )
    )


def test_upgrade_creates_table_trigger_enum_and_column_and_downgrade_removes_them(
    db_engine, monkeypatch
):
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.connect() as connection:
            assert _TABLE in _table_names(connection)

        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            assert _TABLE not in _table_names(connection)
            assert _ENUM not in _enum_names(connection)
            assert not _function_exists(connection, _FUNCTION)
            assert not _column_exists(connection, "release_manifest", "rights_summary_json")
            # …but the vocabulary it merely reuses is untouched.
            assert "submission_actor_kind" in _enum_names(connection)

        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            assert _TABLE in _table_names(connection)
            assert _ENUM in _enum_names(connection)
            assert _function_exists(connection, _FUNCTION)
            assert {_TRIGGER, f"{_TRIGGER}_truncate"} <= _trigger_names(connection, _TABLE)
            assert _column_exists(connection, "release_manifest", "rights_summary_json")
            # No backfill: a migration cannot write consent nobody gave.
            assert connection.scalar(text(f"SELECT count(*) FROM {_TABLE}")) == 0
    finally:
        engine.dispose()
        _restore_head(config)


def test_the_guard_refuses_an_update_and_a_delete(db_engine, monkeypatch):
    """The trigger is not decoration: a direct UPDATE or DELETE is refused."""
    config = _configure(db_engine, monkeypatch)
    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            user_id = connection.scalar(
                text(
                    "INSERT INTO app_user (username, role, is_active) "
                    "VALUES ('rights-migration-user', 'user', true) RETURNING id"
                )
            )
            submission_id = connection.scalar(
                text(
                    "INSERT INTO submission (created_by, submission_kind, source_kind, "
                    "status, public_ref) VALUES (:u, 'thermo', 'api', 'pending', "
                    "'sub_rightsmigrationprobe00000') RETURNING id"
                ),
                {"u": user_id},
            )
            row_id = connection.scalar(
                text(
                    f"INSERT INTO {_TABLE} (submission_id, license_id, basis, "
                    "attested_by, actor_kind, public_ref) VALUES (:s, 'CC-BY-4.0', "
                    "'depositor_agreement', :u, 'user', 'sra_rightsmigrationprobe00000') "
                    "RETURNING id"
                ),
                {"s": submission_id, "u": user_id},
            )
            for statement in (
                f"UPDATE {_TABLE} SET license_id = 'CC0-1.0' WHERE id = :id",
                f"DELETE FROM {_TABLE} WHERE id = :id",
            ):
                try:
                    connection.execute(text("SAVEPOINT probe"))
                    connection.execute(text(statement), {"id": row_id})
                except Exception as exc:
                    assert "append-only" in str(exc)
                    connection.execute(text("ROLLBACK TO SAVEPOINT probe"))
                else:
                    raise AssertionError(f"{statement!r} was not refused")
            # Leave nothing behind for the rest of the suite: the attestation
            # cannot be deleted, so the whole transaction is rolled back.
            connection.rollback()
    finally:
        engine.dispose()
        _restore_head(config)
