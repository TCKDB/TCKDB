"""``scripts/bootstrap_admin.py`` reports a refused bootstrap as a message, not a traceback."""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_admin.py"


@pytest.fixture
def cli(monkeypatch):
    spec = importlib.util.spec_from_file_location("bootstrap_admin_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class _Engine:
        def dispose(self) -> None:
            pass

    class _Session:
        def __init__(self, engine) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def begin(self):
            return contextlib.nullcontext()

    monkeypatch.setattr(module, "create_engine", lambda url: _Engine())
    monkeypatch.setattr(module, "Session", _Session)
    monkeypatch.delenv("TCKDB_BOOTSTRAP_PASSWORD", raising=False)
    return module


def test_a_new_user_without_a_password_exits_2_with_a_hint(cli, monkeypatch, capsys) -> None:
    def _refuse(session, **kwargs):
        assert kwargs["password"] is None
        raise ValueError("password is required when creating a new user")

    monkeypatch.setattr(cli, "bootstrap_user", _refuse)
    assert cli.main(["--username", "admin"]) == 2
    err = capsys.readouterr().err
    assert "error: password is required when creating a new user" in err
    assert "TCKDB_BOOTSTRAP_PASSWORD" in err
    assert "Traceback" not in err
