#!/usr/bin/env python3
"""Resolve the local host port used by backend tests for the dev Postgres DB."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

CANDIDATE_PORTS = (5432, 5433, 5434, 5435)
DB_SERVICE = "db"
CONTAINER_DB_PORT = 5432

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_ROOT = SCRIPT_PATH.parents[2]
REPO_ROOT = SCRIPT_PATH.parents[3]
ENV_FILE = BACKEND_ROOT / ".env.test.local"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
COMPOSE_OVERRIDE_FILE = REPO_ROOT / "docker-compose.dev.local.yml"


@dataclass(frozen=True)
class ComposePort:
    host: str
    port: int


@dataclass
class ResolverStatus:
    applied: bool = False
    compose_command: tuple[str, ...] | None = None
    db_service_found: bool = False
    published_port: int | None = None
    published_port_reachable: bool = False
    selected_port: int | None = None
    selected_reason: str = ""
    errors: list[str] = field(default_factory=list)
    wrote_files: list[Path] = field(default_factory=list)
    compose_restart_recommended: bool = False


def parse_compose_port_output(output: str) -> ComposePort | None:
    """Parse output from `docker compose port db 5432`."""
    line = output.strip().splitlines()[0].strip() if output.strip() else ""
    if not line:
        return None

    if line.startswith("["):
        host, _, port_text = line.rpartition(":")
        host = host.strip("[]")
    else:
        host, _, port_text = line.rpartition(":")

    if not host or not port_text:
        return None

    try:
        return ComposePort(host=host, port=int(port_text))
    except ValueError:
        return None


def find_compose_command() -> tuple[str, ...] | None:
    if shutil.which("docker"):
        try:
            subprocess.run(
                ["docker", "compose", "version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return ("docker", "compose")
        except (OSError, subprocess.SubprocessError):
            pass

    if shutil.which("docker-compose"):
        return ("docker-compose",)

    return None


def inspect_compose_port(compose_command: tuple[str, ...] | None = None) -> tuple[ComposePort | None, list[str]]:
    command = compose_command or find_compose_command()
    if command is None:
        return None, ["Docker Compose command not found. Install Docker with `docker compose` support."]

    try:
        result = subprocess.run(
            [*command, "port", DB_SERVICE, str(CONTAINER_DB_PORT)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, [f"Could not inspect Docker Compose port mapping: {exc}"]

    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        return None, [f"db service port mapping not available: {message or 'docker compose port failed'}"]

    parsed = parse_compose_port_output(result.stdout)
    if parsed is None:
        return None, [f"Could not parse Docker Compose port output: {result.stdout.strip()!r}"]

    return parsed, []


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
    except OSError:
        return False
    return True


def can_connect_postgres(port: int, host: str = "127.0.0.1", timeout_seconds: int = 2) -> bool:
    try:
        import psycopg
    except ImportError:
        return False

    user = os.environ.get("DB_USER", "tckdb")
    password = os.environ.get("DB_PASSWORD", "tckdb")
    db_name = os.environ.get("DB_NAME", "postgres")
    try:
        with psycopg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=db_name,
            connect_timeout=timeout_seconds,
        ):
            return True
    except psycopg.OperationalError:
        return False


def select_port(
    *,
    compose_port: ComposePort | None,
    port_free_checker=is_port_free,
    postgres_checker=can_connect_postgres,
    candidate_ports: tuple[int, ...] = CANDIDATE_PORTS,
) -> tuple[int | None, str, bool]:
    if compose_port and compose_port.port in candidate_ports and postgres_checker(compose_port.port):
        return compose_port.port, "existing Docker Compose mapping is reachable", False

    for port in candidate_ports:
        if port_free_checker(port):
            return port, "first free candidate port", compose_port is None or compose_port.port != port

    return None, "no candidate port is free or already mapped to a reachable TCKDB db container", False


def compose_uses_db_port_variable(compose_file: Path = COMPOSE_FILE) -> bool:
    try:
        return "${DB_PORT" in compose_file.read_text(encoding="utf-8")
    except OSError:
        return False


def render_env(existing_text: str, port: int) -> str:
    updates = {"DB_HOST": "127.0.0.1", "DB_PORT": str(port)}
    seen: set[str] = set()
    lines: list[str] = []

    for line in existing_text.splitlines():
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    return "\n".join(lines).rstrip() + "\n"


def write_env_file(path: Path, port: int) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(render_env(existing, port), encoding="utf-8")


def write_compose_override(path: Path, port: int) -> None:
    path.write_text(
        "\n".join(
            [
                "# Dev-only Docker Compose override generated by backend/scripts/dev/ensure_test_db_port.py.",
                "# Do not use for production or self-hosted deployments.",
                "services:",
                "  db:",
                "    ports: !override",
                f'      - "127.0.0.1:{port}:5432"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def resolve(apply: bool = False) -> ResolverStatus:
    status = ResolverStatus()
    status.applied = apply
    status.compose_command = find_compose_command()
    compose_port, errors = inspect_compose_port(status.compose_command)
    status.errors.extend(errors)
    status.db_service_found = compose_port is not None
    status.published_port = compose_port.port if compose_port else None
    status.published_port_reachable = bool(compose_port and can_connect_postgres(compose_port.port))

    selected_port, reason, restart_recommended = select_port(compose_port=compose_port)
    status.selected_port = selected_port
    status.selected_reason = reason
    status.compose_restart_recommended = restart_recommended

    if selected_port is None:
        status.errors.append(
            "No usable host port found in 5432, 5433, 5434, 5435. "
            "Stop or reconfigure the conflicting local process, then rerun this resolver."
        )
        return status

    if apply:
        write_env_file(ENV_FILE, selected_port)
        status.wrote_files.append(ENV_FILE)
        if not compose_uses_db_port_variable():
            write_compose_override(COMPOSE_OVERRIDE_FILE, selected_port)
            status.wrote_files.append(COMPOSE_OVERRIDE_FILE)

    return status


def format_status(status: ResolverStatus, pytest_args: list[str]) -> str:
    lines = ["TCKDB test DB port resolver", "", "Status:"]
    compose_name = " ".join(status.compose_command) if status.compose_command else "not found"
    lines.append(f"  compose command: {compose_name}")
    lines.append(f"  db service: {'found' if status.db_service_found else 'not found'}")
    lines.append(f"  published host port: {status.published_port or 'none'}")
    lines.append(f"  published port reachable: {'yes' if status.published_port_reachable else 'no'}")
    lines.append(f"  selected DB_PORT: {status.selected_port or 'none'}")
    if status.selected_reason:
        lines.append(f"  selection reason: {status.selected_reason}")

    if status.errors:
        lines.extend(["", "Diagnostics:"])
        lines.extend(f"  - {error}" for error in status.errors)

    if status.wrote_files:
        lines.extend(["", "Wrote:"])
        lines.extend(f"  {path.relative_to(REPO_ROOT)}" for path in status.wrote_files)
    else:
        lines.extend(["", "Wrote:", "  nothing (dry run)"])

    if status.selected_port is not None:
        lines.extend(["", "Use:"])
        if not status.applied:
            lines.append(
                "  conda run -n tckdb_env python backend/scripts/dev/ensure_test_db_port.py --apply"
            )
            return "\n".join(lines)
        if status.compose_restart_recommended:
            if compose_uses_db_port_variable():
                lines.append("  docker compose --env-file .env --env-file backend/.env.test.local up -d db")
            else:
                lines.append("  docker compose -f docker-compose.yml -f docker-compose.dev.local.yml up -d db")
        lines.extend(
            [
                "  set -a",
                "  source backend/.env.test.local",
                "  set +a",
                f"  conda run -n tckdb_env pytest {' '.join(pytest_args) if pytest_args else 'backend/tests/ -v'}",
            ]
        )

    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write local env/override files")
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="optional pytest arguments to echo in the recovery command",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    status = resolve(apply=args.apply)
    print(format_status(status, pytest_args))
    return 1 if status.selected_port is None else 0


if __name__ == "__main__":
    raise SystemExit(main())
