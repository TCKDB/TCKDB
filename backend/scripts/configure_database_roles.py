"""Provision and verify separated PostgreSQL owner and runtime roles.

The API uses ``DB_USER`` / ``DB_PASSWORD``.  Alembic uses
``DB_OWNER_USER`` / ``DB_OWNER_PASSWORD``.  This command connects with the
cluster-administrator credentials in ``DB_ADMIN_USER`` /
``DB_ADMIN_PASSWORD`` and makes that separation real for one database.

Passwords are read only from the environment; they are never accepted as
command-line arguments or printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Mapping

import psycopg
from psycopg import sql


class RoleConfigurationError(RuntimeError):
    """Raised when role provisioning cannot establish the safe contract."""


@dataclass(frozen=True)
class DatabaseRoleSettings:
    host: str
    port: int
    database: str
    admin_user: str
    admin_password: str
    owner_user: str
    owner_password: str
    runtime_user: str
    runtime_password: str
    statement_timeout_ms: int

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DatabaseRoleSettings:
        values = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise RoleConfigurationError(f"{name} must be set")
            return value

        try:
            port = int(values.get("DB_PORT", "5432"))
            statement_timeout_ms = int(values.get("DB_STATEMENT_TIMEOUT_MS", "30000"))
        except ValueError as exc:
            raise RoleConfigurationError("DB_PORT and DB_STATEMENT_TIMEOUT_MS must be integers") from exc
        if not 1 <= port <= 65535:
            raise RoleConfigurationError("DB_PORT must be between 1 and 65535")
        if statement_timeout_ms <= 0:
            raise RoleConfigurationError("DB_STATEMENT_TIMEOUT_MS must be positive")

        result = cls(
            host=values.get("DB_HOST", "127.0.0.1"),
            port=port,
            database=required("DB_NAME"),
            admin_user=required("DB_ADMIN_USER"),
            admin_password=required("DB_ADMIN_PASSWORD"),
            owner_user=required("DB_OWNER_USER"),
            owner_password=required("DB_OWNER_PASSWORD"),
            runtime_user=required("DB_USER"),
            runtime_password=required("DB_PASSWORD"),
            statement_timeout_ms=statement_timeout_ms,
        )
        role_names = {result.admin_user, result.owner_user, result.runtime_user}
        if len(role_names) != 3:
            raise RoleConfigurationError("DB_ADMIN_USER, DB_OWNER_USER, and DB_USER must be distinct")
        return result


@dataclass(frozen=True)
class ApprovalEvidence:
    review_rows: int
    event_rows: int

    @property
    def total(self) -> int:
        return self.review_rows + self.event_rows


def _connect(settings: DatabaseRoleSettings) -> psycopg.Connection:
    return psycopg.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.admin_user,
        password=settings.admin_password,
        connect_timeout=10,
    )


def _relation_exists(cursor: psycopg.Cursor, relation: str) -> bool:
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{relation}",))
    return bool(cursor.fetchone()[0])


def _column_exists(cursor: psycopg.Cursor, table: str, column: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
        )
        """,
        (table, column),
    )
    return bool(cursor.fetchone()[0])


def approval_evidence(cursor: psycopg.Cursor) -> ApprovalEvidence:
    """Count surviving evidence that any scientific review reached approval."""
    if not _relation_exists(cursor, "record_review"):
        return ApprovalEvidence(0, 0)

    first_approval_clause = (
        " OR first_approved_at IS NOT NULL"
        if _column_exists(
            cursor,
            "record_review",
            "first_approved_at",
        )
        else ""
    )
    cursor.execute(
        sql.SQL("SELECT count(*) FROM public.record_review WHERE status IN ('approved', 'deprecated'){}").format(
            sql.SQL(first_approval_clause)
        )
    )
    review_rows = int(cursor.fetchone()[0])

    event_rows = 0
    if _relation_exists(cursor, "record_review_event"):
        cursor.execute(
            """
            SELECT count(DISTINCT record_review_id)
            FROM public.record_review_event
            WHERE from_status = 'approved' OR to_status = 'approved'
            """
        )
        event_rows = int(cursor.fetchone()[0])
    return ApprovalEvidence(review_rows, event_rows)


def _role_exists(cursor: psycopg.Cursor, role: str) -> bool:
    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (role,))
    return bool(cursor.fetchone()[0])


def _ensure_login_role(cursor: psycopg.Cursor, role: str, password: str) -> None:
    attributes = sql.SQL(
        "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
    ).format(sql.Literal(password))
    if _role_exists(cursor, role):
        cursor.execute(sql.SQL("ALTER ROLE {} WITH ").format(sql.Identifier(role)) + attributes)
    else:
        cursor.execute(sql.SQL("CREATE ROLE {} WITH ").format(sql.Identifier(role)) + attributes)


def _is_extension_member_clause(class_name: str, oid_expression: str) -> sql.SQL:
    return sql.SQL(
        "NOT EXISTS ("
        "SELECT 1 FROM pg_depend dependency "
        "WHERE dependency.classid = {}::regclass "
        "AND dependency.objid = {} AND dependency.deptype = 'e'"
        ")"
    ).format(sql.Literal(class_name), sql.SQL(oid_expression))


def _catalog_text(value: str | bytes) -> str:
    """Normalize catalog text for clusters using a SQL_ASCII database."""
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _transfer_public_ownership(cursor: psycopg.Cursor, owner: str) -> int:
    """Transfer non-extension objects in ``public`` to the migration owner."""
    changed = 0
    cursor.execute(
        sql.SQL(
            "SELECT relation.relkind, namespace.nspname, relation.relname "
            "FROM pg_class relation "
            "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
            "WHERE namespace.nspname = 'public' "
            "AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f') "
            "AND (relation.relkind <> 'S' OR NOT EXISTS ("
            "SELECT 1 FROM pg_depend sequence_dependency "
            "WHERE sequence_dependency.classid = 'pg_class'::regclass "
            "AND sequence_dependency.objid = relation.oid "
            "AND sequence_dependency.refclassid = 'pg_class'::regclass "
            "AND sequence_dependency.deptype IN ('a', 'i')"
            ")) AND {} "
            "ORDER BY relation.relkind, relation.relname"
        ).format(_is_extension_member_clause("pg_class", "relation.oid"))
    )
    relation_commands = {
        "r": "TABLE",
        "p": "TABLE",
        "S": "SEQUENCE",
        "v": "VIEW",
        "m": "MATERIALIZED VIEW",
        "f": "FOREIGN TABLE",
    }
    for kind, schema_name, object_name in cursor.fetchall():
        kind = _catalog_text(kind)
        schema_name = _catalog_text(schema_name)
        object_name = _catalog_text(object_name)
        cursor.execute(
            sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                sql.SQL(relation_commands[kind]),
                sql.Identifier(schema_name),
                sql.Identifier(object_name),
                sql.Identifier(owner),
            )
        )
        changed += 1

    cursor.execute(
        sql.SQL(
            "SELECT namespace.nspname, procedure.proname, "
            "pg_get_function_identity_arguments(procedure.oid), procedure.prokind "
            "FROM pg_proc procedure "
            "JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace "
            "WHERE namespace.nspname = 'public' AND procedure.prokind IN ('f', 'p') AND {} "
            "ORDER BY procedure.proname, procedure.oid"
        ).format(_is_extension_member_clause("pg_proc", "procedure.oid"))
    )
    for schema_name, object_name, arguments, kind in cursor.fetchall():
        schema_name = _catalog_text(schema_name)
        object_name = _catalog_text(object_name)
        arguments = _catalog_text(arguments)
        kind = _catalog_text(kind)
        command = "PROCEDURE" if kind == "p" else "FUNCTION"
        cursor.execute(
            sql.SQL("ALTER {} {}.{}({}) OWNER TO {}").format(
                sql.SQL(command),
                sql.Identifier(schema_name),
                sql.Identifier(object_name),
                sql.SQL(arguments),
                sql.Identifier(owner),
            )
        )
        changed += 1

    cursor.execute(
        sql.SQL(
            "SELECT namespace.nspname, type_.typname, type_.typtype "
            "FROM pg_type type_ "
            "JOIN pg_namespace namespace ON namespace.oid = type_.typnamespace "
            "WHERE namespace.nspname = 'public' AND type_.typtype IN ('d', 'e') AND {} "
            "ORDER BY type_.typname"
        ).format(_is_extension_member_clause("pg_type", "type_.oid"))
    )
    for schema_name, object_name, kind in cursor.fetchall():
        schema_name = _catalog_text(schema_name)
        object_name = _catalog_text(object_name)
        kind = _catalog_text(kind)
        command = "DOMAIN" if kind == "d" else "TYPE"
        cursor.execute(
            sql.SQL("ALTER {} {}.{} OWNER TO {}").format(
                sql.SQL(command),
                sql.Identifier(schema_name),
                sql.Identifier(object_name),
                sql.Identifier(owner),
            )
        )
        changed += 1
    return changed


def _grant_runtime_privileges(
    cursor: psycopg.Cursor,
    settings: DatabaseRoleSettings,
) -> None:
    database = sql.Identifier(settings.database)
    owner = sql.Identifier(settings.owner_user)
    runtime = sql.Identifier(settings.runtime_user)

    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(database, runtime))
    cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, runtime))
    cursor.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(database))
    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA public FROM {}").format(runtime))
    cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(runtime))

    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {}").format(runtime))
    cursor.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(runtime))
    cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(runtime))
    cursor.execute(sql.SQL("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {}").format(runtime))
    if _relation_exists(cursor, "alembic_version"):
        cursor.execute(
            sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.alembic_version FROM {}").format(runtime)
        )

    cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON TABLES FROM {}").format(
            owner,
            runtime,
        )
    )
    cursor.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(owner, runtime)
    )
    cursor.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {}").format(
            owner,
            runtime,
        )
    )
    cursor.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
        ).format(owner, runtime)
    )

    cursor.execute(
        sql.SQL("ALTER ROLE {} SET statement_timeout TO {}").format(
            runtime,
            sql.Literal(f"{settings.statement_timeout_ms}ms"),
        )
    )
    cursor.execute(sql.SQL("REVOKE {} FROM {}").format(owner, runtime))
    cursor.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(settings.admin_user), runtime))


def verify_role_contract(
    cursor: psycopg.Cursor,
    settings: DatabaseRoleSettings,
) -> list[str]:
    """Return violations of the migration-owner/runtime contract."""
    violations: list[str] = []
    for role_name, label in (
        (settings.owner_user, "migration owner"),
        (settings.runtime_user, "runtime"),
    ):
        cursor.execute(
            """
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit,
                   rolreplication, rolbypassrls
            FROM pg_roles WHERE rolname = %s
            """,
            (role_name,),
        )
        row = cursor.fetchone()
        if row is None:
            violations.append(f"{label} role {role_name!r} does not exist")
            continue
        if any((row[0], row[1], row[2], row[4], row[5])):
            violations.append(f"{label} role {role_name!r} has elevated cluster privileges")
        if row[3]:
            violations.append(f"{label} role {role_name!r} must be NOINHERIT")

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_auth_members membership
            JOIN pg_roles member ON member.oid = membership.member
            WHERE member.rolname = %s
        )
        """,
        (settings.runtime_user,),
    )
    if cursor.fetchone()[0]:
        violations.append("runtime role must not be a member of another role")

    privilege_checks = (
        ("SELECT has_schema_privilege(%s, 'public', 'CREATE')", "runtime role can create in public schema"),
        (
            "SELECT has_database_privilege(%s, current_database(), 'TEMPORARY')",
            "runtime role can create temporary tables",
        ),
    )
    for query, message in privilege_checks:
        cursor.execute(query, (settings.runtime_user,))
        if cursor.fetchone()[0]:
            violations.append(message)

    cursor.execute(
        """
        SELECT owner.rolname
        FROM pg_database database_
        JOIN pg_roles owner ON owner.oid = database_.datdba
        WHERE database_.datname = current_database()
        """
    )
    if _catalog_text(cursor.fetchone()[0]) != settings.owner_user:
        violations.append("migration owner does not own the database")
    cursor.execute(
        """
        SELECT owner.rolname
        FROM pg_namespace namespace
        JOIN pg_roles owner ON owner.oid = namespace.nspowner
        WHERE namespace.nspname = 'public'
        """
    )
    if _catalog_text(cursor.fetchone()[0]) != settings.owner_user:
        violations.append("migration owner does not own the public schema")

    if _relation_exists(cursor, "record_review"):
        cursor.execute(
            "SELECT has_table_privilege(%s, 'public.record_review', 'TRUNCATE')",
            (settings.runtime_user,),
        )
        if cursor.fetchone()[0]:
            violations.append("runtime role can truncate protected tables")

    cursor.execute(
        """
        SELECT count(*)
        FROM pg_class relation
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_roles owner ON owner.oid = relation.relowner
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
          AND owner.rolname = %s
        """,
        (settings.runtime_user,),
    )
    if cursor.fetchone()[0]:
        violations.append("runtime role owns objects in public schema")

    cursor.execute(
        """
        SELECT count(*)
        FROM pg_proc procedure
        JOIN pg_namespace namespace ON namespace.oid = procedure.pronamespace
        JOIN pg_roles owner ON owner.oid = procedure.proowner
        WHERE namespace.nspname = 'public' AND owner.rolname = %s
        """,
        (settings.runtime_user,),
    )
    if cursor.fetchone()[0]:
        violations.append("runtime role owns functions in public schema")

    cursor.execute(
        """
        SELECT count(*)
        FROM pg_type type_
        JOIN pg_namespace namespace ON namespace.oid = type_.typnamespace
        JOIN pg_roles owner ON owner.oid = type_.typowner
        WHERE namespace.nspname = 'public'
          AND type_.typtype IN ('d', 'e')
          AND owner.rolname = %s
        """,
        (settings.runtime_user,),
    )
    if cursor.fetchone()[0]:
        violations.append("runtime role owns application types in public schema")
    return violations


def configure_database_roles(
    settings: DatabaseRoleSettings,
    *,
    allow_existing_approvals: bool = False,
) -> tuple[ApprovalEvidence, int]:
    """Apply the role split atomically and return preflight/transfer counts."""
    with _connect(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            row = cursor.fetchone()
            if row is None or not row[0]:
                raise RoleConfigurationError("DB_ADMIN_USER must be a PostgreSQL superuser")

            evidence = approval_evidence(cursor)
            if evidence.total and not allow_existing_approvals:
                raise RoleConfigurationError(
                    "approval history exists; inspect it before provisioning or pass "
                    "--allow-existing-approvals after an explicit curator audit"
                )

            # RDKit is the one superuser-owned prerequisite used by the
            # initial schema migration.  Install it before handing ordinary
            # application migrations to the non-superuser owner.
            cursor.execute("CREATE EXTENSION IF NOT EXISTS rdkit")
            _ensure_login_role(cursor, settings.owner_user, settings.owner_password)
            _ensure_login_role(cursor, settings.runtime_user, settings.runtime_password)
            cursor.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(settings.database),
                    sql.Identifier(settings.owner_user),
                )
            )
            cursor.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(sql.Identifier(settings.owner_user)))
            changed = _transfer_public_ownership(cursor, settings.owner_user)
            _grant_runtime_privileges(cursor, settings)
            violations = verify_role_contract(cursor, settings)
            if violations:
                raise RoleConfigurationError("; ".join(violations))
        connection.commit()
    return evidence, changed


def check_database_roles(settings: DatabaseRoleSettings) -> tuple[ApprovalEvidence, list[str]]:
    """Read and verify the deployed contract without changing database state."""
    with _connect(settings) as connection:
        with connection.cursor() as cursor:
            evidence = approval_evidence(cursor)
            violations = verify_role_contract(cursor, settings)
        connection.rollback()
    return evidence, violations


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command", required=True)
    apply_parser = subcommands.add_parser("apply", help="provision roles, ownership, and privileges")
    apply_parser.add_argument(
        "--allow-existing-approvals",
        action="store_true",
        help="continue only after an explicit curator audit of existing approval history",
    )
    subcommands.add_parser("check", help="verify roles and privileges without mutation")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        settings = DatabaseRoleSettings.from_environment()
        if args.command == "apply":
            evidence, changed = configure_database_roles(
                settings,
                allow_existing_approvals=args.allow_existing_approvals,
            )
            print(
                f"database roles configured; transferred_objects={changed}; "
                f"approval_review_rows={evidence.review_rows}; approval_event_rows={evidence.event_rows}"
            )
        else:
            evidence, violations = check_database_roles(settings)
            if violations:
                print("database role contract is unsafe:", file=sys.stderr)
                for violation in violations:
                    print(f"- {violation}", file=sys.stderr)
                return 1
            print(
                "database role contract is safe; "
                f"approval_review_rows={evidence.review_rows}; approval_event_rows={evidence.event_rows}"
            )
        return 0
    except (RoleConfigurationError, psycopg.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
