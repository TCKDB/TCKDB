from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "ensure_test_db_port.py"
spec = importlib.util.spec_from_file_location("ensure_test_db_port", SCRIPT_PATH)
assert spec is not None
ensure_test_db_port = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = ensure_test_db_port
spec.loader.exec_module(ensure_test_db_port)


def test_selects_5432_when_free():
    selected, reason, restart = ensure_test_db_port.select_port(
        compose_port=None,
        port_free_checker=lambda port: port == 5432,
        postgres_checker=lambda port: False,
    )

    assert selected == 5432
    assert reason == "first free candidate port"
    assert restart is True


def test_selects_5433_when_5432_is_occupied():
    selected, _, restart = ensure_test_db_port.select_port(
        compose_port=None,
        port_free_checker=lambda port: port == 5433,
        postgres_checker=lambda port: False,
    )

    assert selected == 5433
    assert restart is True


def test_parses_docker_compose_port_output():
    parsed = ensure_test_db_port.parse_compose_port_output("127.0.0.1:5433\n")

    assert parsed == ensure_test_db_port.ComposePort(host="127.0.0.1", port=5433)


def test_writes_env_file_safely(tmp_path):
    env_file = tmp_path / ".env.test.local"

    ensure_test_db_port.write_env_file(env_file, 5434)

    assert env_file.read_text(encoding="utf-8") == "DB_HOST=127.0.0.1\nDB_PORT=5434\n"


def test_preserves_unrelated_env_values_when_updating(tmp_path):
    env_file = tmp_path / ".env.test.local"
    env_file.write_text("DB_USER=tckdb\nDB_PORT=5432\n# keep me\nTOKEN=value\n", encoding="utf-8")

    ensure_test_db_port.write_env_file(env_file, 5435)

    assert env_file.read_text(encoding="utf-8") == (
        "DB_USER=tckdb\nDB_PORT=5435\n# keep me\nTOKEN=value\nDB_HOST=127.0.0.1\n"
    )


def test_fails_clearly_when_no_candidate_ports_are_available():
    selected, reason, restart = ensure_test_db_port.select_port(
        compose_port=None,
        port_free_checker=lambda port: False,
        postgres_checker=lambda port: False,
    )

    assert selected is None
    assert "no candidate port" in reason
    assert restart is False


def test_dry_run_does_not_write_files(monkeypatch, tmp_path):
    monkeypatch.setattr(ensure_test_db_port, "ENV_FILE", tmp_path / ".env.test.local")
    monkeypatch.setattr(ensure_test_db_port, "find_compose_command", lambda: None)
    monkeypatch.setattr(ensure_test_db_port, "inspect_compose_port", lambda command=None: (None, []))
    monkeypatch.setattr(
        ensure_test_db_port,
        "select_port",
        lambda compose_port: (5432, "first free candidate port", True),
    )

    status = ensure_test_db_port.resolve(apply=False)

    assert status.selected_port == 5432
    assert not status.wrote_files
    assert not (tmp_path / ".env.test.local").exists()


def test_apply_writes_expected_files(monkeypatch, tmp_path):
    env_file = tmp_path / ".env.test.local"
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text('ports:\n  - "127.0.0.1:${DB_PORT:-5432}:5432"\n', encoding="utf-8")
    monkeypatch.setattr(ensure_test_db_port, "ENV_FILE", env_file)
    monkeypatch.setattr(ensure_test_db_port, "COMPOSE_FILE", compose_file)
    monkeypatch.setattr(ensure_test_db_port, "find_compose_command", lambda: None)
    monkeypatch.setattr(ensure_test_db_port, "inspect_compose_port", lambda command=None: (None, []))
    monkeypatch.setattr(
        ensure_test_db_port,
        "select_port",
        lambda compose_port: (5433, "first free candidate port", True),
    )

    status = ensure_test_db_port.resolve(apply=True)

    assert env_file.read_text(encoding="utf-8") == "DB_HOST=127.0.0.1\nDB_PORT=5433\n"
    assert status.wrote_files == [env_file]


def test_backend_testing_docs_mention_recovery_triggers_and_command():
    docs = (Path(__file__).resolve().parents[2] / "docs" / "testing.md").read_text(encoding="utf-8")

    assert "psycopg.OperationalError" in docs
    assert "127.0.0.1:5432" in docs
    assert "ensure_test_db_port.py --apply" in docs
