"""Keep in-process Alembic runs from silently disabling log capture.

``tests/db/test_dataset_release_migration.py`` and
``tests/db/test_upload_job_lease_migration.py`` call ``alembic.command``
directly, which execs ``alembic/env.py`` in *this* process. That file does::

    if config.config_file_name is not None:
        fileConfig(config.config_file_name)

and ``logging.config.fileConfig`` defaults to ``disable_existing_loggers=True``
and replaces the root logger's handlers with the one declared in
``alembic.ini``. The handler it throws away is the one pytest's ``caplog``
fixture reads from, and the loggers it disables include every
``app.*`` logger already created.

Nothing announces this. It reads as five unrelated tests in three other trees
quietly asserting against an empty ``caplog`` — ``assert 'Refusing to fetch
unverified' in ''`` — for the rest of the pytest process, hundreds of tests
after the migration test that caused it. Reproduce it in three seconds with::

    pytest -p no:randomly tests/db/test_upload_job_lease_migration.py \\
        tests/services/test_best_effort_isolation.py \\
        tests/importers/cccbdb/test_url_guard.py

Repairing the damage afterwards is the wrong shape — pytest's logging plugin
adds and removes its own root handlers around each test phase, so restoring a
snapshot taken at setup would put a stale handler list back at teardown.
Preventing it is exact: during a test, the harness owns logging configuration,
so Alembic's copy of it is a no-op. Alembic's own output is unaffected on the
command line and in deployment, where ``env.py`` is untouched.
"""

from __future__ import annotations

import logging.config
from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def _alembic_must_not_reconfigure_logging(monkeypatch) -> Iterator[None]:
    """Neutralise ``fileConfig`` for the duration of each test in this tree.

    ``env.py`` re-executes ``from logging.config import fileConfig`` every time
    Alembic runs it, so patching the attribute on the module is enough — there
    is no early-bound reference to work around.

    Any future test elsewhere that calls ``alembic.command`` in-process needs
    this too; move the fixture up to ``tests/conftest.py`` rather than
    rediscovering the symptom.
    """
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    yield
