"""Tests for hosted PostgreSQL owner/runtime role separation."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import psycopg
import pytest
from psycopg import sql

from scripts.configure_database_roles import (
    DatabaseRoleSettings,
    RoleConfigurationError,
    check_database_roles,
    configure_database_roles,
)


def _settings_environment() -> dict[str, str]:
    return {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
        "DB_NAME": "tckdb",
        "DB_ADMIN_USER": "admin",
        "DB_ADMIN_PASSWORD": "admin-password",
        "DB_OWNER_USER": "owner",
        "DB_OWNER_PASSWORD": "owner-password",
        "DB_USER": "runtime",
        "DB_PASSWORD": "runtime-password",
        "DB_STATEMENT_TIMEOUT_MS": "15000",
    }


def test_settings_require_three_distinct_roles() -> None:
    environment = _settings_environment()
    environment["DB_USER"] = environment["DB_OWNER_USER"]

    with pytest.raises(RoleConfigurationError, match="must be distinct"):
        DatabaseRoleSettings.from_environment(environment)


def test_settings_do_not_fall_back_to_runtime_for_owner_or_admin() -> None:
    environment = _settings_environment()
    del environment["DB_OWNER_PASSWORD"]

    with pytest.raises(RoleConfigurationError, match="DB_OWNER_PASSWORD must be set"):
        DatabaseRoleSettings.from_environment(environment)


def _admin_connection(database: str, *, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=database,
        user=os.environ.get("DB_USER", "tckdb"),
        password=os.environ.get("DB_PASSWORD", "tckdb"),
        autocommit=autocommit,
    )


def test_configure_roles_enforces_runtime_boundary() -> None:
    suffix = uuid.uuid4().hex[:10]
    database = f"tckdb_role_test_{suffix}"
    owner = f"tckdb_owner_{suffix}"
    runtime = f"tckdb_app_{suffix}"
    owner_password = f"owner-{suffix}"
    runtime_password = f"runtime-{suffix}"
    admin_user = os.environ.get("DB_USER", "tckdb")

    with _admin_connection("postgres", autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cursor.fetchone()[0]:
                pytest.skip("database-role integration test requires a PostgreSQL superuser")
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    try:
        with _admin_connection(database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE TYPE public.review_status_test AS ENUM ('approved', 'deprecated', 'other')")
                cursor.execute(
                    """
                    CREATE TABLE public.record_review (
                        id bigserial PRIMARY KEY,
                        status public.review_status_test NOT NULL,
                        first_approved_at timestamp NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE public.record_review_event (
                        id bigserial PRIMARY KEY,
                        record_review_id bigint NOT NULL,
                        from_status public.review_status_test NULL,
                        to_status public.review_status_test NULL
                    )
                    """
                )
                cursor.execute("CREATE TABLE public.science_row (id bigserial PRIMARY KEY, value text NOT NULL)")
                cursor.execute("CREATE TABLE public.alembic_version (version_num text PRIMARY KEY)")
            connection.commit()

        settings = DatabaseRoleSettings(
            host=os.environ.get("DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("DB_PORT", "5432")),
            database=database,
            admin_user=admin_user,
            admin_password=os.environ.get("DB_PASSWORD", "tckdb"),
            owner_user=owner,
            owner_password=owner_password,
            runtime_user=runtime,
            runtime_password=runtime_password,
            statement_timeout_ms=12_345,
        )

        with _admin_connection(database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO public.record_review (status) VALUES ('approved')")
            connection.commit()
        with pytest.raises(RoleConfigurationError, match="approval history exists"):
            configure_database_roles(settings)
        with _admin_connection(database) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM public.record_review")
            connection.commit()

        evidence, changed = configure_database_roles(settings)
        assert evidence.total == 0
        assert changed > 0
        assert check_database_roles(settings) == (evidence, [])

        runtime_connection = psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=database,
            user=runtime,
            password=runtime_password,
        )
        try:
            with runtime_connection.cursor() as cursor:
                cursor.execute("INSERT INTO public.science_row (value) VALUES ('allowed')")
                cursor.execute("SELECT value FROM public.science_row")
                value = cursor.fetchone()[0]
                assert (value.decode() if isinstance(value, bytes) else value) == "allowed"
                cursor.execute("SHOW statement_timeout")
                timeout = cursor.fetchone()[0]
                assert (timeout.decode() if isinstance(timeout, bytes) else timeout) == "12345ms"
            runtime_connection.commit()

            with runtime_connection.cursor() as cursor, pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute("TRUNCATE public.science_row")
            runtime_connection.rollback()

            with runtime_connection.cursor() as cursor, pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute("ALTER TABLE public.science_row ADD COLUMN forbidden integer")
            runtime_connection.rollback()

            with runtime_connection.cursor() as cursor, pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute("CREATE TEMPORARY TABLE forbidden_temp (id integer)")
            runtime_connection.rollback()
        finally:
            runtime_connection.close()

        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=database,
            user=owner,
            password=owner_password,
        ) as owner_connection:
            with owner_connection.cursor() as cursor:
                cursor.execute("CREATE TABLE public.future_row (id bigserial PRIMARY KEY, value text NOT NULL)")
            owner_connection.commit()

        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=database,
            user=runtime,
            password=runtime_password,
        ) as runtime_connection:
            with runtime_connection.cursor() as cursor:
                cursor.execute("INSERT INTO public.future_row (value) VALUES ('default privilege')")
            runtime_connection.commit()
    finally:
        with _admin_connection("postgres", autocommit=True) as admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                    (database,),
                )
                cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
                cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime)))
                cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))


def test_migration_owner_can_build_schema_and_runtime_receives_dml_privileges() -> None:
    suffix = uuid.uuid4().hex[:10]
    database = f"tckdb_role_migration_{suffix}"
    owner = f"tckdb_owner_{suffix}"
    runtime = f"tckdb_app_{suffix}"
    owner_password = f"owner-{suffix}"
    runtime_password = f"runtime-{suffix}"
    admin_user = os.environ.get("DB_USER", "tckdb")

    with _admin_connection("postgres", autocommit=True) as admin:
        with admin.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            if not cursor.fetchone()[0]:
                pytest.skip("database-role integration test requires a PostgreSQL superuser")
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

    settings = DatabaseRoleSettings(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=database,
        admin_user=admin_user,
        admin_password=os.environ.get("DB_PASSWORD", "tckdb"),
        owner_user=owner,
        owner_password=owner_password,
        runtime_user=runtime,
        runtime_password=runtime_password,
        statement_timeout_ms=30_000,
    )

    try:
        configure_database_roles(settings)
        migration_environment = os.environ.copy()
        migration_environment.update(
            {
                "DEPLOYMENT_MODE": "hosted_public",
                "DB_HOST": settings.host,
                "DB_PORT": str(settings.port),
                "DB_NAME": database,
                "DB_USER": runtime,
                "DB_PASSWORD": runtime_password,
                "DB_OWNER_USER": owner,
                "DB_OWNER_PASSWORD": owner_password,
            }
        )
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=os.fspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            env=migration_environment,
            capture_output=True,
            text=True,
        )

        evidence, violations = check_database_roles(settings)
        assert evidence.total == 0
        assert violations == []
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=database,
            user=runtime,
            password=runtime_password,
        ) as runtime_connection:
            with runtime_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT has_table_privilege(current_user, 'public.record_review', 'SELECT'), "
                    "has_table_privilege(current_user, 'public.record_review', 'TRUNCATE')"
                )
                assert cursor.fetchone() == (True, False)
    finally:
        with _admin_connection("postgres", autocommit=True) as admin:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
                    (database,),
                )
                cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
                cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime)))
                cursor.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))
