"""The coverage inventories never read a database nobody named.

Inside a deployed container the configured database is the live one. A report that connected to it because no flag
said otherwise would read production silently, so each inventory script refuses to run without
``--database-url`` or, on purpose, ``--use-configured-database``. The transaction it then opens is REPEATABLE READ and
READ ONLY.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.read_only_report import choose_database_url, print_read_only_report

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = {
    "kinetics": (BACKEND / "scripts/ops/kinetics_selection_coverage_inventory.py", "kinetics_records"),
    "thermo": (BACKEND / "scripts/ops/thermo_h298_coverage_inventory.py", "thermo_records"),
    "network": (BACKEND / "scripts/ops/network_selection_coverage_inventory.py", "network_solves"),
}
#: Nothing listens here, so a script that tried to connect would fail with a connection error, not a usage error.
UNREACHABLE = {"DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_USER": "nobody", "DB_PASSWORD": "x", "DB_NAME": "none"}


def _run(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(BACKEND), *sys.path]), **(env or {})}
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, env=full_env, cwd=BACKEND, timeout=120
    )


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_without_a_named_database_the_script_prints_usage_and_exits_2_without_connecting(which):
    script, _ = SCRIPTS[which]
    result = _run(script, env=UNREACHABLE)
    assert result.returncode == 2, result.stderr
    assert "--database-url" in result.stderr and "--use-configured-database" in result.stderr
    assert "required" in result.stderr
    assert result.stdout == ""
    assert "connection" not in result.stderr.lower()  # it never tried to reach the (unreachable) configured database


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_naming_both_databases_is_refused_too(which):
    script, _ = SCRIPTS[which]
    result = _run(script, "--database-url", "postgresql://x/y", "--use-configured-database", env=UNREACHABLE)
    assert result.returncode == 2 and "not allowed" in result.stderr


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_a_named_database_url_is_the_one_read(which, db_engine):
    script, key = SCRIPTS[which]
    url = db_engine.url.render_as_string(hide_password=False)
    result = _run(script, "--database-url", url, env=UNREACHABLE)  # the configured one is unreachable, so it was not used
    assert result.returncode == 0, result.stderr
    assert key in json.loads(result.stdout)


@pytest.mark.parametrize("which", sorted(SCRIPTS))
def test_the_configured_database_is_read_only_when_asked_for_by_name(which, db_engine):
    script, key = SCRIPTS[which]
    url = db_engine.url
    env = {
        "DB_HOST": str(url.host), "DB_PORT": str(url.port), "DB_USER": str(url.username),
        "DB_PASSWORD": str(url.password), "DB_NAME": str(url.database),
    }
    result = _run(script, "--use-configured-database", env=env)
    assert result.returncode == 0, result.stderr
    assert key in json.loads(result.stdout)


def test_the_helper_returns_the_named_url_and_exits_on_none():
    assert choose_database_url(["--database-url", "postgresql://a/b"], prog="p", description="d") == "postgresql://a/b"
    with pytest.raises(SystemExit) as caught:
        choose_database_url([], prog="p", description="d")
    assert caught.value.code == 2


def test_the_report_runs_in_a_read_only_repeatable_read_transaction(db_engine, capsys):
    url = db_engine.url.render_as_string(hide_password=False)

    def report(session):
        return {
            "isolation": str(session.scalar(text("SELECT current_setting('transaction_isolation')"))),
            "read_only": str(session.scalar(text("SELECT current_setting('transaction_read_only')"))),
        }

    print_read_only_report(url, report)
    assert json.loads(capsys.readouterr().out) == {"isolation": "repeatable read", "read_only": "on"}


@pytest.mark.parametrize("which", sorted(SCRIPTS))
@pytest.mark.parametrize("empty", [["--database-url="], ["--database-url", ""], ["--database-url", "   "]])
def test_an_empty_database_url_is_refused_and_never_falls_back_to_the_configured_database(which, empty):
    """``--database-url "$URL"`` with ``URL`` unset must not read the configured (live) database.

    A listener stands in for the configured database: the script is pointed at it through the DB_* environment, and
    it must exit 2 having made no connection to it at all.
    """
    script, _ = SCRIPTS[which]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.5)
        env = {**UNREACHABLE, "DB_PORT": str(listener.getsockname()[1])}
        result = _run(script, *empty, env=env)
        try:
            listener.accept()
            connected = True
        except TimeoutError:
            connected = False
    assert result.returncode == 2, (empty, result.stderr)
    assert "empty" in result.stderr and result.stdout == ""
    assert not connected, "the script connected to the configured database"


def test_the_helper_refuses_an_empty_url_too():
    for value in ("", "  "):
        with pytest.raises(SystemExit) as caught:
            choose_database_url(["--database-url", value], prog="p", description="d")
        assert caught.value.code == 2


#: ``(script, name of the inventory function the script imports)``.
INVENTORY_FUNCTIONS = {
    "kinetics": "kinetics_coverage_inventory",
    "thermo": "h298_coverage_inventory",
    "network": "network_coverage_inventory",
}


@pytest.mark.parametrize("which", sorted(INVENTORY_FUNCTIONS))
def test_each_script_runs_its_report_inside_a_read_only_repeatable_read_transaction(which, db_engine, capsys, monkeypatch):
    """A script that opened its own transaction instead of the shared helper would not be read-only; this notices.

    The script's inventory function is replaced by a probe that reports the transaction it was given, and the
    script's ``main`` is run in-process against the named database.
    """
    import importlib.util

    script, _ = SCRIPTS[which]
    spec = importlib.util.spec_from_file_location(f"inventory_script_{which}", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def probe(session):
        return {
            "isolation": str(session.scalar(text("SELECT current_setting('transaction_isolation')"))),
            "read_only": str(session.scalar(text("SELECT current_setting('transaction_read_only')"))),
        }

    monkeypatch.setattr(module, INVENTORY_FUNCTIONS[which], probe)
    module.main(["--database-url", db_engine.url.render_as_string(hide_password=False)])
    assert json.loads(capsys.readouterr().out) == {"isolation": "repeatable read", "read_only": "on"}
