"""Keep in-process Alembic runs from silently disabling log capture.

Tests here call ``alembic.command`` directly, which execs ``alembic/env.py`` in this process. That file calls
``logging.config.fileConfig``, which by default disables every logger already created and replaces the root handlers,
including the one pytest's ``caplog`` reads. Later tests then assert against an empty ``caplog``.

During a test the harness owns logging configuration, so Alembic's copy of it is a no-op. Alembic's own output is
unaffected on the command line and in deployment.
"""

from __future__ import annotations

import logging.config
from typing import Iterator

import pytest
from sqlalchemy import text

from tests.db._migration_chain import current_head

#: Node id of the first test observed to finish with the shared database away from alembic head.
_FIRST_OFFENDER: str | None = None


@pytest.fixture(autouse=True)
def _alembic_must_not_reconfigure_logging(monkeypatch) -> Iterator[None]:
    """Neutralise ``fileConfig`` for the duration of each test in this tree.

    ``env.py`` re-executes ``from logging.config import fileConfig`` every time Alembic runs it, so patching the
    attribute on the module is enough.
    """
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    yield


@pytest.fixture(scope="session")
def alembic_head() -> str:
    """The revision ``alembic upgrade head`` stops at, read from the script directory."""
    return current_head()


@pytest.fixture(autouse=True)
def _must_leave_the_database_at_head(request, db_engine, alembic_head) -> Iterator[None]:
    """Fail the test that downgraded the shared database, not its victims.

    A test that moves the per-run database off head and does not put it back still passes; the damage lands on whatever
    runs next, in files unrelated to migrations. Checked at teardown: a test may move the version while it runs, but
    must finish where it started. Every later test fails too, since each ran against the wrong schema, but only the
    first is blamed.
    """
    yield
    with db_engine.connect() as connection:
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if version == alembic_head:
        return

    global _FIRST_OFFENDER
    if _FIRST_OFFENDER is None:
        _FIRST_OFFENDER = request.node.nodeid
        blame = "This test left it there."
    else:
        blame = (
            f"{_FIRST_OFFENDER} left it there; this test is downstream of "
            "that, and its result means nothing until that one is fixed."
        )
    raise AssertionError(
        f"the shared test database is at alembic revision {version!r}, not "
        f"head ({alembic_head!r}). {blame} Every test that follows runs "
        "against a different schema, and will fail somewhere unrelated with "
        'no sign of what moved. Restore it with ``command.upgrade(config, "head")``.'
    )
